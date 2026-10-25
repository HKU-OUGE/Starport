"""Register Starship on macOS without replacing an active session service."""
from pathlib import Path
import argparse
import json
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from starport_common.runtime import fcntl,secure_dir,WINDOWS
from starport_common import services
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
            with path.open('r+') as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError('Starship is running. Finish managed work and stop its agents before updating.') from None
    for path in state.glob('*.json'):
        if len(path.stem) == 36:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('status') in ('running', 'starting', 'waiting_approval'):
                raise ValueError('An unfinished managed session must be reconciled before updating Starship.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install Starship on Linux, macOS or Windows')
    parser.add_argument('--platform',choices=('Linux','Darwin','Windows'),help='Plan generation only')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--no-start', action='store_true')
    args = parser.parse_args(argv)
    os.umask(0o077)
    home = Path.home(); root, units = installation(home, Path(sys.executable))
    system=args.platform or platform.system()
    jobs=(('starship','client.py',[],None),('starship-activity','activity_observer.py',['serve'],None),('starship-model-settings','model_settings.py',['serve'],None),('starship-auth','session_auth_check.py',[],300))
    if args.plan:
        definitions=dict(services.definition(name,[sys.executable,root/script,*arguments],root,system) for name,script,arguments,interval in jobs)
        print(json.dumps({'role':'Starship','services':definitions},indent=2));return
    if args.platform:parser.error('--platform is only valid with --plan')
    if system not in ('Linux','Darwin','Windows') or sys.version_info < (3, 10):
        parser.error('Starship requires Python 3.10+ on Linux, macOS or Windows. Use --plan to inspect on another platform.')
    sys.path.insert(0, str(SOURCE))
    from client import cli
    try:
        cli()
        check_inactive(root)
    except ValueError as exc:
        parser.error(str(exc))
    if args.check:
        print('Starship prerequisites available. Enable Remote Login and authenticate Claude on this device.'); return
    if not args.yes and input('Install Starship background services? [y/N] ').lower() != 'y':
        return
    secure_dir(root)
    if root.is_symlink():
        parser.error('Linked installation directory is not supported')
    secure_dir(root/'state')
    shutil.copytree(REPO/'starport_common',root/'starport_common',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for source in SOURCE.glob('*.py'):
        destination = root / source.name
        if destination.is_symlink():
            parser.error('Linked installation file is not supported')
        shutil.copyfile(source, destination); destination.chmod(0o600)
    launcher=root/('rpc.cmd' if WINDOWS else 'rpc')
    if launcher.is_symlink():parser.error('Linked launcher is not supported')
    if WINDOWS:
        launcher.write_text('@echo off\r\n"'+sys.executable+'" "'+str(root/'gateway.py')+'" %*\r\n',encoding='utf-8')
    else:
        launcher.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(root/'gateway.py'))+' "$@"\n',encoding='utf-8')
    launcher.chmod(0o700)
    definitions=dict(services.prepare(name,[sys.executable,root/script,*arguments],root,root/'state',sys.executable,interval=interval)
                     for name,script,arguments,interval in jobs)
    services.register(definitions,args.no_start)
    print('Starship installed. Use ./bin/starship status and pair its SSH key with Starport.')
    print('Services run in your user session. After a reboot, sign in once; locking the screen preserves them.')


if __name__ == '__main__':
    main()
