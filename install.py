"""Attach to existing tools; register only this management service."""
from pathlib import Path
import argparse
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
from isolation_toolkit.config import ROOT, config_dir, state_dir


def quote_unit(s):
    if any(c in s for c in '\n\r\x00'):
        raise ValueError('invalid_path')
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def main():
    p = argparse.ArgumentParser(description='Guided installation; existing router, training and Claude services are preserved')
    p.add_argument('--legacy-root', default='..')
    p.add_argument('--private-interface', help='Existing private interface, such as a ZeroTier interface; no network is created')
    p.add_argument('--router-profile', help='External JSON router adapter profile; never copied into source')
    p.add_argument('--port', type=int, default=18882)
    p.add_argument('--yes', action='store_true')
    p.add_argument('--check', action='store_true', help='Read-only preflight')
    p.add_argument('--no-start', action='store_true', help='Write configuration and register, without starting')
    a = p.parse_args(); os.umask(0o077)
    if not 1024 <= a.port <= 65535: p.error('Invalid port')
    root = Path(a.legacy_root).expanduser().resolve()
    if not (root / 'mac-mini').is_file() and not a.yes and not a.check:
        root = Path(input('Existing toolkit directory: ').strip()).expanduser().resolve()
    if not (root / 'mac-mini').is_file(): p.error('Existing toolkit was not found; specify --legacy-root')
    required = ['systemctl', 'loginctl', 'ip']
    if any(not shutil.which(x) for x in required): p.error('Linux systemd and iproute2 are required')
    try: import yaml
    except ImportError: p.error('PyYAML is required. Install requirements.txt into a virtual environment, then run install.py with that Python.')
    addresses, subnets = ['127.0.0.1'], ['127.0.0.0/8']
    interface = a.private_interface
    if not a.yes and not a.check and not interface:
        interface = input('Private interface (leave empty for local access only): ').strip() or None
    if interface:
        d = json.loads(subprocess.check_output(['ip', '-j', 'address', 'show', 'dev', interface], text=True))
        entries = [x for n in d for x in n.get('addr_info', []) if x['family'] == 'inet']
        if len(entries) != 1: p.error('The selected interface must have exactly one IPv4 address')
        addr = ipaddress.ip_interface(entries[0]['local'] + '/' + str(entries[0]['prefixlen']))
        if not addr.ip.is_private or addr.ip.is_loopback: p.error('A private interface address is required')
        addresses.append(str(addr.ip)); subnets.append(str(addr.network))
    previous = config_dir() / 'config.json'
    if previous.exists():
        old = json.loads(previous.read_text())
        # Do not silently widen/reset a prior installation during a routine update.
        if not a.private_interface:
            addresses, subnets = old['listen'], old['allowed_subnets']
    print('Existing toolkit detected. This installation changes only the management panel.')
    print('Panel listeners:', ', '.join(f'http://{h}:{a.port}/' for h in addresses))
    print('Router / training / Claude / network policy: preserved.')
    if a.check: return
    if not a.yes and input('Register and start the management panel? [y/N] ').lower() != 'y': return
    config_dir().mkdir(parents=True, exist_ok=True, mode=0o700); state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    key = root / 'claude-console/state/access.key'
    shared = key.exists() and key.stat().st_uid == os.getuid() and not (key.stat().st_mode & 0o077)
    if not shared:
        key = config_dir() / 'access.key'
        if not key.exists(): key.write_text(secrets.token_urlsafe(32))
        key.chmod(0o600)
    console_host = addresses[-1]
    config = {'version': 1, 'legacy_root': str(root), 'listen': addresses, 'allowed_subnets': subnets, 'port': a.port,
              'access_key_file': str(key), 'state_dir': str(state_dir()), 'monitor_port': 18880,
              'client_interface': 'enp4s0', 'links': {'console': f'http://{console_host}:18881/', 'monitor_local': 'http://127.0.0.1:18880/'}}
    if previous.exists():
        shutil.copy2(previous, previous.with_suffix('.json.previous'))
        config.update({k: old[k] for k in ('links', 'client_interface', 'monitor_port', 'access_key_file', 'router') if k in old})
    if a.router_profile:
        profile_path = Path(a.router_profile).expanduser().resolve()
        config['router'] = json.loads(profile_path.read_text())
        exit_dir = Path(config['router']['host_exit_dir'])
        if not exit_dir.is_absolute():
            config['router']['host_exit_dir'] = str((profile_path.parent / exit_dir).resolve())
    previous.write_text(json.dumps(config, indent=2) + '\n'); previous.chmod(0o600)
    unit_dir = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user'
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / 'claude-isolation-panel.service'
    if unit.exists(): shutil.copy2(unit, unit.with_suffix('.service.previous'))
    import sys
    unit.write_text('[Unit]\nDescription=Starport management panel\nAfter=network-online.target\nWants=network-online.target\nStartLimitIntervalSec=0\n\n[Service]\nType=simple\nWorkingDirectory=' + str(ROOT).replace('%', '%%') + '\nExecStart=' + quote_unit(sys.executable) + ' -m isolation_toolkit serve\nRestart=on-failure\nRestartSec=5\nUMask=0077\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nNoNewPrivileges=yes\nPrivateTmp=yes\nTimeoutStopSec=20\n\n[Install]\nWantedBy=default.target\n')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', unit.name], check=True)
    if not a.no_start: subprocess.run(['systemctl', '--user', 'restart', unit.name], check=True)
    print('Registered:', unit.name)
    print('Access code:', 'same as the existing console' if shared else 'generated in external private configuration')
    print('To display it locally: ./bin/starport key')
    print('User services start at login. Check linger before claiming boot-without-login support.')


if __name__ == '__main__': main()
