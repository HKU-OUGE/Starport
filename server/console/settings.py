"""Operator configuration; none of these fields comes from a browser request."""
from pathlib import Path
from urllib.parse import urlsplit
import ipaddress
import json
import os
import re


def config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'starport/console.json'


def state_path():
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'starport/console'


def validate(value):
    allowed = {'host', 'port', 'ssh_host', 'state_dir', 'public_origin', 'tunnel_port', 'management_url'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('unknown_console_configuration')
    data = {'host': '127.0.0.1', 'port': 18881, 'ssh_host': 'starship',
            'state_dir': str(state_path()), 'public_origin': '', 'tunnel_port': 18883,
            'management_url': '', **value}
    ip = ipaddress.ip_address(data['host'])
    private = ip.is_loopback or any(ip in ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    if ip.version != 4 or not private:
        raise ValueError('private_ipv4_listener_required')
    for key in ('port', 'tunnel_port'):
        if type(data[key]) is not int or not 1024 <= data[key] <= 65535:
            raise ValueError('invalid_port')
    if data['port'] == data['tunnel_port']:
        raise ValueError('separate_tunnel_port_required')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', data['ssh_host']):
        raise ValueError('invalid_ssh_alias')
    if data['public_origin'] and not re.fullmatch(r'https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', data['public_origin']):
        raise ValueError('exact_https_origin_required')
    url = urlsplit(data['management_url'])
    if data['management_url'] and (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.fragment):
        raise ValueError('invalid_management_url')
    if not isinstance(data['state_dir'], str) or not data['state_dir']:
        raise ValueError('invalid_state_directory')
    return data


def load(path=None):
    p = Path(path).expanduser() if path else config_path()
    data = validate(json.loads(p.read_text()) if p.is_file() else {})
    state = Path(data['state_dir']).expanduser()
    data['state_dir'] = str((p.parent / state).resolve() if not state.is_absolute() else state.resolve())
    return data
