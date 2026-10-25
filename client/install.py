"""Register Starship on macOS without replacing an active session service."""
from pathlib import Path
import argparse
import fcntl
import json
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent / 'starship'
ROLE = 'io.starport.starship'


def installation(home, python):
    root = home / '.local/share/starship'
    state = root / 'state'
    units = {}
    for suffix, script, args in (
        ('', 'client.py', []), ('-activity', 'activity_observer.py', ['serve']),
        ('-model-settings', 'model_settings.py', ['serve']), ('-auth', 'session_auth_check.py', [])):
        label = ROLE + suffix
        value = {'Label': label, 'ProgramArguments': [str(python), str(root / script), *args],
                 'RunAtLoad': True, 'ProcessType': 'Background', 'ThrottleInterval': 5,
                 'WorkingDirectory': str(root), 'Umask': 0o077,
                 'EnvironmentVariables': {'PATH': str(home / '.local/bin') + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                                          'PYTHONDONTWRITEBYTECODE': '1'},
                 'StandardOutPath': str(state / (label + '.log')),
                 'StandardErrorPath': str(state / (label + '.log'))}
        if suffix == '-auth':
            value['StartInterval'] = 300
        else:
            value['KeepAlive'] = {'SuccessfulExit': False}
        units[label + '.plist'] = value
    return root, units


def check_inactive(root):
    # Refuse all running daemons, even idle ones: updating shared Python modules
    # beneath a live process can mix versions. The operator stops them explicitly.
    state = root / 'state'
    locks = ('service.lock', 'activity.lock', 'model-settings/agent.lock', 'artifact-sources/sync.lock', 'auth-maintenance/probe.lock')
    for name in locks:
        path = state / name
        if path.exists():
            with path.open('r') as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError('Starship is running. Finish managed work and stop its agents before updating.') from None
    for path in state.glob('*.json'):
        if len(path.stem) == 36:
            value = json.loads(path.read_text())
            if value.get('status') in ('running', 'starting', 'waiting_approval'):
                raise ValueError('An unfinished managed session must be reconciled before updating Starship.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install Starship on macOS')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--no-start', action='store_true')
    args = parser.parse_args(argv)
    os.umask(0o077)
    home = Path.home(); root, units = installation(home, Path(sys.executable))
    if args.plan:
        print(json.dumps({'role': 'Starship', 'agents': units}, indent=2)); return
    if platform.system() != 'Darwin' or sys.version_info < (3, 10):
        parser.error('Starship requires macOS and Python 3.10+. Use --plan to inspect on another platform.')
    sys.path.insert(0, str(SOURCE))
    from client import cli
    try:
        cli()
        check_inactive(root)
    except ValueError as exc:
        parser.error(str(exc))
    domain = 'gui/' + str(os.getuid())
    if not args.check:
        for filename in units:
            if subprocess.run(['/bin/launchctl', 'print', domain + '/' + filename[:-6]], capture_output=True).returncode == 0:
                parser.error('Starship agents are loaded. Unload them after managed work finishes before updating.')
    if args.check:
        print('Starship prerequisites available. Enable Remote Login and authenticate Claude on this device.'); return
    if not args.yes and input('Install Starship background services? [y/N] ').lower() != 'y':
        return
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        parser.error('Linked installation directory is not supported')
    (root / 'state').mkdir(exist_ok=True, mode=0o700)
    for source in SOURCE.glob('*.py'):
        destination = root / source.name
        if destination.is_symlink():
            parser.error('Linked installation file is not supported')
        shutil.copyfile(source, destination); destination.chmod(0o600)
    launcher = root / 'rpc'
    if launcher.is_symlink():
        parser.error('Linked launcher is not supported')
    launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(root / 'gateway.py')) + ' "$@"\n')
    launcher.chmod(0o700)
    folder = home / 'Library/LaunchAgents'; folder.mkdir(parents=True, exist_ok=True)
    for name, value in units.items():
        path = folder / name
        if path.is_symlink():
            parser.error('Linked LaunchAgent is not supported')
        if path.exists():
            shutil.copy2(path, path.with_suffix('.plist.previous'))
        path.write_bytes(plistlib.dumps(value)); path.chmod(0o600)
        if not args.no_start:
            subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(path)], check=True)
    print('Starship installed. Use ./bin/starship status and pair its SSH key with Starport.')
    print('After a reboot, log in to macOS once. Locking the screen does not unload the agents.')


if __name__ == '__main__':
    main()
