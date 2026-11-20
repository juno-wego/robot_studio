"""Download and prepare board-specific Jetson Linux releases."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "wego-jetson"
NVIDIA = "https://developer.download.nvidia.com/embedded/L4T"
AVERMEDIA = "https://s3.us-west-2.amazonaws.com/storage.avermedia.com/web_release_www"
USB_MODELS = {
    "7323": "Orin NX 16GB", "7423": "Orin NX 8GB",
    "7523": "Orin Nano 8GB", "7623": "Orin Nano 4GB",
    "7023": "Jetson AGX Orin / Industrial", "7223": "Jetson AGX Orin 32GB",
    "7026": "Jetson Thor T5000", "7226": "Jetson Thor T4000",
}
UNSUPPORTED_USB_MODELS = {
    "7e19": "Jetson Xavier NX",
    "7019": "Jetson AGX Xavier", "7819": "Jetson AGX Xavier Industrial",
    "7919": "Jetson AGX Xavier Industrial", "7f21": "Jetson Nano",
    "7c18": "Jetson TX2 / TX2 NX", "7018": "Jetson TX2i",
    "7418": "Jetson TX2 4GB", "7721": "Jetson TX1",
}
APX_IDS = USB_MODELS.keys() | UNSUPPORTED_USB_MODELS.keys()
NVME = "NVMe 새로 설치"
QSPI = "QSPI만 설치 · 복제 SSD 보존"
EMMC = "내장 eMMC 새로 설치"
ORIN_NX_NANO_PIDS = frozenset(("7323", "7423", "7523", "7623"))
THOR_BOARD_CONFIGS = {
    "7026": "jetson-agx-thor-devkit", "7226": "jetson-agx-thor-t4000",
}
AGX_ORIN_SKUS = {
    "7023": frozenset(("0000", "0005", "0008")), "7223": frozenset(("0004",)),
}
NVIDIA_BOARD_NAMES = {
    "P3768": "NVIDIA 정품 P3768 · Orin NX/Nano",
    "P3737": "NVIDIA 정품 P3737 · AGX Orin",
    "P4071": "NVIDIA 정품 P4071 · AGX Thor",
}


@dataclass(frozen=True)
class Artifact:
    filename: str
    url: str
    size: int
    sha1: str = ""


@dataclass(frozen=True)
class Profile:
    key: str
    board: str
    label: str
    jetpack: str
    l4t: str
    artifacts: tuple[Artifact, ...]
    vendor: bool = False

    @property
    def folder(self):
        return CACHE / self.key

    @property
    def modes(self):
        if self.board == "P3737":
            return (EMMC,)
        return (QSPI, NVME) if self.board == "D115" else (NVME,)

    @property
    def supported_pids(self):
        if self.board in ("P3768", "D115", "D135"):
            return ORIN_NX_NANO_PIDS
        if self.board == "P3737":
            return AGX_ORIN_SKUS.keys()
        if self.board == "P4071":
            return THOR_BOARD_CONFIGS.keys()
        return ()

    def supports_pid(self, pid, mode):
        return (mode in self.modes and pid in self.supported_pids
                and (mode != QSPI or pid == "7323"))


def nvidia_profile(board, jetpack, l4t, sizes, hashes):
    major, revision = l4t.split(".", 1)
    base = f"{NVIDIA}/r{major}_Release_v{revision}/release"
    files = ["Jetson_Linux", "Tegra_Linux_Sample-Root-Filesystem"]
    names = [f"{file}_R{l4t}_aarch64.tbz2" for file in files]
    artifacts = tuple(
        Artifact(name, f"{base}/{name}", size, digest)
        for name, size, digest in zip(names, sizes, hashes)
    )
    return Profile(f"{board.lower()}-{jetpack}", board, NVIDIA_BOARD_NAMES[board], jetpack, l4t, artifacts)


_av_release = "AVERMEDIA_JETPACK-R1.2.3.6.2.1"
_av_file = f"{_av_release}_desktop.tar.gz"
_av_artifacts = (
    Artifact(_av_file, f"{AVERMEDIA}/BSP+{_av_release}/{_av_file}", 3675457599),
)
# Published NVIDIA release_sha_hashes.txt values. The two R36.4.4 carrier
# profiles intentionally share files in the same download cache.
_r3644_sizes = (746324969, 1831285525)
_r3644_hashes = ("1039c377717e443cbabd9a1a719162dd84ab4678", "73df3f66ad77f29d1424d61dbb45d5587090c912")
PROFILES = {profile.key: profile for profile in (
    Profile("d115-6.2.1", "D115", "AVerMedia NX215B (D115)", "6.2.1", "36.4.4", _av_artifacts, True),
    Profile("d135-6.2.1", "D135", "AVerMedia D135", "6.2.1", "36.4.4", _av_artifacts, True),
    nvidia_profile("P3768", "6.2.1", "36.4.4", _r3644_sizes, _r3644_hashes),
    nvidia_profile("P3768", "6.2", "36.4.3", (715765074, 1827877026),
                   ("3eb3c5a19a417313383c3bce297e07274a237e36", "0bdb4e655d48bdf7e7bd98d3b7b69480576bfd7e")),
    nvidia_profile("P3737", "6.2.1", "36.4.4", _r3644_sizes, _r3644_hashes),
    nvidia_profile("P4071", "7.2.1", "39.2.1", (1287223769, 2022864741),
                   ("8fe9a3ac9a98550b6077df450441599141ba47fb", "9a991a4f94b09304d10a73c89da7d5feae11c303")),
)}


def artifact_path(profile, artifact):
    directory = CACHE / "downloads" / ("avermedia" if profile.vendor else "nvidia")
    return directory / artifact.filename


def check_archive(path, artifact):
    if not path.is_file() or path.stat().st_size != artifact.size:
        raise ValueError(f"다운로드가 완료되지 않았습니다. {artifact.filename}")
    if artifact.sha1:
        digest = hashlib.sha1(usedforsecurity=False)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != artifact.sha1:
            raise ValueError(f"NVIDIA 체크섬 불일치. {artifact.filename}")
    else:
        with path.open("rb") as stream:
            if stream.read(2) != b"\x1f\x8b":
                raise ValueError(f"올바른 gzip 파일이 아닙니다. {artifact.filename}")


def download_vendor_ranges(artifact, partial, have, update, cancelled):
    # The vendor's S3 file supports byte ranges. Write blocks in order so an
    # interrupted download can still resume from a single contiguous file.
    block_size = 16 * 1024 * 1024
    workers = 6

    def fetch(start):
        end = min(start + block_size, artifact.size) - 1
        request = Request(artifact.url, headers={"Range": f"bytes={start}-{end}"})
        with urlopen(request, timeout=30) as source:
            if source.status != 206 or source.headers.get("Content-Range") != (
                f"bytes {start}-{end}/{artifact.size}"
            ):
                raise ValueError("AVerMedia 서버의 파일 범위 응답이 올바르지 않습니다.")
            expected = end - start + 1
            block = source.read(expected + 1)
            if len(block) != expected:
                raise ValueError("AVerMedia 서버에서 파일 일부만 받았습니다.")
            return block

    with partial.open("ab") as stream, ThreadPoolExecutor(workers) as pool:
        while have < artifact.size:
            if cancelled():
                raise InterruptedError("다운로드를 일시 중지했습니다.")
            starts = range(have, min(have + workers * block_size, artifact.size), block_size)
            for block in pool.map(fetch, starts):
                if cancelled():
                    raise InterruptedError("다운로드를 일시 중지했습니다.")
                stream.write(block)
                have += len(block)
                update(have)


def download(profile, report=print, progress=lambda *_: None, cancelled=lambda: False):
    total = sum(artifact.size for artifact in profile.artifacts)
    completed = 0
    for artifact in profile.artifacts:
        if cancelled():
            raise InterruptedError("다운로드를 일시 중지했습니다.")
        target = artifact_path(profile, artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        with target.with_suffix(target.suffix + ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(f"다른 창에서 다운로드 중입니다. {artifact.filename}") from None
            if target.exists():
                report(f"저장된 파일 확인 중 · {artifact.filename}")
                try:
                    check_archive(target, artifact)
                except ValueError:
                    target.unlink()
                else:
                    completed += artifact.size
                    progress(completed, total)
                    continue
            have = partial.stat().st_size if partial.exists() else 0
            if have > artifact.size:
                partial.unlink()
                have = 0
            if shutil.disk_usage(target.parent).free < artifact.size - have:
                raise ValueError("다운로드 폴더의 여유 공간이 부족합니다.")
            report(f"다운로드 중 · {artifact.filename}")
            if have < artifact.size and profile.vendor:
                download_vendor_ranges(artifact, partial, have,
                                       lambda count: progress(completed + count, total), cancelled)
            elif have < artifact.size:
                headers = {"Range": f"bytes={have}-"} if have else {}
                with urlopen(Request(artifact.url, headers=headers), timeout=30) as source:
                    if have and source.status == 200:
                        have = 0
                    elif source.status == 206 and source.headers.get("Content-Range") != (
                        f"bytes {have}-{artifact.size - 1}/{artifact.size}"
                    ):
                        raise ValueError(f"서버의 다운로드 범위가 올바르지 않습니다. {artifact.filename}")
                    elif source.status not in (200, 206):
                        raise ValueError(f"예상하지 못한 다운로드 응답. HTTP {source.status}")
                    with partial.open("ab" if have else "wb") as stream:
                        while (chunk := source.read(2 * 1024 * 1024)):
                            if cancelled():
                                raise InterruptedError("다운로드를 일시 중지했습니다.")
                            have += len(chunk)
                            if have > artifact.size:
                                raise ValueError(f"서버 파일 크기가 변경되었습니다. {artifact.filename}")
                            stream.write(chunk)
                            progress(completed + have, total)
            report(f"파일 검증 중 · {artifact.filename}")
            try:
                check_archive(partial, artifact)
                if profile.vendor:
                    subprocess.run(["gzip", "-t", str(partial)], check=True)
            except (ValueError, subprocess.CalledProcessError):
                if partial.exists() and partial.stat().st_size == artifact.size:
                    partial.unlink()
                raise
            partial.replace(target)
            completed += artifact.size
            progress(completed, total)
    report("다운로드 완료")


@dataclass(frozen=True)
class RecoveryDevice:
    pid: str
    port: str

    @property
    def model(self):
        return USB_MODELS.get(self.pid) or UNSUPPORTED_USB_MODELS[self.pid]


class RecoveryMissingError(ValueError):
    pass


def recovery_device():
    devices = []
    for device in Path("/sys/bus/usb/devices").iterdir():
        try:
            if (device / "idVendor").read_text().strip() != "0955":
                continue
            pid = (device / "idProduct").read_text().strip()
            if pid in APX_IDS and re.fullmatch(r"[0-9]+-[0-9]+(?:\.[0-9]+)*", device.name):
                devices.append(RecoveryDevice(pid, device.name))
        except (FileNotFoundError, NotADirectoryError):
            continue
    if not devices:
        raise RecoveryMissingError("USB 리커버리 장치가 없습니다.")
    if len(devices) != 1:
        raise ValueError("리커버리 모드의 Jetson을 한 대만 연결하세요.")
    return devices[0]


def recovery_pid():
    device = recovery_device()
    if device.pid not in USB_MODELS:
        raise ValueError(f"{device.model} (0955:{device.pid}) 감지됨. NVIDIA SDK Manager 또는 제조사 BSP를 사용하세요.")
    return device.pid


def require_recovery(expected_pid, expected_port=None, reconnect=False):
    deadline = time.monotonic() + 10
    while True:
        try:
            device = recovery_device()
        except RecoveryMissingError:
            if not reconnect or time.monotonic() >= deadline:
                raise
            time.sleep(0.2)
        else:
            if device.pid != expected_pid or (expected_port and device.port != expected_port):
                raise ValueError("리커버리 장치 또는 USB 포트가 바뀌었습니다.")
            return device


def run(*arguments, cwd=None, input=None):
    return subprocess.run(arguments, cwd=cwd, input=input, check=True, text=True)


def flash(profile, mode, expected_pid, expected_port=None):
    if not profile.supports_pid(expected_pid, mode):
        raise ValueError("선택한 캐리어보드, JetPack, 모듈 또는 설치 방식이 맞지 않습니다.")
    if platform.machine() != "x86_64" or platform.freedesktop_os_release().get("ID") != "ubuntu":
        raise ValueError("플래싱은 x86-64 Ubuntu PC에서 실행하세요.")
    if re.search(r"\s", str(profile.folder)):
        raise ValueError("NVIDIA 플래싱 도구는 공백이 있는 작업 경로를 지원하지 않습니다.")
    device = require_recovery(expected_pid, expected_port)
    archives = [artifact_path(profile, artifact) for artifact in profile.artifacts]
    for path, artifact in zip(archives, profile.artifacts):
        print(f"파일 확인 중 · {path.name}", flush=True)
        check_archive(path, artifact)
    profile.folder.mkdir(parents=True, exist_ok=True)
    with (CACHE / "flash.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("다른 창에서 플래싱 중입니다.") from None
        run("sudo", "-v")
        prepare_and_flash(profile, mode, expected_pid, archives, device.port)


def require_layout(bsp, path, devices, app_device=None):
    import xml.etree.ElementTree as ET

    layout = ET.parse(bsp / path).findall(".//device")
    types = [(device.get("type"), device.get("instance")) for device in layout]
    app_on = [(device.get("type"), device.get("instance")) for device in layout
              if any(partition.get("name") == "APP" for partition in device.findall("partition"))]
    if types != devices or (app_device and app_on != [app_device]) or (not app_device and app_on):
        raise ValueError(f"선택한 저장 장치와 플래싱 레이아웃이 다릅니다. {path}")


def agx_orin_board_config(bsp, device):
    # 7023 is shared by standard and Industrial AGX modules. NVIDIA's read-info
    # dumps module EEPROM and exits before partition generation or flashing.
    probe = ("sudo", "bash", "./flash.sh", "--read-info", "--usb-instance", device.port,
             "jetson-agx-orin-devkit", "internal")
    print("AGX Orin 모듈의 EEPROM SKU 확인 중", flush=True)
    result = subprocess.run(probe, cwd=bsp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    if result.returncode != 0:
        print(result.stdout, flush=True)
        result.check_returncode()
    identities = set(re.findall(r"^Board ID\(([0-9]+)\) version\([^\n]*?\) sku\(([0-9]+)\) revision\(",
                                result.stdout, re.MULTILINE))
    if len(identities) != 1:
        raise ValueError("AGX Orin 모듈의 EEPROM SKU를 하나로 확인할 수 없습니다.")
    board_id, sku = identities.pop()
    if board_id != "3701" or sku not in AGX_ORIN_SKUS[device.pid]:
        raise ValueError(f"USB 모듈과 EEPROM 정보가 일치하지 않습니다. P{board_id}-{sku}")
    print(f"EEPROM 모듈 · P{board_id}-{sku}", flush=True)
    if sku == "0008":
        return "jetson-agx-orin-devkit-industrial", "flash_t234_qspi_sdmmc_industrial.xml"
    return "jetson-agx-orin-devkit", "flash_t234_qspi_sdmmc.xml"


def prepare_and_flash(profile, mode, expected_pid, archives, expected_port=None):
    import tempfile

    if not profile.supports_pid(expected_pid, mode):
        raise ValueError("선택한 캐리어보드, JetPack, 모듈 또는 설치 방식이 맞지 않습니다.")
    device = require_recovery(expected_pid, expected_port)
    temp = tempfile.mkdtemp(prefix="flash-", dir=profile.folder)
    stage = Path(temp)
    try:
        print("BSP 압축 해제 중", flush=True)
        run("sudo", "tar", "-xf", str(archives[0]), "-C", temp)
        candidates = [path for path in [stage / "Linux_for_Tegra", *stage.glob("*/Linux_for_Tegra")]
                      if (path / "flash.sh").is_file()]
        if len(candidates) != 1:
            raise ValueError("압축 파일에서 Linux_for_Tegra를 찾을 수 없습니다.")
        bsp = candidates[0]
        print("PC 플래싱 도구 및 BSP 설정 준비 중", flush=True)
        run("sudo", "bash", "./tools/l4t_flash_prerequisites.sh", cwd=bsp)
        if profile.vendor:
            settings = sorted(p.name for p in (bsp / "settings").iterdir() if p.is_dir())
            selection = settings.index(profile.board)
            # The R1.2.3 setup script enumerates settings folders using glob order.
            run("sudo", "env", "LC_ALL=C", "bash", "./setup.sh", cwd=bsp,
                input=f"{selection}\n0\n0\n")
            marker = (bsp / "rootfs/etc/avt_carrier_board.conf").read_text().splitlines()
            board_conf = f"jetson-orin-{profile.board.lower()}"
            setup = (bsp / "install.sh").read_text().splitlines()
            if f"CARRIER_BOARD_NAME={profile.board}" not in marker or f"MODULE_NAME={board_conf}" not in setup:
                raise ValueError("AVerMedia BSP의 보드 설정이 선택한 모델과 일치하지 않습니다.")
        else:
            run("sudo", "tar", "-xpf", str(archives[1]), "-C", str(bsp / "rootfs"))
            run("sudo", "bash", "./apply_binaries.sh", cwd=bsp)
        release = (bsp / "rootfs/etc/nv_tegra_release").read_text().splitlines()[0]
        major, revision = profile.l4t.split(".", 1)
        if not release.startswith(f"# R{major} (release), REVISION: {revision},"):
            raise ValueError("BSP의 실제 L4T 버전이 선택한 버전과 다릅니다.")
        require_recovery(device.pid, device.port)
        print(f"{profile.label} · JetPack {profile.jetpack} · {mode}", flush=True)
        if mode == QSPI:
            run("sudo", "bash", str(HERE / "flash-qspi-only.sh"), "--bsp", str(bsp))
        elif profile.board == "P4071":
            board_conf = THOR_BOARD_CONFIGS[device.pid]
            require_board_config(bsp, board_conf)
            require_layout(bsp, "tools/kernel_flash/flash_l4t_t264_nvme.xml",
                           [("external", "0")], ("external", "0"))
            print("Thor 기본 NVMe 레이아웃은 234 GiB 이상이 필요합니다.", flush=True)
            run("sudo", "bash", "./l4t_initrd_flash.sh", "--erase-all",
                "--usb-instance", device.port, board_conf, "internal", cwd=bsp)
            print("플래싱 완료. Thor 보드의 RESET 버튼을 눌러 Ubuntu로 부팅하세요.", flush=True)
        elif profile.board == "P3737":
            require_board_config(bsp, "jetson-agx-orin-devkit")
            board_conf, xml = agx_orin_board_config(bsp, device)
            require_board_config(bsp, board_conf)
            path = f"bootloader/generic/cfg/{xml}"
            require_layout(bsp, path, [("spi", "0"), ("sdmmc_user", "3")], ("sdmmc_user", "3"))
            require_recovery(device.pid, device.port, reconnect=True)
            run("sudo", "bash", "./flash.sh", "--usb-instance", device.port, "-c", path,
                board_conf, "internal", cwd=bsp)
        else:
            if profile.board == "P3768":
                board_conf = "jetson-orin-nano-devkit"
            qspi = "bootloader/generic/cfg/flash_t234_qspi.xml"
            nvme = "tools/kernel_flash/flash_l4t_t234_nvme.xml"
            require_board_config(bsp, board_conf)
            require_layout(bsp, qspi, [("spi", "0")])
            require_layout(bsp, nvme, [("external", "0")], ("external", "0"))
            run("sudo", "bash", "./tools/kernel_flash/l4t_initrd_flash.sh",
                "--external-device", "nvme0n1p1", "-c", nvme, "-p", f"-c {qspi}",
                "--showlogs", "--network", "usb0", board_conf, "internal", cwd=bsp)
    finally:
        # Vendor archives contain root-owned directories and files.
        # Remove only the private directory created for this transaction.
        mounts = Path("/proc/self/mountinfo").read_text().splitlines()
        prefix = str(stage.resolve()) + "/"
        if any(line.split()[4].startswith(prefix) for line in mounts):
            print(f"플래싱 작업 폴더에 마운트가 남았습니다. {stage}", flush=True)
        else:
            try:
                run("sudo", "find", temp, "-xdev", "-mindepth", "1", "-delete")
                stage.rmdir()
            except (OSError, subprocess.SubprocessError) as error:
                print(f"작업 폴더를 정리하지 못했습니다. {stage}\n{error}", flush=True)


def require_board_config(bsp, board_conf):
    if not (bsp / f"{board_conf}.conf").is_file():
        raise ValueError(f"BSP 구성 파일을 찾을 수 없습니다. {board_conf}.conf")
