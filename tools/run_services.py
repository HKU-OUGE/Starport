"""Run installer-generated services in the foreground, without login registration."""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from starport_common.runtime import state_home,config_home,process_options,stop_process,fcntl,secure_dir


def profiles(role):
    if role=='server':
        config=json.loads((config_home()/'starport/console.json').read_text(encoding='utf-8'))
        root=Path(config['state_dir'])/'services'
        names=('starport-console','starport-panel')
    else:
        root=Path.home()/'.local/share/starship/state/services'
        names=('starship','starship-activity','starship-model-settings','starship-auth')
    result=[root/(n+'.json') for n in names]
    if any(p.is_symlink() or not p.is_file() for p in result):
        raise ValueError('Install the selected role with --no-register first.')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role',choices=('server','client'))
    args=parser.parse_args(argv);children=[];stopping=False
    try:
        configs=profiles(args.role)
        if args.role=='client':
            spec=importlib.util.spec_from_file_location('starship_installer',ROOT/'client/install.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            module.check_inactive(Path.home()/'.local/share/starship')
        else:
            config=json.loads((config_home()/'starport/console.json').read_text(encoding='utf-8'))
            for port in (config['port'],config['tunnel_port']):
                try:
                    with socket.create_connection((config['host'],port),timeout=.5):
                        raise ValueError('A server listener is already running. Stop the existing instance first.')
                except OSError:pass
        folder=secure_dir(configs[0].parent)
        with (folder/'foreground.lock').open('a+b') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('A foreground service group is already running.') from None
            def stop(*_):
                nonlocal stopping
                stopping=True
            for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,stop)
            for profile in configs:
                children.append(subprocess.Popen([sys.executable,str(ROOT/'starport_common/supervise.py'),str(profile)],**process_options()))
            print('Starport services running in this terminal. Finish active sessions before Ctrl+C.',flush=True)
            while not stopping:
                if any(p.poll() is not None for p in children):raise RuntimeError('A service supervisor stopped; inspect its local log.')
                time.sleep(.3)
        return 0
    except (OSError,ValueError,RuntimeError) as exc:
        parser.exit(1,str(exc)+'\n')
    finally:
        for p in children:
            try:stop_process(p)
            except OSError:pass
        for p in children:
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:p.kill();p.wait()


if __name__=='__main__':raise SystemExit(main())
