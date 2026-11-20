# Robot Studio

Ubuntu에서 Jetson 플래싱, 로봇 소프트웨어 설치, ROS 토픽 검수를 진행하는 Qt 6 C++ 도구입니다.

## 설치

[Releases](https://github.com/juno-wego/robot_studio/releases)에서 호스트 Ubuntu 버전에 맞는 amd64 `.deb`를 받은 뒤 설치합니다.

```sh
sudo apt install ./wego-jetson_*.deb
```

앱 목록에서 **Robot Studio**를 실행합니다. 다운로드와 로그는 기본적으로 `~/.cache/wego-jetson/`에 저장됩니다.

| 캐리어보드 | Jetson 모듈 | JetPack | 설치 방식 |
|---|---|---|---|
| NVIDIA P3768 | Orin NX / Nano | 6.2, 6.2.1 | NVMe |
| NVIDIA P3737 | AGX Orin | 6.2.1 | eMMC |
| NVIDIA P4071 | Thor T5000 / T4000 | 7.2.1 | NVMe |
| AVerMedia NX215B (D115) | Orin NX / Nano | 6.2.1 | NVMe, NX 16GB에서 QSPI 전용 |
| AVerMedia D135 | Orin NX / Nano | 6.2.1 | NVMe |

USB 리커버리에서는 Jetson 모듈을 자동으로 표시합니다. 캐리어보드는 실물 모델을 선택합니다. 다른 NVIDIA 보드는 SDK Manager 호환 목록을 사용합니다. 제조사 보드는 해당 BSP 파일, 제조사 SDK Manager extra config 또는 설치 스크립트로 진행합니다.

| 로봇 | 설치 구성 |
|---|---|
| Unitree Go2 | WeGo 드라이버, MID360·D435i 지원, Go2 2D 매핑 예제 |
| Unitree A2, B2 | 제조사 SDK2 및 ROS 2 메시지 |
| AgileX Ranger Mini | 제조사 ROS 2 드라이버 및 UGV SDK. Humble V1–V3, Jazzy V1–V2 |
| AgileX Scout Mini | 제조사 ROS 2 드라이버 및 UGV SDK |
| 사용자 지정 | ROS 2 및 별도로 추가하는 제조사 ARM64 드라이버 패키지 |

부팅 후 설치는 Ubuntu 22.04/Humble 및 24.04/Jazzy 대상입니다. 센서 포트와 검수 토픽은 장비별로 입력합니다. SLAM Toolbox와 Nav2를 설치할 수 있고, 주행 파라미터는 로봇과 센서에 맞게 설정합니다. 매뉴얼 탭에서 [설치·검수](docs/usage.html), [SLAM·Navigation](docs/navigation.html) 안내를 열고 문서를 장비와 버전으로 등록할 수 있습니다.

## 빌드 및 배포

Ubuntu 22.04/24.04 x86-64에서 아래 순서로 빌드합니다.

```sh
sudo apt install cmake ninja-build g++ qt6-base-dev python3-venv
python3 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DPython3_EXECUTABLE="$PWD/.venv/bin/python"
cmake --build build
ctest --test-dir build --output-on-failure
cpack --config build/CPackConfig.cmake -G DEB -B dist
```

`dist/`의 `.deb`가 배포 파일입니다. Qt 실행 파일과 설치 도우미를 포함합니다. BSP와 SDK는 앱에서 해당 공급처로부터 받습니다. GitHub Actions는 브랜치에서 두 Ubuntu 버전을 빌드하고, `v0.1.0` 형태의 태그에서 `.deb`와 SHA-256 목록을 Release에 첨부합니다.

[JetPack 아카이브](https://developer.nvidia.com/embedded/jetpack-archive) · [NVIDIA SDK Manager](https://developer.nvidia.com/sdk-manager) · [AVerMedia BSP](https://professional.avermedia.com/support/download?model_number=D135)
