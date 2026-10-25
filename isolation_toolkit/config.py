from pathlib import Path
import ipaddress
import json
import os

ROOT = Path(__file__).resolve().parents[1]


def config_dir():
    root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    current = root / 'starport'
    previous = root / 'claude-isolation'
    return current if (current / 'config.json').exists() or not (previous / 'config.json').exists() else previous


def state_dir():
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'starport/panel'


def load(path=None):
    p = Path(path) if path else config_dir() / 'config.json'
    d = json.loads(p.read_text())
    for key in ('legacy_root', 'access_key_file', 'state_dir'):
        if key == 'legacy_root' and not d.get(key):
            d[key] = None
            continue
        value = Path(d[key]).expanduser()
        d[key] = str((p.parent / value).resolve() if not value.is_absolute() else value.resolve())
    addresses = d.get('listen', ['127.0.0.1'])
    if not addresses or len(addresses) > 3:
        raise ValueError('invalid_listeners')
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if ip.version != 4 or ip.is_unspecified or not (ip.is_private or ip.is_loopback):
            raise ValueError('private_ipv4_listener_required')
    if not isinstance(d.get('port'), int) or not 1024 <= d['port'] <= 65535:
        raise ValueError('invalid_port')
    return d
