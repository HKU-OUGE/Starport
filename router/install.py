"""Install only the fixed executor on an existing Linux router gateway."""
from pathlib import Path
import argparse,os,platform,shutil,sys

def main():
    p=argparse.ArgumentParser(description='Register the Starport router executor on an existing Linux gateway')
    p.add_argument('--check',action='store_true');p.add_argument('--config',help='Root-owned gateway profile')
    a=p.parse_args()
    if platform.system()!='Linux':p.error('Run this installer inside the Linux router VM or gateway')
    try:import yaml
    except ImportError:p.error('Install python3-yaml first')
    if a.check:print('Executor runtime available; an existing guarded router profile is required.');return
    if os.geteuid()!=0:p.error('Administrator access is required on the gateway')
    if not a.config:p.error('Provide the existing gateway configuration with --config')
    source=Path(__file__).resolve().parents[1];target=Path('/usr/local/lib/starport-router')
    target.mkdir(parents=True,exist_ok=True,mode=0o755)
    for name in ('starport_common','isolation_toolkit'):
        shutil.copytree(source/name,target/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copy2(source/'apply_exit.py',target/'apply_exit.py')
    (target/'router').mkdir(exist_ok=True);shutil.copy2(source/'router/executor.py',target/'router/executor.py')
    profile=Path('/etc/starport-router/executor.json');profile.parent.mkdir(mode=0o700,exist_ok=True)
    if profile.exists():shutil.copy2(profile,profile.with_suffix('.json.previous'))
    # Resolve all profile paths before relocating its config file.
    sys.path.insert(0,str(source));from isolation_toolkit.config import load
    import json
    config=load(Path(a.config).resolve())
    if config.get('router',{}).get('executor_ssh'):p.error('The gateway requires its local guarded-router profile')
    host=Path(config['router']['host_exit_dir'])
    if not host.is_absolute():config['router']['host_exit_dir']=str((Path(a.config).resolve().parent/host).resolve())
    profile.write_text(json.dumps(config), encoding='utf-8');profile.chmod(0o600)
    entry=Path('/usr/local/bin/starport-router-executor')
    entry.write_text('#!/bin/sh\nexec /usr/bin/python3 /usr/local/lib/starport-router/router/executor.py\n', encoding='utf-8');entry.chmod(0o755)
    print('Executor installed. No router process or firewall rule was changed.')

if __name__=='__main__':main()
