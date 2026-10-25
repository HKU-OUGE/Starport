"""Fixed SSH entry points. Also usable as an authorized_keys forced command."""
from pathlib import Path
import os
import sys
from bootstrap import WINDOWS
from starport_common.transport import command as wire_command

COMMAND = '"$HOME/.local/share/starship/rpc" '
ENDPOINTS = {
    'session': ('client.py', 'rpc'),
    'settings': ('model_settings.py', 'rpc'),
    'activity': ('activity_observer.py', 'rpc'),
    'documents': ('document_reader.py',),
    'artifacts': ('artifact_reader.py',),
    'artifact-sync': ('artifact_sync_control.py',),
}


def resolve(argv, original=None):
    if original is not None:
        matches=[name for name in ENDPOINTS if original in (wire_command(name,'posix'),wire_command(name,'windows'))]
        if argv or len(matches)!=1:
            raise ValueError('ssh_command_blocked')
        argv = matches
    if len(argv) != 1 or argv[0] not in ENDPOINTS:
        raise ValueError('ssh_command_blocked')
    return ENDPOINTS[argv[0]]


def main():
    try:
        script, *args = resolve(sys.argv[1:], os.environ.get('SSH_ORIGINAL_COMMAND'))
    except ValueError:
        print('{"ok":false,"error":"ssh_command_blocked"}')
        return 1
    root = Path(__file__).resolve().parent
    # Preserve the device's own environment, never an SSH-forwarded agent.
    env = {k: v for k, v in os.environ.items() if k not in ('SSH_AUTH_SOCK', 'SSH_ORIGINAL_COMMAND') and not k.startswith('STARPORT_')}
    os.execve(sys.executable, [sys.executable, str(root / script), *args], env)


if __name__ == '__main__':
    raise SystemExit(main())
