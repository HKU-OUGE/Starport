import argparse
import json
from pathlib import Path
from .config import load
from .operations import Manager, monitor


def main():
    p = argparse.ArgumentParser(description='Starport: operator-only workspace management')
    p.add_argument('--config', help='External local configuration; never commit it')
    s = p.add_subparsers(dest='command', required=True)
    for cmd in ('status', 'services', 'monitor', 'serve', 'urls', 'key'):
        s.add_parser(cmd)
    r = s.add_parser('router'); r.add_argument('--internet', action='store_true')
    a = s.add_parser('service'); a.add_argument('id'); a.add_argument('action'); a.add_argument('--apply', action='store_true')
    a = s.add_parser('apply-exit'); a.add_argument('plan'); a.add_argument('--check', action='store_true')
    args = p.parse_args(); config = load(args.config); m = Manager(config)
    if args.command == 'apply-exit':
        import subprocess, sys, re
        from .config import ROOT, config_dir
        if not re.fullmatch('[a-f0-9]{32}', args.plan): p.error('Invalid plan ID')
        if config.get('router',{}).get('executor_ssh'):
            from .router_remote import call
            folder=Path(config['state_dir'])/'subscriptions'
            plan=json.loads((folder/f'plan-{args.plan}.json').read_text(encoding='utf-8'))
            result=call(config,{'action':'check' if args.check else 'apply','plan':plan})
            (folder/f'apply-{args.plan}.json').write_text(json.dumps(result), encoding='utf-8')
            print(json.dumps(result));return
        import platform
        if platform.system()!='Linux':p.error('Configure a Linux gateway executor for this router profile')
        command = ['pkexec', sys.executable, str(ROOT / 'apply_exit.py'), '--config', str(Path(args.config).resolve() if args.config else config_dir() / 'config.json'), '--plan', args.plan]
        if args.check: command.append('--check')
        raise SystemExit(subprocess.run(command).returncode)
    if args.command == 'serve':
        from .server import serve
        return serve(config)
    if args.command == 'key':
        print(Path(config['access_key_file']).read_text(encoding='utf-8').strip()); return
    if args.command == 'urls':
        result = {'panel': [f'http://{host}:{config["port"]}/' for host in config['listen']], 'links': config.get('links', {})}
    elif args.command == 'monitor':
        result = monitor(config)
    elif args.command == 'router':
        import time
        m.check_router(args.internet)
        while m.router_status()['state'] == 'checking':
            time.sleep(0.2)
        result = m.router_status()
    elif args.command == 'service':
        result = m.preview(args.id, args.action)
        if args.apply:
            result = m.execute(result['payload'], result['digest'])
    else:
        result = m.overview()
        if args.command == 'services': result = result['services']
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
