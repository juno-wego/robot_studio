#!/usr/bin/env bash
set -euo pipefail

robot="$1"
ros_distro="$2"
workspace="${WEGO_ROBOT_WS_ROOT:-$HOME}/wego_${robot//-/_}_ws"
mkdir -p "$workspace/src"
exec 9>"$workspace/.install.lock"
flock -n 9 || { echo '이 로봇의 드라이버를 이미 설치 중입니다.' >&2; exit 1; }

checkout() {
    local name="$1" url="$2" revision="$3" stage
    local target="$workspace/src/$name"
    if [[ -e "$target" ]]; then
        if [[ "$(git -C "$target" rev-parse HEAD)" != "$revision" ]]; then
            printf '기존 드라이버 버전이 다릅니다. %s\n' "$target" >&2
            return 1
        fi
        return
    fi
    stage="$(mktemp -d "$workspace/.source.XXXXXXXX")"
    if ! (
        set -e
        git -C "$stage" init -q &&
        git -C "$stage" remote add origin "$url" &&
        git -C "$stage" fetch --depth=1 origin "$revision" &&
        git -C "$stage" checkout --detach FETCH_HEAD &&
        git -C "$stage" submodule update --init --recursive &&
        mv -T --no-clobber "$stage" "$target" &&
        [[ ! -e "$stage" ]]
    ); then
        rm -rf -- "$stage"
        return 1
    fi
}

case "$robot" in
    a2|b2)
        checkout unitree_sdk2 https://github.com/unitreerobotics/unitree_sdk2.git b7fec6020eb93d4c059a6edf19905e12a75a8d3d
        checkout unitree_ros2 https://github.com/unitreerobotics/unitree_ros2.git 668d1ec5a05d1c38d3306bdca7d59f2ba3581a88
        cmake -S "$workspace/src/unitree_sdk2" -B "$workspace/sdk-build" \
            -DCMAKE_BUILD_TYPE=Release -DBUILD_EXAMPLES=OFF -DCMAKE_INSTALL_PREFIX="$workspace/sdk"
        cmake --build "$workspace/sdk-build" --parallel 2
        cmake --install "$workspace/sdk-build"
        packages="$workspace/src/unitree_ros2/cyclonedds_ws/src/unitree"
        ;;
    ranger-mini|scout-mini)
        sdk_revision=f2704eacdc90357078cd93ec60aae08bb4baab35
        if [[ "$robot" == ranger-mini ]]; then
            revision=b6ea21a275ca5e7168130cc6470e61474681d679
            if [[ "$ros_distro" == jazzy ]]; then
                # Jazzy's Mini V1/V2 driver uses RangerRobot(bool).
                revision=a1bc20675ae3655d4048b8e4b1139914de159b52
                sdk_revision=012a9aa02436ffe2ef66f211dd193be7b69902ba
            fi
            checkout ranger_ros2 https://github.com/agilexrobotics/ranger_ros2.git "$revision"
        else
            revision=bf0110ff44cc70cdda2931d69f0e2669a3e26b9d
            if [[ "$ros_distro" == jazzy ]]; then revision=48f70cbefa74b94d4442ed1a4ac9237a088abbb7; fi
            checkout scout_ros2 https://github.com/agilexrobotics/scout_ros2.git "$revision"
        fi
        checkout ugv_sdk https://github.com/agilexrobotics/ugv_sdk.git "$sdk_revision"
        packages="$workspace/src"
        ;;
    *) exit 2 ;;
esac

cd -- "$workspace"
# ROS setup hooks can refer to optional, unset variables.
set +u
source "/opt/ros/$ros_distro/setup.bash"
set -u
CMAKE_BUILD_PARALLEL_LEVEL=2 colcon build --base-paths "$packages" \
    --parallel-workers 2 --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DPython3_EXECUTABLE=/usr/bin/python3
printf '드라이버 워크스페이스 %s\nsource %s/install/setup.bash\n' "$workspace" "$workspace"
