#!/usr/bin/env bash
set -Eeuo pipefail

# AVerMedia NX215B / D115, Orin NX 16GB, JetPack 6.2.1 (L4T 36.4.4).
# Regenerate firmware for the connected module; exclude the cloned NVMe SSD.
nx215_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
nx215_bsp="$nx215_script_dir/Linux_for_Tegra"
nx215_check_only=false

usage() {
    echo "Usage: sudo bash $0 [--bsp <Linux_for_Tegra>]"
    echo "       bash $0 --check [--bsp <Linux_for_Tegra>]"
}

while (( $# )); do
    case "$1" in
        --check) nx215_check_only=true; shift ;;
        --bsp)
            if (( $# < 2 )); then usage >&2; exit 2; fi
            nx215_bsp="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

for nx215_tool in bash python3 grep lsusb flock tee; do
    command -v "$nx215_tool" >/dev/null || {
        echo "Missing command: $nx215_tool" >&2
        exit 1
    }
done

if [[ ! -d "$nx215_bsp" ]]; then
    echo "BSP directory not found: $nx215_bsp" >&2
    echo 'Use --bsp to select the prepared Linux_for_Tegra directory.' >&2
    exit 1
fi
cd -- "$nx215_bsp"

test -f flash.sh
test -f jetson-orin-d115.conf
grep -qx 'CARRIER_BOARD_NAME=D115' rootfs/etc/avt_carrier_board.conf
grep -q '^# R36 (release), REVISION: 4.4,' rootfs/etc/nv_tegra_release
test -s kernel/dtb/tegra234-p3768-0000+p3767-0000-nv-d115.dtb
python3 - <<'PY'
import xml.etree.ElementTree as ET
from pathlib import Path

path = Path('bootloader/generic/cfg/flash_t234_qspi.xml')
devices = ET.parse(path).findall('.//device')
assert len(devices) == 1, 'Unexpected additional storage devices'
assert devices[0].get('type') == 'spi' and devices[0].get('instance') == '0'
assert not any(p.get('name') == 'APP' for p in devices[0].findall('partition'))
print('BSP verified: D115 / L4T 36.4.4 / SPI only; no APP/NVMe device.')
PY

if "$nx215_check_only"; then
    echo 'Check completed. No USB access or flashing was performed.'
    exit 0
fi

if (( EUID != 0 )); then
    echo "Run: sudo bash $nx215_script_dir/flash-qspi-only.sh" >&2
    exit 1
fi

exec 9>/run/lock/nx215-qspi-only.lock
flock -n 9 || { echo 'Another QSPI flash is running.' >&2; exit 1; }
mkdir -p -- "$nx215_script_dir/logs"
nx215_log_file="$nx215_script_dir/logs/nx215-qspi-only-$(date +%Y%m%d-%H%M%S).log"
touch "$nx215_log_file"
chmod 0644 "$nx215_log_file"
exec > >(tee -a "$nx215_log_file") 2>&1
trap 'nx215_status=$?; echo "FAILED (exit $nx215_status). Log: $nx215_log_file"; exit "$nx215_status"' ERR

echo 'NX215 / Orin NX 16GB: QSPI ONLY; no NVMe partition writes.'
echo "Log: $nx215_log_file"
mapfile -t nx215_devices < <(lsusb -d 0955:)
if (( ${#nx215_devices[@]} != 1 )) || [[ "${nx215_devices[0]:-}" != *'0955:7323'* ]]; then
    echo 'Expected exactly one Orin NX 16GB in APX recovery mode.' >&2
    printf '%s\n' "${nx215_devices[@]}"
    exit 1
fi
printf '%s\n' "${nx215_devices[@]}"

# nvme0n1p1 selects the existing rootfs. The explicit layout is SPI only.
bash ./flash.sh --no-flash --no-systemimg \
    -c bootloader/generic/cfg/flash_t234_qspi.xml \
    jetson-orin-d115 nvme0n1p1

python3 -u - <<'PY'
import hashlib
from pathlib import Path
import shlex
import subprocess
import xml.etree.ElementTree as ET

bootloader = Path('bootloader').resolve()
command = shlex.split((bootloader / 'flashcmd.txt').read_text())
assert command and command[0] == './tegraflash.py', 'Unexpected flash program'
assert command.count('--cfg') == 1 and command.count('--cmd') == 1
assert command[command.index('--chip') + 1] == '0x23'
operations = [s.strip() for s in command[command.index('--cmd') + 1].split(';') if s.strip()]
assert operations == ['flash', 'reboot'], f'Unexpected operations: {operations}'
cfg = (bootloader / command[command.index('--cfg') + 1]).resolve()
assert cfg.parent == bootloader, 'Unexpected configuration path'
devices = ET.parse(cfg).findall('.//device')
assert len(devices) == 1, 'Generated layout includes more than one device'
device = devices[0]
assert device.get('type') == 'spi' and device.get('instance') == '0', device.attrib
partitions = [p.get('name') for p in device.findall('partition')]
assert partitions and 'APP' not in partitions
print('GENERATED LAYOUT VERIFIED:', cfg, flush=True)
print('Storage:', device.attrib, flush=True)
print('Partitions:', ', '.join(partitions), flush=True)
print('Layout SHA256:', hashlib.sha256(cfg.read_bytes()).hexdigest(), flush=True)
print('Writing SPI firmware. NVMe is excluded.', flush=True)
subprocess.run(command, cwd=bootloader, check=True)
PY

echo "QSPI flash completed. Log: $nx215_log_file"
