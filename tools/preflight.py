"""Read-only installation checks. Never exports environment values or credentials."""
from pathlib import Path
import argparse
import importlib.util
import json
import platform
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'client/starship'))
from runner_locator import find_claude


def checks(role, system=None, which=shutil.which, run=subprocess.run):
    system = system or platform.system()
    rows = []
    def add(name, ok, action, required=True):
        rows.append({'check': name, 'status': 'pass' if ok else 'missing',
                     'required': required, 'next': '' if ok else action})
    add('python', sys.version_info >= (3, 10), 'Install Python 3.10+ and open a new terminal.')
    add('platform', system in ('Linux', 'Darwin', 'Windows'), 'Use Linux, macOS or Windows.')
    manager = {'Linux':'systemctl', 'Darwin':'launchctl', 'Windows':'schtasks.exe'}.get(system)
    add('user_services', bool(manager and which(manager)), 'Check the user service manager.')
    for program in ('ssh', 'ssh-keygen'):
        add(program, bool(which(program)), 'Install the OpenSSH client tools.')
    if role == 'server':
        add('npm', bool(which('npm')), 'Install Node.js 22.12+ with npm, then reopen the terminal.')
        node_ok = False
        if which('node'):
            try:
                r = run(['node','--version'], capture_output=True, text=True, timeout=5)
                match = re.fullmatch(r'v?(\d+)\.(\d+)\.\d+\s*', r.stdout)
                node_ok = r.returncode == 0 and bool(match) and tuple(map(int,match.groups())) >= (22,12)
            except (OSError, subprocess.SubprocessError):
                pass
        add('node_22_12_or_newer', node_ok, 'Install Node.js 22.12+; the OS package may be older.')
        add('python_venv', importlib.util.find_spec('ensurepip') is not None or bool(which('uv')),
            'Install the matching Python venv package or uv.')
    else:
        try:
            find_claude(system=system)
            runner = True
        except ValueError:
            runner = False
        add('claude_executable', runner, 'Install Claude Code on this device; see docs/MAC.md or docs/QUICKSTART.md.')
        add('git', bool(which('git')), 'Install Git for project tools.', required=False)
        if system == 'Darwin':
            try:
                r = run(['xcode-select','-p'], capture_output=True, text=True, timeout=5)
                devtools = r.returncode == 0
            except (OSError, subprocess.SubprocessError):
                devtools = False
            add('mac_command_line_tools', devtools, 'Run xcode-select --install locally if developer tools are needed.', required=False)
    return {'role': role, 'platform': system, 'checks': rows,
            'prerequisites_ready': all(x['status'] == 'pass' for x in rows if x['required']),
            'not_verified': ['SSH host identity and pairing', 'Claude account authentication',
                             'Network isolation and proxy failure behavior', 'Login-start recovery'],
            'read_only': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('server','client'))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    result = checks(args.role)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for row in result['checks']:
            print(f"{row['status'].upper():7} {row['check']}" + (': '+row['next'] if row['next'] else ''))
        print('Manual acceptance: '+', '.join(result['not_verified'])+'.')
    return 0 if result['prerequisites_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
