"""Transport for postboot actions over the Jetson's USB network interface."""

import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from installer_backend import CACHE, HERE, PROFILES


ROBOTS = {
    "go2": ("Unitree Go2", "/go2/sport_state"),
    "a2": ("Unitree A2", "/lf/sportmodestate"),
    "b2": ("Unitree B2", "/sportmodestate"),
    "ranger-mini": ("AgileX Ranger Mini", "/system_state"),
    "scout-mini": ("AgileX Scout Mini", "/scout_status"),
    "generic": ("사용자 지정", ""),
}
ACTIONS = ("check", "jetpack", "ros", "navigation", "ports", "network", "ethernet", "driver", "verify")
PAYLOADS = {"network": "device-network.json", "driver": "robot-driver.deb", "verify": "acceptance.json"}


def usb_address():
    address = "192.168.55.1"
    routes = json.loads(subprocess.check_output(["ip", "-j", "-4", "route", "get", address], text=True))
    route = routes[0] if routes else {}
    interface = Path("/sys/class/net") / route.get("dev", "") / "device"
    usb = next((p for p in interface.resolve().parents
                if (p / "idVendor").is_file() and (p / "idProduct").is_file()), None)
    if not usb or route.get("gateway") or (usb / "idVendor").read_text().strip() != "0955":
        raise ValueError("부팅된 Jetson의 USB 네트워크 연결을 확인하세요.")
    serial_file = usb / "serial"
    serial = serial_file.read_text().strip() if serial_file.is_file() else ""
    identity = serial if re.fullmatch(r"[A-Za-z0-9-]+", serial) else usb.name
    return address, f"wego-jetson-{identity}"


def postboot(profile_key, login, action, robot, payload="", go2_port="", lidar_port=""):
    if robot not in ROBOTS or action not in ACTIONS:
        raise ValueError("로봇과 Jetson 작업을 확인하세요.")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", login):
        raise ValueError("Jetson 사용자 이름을 확인하세요.")
    if any(port and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", port)
           for port in (go2_port, lidar_port)):
        raise ValueError("이더넷 포트 이름을 확인하세요.")
    local_file = None
    if action in PAYLOADS:
        if not payload:
            raise ValueError("이 작업에 필요한 설정 또는 드라이버 파일을 선택하세요.")
        local_file = Path(payload).resolve(strict=True)
        if not local_file.is_file() or (action == "driver" and local_file.suffix.lower() != ".deb"):
            raise ValueError("올바른 설정 또는 드라이버 파일을 선택하세요.")
    elif payload:
        raise ValueError("이 작업에는 추가 파일이 필요하지 않습니다.")
    profile = PROFILES[profile_key] if profile_key != "auto" else None
    release = [profile.board, profile.jetpack, profile.l4t] if profile else ["auto"] * 3

    address, alias = usb_address()
    CACHE.mkdir(parents=True, exist_ok=True)
    target = f"{login}@{address}"
    with tempfile.TemporaryDirectory(prefix="wego-ssh-") as folder:
        common = ("-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=10", "-o",
                  "ServerAliveCountMax=3", "-o", f"ControlPath={folder}/sock",
                  "-o", f"UserKnownHostsFile={CACHE / 'known_hosts'}", "-o", f"HostKeyAlias={alias}",
                  "-o", "StrictHostKeyChecking=ask")
        subprocess.run(["ssh", *common, "-M", "-N", "-f", "--", target], check=True)
        remote = None
        try:
            remote = subprocess.check_output(
                ["ssh", *common, "--", target, "mktemp", "-d", "/tmp/wego-jetson.XXXXXXXX"],
                text=True,
            ).strip()
            if not re.fullmatch(r"/tmp/wego-jetson\.[A-Za-z0-9]{8}", remote):
                remote = None
                raise ValueError("Jetson에서 임시 폴더를 만들지 못했습니다.")
            host_files = ["postboot.sh", "configure-ethernet.py"]
            if action == "ros":
                host_files.append("robot_stack.sh")
            if action == "navigation" and robot == "go2":
                host_files.append("go2_navigation")
            if action == "verify":
                host_files.append("verify_ros.py")
            subprocess.run(["scp", "-r", *common, *(str(HERE / f) for f in host_files),
                            f"{target}:{remote}/"], check=True)
            if local_file:
                subprocess.run(["scp", *common, str(local_file),
                                f"{target}:{remote}/{PAYLOADS[action]}"], check=True)
            command = shlex.join(["bash", f"{remote}/postboot.sh", *release,
                                  action, go2_port, lidar_port, robot])
            subprocess.run(["ssh", *common, "-t", "--", target, command], check=True)
        finally:
            if remote:
                subprocess.run(["ssh", *common, "--", target, "rm", "-rf", "--", remote], check=False)
            subprocess.run(["ssh", *common, "-O", "exit", "--", target], check=False)
