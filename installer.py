#!/usr/bin/env python3
"""Command backend for the WeGo Jetson Qt application."""

import argparse
import json
import os
import signal
from pathlib import Path
import subprocess
import sys

from installer_backend import CACHE, HERE, PROFILES, QSPI, USB_MODELS
from installer_backend import download, flash, recovery_device
from installer_host import ACTIONS, ROBOTS, postboot
from installer_support import fetch_asset, manufacturer_tool, sdk_manager


def display_catalog():
    data = {
        "profiles": [
            {"key": profile.key, "board": profile.board, "label": profile.label, "jetpack": profile.jetpack,
             "l4t": profile.l4t, "modes": profile.modes, "pids": sorted(profile.supported_pids)}
            for profile in PROFILES.values()
        ],
        "robots": [{"key": key, "label": label, "topic": topic}
                   for key, (label, topic) in ROBOTS.items()],
        "cache": str(CACHE),
        "qspi": QSPI,
    }
    print(json.dumps(data, ensure_ascii=False))


def stop_download(sig, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("catalog")
    commands.add_parser("recovery")
    download_cmd = commands.add_parser("download")
    download_cmd.add_argument("profile", choices=PROFILES)
    flash_cmd = commands.add_parser("flash")
    flash_cmd.add_argument("profile", choices=PROFILES)
    flash_cmd.add_argument("mode", choices=sorted({mode for p in PROFILES.values() for mode in p.modes}))
    flash_cmd.add_argument("pid", choices=USB_MODELS)
    flash_cmd.add_argument("--usb-port")
    boot_cmd = commands.add_parser("postboot")
    boot_cmd.add_argument("profile", choices=["auto", *PROFILES])
    boot_cmd.add_argument("login")
    boot_cmd.add_argument("action", choices=ACTIONS)
    boot_cmd.add_argument("go2_port")
    boot_cmd.add_argument("lidar_port")
    boot_cmd.add_argument("robot", choices=ROBOTS, default="go2", nargs="?")
    boot_cmd.add_argument("--payload", default="")
    sdk = commands.add_parser("sdkmanager")
    sdk.add_argument("operation", choices=("install", "downloadonly"))
    sdk.add_argument("--config", default="")
    sdk.add_argument("--archived", action="store_true")
    vendor = commands.add_parser("manufacturer")
    vendor.add_argument("script")
    fetch = commands.add_parser("fetch")
    fetch.add_argument("url")
    fetch.add_argument("sha256", nargs="?", default="")
    args = parser.parse_args()
    if getattr(sys, "frozen", False):
        original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            os.environ.pop("LD_LIBRARY_PATH", None)
        else:
            os.environ["LD_LIBRARY_PATH"] = original
    try:
        if args.command == "catalog":
            display_catalog()
        elif args.command == "recovery":
            device = recovery_device()
            print(json.dumps({"pid": device.pid, "port": device.port, "model": device.model}, ensure_ascii=False))
        elif args.command == "download":
            signal.signal(signal.SIGTERM, stop_download)
            last = -1

            def progress(done, total):
                nonlocal last
                percentage = int(done * 100 / total)
                if percentage != last:
                    last = percentage
                    print(f"PROGRESS {percentage} {done} {total}", flush=True)

            download(PROFILES[args.profile], lambda msg: print(msg, flush=True), progress)
        elif args.command == "flash":
            flash(PROFILES[args.profile], args.mode, args.pid, args.usb_port)
        elif args.command == "postboot":
            postboot(args.profile, args.login, args.action, args.robot, args.payload,
                     args.go2_port, args.lidar_port)
        elif args.command == "sdkmanager":
            sdk_manager(args.operation, args.config, args.archived)
        elif args.command == "manufacturer":
            manufacturer_tool(args.script)
        elif args.command == "fetch":
            signal.signal(signal.SIGTERM, stop_download)
            fetch_asset(args.url, args.sha256)
        else:
            binary = HERE / "build" / "wego-jetson"
            if not binary.is_file():
                parser.exit(2, "Qt 앱을 빌드하거나 설치 패키지를 사용하세요.\n")
            return subprocess.run([str(binary)], check=False).returncode
    except KeyboardInterrupt:
        parser.exit(130, "다운로드를 중지했습니다.\n")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
