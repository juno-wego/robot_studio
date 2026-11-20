"""Host-side integrations for NVIDIA SDK Manager and manufacturer downloads."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from installer_backend import CACHE


SDK_VERSION = "26.1.0-14392"
SDK_SHA256 = "e2b9502b149c9886601e7fd6c0251480a1a8d073c59472f57985b955def0c3f7"


def https_url(url):
    location = urlsplit(url)
    if location.scheme != "https" or not location.hostname or location.username or location.password:
        raise ValueError("제조사의 HTTPS 다운로드 주소를 입력하세요.")
    return location


class HttpsRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(2 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_asset(url, checksum="", folder=None):
    location = https_url(url)
    if checksum and not re.fullmatch(r"[A-Fa-f0-9]{64}", checksum):
        raise ValueError("SHA-256은 64자리 16진수입니다.")
    # Keep credentials that may occur in a signed URL out of app logs and paths.
    name = Path(unquote(location.path)).name
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", name).lstrip(".")[:170] or "manufacturer-bsp"
    if folder is None:
        folder = CACHE / "manufacturer" / hashlib.sha256(url.encode()).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if checksum and target.is_file() and file_sha256(target) == checksum.lower():
        print(f"저장된 파일 확인  {target}", flush=True)
        return target

    fd, temporary = tempfile.mkstemp(prefix=".download-", dir=folder)
    temp = Path(temporary)
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as stream, build_opener(HttpsRedirect).open(url, timeout=30) as response:
            if response.headers.get_content_type() in ("text/html", "application/xhtml+xml"):
                raise ValueError("다운로드 파일 대신 웹페이지가 반환됐습니다. 제조사의 파일 주소를 확인하세요.")
            size = response.headers.get("Content-Length")
            written = 0
            if size and int(size) > shutil.disk_usage(folder).free:
                raise ValueError("다운로드 공간이 부족합니다.")
            while (chunk := response.read(2 * 1024 * 1024)):
                stream.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if written // (64 * 1024 * 1024) != (written - len(chunk)) // (64 * 1024 * 1024):
                    print(f"다운로드  {written / 1e9:.2f} GB", flush=True)
        if not written or (size and written != int(size)):
            raise ValueError("파일을 끝까지 받지 못했습니다.")
        if checksum and digest.hexdigest() != checksum.lower():
            raise ValueError("제조사 파일의 SHA-256이 일치하지 않습니다.")
        temp.replace(target)
        print(f"{target}\nSHA-256  {digest.hexdigest()}", flush=True)
        return target
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def hardware_lock():
    CACHE.mkdir(parents=True, exist_ok=True)
    with (CACHE / "flash.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("다른 플래싱 작업이 진행 중입니다.") from None
        yield


def sdk_manager(operation, config="", archived=False):
    if operation not in ("install", "downloadonly"):
        raise ValueError("SDK Manager 작업을 확인하세요.")
    path = None
    if config:
        path = Path(config).resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != ".json":
            raise ValueError("제조사 SDK Manager JSON 파일을 선택하세요.")
        with path.open(encoding="utf-8") as stream:
            manifest = json.load(stream)
        if not isinstance(manifest, dict) or not ("hw" in manifest or "sw" in manifest):
            raise ValueError("제조사 SDK Manager extra config 파일 형식이 아닙니다.")
    with hardware_lock():
        command = shutil.which("sdkmanager")
        if not command:
            release = platform.freedesktop_os_release()
            distro = release.get("VERSION_ID", "").replace(".", "")
            if release.get("ID") != "ubuntu" or distro not in {"2204", "2404"} or platform.machine() != "x86_64":
                raise ValueError("자동 SDK Manager 설치는 Ubuntu 22.04/24.04 x86-64에서 지원합니다.")
            base = f"https://developer.download.nvidia.com/compute/cuda/repos/ubuntu{distro}/x86_64"
            package = fetch_asset(f"{base}/sdkmanager_{SDK_VERSION}_amd64.deb", SDK_SHA256, CACHE / "sdkmanager")
            subprocess.run(["sudo", "apt-get", "install", "--no-remove", str(package)], check=True)
            command = shutil.which("sdkmanager")
            if not command:
                raise ValueError("SDK Manager 실행 파일을 찾을 수 없습니다.")
        folder = CACHE / "sdkmanager-downloads"
        folder.mkdir(parents=True, exist_ok=True)
        arguments = [command, "--cli", "--product", "Jetson", "--action", operation,
                     "--download-folder", str(folder)]
        if operation == "install":
            arguments.append("--flash")
        if archived:
            arguments.append("--archived-versions")
        if path:
            # Current SDK Manager help uses --extra-config; older builds used --extraconfig.
            help_result = subprocess.run([command, "--help"], capture_output=True, text=True, timeout=15, check=True)
            arguments += ["--extra-config" if re.search(r"--extra-config\b", help_result.stdout) else "--extraconfig",
                          str(path)]
        subprocess.run(arguments, check=True)


def manufacturer_tool(script):
    path = Path(script).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in (".sh", ".bash"):
        raise ValueError("제조사 설치 스크립트(.sh)를 선택하세요.")
    with hardware_lock():
        subprocess.run(["bash", str(path)], cwd=path.parent, check=True)
