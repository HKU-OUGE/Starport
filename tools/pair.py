"""Create restricted SSH pairing records; changes require an explicit --apply."""
from pathlib import Path
import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import shlex
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from starport_common.runtime import secure_dir
from starport_common.transport import command


def public_key(text):
    if '\n' in text.strip() or '\r' in text.strip():
        raise ValueError('Provide one Ed25519 public key, not a key file bundle.')
    parts = text.strip().split()
    if len(parts) < 2 or parts[0] != 'ssh-ed25519':
        raise ValueError('An ssh-ed25519 public key is required. Never provide a private key.')
    try:
        raw = base64.b64decode(parts[1], validate=True)
        expected = struct.pack('>I', 11)+b'ssh-ed25519'+struct.pack('>I', 32)
        if len(raw) != len(expected)+32 or not raw.startswith(expected):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Invalid Ed25519 public key.') from None
    fingerprint = 'SHA256:'+base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
    return 'ssh-ed25519 '+parts[1], fingerprint


def replace_checked(path, before, text):
    path = Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('Linked SSH paths are not supported.')
    secure_dir(path.parent)
    if path.exists() and os.name != 'nt' and path.stat().st_uid != os.getuid():
        raise ValueError('SSH file must belong to this user.')
    if (path.read_text(encoding='utf-8') if path.exists() else '') != before:
        raise ValueError('SSH configuration changed; inspect it and retry.')
    fd, temporary = tempfile.mkstemp(prefix='.starport-', dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(text);stream.flush();os.fsync(stream.fileno())
        os.chmod(temporary,0o600)
        if (path.read_text(encoding='utf-8') if path.exists() else '') != before:
            raise ValueError('SSH configuration changed; inspect it and retry.')
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def authorize(key_text, home=None, system=None, apply=False):
    home = Path(home) if home is not None else Path.home()
    windows = (system == 'Windows') if system else os.name == 'nt'
    if windows and os.name == 'nt':
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            raise ValueError('Use the non-administrator Starship account; administrator SSH keys have a different shared location.')
    key, fingerprint = public_key(key_text)
    launcher = home / '.local/share/starship' / ('rpc.cmd' if windows else 'rpc')
    if not launcher.is_file() or launcher.is_symlink():
        raise ValueError('Install Starship before adding its restricted SSH key.')
    raw_command = subprocess.list2cmdline([str(launcher)]) if windows else shlex.quote(str(launcher))
    escaped = raw_command.replace('\\','\\\\').replace('"','\\"')
    line = 'restrict,command="'+escaped+'" '+key+' starport-pairing'
    path = home / '.ssh/authorized_keys'
    if path.is_symlink() or path.parent.is_symlink():raise ValueError('Linked SSH paths are not supported.')
    before = path.read_text(encoding='utf-8') if path.exists() else ''
    matching = [row for row in before.splitlines() if not row.lstrip().startswith('#') and key.split()[1] in row.split()]
    if matching and matching != [line]:
        raise ValueError('This public key already has a different rule. Review it manually; no rule was overwritten.')
    if apply and not matching:
        replace_checked(path,before,before+('' if not before or before.endswith('\n') else '\n')+line+'\n')
    return {'fingerprint':fingerprint,'restricted':True,'already_present':bool(matching),
            'changed':bool(apply and not matching),'applied':apply,'target':'current user authorized_keys'}


def host_value(value):
    # Literal private addresses avoid accidental public or DNS-rebound pairing.
    try:address=ipaddress.ip_address(value)
    except ValueError:raise ValueError('Use the device private IPv4 address.') from None
    private = any(address in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))
    if address.version!=4 or not private:raise ValueError('Use the device private IPv4 address.')
    return str(address)


def pin(host, fingerprint, port=22, apply=False, home=None, run=subprocess.run):
    host=host_value(host)
    if not 1<=port<=65535:raise ValueError('Invalid SSH port.')
    if not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}',fingerprint):raise ValueError('Copy the SHA256 host fingerprint from the device itself.')
    scan=run(['ssh-keyscan','-T','5','-p',str(port),'-t','ed25519',host],capture_output=True,text=True,timeout=12)
    matches=[]
    for row in scan.stdout.splitlines():
        parts=row.split()
        if len(parts)<3 or row.startswith('#'):continue
        try:key,actual=public_key(' '.join(parts[1:3]))
        except ValueError:continue
        if actual==fingerprint:matches.append(key)
    if len(set(matches))!=1:raise ValueError('Host fingerprint did not match. No host key was trusted.')
    name=host if port==22 else '['+host+']:'+str(port)
    record=name+' '+matches[0]
    path=(Path(home) if home is not None else Path.home())/'.ssh/starport_known_hosts'
    if path.is_symlink() or path.parent.is_symlink():raise ValueError('Linked SSH paths are not supported.')
    before=path.read_text(encoding='utf-8') if path.exists() else ''
    rows=[r for r in before.splitlines() if r.split() and name in r.split()[0].split(',')]
    if rows and rows!=[record]:raise ValueError('A different pinned host key already exists. Investigate the change first.')
    if apply and not rows:replace_checked(path,before,before+('' if not before or before.endswith('\n') else '\n')+record+'\n')
    return {'fingerprint_verified':True,'applied':apply,'changed':bool(apply and not rows)}


def ssh_config(host,user,port=22):
    host=host_value(host)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',user) or user.startswith('-'):raise ValueError('Use a local account short name.')
    if not 1<=port<=65535:raise ValueError('Invalid SSH port.')
    return f'''Host starship
    HostName {host}
    Port {port}
    User {user}
    IdentityFile ~/.ssh/starport_starship
    UserKnownHostsFile ~/.ssh/starport_known_hosts
    IdentitiesOnly yes
    StrictHostKeyChecking yes
    ForwardAgent no
    SendEnv -*
    ServerAliveInterval 10
    ServerAliveCountMax 2
'''


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='op',required=True)
    a=sub.add_parser('authorize');a.add_argument('--public-key',required=True);a.add_argument('--apply',action='store_true')
    a=sub.add_parser('pin');a.add_argument('--host',required=True);a.add_argument('--fingerprint',required=True);a.add_argument('--port',type=int,default=22);a.add_argument('--apply',action='store_true')
    a=sub.add_parser('config');a.add_argument('--host',required=True);a.add_argument('--user',required=True);a.add_argument('--port',type=int,default=22)
    a=sub.add_parser('verify');a.add_argument('--alias',default='starship');a.add_argument('--client-platform',choices=('posix','windows'),default='posix')
    args=p.parse_args(argv)
    try:
        if args.op=='authorize':result=authorize(Path(args.public_key).read_text(encoding='utf-8'),apply=args.apply)
        elif args.op=='pin':result=pin(args.host,args.fingerprint,args.port,args.apply)
        elif args.op=='config':print(ssh_config(args.host,args.user,args.port));return 0
        else:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',args.alias):raise ValueError('Invalid SSH alias.')
            r=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ForwardAgent=no',
                '-o','SendEnv=-*','-o','ConnectTimeout=10',args.alias,command('session',args.client_platform)],
                input='{"action":"health"}\n',capture_output=True,text=True,timeout=30)
            if r.returncode:raise ValueError('SSH/RPC unavailable. Check the host fingerprint, account and restricted public key.')
            try:health=json.loads(r.stdout)
            except ValueError:raise ValueError('Unexpected RPC response; verify the fixed Starship command.') from None
            result={k:health.get(k) for k in ('ok','authenticated','running')}
            if result['ok'] is not True:raise ValueError('Starship did not return a healthy RPC response.')
        print(json.dumps(result,indent=2));return 0
    except (ValueError,OSError,subprocess.SubprocessError) as exc:
        p.exit(1,str(exc)+'\n')


if __name__=='__main__':raise SystemExit(main())
