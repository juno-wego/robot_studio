#!/usr/bin/env python3
"""Prepare persistent static IPv4 settings for Jetson device ports."""

import argparse
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Interface
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid


ADDRESSES = {'go2': '192.168.123.99/24', 'mid360': '192.168.1.5/24'}
BOARDS = ('D115', 'D135', 'P3768', 'P3737', 'P4071', 'auto')
EXPLICIT_PORT_BOARDS = ('P3737', 'P4071', 'auto')
FIELDS = [
    'connection.id', 'connection.interface-name', 'connection.autoconnect',
    'connection.autoconnect-priority', '802-3-ethernet.mac-address',
    '802-3-ethernet.cloned-mac-address', 'ipv4.method', 'ipv4.addresses',
    'ipv4.gateway', 'ipv4.dns', 'ipv4.ignore-auto-dns', 'ipv4.never-default',
    'ipv6.method',
]


@dataclass(frozen=True)
class Ethernet:
    name: str
    mac: str
    driver: str
    usb: bool


def run(arguments):
    return subprocess.run(arguments, check=True, text=True, capture_output=True,
                          timeout=15).stdout


def mac_address(value):
    value = value.lower()
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', value):
        raise ValueError('Invalid MAC address: ' + value)
    if value == '00:00:00:00:00:00' or int(value[:2], 16) & 1:
        raise ValueError('Invalid Ethernet MAC: ' + value)
    return value


def ethernet_devices():
    devices = []
    for path in sorted(Path('/sys/class/net').iterdir()):
        if not (path / 'device').exists() or (path / 'wireless').exists():
            continue
        if (path / 'type').read_text().strip() != '1':
            continue
        device = (path / 'device').resolve()
        if 'gadget' in device.parts:
            continue
        mac = None
        if shutil.which('ethtool'):
            try:
                match = re.search(r'Permanent address:\s*([0-9a-fA-F:]{17})',
                                  run(['ethtool', '-P', path.name]))
                if match:
                    mac = mac_address(match.group(1))
            except (subprocess.SubprocessError, ValueError):
                pass
        if mac is None:
            if (path / 'addr_assign_type').read_text().strip() != '0':
                raise ValueError(f'{path.name}: permanent MAC unavailable; install ethtool.')
            mac = mac_address((path / 'address').read_text().strip())
        devices.append(Ethernet(
            path.name, mac, (path / 'device/driver').resolve().name,
            any(part.startswith('usb') for part in device.parts)))
    return devices


def select_devices(devices, go2_name=None, lidar_name=None):
    def choose(role, explicit):
        if explicit:
            candidates = [d for d in devices if d.name == explicit]
        else:
            eligible = [d for d in devices if not d.usb] if role == 'go2' else [
                d for d in devices if d.usb and d.driver in ('ax88179_178a', 'asix')]
            candidates = [d for d in eligible if d.name == role] or eligible
        if len(candidates) != 1:
            raise ValueError(f'{role}: choose a port with --go2-interface or --lidar-interface.')
        return candidates[0]

    selected = {'go2': choose('go2', go2_name), 'mid360': choose('mid360', lidar_name)}
    if selected['go2'].name == selected['mid360'].name:
        raise ValueError('Go2 and MID360 require different Ethernet ports.')
    if selected['go2'].mac == selected['mid360'].mac:
        raise ValueError('Go2 and MID360 require different permanent MAC addresses.')
    return selected


def wired_profiles():
    profiles = []
    output = run(['nmcli', '--escape', 'no', '-t', '-f', 'UUID,TYPE', 'connection', 'show'])
    for line in output.splitlines():
        identity, kind = line.split(':', 1)
        if kind not in ('802-3-ethernet', 'ethernet'):
            continue
        values = run(['nmcli', '--escape', 'no', '-g', ','.join(FIELDS),
                      'connection', 'show', 'uuid', identity]).splitlines()
        if len(values) != len(FIELDS):
            raise ValueError('Unexpected nmcli profile output for ' + identity)
        properties = dict(zip(FIELDS, ('' if v == '--' else v for v in values)))
        profiles.append({'uuid': identity, 'properties': properties})
    return profiles


def choose_profile(role, profiles):
    matches = []
    for profile in profiles:
        props = profile['properties']
        if (props['connection.id'] in (role, 'wego-' + role)
                or props['connection.interface-name'] == role
                or ADDRESSES[role] in props['ipv4.addresses'].split(',')):
            matches.append(profile)
    if len(matches) > 1:
        raise ValueError(f'{role}: multiple existing sensor profiles; resolve duplicates first.')
    return matches[0] if matches else None


def settings(role, device, address, connection_id):
    return {
        'connection.id': connection_id, 'connection.interface-name': role,
        'connection.autoconnect': 'yes', 'connection.autoconnect-priority': '100',
        '802-3-ethernet.mac-address': device.mac,
        '802-3-ethernet.cloned-mac-address': 'permanent',
        'ipv4.method': 'manual', 'ipv4.addresses': address,
        'ipv4.gateway': '', 'ipv4.dns': '', 'ipv4.ignore-auto-dns': 'yes',
        'ipv4.never-default': 'yes', 'ipv6.method': 'disabled',
    }


def pairs(properties):
    return [part for key, value in properties.items() for part in (key, value)]


def configuration_files(selected):
    files = {}
    for role, device in selected.items():
        files[Path('/etc/systemd/network') / f'10-{role}.link'] = (
            f'[Match]\nMACAddress={device.mac}\n\n[Link]\nNamePolicy=\nName={role}\n')
        files[Path('/etc/NetworkManager/conf.d') / f'20-{role}-ethernet.conf'] = (
            f'[device-{role}]\nmatch-device=interface-name:{role}\nignore-carrier=yes\n')
    return files


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_settings(selected, profiles, addresses=None, connection_prefix=''):
    addresses = ADDRESSES if addresses is None else addresses
    files = configuration_files(selected)
    for path in files:
        if path.is_symlink():
            raise ValueError('Refusing to replace a symlink: ' + str(path))
    previous = {path: (path.read_bytes(), path.stat().st_mode & 0o777)
                if path.exists() else None for path in files}
    backup = Path('/var/backups') / ('wego-ethernet-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(mode=0o700)
    for path, original in previous.items():
        if original:
            (backup / path.name).write_bytes(original[0])
    (backup / 'profiles.json').write_text(json.dumps(profiles, indent=2))
    changed = []
    try:
        for role, device in selected.items():
            original = profiles[role]
            identity = original['uuid'] if original else str(uuid.uuid4())
            changed.append((identity, original))
            if original:
                command = ['nmcli', 'connection', 'modify', 'uuid', identity]
            else:
                command = ['nmcli', 'connection', 'add', 'type', 'ethernet',
                           'connection.uuid', identity]
            run(command + pairs(settings(role, device, addresses[role], connection_prefix + role)))
        for path, text in files.items():
            atomic_write(path, text.encode())
    except Exception:
        for path, original in previous.items():
            if original:
                atomic_write(path, original[0], original[1])
            elif path.exists():
                path.unlink()
        for identity, original in reversed(changed):
            try:
                run(['nmcli', 'connection', 'modify', 'uuid', identity]
                    + pairs(original['properties']) if original else
                    ['nmcli', 'connection', 'delete', 'uuid', identity])
            except subprocess.SubprocessError:
                pass
        raise
    print('설정 저장 완료. sudo reboot 후 적용됩니다.')
    print('기존 설정 백업:', backup)


def explicit_ports(path, devices, current_interfaces):
    with path.open('rb') as file:
        payload = file.read(8193)
    if len(payload) > 8192:
        raise ValueError('이더넷 설정 파일이 너무 큽니다.')
    rows = json.loads(payload)
    if not isinstance(rows, list) or not (1 <= len(rows) <= 16):
        raise ValueError('이더넷 설정에는 1~16개의 포트 행이 필요합니다.')
    by_port = {device.name: device for device in devices}
    selected, addresses = {}, {}
    ports, macs, subnets = set(), set(), []
    for row in rows:
        if not (isinstance(row, dict) and set(row) == {'name', 'port', 'address'}
                and all(isinstance(value, str) for value in row.values())):
            raise ValueError('이더넷 설정 행에 name, port, address가 필요합니다.')
        name, port, address = row['name'], row['port'], row['address']
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,14}', name) or name == 'lo':
            raise ValueError(f'올바른 인터페이스 이름이 아닙니다. {name}')
        if name in current_interfaces and name != port:
            raise ValueError(f'이미 다른 인터페이스에서 사용하는 이름입니다. {name}')
        if port not in by_port:
            raise ValueError(f'물리적 이더넷 포트를 찾을 수 없습니다. {port}')
        if name in selected or port in ports or by_port[port].mac in macs:
            raise ValueError(f'인터페이스 이름, 포트, MAC 주소는 서로 달라야 합니다. {name} / {port}')
        if not re.fullmatch(r'[0-9.]+/[0-9]{1,2}', address):
            raise ValueError(f'IPv4 주소와 CIDR 접두사를 입력하세요. {address}')
        interface = IPv4Interface(address)
        network = interface.network
        if (not network.prefixlen or interface.ip.is_multicast or interface.ip.is_loopback
                or interface.ip.is_unspecified or int(interface.ip) == 0xffffffff
                or (network.prefixlen < 31
                    and interface.ip in (network.network_address, network.broadcast_address))):
            raise ValueError(f'장치 포트에서 사용할 수 없는 IPv4 주소입니다. {address}')
        if any(network.overlaps(previous) for previous in subnets):
            raise ValueError(f'두 포트의 IPv4 서브넷이 겹칩니다. {network}')
        selected[name], addresses[name] = by_port[port], str(interface)
        ports.add(port)
        macs.add(by_port[port].mac)
        subnets.append(network)
    return selected, addresses


def device_profiles(selected, existing, prefix):
    profiles = {}
    for role, device in selected.items():
        managed_ids = {prefix + role}
        if role in ADDRESSES:
            managed_ids.update({role, 'wego-' + role})
        matches = [profile for profile in existing
                   if profile['properties']['connection.id'] in managed_ids]
        if len(matches) > 1:
            raise ValueError(f'포트 프로필 이름이 중복됩니다. {prefix + role}')
        if matches and matches[0]['properties']['802-3-ethernet.mac-address'].lower() not in ('', device.mac):
            raise ValueError(f'기존 포트 프로필이 다른 MAC 주소에 연결되어 있습니다. {prefix + role}')
        profiles[role] = matches[0] if matches else None
    return profiles


def verify_board(board):
    device_tree = Path('/proc/device-tree')
    model_file = device_tree / 'model'
    compatible_file = device_tree / 'compatible'
    release_file = Path('/etc/nv_tegra_release')
    if not (model_file.is_file() and compatible_file.is_file() and release_file.is_file()):
        raise ValueError('Jetson의 모델과 L4T 정보를 찾을 수 없습니다.')

    model = model_file.read_bytes().decode(errors='replace').strip('\x00\n')
    compatibles = compatible_file.read_bytes().lower().split(b'\x00')
    original_soc = any(re.fullmatch(rb'nvidia,tegra[0-9]+', value) for value in compatibles)
    if not original_soc or not re.match(r'# R[0-9]+ \(release\), REVISION:', release_file.read_text()):
        raise ValueError('NVIDIA Jetson에서 실행하세요.')

    carrier_file = Path('/etc/avt_carrier_board.conf')
    carrier_exists = carrier_file.is_file()
    if board == 'auto':
        return model
    if board in ('D115', 'D135'):
        carrier = carrier_file.read_text().splitlines() if carrier_exists else []
        matches = f'CARRIER_BOARD_NAME={board}' in carrier or (
            board == 'D115' and not carrier_exists and 'NX215' in model)
        matches = matches and re.search(r'Jetson Orin (NX|Nano)', model, re.I)
    else:
        model_patterns = {
            'P3768': (r'NVIDIA Jetson Orin Nano Developer Kit', r'Jetson Orin (NX|Nano)'),
            'P3737': (r'NVIDIA Jetson AGX Orin Developer Kit', r'Jetson AGX Orin'),
            'P4071': (r'NVIDIA Jetson AGX Thor Developer Kit', r'Jetson .*?(Thor|T[45]000)'),
        }
        official_model, module = model_patterns[board]
        board_id = (board.lower() + '-0000').encode()
        explicit_carrier = any(board_id in value for value in compatibles)
        matches = not carrier_exists and re.search(module, model, re.I) and (
            explicit_carrier or re.fullmatch(official_model, model, re.I))
    if not matches:
        raise ValueError(f'선택한 캐리어보드({board})와 연결된 Jetson 모델({model})이 다릅니다.')
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true', help='Save persistent settings; reboot afterward.')
    action.add_argument('--list', action='store_true', help='List physical Ethernet ports only.')
    action.add_argument('--verify-board', action='store_true', help='Verify the Jetson board only.')
    parser.add_argument('--board', choices=BOARDS, default='D115')
    parser.add_argument('--config', type=Path, help='Load explicit device port rows from a JSON file.')
    parser.add_argument('--go2-interface', help='Explicit Go2 port name.')
    parser.add_argument('--lidar-interface', help='Explicit MID360 port name.')
    args = parser.parse_args()
    model = verify_board(args.board)
    if args.verify_board:
        print(model)
        return
    if args.config and (args.go2_interface or args.lidar_interface):
        parser.error('--config와 Go2/MID360 전용 포트 옵션은 함께 쓸 수 없습니다.')
    if args.board in EXPLICIT_PORT_BOARDS and not args.list and not args.config and (
            not args.go2_interface or not args.lidar_interface):
        parser.error('포트 목록을 확인한 뒤 Go2와 MID360 포트를 각각 지정하세요.')
    devices = ethernet_devices()
    for device in devices:
        print(f'{device.name:16} {device.mac}  {device.driver}')
    if args.list:
        return
    if not shutil.which('nmcli'):
        parser.error('NetworkManager (nmcli) is required.')
    existing = wired_profiles()
    if args.config:
        selected, addresses = explicit_ports(args.config, devices, os.listdir('/sys/class/net'))
        prefix = 'wego-device-'
        profiles = device_profiles(selected, existing, prefix)
        primary = {
            route.get('dev')
            for family in ('-4', '-6')
            for route in json.loads(run(['ip', '-j', family, 'route', 'show', 'default']))
        }
        if any(device.name in primary for device in selected.values()):
            raise ValueError('기본 인터넷 연결에 쓰는 포트는 장치용 포트로 지정할 수 없습니다.')
    else:
        selected = select_devices(devices, args.go2_interface, args.lidar_interface)
        addresses, prefix = ADDRESSES, ''
        profiles = {role: choose_profile(role, existing) for role in selected}
    for role, device in selected.items():
        print(f'{device.name} -> {role}: {addresses[role]}  MAC={device.mac}')
    if not args.apply:
        print('확인만 했습니다. 저장하려면 sudo python3 configure-ethernet.py --apply')
        return
    if os.geteuid() != 0:
        parser.error('Use sudo with --apply.')
    apply_settings(selected, profiles, addresses, prefix)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
