#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C.UTF-8

if (( $# < 6 || $# > 7 )); then
    echo 'Usage: postboot.sh board jetpack l4t action go2_port lidar_port [robot]' >&2
    exit 2
fi
board="$1"
jetpack="$2"
l4t="$3"
action="$4"
go2_port="$5"
lidar_port="$6"
robot="${7:-go2}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$script_dir"

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

case "$robot" in go2|a2|b2|ranger-mini|scout-mini|generic) ;;
    *) fail "지원하지 않는 로봇 프로필입니다. $robot" ;;
esac
case "$action" in check|verify|jetpack|ros|driver|navigation|ports|ethernet|network) ;;
    *) fail "지원하지 않는 작업입니다. $action" ;;
esac
if [[ "$(dpkg --print-architecture)" != arm64 ]]; then
    fail 'Ubuntu가 부팅된 ARM64 Jetson에서 실행하세요.'
fi
if (( EUID == 0 )); then
    fail 'Jetson의 일반 Ubuntu 계정에서 설치를 실행하세요.'
fi
model="$(python3 configure-ethernet.py --board "$board" --verify-board)"
read -r -a os_identity <<< "$(. /etc/os-release && printf '%s %s\n' "$ID" "${UBUNTU_CODENAME:-${VERSION_CODENAME:-unknown}}")"
if [[ "${os_identity[0]}" != ubuntu ]]; then
    fail 'Ubuntu가 부팅된 Jetson에서 실행하세요.'
fi
codename="${os_identity[1]}"
case "$codename" in
    jammy) ros=humble ;;
    noble) ros=jazzy ;;
    *) ros='' ;;
esac
release="$(head -n 1 /etc/nv_tegra_release)"
release_pattern='^# R([0-9]+) \(release\), REVISION: ([0-9]+)\.([0-9]+),'
[[ "$release" =~ $release_pattern ]] || fail '현재 Jetson의 L4T 버전을 읽을 수 없습니다.'
installed_l4t="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}"
if [[ "$board" == auto && "$l4t" == auto ]]; then
    l4t="$installed_l4t"
elif [[ "$l4t" != "$installed_l4t" ]]; then
    fail "선택한 L4T는 $l4t, 현재 Jetson의 L4T는 $installed_l4t 입니다."
fi
printf '%s · L4T %s · Ubuntu %s\n' "$model" "$l4t" "$codename"

# Serialize writes from concurrent installer sessions for the same Jetson account.
if [[ "$action" != check && "$action" != verify && "$action" != ports ]]; then
    mkdir -p "$HOME/.cache/wego-jetson"
    exec 9>"$HOME/.cache/wego-jetson/setup.lock"
    flock -n 9 || fail '이 Jetson에서 이미 다른 설치 작업을 실행 중입니다.'
fi

apt_install() {
    # Keep the flashed vendor kernel, device trees and bootloader on the board.
    # Refuse any plan that changes an installed NVIDIA L4T package.
    local plan changed preferences="$script_dir/jetson-apt.pref"
    local apt=(apt-get -o "Dir::Etc::Preferences=$preferences" -o DPkg::Lock::Timeout=60)
    printf 'Package: nvidia-l4t-*\nPin: version %s-*\nPin-Priority: 995\n\n' "$l4t" > "$preferences"
    if [[ -r /etc/apt/preferences ]]; then cat /etc/apt/preferences >> "$preferences"; fi
    plan="$("${apt[@]}" -s --no-remove install "$@")" || { printf '%s\n' "$plan"; return 1; }
    if grep -E '^Inst nvidia-l4t-[^ ]+ \[' <<< "$plan"; then
        echo '이 설치는 현재 L4T 구성 요소를 바꾸므로 중단했습니다.' >&2
        return 1
    fi
    changed="$(awk -v target="$l4t-" '/^Inst nvidia-l4t-/ && index($3, "(" target) != 1 {print}' <<< "$plan")"
    if [[ -n "$changed" ]]; then
        printf '설치하려는 L4T 패키지가 %s 버전과 다릅니다.\n%s\n' "$l4t" "$changed" >&2
        return 1
    fi
    sudo "${apt[@]}" install -y --no-remove "$@"
}

jetpack_for_l4t() {
    # Explicit matches follow NVIDIA's JetPack archive. An OEM's newer or
    # differently numbered BSP must use the OEM's documented package versions.
    case "$1" in
        32.7.6) echo 4.6.6 ;;
        32.7.5) echo 4.6.5 ;;
        32.7.4) echo 4.6.4 ;;
        32.7.3) echo 4.6.3 ;;
        32.7.2) echo 4.6.2 ;;
        32.7.1) echo 4.6.1 ;;
        35.7.0) echo 5.1.8 ;;
        35.6.5) echo 5.1.7 ;;
        35.6.4) echo 5.1.6 ;;
        35.6.1|35.6.2) echo 5.1.5 ;;
        35.6.0) echo 5.1.4 ;;
        35.5.0) echo 5.1.3 ;;
        35.4.1) echo 5.1.2 ;;
        35.3.1) echo 5.1.1 ;;
        35.2.1) echo 5.1 ;;
        36.3.0) echo 6.0 ;;
        36.4.0) echo 6.1 ;;
        36.4.3) echo 6.2 ;;
        36.4.4) echo 6.2.1 ;;
        36.5.0) echo 6.2.2 ;;
        36.5.2) echo 6.2.3 ;;
        38.2.0|38.2.1) echo 7.0 ;;
        38.4.0) echo 7.1 ;;
        39.2.0) echo 7.2 ;;
        39.2.1) echo 7.2.1 ;;
        39.2.2) echo 7.2.2 ;;
        *) return 1 ;;
    esac
}

prepare_ros_apt() {
    [[ -n "$ros" ]] || fail 'ROS 2 자동 설치는 Ubuntu 22.04(Humble)와 24.04(Jazzy)에서 지원합니다.'
    sudo apt-get update
    if [[ -z "$(apt-cache madison "ros-$ros-desktop")" ]]; then
        local version deb
        apt_install curl ca-certificates software-properties-common
        sudo add-apt-repository -yn universe
        version="$(curl --proto '=https' -fsSL --max-time 60 -o /dev/null -w '%{url_effective}' \
            https://github.com/ros-infrastructure/ros-apt-source/releases/latest)"
        version="${version##*/}"
        [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'ROS 2 APT 버전을 확인할 수 없습니다.'
        deb="ros2-apt-source_${version}.${codename}_all.deb"
        curl --proto '=https' -fSL --retry 2 --max-time 120 -o "$deb" \
            "https://github.com/ros-infrastructure/ros-apt-source/releases/download/$version/$deb"
        [[ "$(dpkg-deb -f "$deb" Package)" == ros2-apt-source && \
           "$(dpkg-deb -f "$deb" Architecture)" == all ]] || fail 'ROS 2 APT 패키지를 확인할 수 없습니다.'
        apt_install "./$deb"
        sudo apt-get update
    fi
}

install_go2_driver() {
    local workspace="$HOME/wego_ws"
    if [[ ! -e "$workspace" ]]; then
        # Stage a new checkout so an interrupted clone can be retried.
        staging="$(mktemp -d "$HOME/.wego-ws.XXXXXXXX")"
        trap 'rm -rf -- "$staging"' EXIT
        git clone https://github.com/juno-wego/go2_setup.git "$staging"
        git -C "$staging" checkout --detach fab4092f242fc02837d4aeeeb36d933776cb0f50
        git -C "$staging" submodule update --init --recursive
        mv -T --no-clobber -- "$staging" "$workspace"
        [[ ! -e "$staging" ]]
        trap - EXIT
    fi
    cd -- "$workspace"
    WEGO_ROS_DISTRO="$ros" bash ./build.sh
    printf '실행 명령\nsource ~/wego_ws/install/setup.bash\nros2 launch wego bringup.launch.py\n'
}

install_go2_mapping() {
    local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    local dest="$data_home/wego-jetson/go2_navigation"
    local name
    mkdir -p "$dest"
    for name in mapping.launch.py slam_params.yaml; do
        # Leave edited parameters alone when reinstalling navigation packages.
        if [[ ! -e "$dest/$name" && ! -L "$dest/$name" ]]; then
            install -m 0644 "$script_dir/go2_navigation/$name" "$dest/$name"
        fi
    done
    printf 'Go2 드라이버와 MID360 실행 후 별도 터미널에서 매핑 시작\n'
    printf 'source ~/wego_ws/install/setup.bash\nros2 launch %q\n' "$dest/mapping.launch.py"
    printf '지도 저장\nros2 run nav2_map_server map_saver_cli -f ~/map\n'
}

case "$action" in
    check)
        packages=(nvidia-jetpack)
        if [[ -n "$ros" ]]; then
            packages+=("ros-$ros-desktop" "ros-$ros-slam-toolbox" "ros-$ros-navigation2" "ros-$ros-nav2-bringup")
        fi
        dpkg-query -W -f='${Package} ${Version}\n' "${packages[@]}" 2>/dev/null || true
        ip -br -4 addr show dev go2 2>/dev/null || true
        ip -br -4 addr show dev mid360 2>/dev/null || true
        if [[ -f "$HOME/wego_ws/install/setup.bash" ]]; then
            echo 'wego_ws 빌드 결과 있음'
        fi
        ;;
    verify)
        [[ -n "$ros" && -f "/opt/ros/$ros/setup.bash" ]] || fail '설치된 ROS 2 환경을 찾을 수 없습니다.'
        [[ -f verify_ros.py && -f acceptance.json ]] || fail 'ROS 확인 스크립트와 검증 설정이 필요합니다.'
        # The generated ROS setup scripts can read unset shell variables.
        set +u
        source "/opt/ros/$ros/setup.bash"
        if [[ "$robot" == go2 && -f "$HOME/wego_ws/install/setup.bash" ]]; then
            source "$HOME/wego_ws/install/setup.bash"
        elif [[ "$robot" != generic && -f "$HOME/wego_${robot//-/_}_ws/install/setup.bash" ]]; then
            source "$HOME/wego_${robot//-/_}_ws/install/setup.bash"
        fi
        set -u
        /usr/bin/python3 "$script_dir/verify_ros.py" "$script_dir/acceptance.json"
        ;;
    jetpack)
        if [[ "$jetpack" == auto ]]; then
            jetpack="$(jetpack_for_l4t "$l4t")" || fail "L4T $l4t에 맞는 JetPack 패키지는 제조사 자료를 확인하세요."
        fi
        sudo apt-get update
        version="$(apt-cache madison nvidia-jetpack | awk -F'|' -v target="$jetpack" '
            { gsub(/ /,"",$2); if (index($2, target "+")==1 || index($2, target "-")==1 || $2==target) { print $2; exit } }')"
        if [[ -z "$version" ]]; then
            echo "NVIDIA APT 저장소에서 JetPack $jetpack 패키지를 찾을 수 없습니다." >&2
            exit 1
        fi
        apt_install "nvidia-jetpack=$version"
        dpkg-query -W nvidia-jetpack
        ;;
    ros)
        if [[ "$robot" == go2 && -e "$HOME/wego_ws" && ! -f "$HOME/wego_ws/build.sh" ]]; then
            fail '기존 ~/wego_ws 폴더에 build.sh가 없습니다.'
        fi
        if [[ "$robot" != go2 && "$robot" != generic && ! -f robot_stack.sh ]]; then
            fail '로봇 드라이버 설치 파일(robot_stack.sh)을 전송하세요.'
        fi
        prepare_ros_apt
        packages=("ros-$ros-desktop" "ros-$ros-rmw-cyclonedds-cpp" python3-colcon-common-extensions)
        if [[ "$robot" != generic ]]; then
            packages+=(git build-essential cmake)
        fi
        case "$robot" in
            ranger-mini|scout-mini) packages+=(libasio-dev libboost-all-dev can-utils) ;;
            a2|b2) packages+=(libeigen3-dev "ros-$ros-rosidl-generator-dds-idl") ;;
        esac
        if [[ "$robot" == go2 ]]; then
            packages+=(
                "ros-$ros-realsense2-camera" "ros-$ros-pcl-conversions" "ros-$ros-xacro"
                "ros-$ros-compressed-image-transport" "ros-$ros-ament-cmake-auto" "ros-$ros-rosbag2"
                pkg-config libusb-1.0-0-dev libssl-dev libeigen3-dev
                libpcl-dev libapr1-dev libboost-all-dev python3-opencv python3-numpy python3-yaml
            )
        fi
        apt_install "${packages[@]}"
        case "$robot" in
            go2) install_go2_driver ;;
            generic) ;;
            *) bash "$script_dir/robot_stack.sh" "$robot" "$ros" ;;
        esac
        printf 'ROS 2 환경\nsource /opt/ros/%s/setup.bash\n' "$ros"
        ;;
    driver)
        [[ -f robot-driver.deb ]] || fail '제조사 로봇 또는 센서 드라이버 .deb 파일을 선택하세요.'
        package="$(dpkg-deb -f robot-driver.deb Package)"
        arch="$(dpkg-deb -f robot-driver.deb Architecture)"
        version="$(dpkg-deb -f robot-driver.deb Version)"
        [[ "$package" =~ ^[a-z0-9][a-z0-9.+-]+$ && -n "$version" ]] || fail '올바른 .deb 패키지가 아닙니다.'
        [[ "$arch" == arm64 || "$arch" == all ]] || fail "Jetson용 arm64 또는 all 패키지가 필요합니다. 감지된 아키텍처: $arch"
        printf '로봇 드라이버 · %s %s (%s)\n' "$package" "$version" "$arch"
        sudo apt-get update
        apt_install ./robot-driver.deb
        ;;
    navigation)
        if [[ "$robot" == go2 ]]; then
            [[ -f go2_navigation/mapping.launch.py && -f go2_navigation/slam_params.yaml ]] ||
                fail 'Go2 매핑 설정 파일을 설치 프로그램과 함께 전송하세요.'
        fi
        prepare_ros_apt
        apt_install "ros-$ros-slam-toolbox" "ros-$ros-navigation2" "ros-$ros-nav2-bringup" \
            "ros-$ros-pointcloud-to-laserscan"
        if [[ "$robot" == go2 ]]; then install_go2_mapping; fi
        printf 'Navigation2 설치 완료. 실물 주행 전에 로봇의 속도·외곽 크기·정지 동작을 검증하세요.\n'
        ;;
    ports)
        python3 configure-ethernet.py --board "$board" --list
        ;;
    network)
        [[ -f device-network.json ]] || fail '장치 포트 설정 파일이 필요합니다.'
        command -v nmcli >/dev/null || fail '장치 포트 저장에는 NetworkManager가 필요합니다.'
        if ! command -v ethtool >/dev/null; then
            sudo apt-get update
            apt_install ethtool
        fi
        python3 configure-ethernet.py --board "$board" --config device-network.json
        sudo python3 configure-ethernet.py --board "$board" --config device-network.json --apply
        ;;
    ethernet)
        [[ "$robot" == go2 ]] || fail '이더넷 설정은 Go2 및 MID360 프로필용입니다.'
        if [[ "$board" == auto || "$board" == P3737 || "$board" == P4071 ]]; then
            [[ -n "$go2_port" && -n "$lidar_port" ]] || fail 'Go2와 MID360의 포트를 각각 지정하세요.'
            command -v nmcli >/dev/null || fail '이 보드의 네트워크는 제조사 절차대로 설정하세요. 자동 저장에는 NetworkManager가 필요합니다.'
        fi
        if ! command -v nmcli >/dev/null; then
            sudo apt-get update
            apt_install network-manager
        fi
        if ! command -v ethtool >/dev/null; then
            sudo apt-get update
            apt_install ethtool
        fi
        options=(--board "$board")
        if [[ -n "$go2_port" ]]; then options+=(--go2-interface "$go2_port"); fi
        if [[ -n "$lidar_port" ]]; then options+=(--lidar-interface "$lidar_port"); fi
        sudo python3 configure-ethernet.py "${options[@]}"
        sudo python3 configure-ethernet.py "${options[@]}" --apply
        ;;
    *) exit 2 ;;
esac
