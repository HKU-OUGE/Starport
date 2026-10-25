"""Install Starport's console and management panel; network adapters are optional."""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server/console'))
from settings import config_path, load, state_path, validate


def unit_quote(value):
    if any(x in str(value) for x in ('\n', '\r', '\x00')):
        raise ValueError('invalid_unit_path')
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def unit(description, command):
    return ('[Unit]\nDescription=' + description + '\nAfter=network-online.target\nWants=network-online.target\n'
            'StartLimitIntervalSec=0\n\n[Service]\nType=simple\nExecStart=' + ' '.join(map(unit_quote, command)) +
            '\nWorkingDirectory=' + str(ROOT).replace('%', '%%') + '\nRestart=on-failure\nRestartSec=5\nUMask=0077\n'
            'Environment=PYTHONDONTWRITEBYTECODE=1\nNoNewPrivileges=yes\nPrivateTmp=yes\nTimeoutStopSec=20\n'
            '\n[Install]\nWantedBy=default.target\n')


def save(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('linked_configuration_blocked')
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + '.previous'))
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(text); temp.chmod(0o600); temp.replace(path)


def make_plan(args):
    config = load(args.config)
    for key in ('host', 'port', 'ssh_host', 'public_origin', 'management_url'):
        value = getattr(args, key, None)
        if value is not None:
            config[key] = value
    config = validate(config)
    if config['port'] == args.panel_port or config['tunnel_port'] == args.panel_port:
        raise ValueError('distinct_listener_ports_required')
    if not 1024 <= args.panel_port <= 65535:
        raise ValueError('invalid_panel_port')
    legacy = Path(args.legacy_root).expanduser().resolve() if args.legacy_root else None
    if legacy and not (legacy / 'mac-mini').is_file():
        raise ValueError('legacy_toolkit_not_found')
    destination = config_path()
    panel_path = destination.parent / 'config.json'
    state = Path(config['state_dir'])
    panel = {'version': 1, 'legacy_root': str(legacy) if legacy else None,
             'listen': [config['host']], 'allowed_subnets': [config['host'] + '/32'],
             'port': args.panel_port, 'access_key_file': str(state / 'access.key'),
             'state_dir': str(state.parent / 'panel'), 'monitor_port': 18880,
             'links': {'console': f'http://{config["host"]}:{config["port"]}/'}}
    # Browser peers are selected by the operator's private interface, never 0/0.
    if args.allowed_subnet:
        import ipaddress
        nets = [ipaddress.ip_network(x, strict=True) for x in args.allowed_subnet]
        private = [ipaddress.ip_network(x) for x in ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
        if any(n.version != 4 or not any(n.subnet_of(p) for p in private) for n in nets):
            raise ValueError('private_subnet_required')
        panel['allowed_subnets'] = [str(n) for n in nets]
    if panel_path.exists():
        old = json.loads(panel_path.read_text())
        for key in ('router', 'client_interface', 'monitor_port', 'links'):
            if key in old:
                panel[key] = old[key]
        if not args.legacy_root:
            panel['legacy_root'] = old.get('legacy_root')
        if not args.allowed_subnet:
            panel['allowed_subnets'] = old.get('allowed_subnets', panel['allowed_subnets'])
    if args.router_profile:
        path = Path(args.router_profile).expanduser().resolve()
        panel['router'] = json.loads(path.read_text())
        exit_path = Path(panel['router']['host_exit_dir'])
        panel['router']['host_exit_dir'] = str((path.parent / exit_path).resolve() if not exit_path.is_absolute() else exit_path)
    venv = ROOT / 'server/.venv'
    python = venv / 'bin/python'
    units = {
        'starport-console.service': unit('Starport session console', [python, ROOT / 'server/console/server.py', '--config', destination]),
        'starport-panel.service': unit('Starport management panel', [python, '-m', 'isolation_toolkit', '--config', panel_path, 'serve']),
    }
    return config, panel, units


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install Starport on Linux')
    parser.add_argument('--config', help='External JSON configuration to import')
    parser.add_argument('--host', help='Loopback or private IPv4 listener')
    parser.add_argument('--port', type=int)
    parser.add_argument('--panel-port', type=int, default=18882)
    parser.add_argument('--ssh-host', help='SSH alias for Starship')
    parser.add_argument('--public-origin', help='Optional exact HTTPS tunnel origin')
    parser.add_argument('--management-url', help='Optional management link visible in the console')
    parser.add_argument('--allowed-subnet', action='append', help='Private subnet allowed to open the management panel')
    parser.add_argument('--legacy-root', help='Optional existing network/compute toolkit')
    parser.add_argument('--router-profile', help='Optional existing router adapter profile')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--check', action='store_true', help='Read-only host checks')
    parser.add_argument('--plan', action='store_true', help='Print units without installing or connecting')
    parser.add_argument('--no-start', action='store_true', help='Register services without restarting them')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        config, panel, units = make_plan(args)
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    if args.plan:
        print(json.dumps({'role': 'Starport', 'units': units, 'listener': config['host'],
                          'console_port': config['port'], 'panel_port': panel['port']}, indent=2)); return
    missing = [x for x in ('systemctl', 'ssh', 'node', 'npm') if not shutil.which(x)]
    if platform.system() != 'Linux' or sys.version_info < (3, 10) or missing:
        parser.error('Requires Linux, Python 3.10+, systemd, OpenSSH, Node.js 22.12+ and npm. Missing: ' + ', '.join(missing))
    use_uv = importlib.util.find_spec('ensurepip') is None
    if use_uv and not shutil.which('uv'):
        parser.error('Python venv support is missing. Install the matching python3-venv package or uv first.')
    node = subprocess.check_output(['node', '--version'], text=True).strip().lstrip('v').split('.')
    if tuple(map(int, node[:2])) < (22, 12):
        parser.error('Node.js 22.12+ is required')
    if args.check:
        print('Starport prerequisites available. SSH pairing and Claude authentication must be checked on the target device.'); return
    if not args.yes and input('Install Starport console and panel? [y/N] ').lower() != 'y':
        return
    venv = ROOT / 'server/.venv'
    if use_uv:
        subprocess.run(['uv', 'venv', '--python', sys.executable, '--allow-existing', str(venv)], check=True)
        subprocess.run(['uv', 'pip', 'install', '--python', str(venv / 'bin/python'), '-r', str(ROOT / 'server/requirements.txt')], check=True)
    else:
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
        subprocess.run([str(venv / 'bin/python'), '-m', 'pip', 'install', '-r', str(ROOT / 'server/requirements.txt')], check=True)
    web = ROOT / 'server/console/web'
    subprocess.run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=web, check=True)
    subprocess.run(['npm', 'run', 'build'], cwd=web, check=True)
    (venv / '.starport-ready').write_text('ready\n')
    config_file = config_path()
    state = Path(config['state_dir'])
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = state / 'access.key'
    if not key.exists():
        save(key, secrets.token_urlsafe(32) + '\n')
    save(config_file, json.dumps(config, indent=2) + '\n')
    save(config_file.parent / 'config.json', json.dumps(panel, indent=2) + '\n')
    unit_dir = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user'
    for name, text in units.items():
        save(unit_dir / name, text)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', *units], check=True)
    if not args.no_start:
        subprocess.run(['systemctl', '--user', 'restart', *units], check=True)
    print('Starport installed. Use ./bin/starport urls and ./bin/starport key.')
    print('User services start at login; linger is an explicit host setting.')


if __name__ == '__main__':
    main()
