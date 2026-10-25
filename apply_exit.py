"""Local, explicitly approved exit-plan application. No browser-side root API."""
from pathlib import Path
import argparse
from starport_common.runtime import fcntl
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import yaml


def replace_set(text, name, endpoints):
    elements = ', '.join(f'{ip} . {port}' for ip, port in sorted(set(endpoints)))
    pattern = r'(set\s+' + re.escape(name) + r'\s*\{\s*type\s+ipv4_addr\s*\.\s*inet_service\s*;\s*elements\s*=\s*\{)[^}]*(\})'
    result, count = re.subn(pattern, lambda m: m[1] + ' ' + elements + ' ' + m[2], text)
    if count not in (1, 2): raise ValueError('unrecognized_guard_layout')
    return result


def run(argv, data=None, timeout=30):
    p = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
    if p.returncode: raise ValueError('operation_failed_' + Path(argv[0]).name)
    return p.stdout


def secure_host_config(host_dir):
    if host_dir.is_symlink() or host_dir.stat().st_uid!=0: raise ValueError('host_configuration_not_root_owned')
    for name in ('host-guard.nft','host-routes.py','endpoints.json'):
        p=host_dir/name
        if p.is_symlink() or p.stat().st_uid!=0 or p.stat().st_mode&0o022: raise ValueError('unsafe_host_configuration')


def apply(config, plan_id, check=False):
    if os.geteuid() != 0: raise ValueError('local_administrator_required')
    if not re.fullmatch('[a-f0-9]{32}', plan_id): raise ValueError('invalid_plan')
    state=Path(config['state_dir']); path=state/'subscriptions'/f'plan-{plan_id}.json'
    if path.is_symlink() or path.stat().st_size>1024*1024: raise ValueError('invalid_plan_file')
    plan=json.loads(path.read_text())
    if time.time()-plan['created_at']>1800: raise ValueError('plan_expired')
    profile=config['router']; host_dir=Path(profile['host_exit_dir'])
    secure_host_config(host_dir)
    ssh=profile['ssh']
    def remote(cmd,data=None,timeout=30): return run(ssh+[cmd],data,timeout)
    lock_path=host_dir/'toolkit-apply.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        old_raw=remote('cat /etc/mm-proxy/config.yaml'); old=yaml.safe_load(old_raw)
        candidate=plan['candidate']
        if old==candidate: return {'ok':True,'unchanged':True}
        if hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest()!=plan['base_sha256']: raise ValueError('router_changed_preview_again')
        # Only nodes, group selection and endpoint route exclusions may change.
        other=copy_without_exit(old); wanted=copy_without_exit(candidate)
        if other!=wanted or candidate.get('ipv6') is not False or not candidate['tun'].get('strict-route'): raise ValueError('immutable_boundary_changed')
        groups=candidate['proxy-groups']; old_group=old['proxy-groups'][0]
        if len(groups)!=1 or groups[0]['name']!=old_group['name'] or groups[0]['type'] not in ('fallback','select'): raise ValueError('group_boundary_changed')
        group=groups[0]
        if {k:v for k,v in group.items() if k not in ('type','proxies')}!={k:v for k,v in old_group.items() if k not in ('type','proxies')}: raise ValueError('group_options_changed')
        eps=plan['endpoints']; new_pairs=[]
        if len(eps)!=3 or len(candidate['proxies'])!=3: raise ValueError('three_slots_required')
        names=[n['name'] for n in old['proxies']]
        if group['proxies'] not in (names,names[:1]): raise ValueError('invalid_group_nodes')
        for ep,node,name in zip(eps,candidate['proxies'],names):
            address=ipaddress.ip_address(ep['ip']);port=ep['port']
            if address.version!=4 or not address.is_global or type(port)!=int or not 1<=port<=65535 or ep['protocol']!='tcp': raise ValueError('invalid_endpoint')
            if ep['node']!=name or node['name']!=name or node['server']!=str(address) or node['port']!=port or node.get('udp') is not False or node.get('type') not in ('ss','vmess','vless','trojan') or node.get('interface-name')!=old['interface-name']: raise ValueError('invalid_node_boundary')
            from isolation_toolkit.subscriptions import normalize
            normalized=normalize(node);supported=normalized.pop('_supported');normalized['interface-name']=old['interface-name']
            if not supported or normalized!=node: raise ValueError('unsupported_node_fields')
            new_pairs.append((str(address),port))
        if len(set(new_pairs))!=3: raise ValueError('distinct_endpoints_required')
        host_guard=(host_dir/'host-guard.nft').read_text();old_endpoints=(host_dir/'endpoints.json').read_bytes()
        guest_guard=remote('cat /etc/mm-proxy/guard.nft').decode()
        new_host=replace_set(host_guard,'tcp_endpoints',new_pairs);new_guest=replace_set(guest_guard,'nodes',new_pairs)
        encoded=yaml.safe_dump(candidate,allow_unicode=True,sort_keys=False).encode()
        # Temporary validation is private and does not reload any running service.
        remote('umask 077; cat > /etc/mm-proxy/toolkit-candidate.yaml',encoded)
        remote('/usr/sbin/mm-mihomo -t -d /tmp/mm-proxy-state -f /etc/mm-proxy/toolkit-candidate.yaml',timeout=30)
        remote('umask 077; cat > /etc/mm-proxy/toolkit-guard.nft',new_guest.encode())
        remote('nft -c -f /etc/mm-proxy/toolkit-guard.nft')
        run(['nft','-c','-f','-'],new_host.encode())
        if check: return {'ok':True,'validated':True,'changed':False}
        backup=host_dir/'toolkit-backups'/plan_id;backup.mkdir(parents=True,mode=0o700)
        for name,content in [('host-guard.nft',host_guard.encode()),('endpoints.json',old_endpoints),('guest-guard.nft',guest_guard.encode()),('config.yaml',old_raw)]:
            (backup/name).write_bytes(content);(backup/name).chmod(0o600)
        routes=['python3',str(host_dir/'host-routes.py')]
        allowlist=['--allowlist',str(host_dir/'endpoints.json')]
        try:
            # Close only the managed egress first; independent kill switches remain.
            run(routes+['close']+allowlist)
            remote('/etc/init.d/mm-proxy stop')
            (host_dir/'host-guard.nft').write_text(new_host)
            (host_dir/'endpoints.json').write_text(json.dumps({'endpoints':eps}))
            run(['nft','-f',str(host_dir/'host-guard.nft')])
            remote('cat > /etc/mm-proxy/guard.nft',new_guest.encode())
            remote('nft -f /etc/mm-proxy/guard.nft')
            remote('cat > /etc/mm-proxy/config.yaml',encoded)
            run(routes+['activate']+allowlist)
            remote('/etc/init.d/mm-proxy start')
            success=False
            for _ in range(3):
                time.sleep(3)
                try:
                    remote("curl --silent --fail --max-time 12 --proxy http://127.0.0.1:17890 --output /dev/null https://example.com/",timeout=16);success=True;break
                except Exception: pass
            if not success: raise ValueError('new_exit_https_failed')
            result={'ok':True,'applied':True,'plan':plan_id,'checked_at':time.time()}
        except Exception:
            try:
                run(routes+['close']+allowlist)
                remote('/etc/init.d/mm-proxy stop')
                (host_dir/'host-guard.nft').write_text(host_guard);(host_dir/'endpoints.json').write_bytes(old_endpoints)
                run(['nft','-f',str(host_dir/'host-guard.nft')])
                remote('cat > /etc/mm-proxy/guard.nft',guest_guard.encode());remote('nft -f /etc/mm-proxy/guard.nft')
                remote('cat > /etc/mm-proxy/config.yaml',old_raw)
                run(routes+['activate']+allowlist);remote('/etc/init.d/mm-proxy start')
                result={'ok':False,'error':'apply_failed_previous_restored'}
            except Exception:
                # Leave the egress closed if recovery cannot be completed.
                try: run(routes+['close']+allowlist)
                except Exception: pass
                result={'ok':False,'error':'recovery_incomplete_egress_closed'}
        report=state/'subscriptions'/f'apply-{plan_id}.json';report.write_text(json.dumps(result));report.chmod(0o600)
        uid=int(os.environ.get('PKEXEC_UID',os.environ.get('SUDO_UID',os.getuid())))
        if uid: os.chown(report,uid,-1)
        return result


def copy_without_exit(d):
    d=json.loads(json.dumps(d));d.pop('proxies');d.pop('proxy-groups');d['tun'].pop('route-exclude-address',None);return d


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--plan',required=True);p.add_argument('--check',action='store_true');a=p.parse_args()
    os.umask(0o077)
    try:
        result=apply(json.loads(Path(a.config).read_text()),a.plan,a.check);print(json.dumps(result));sys.exit(0 if result['ok'] else 1)
    except Exception as e:
        # No raw library exception, URL, credential or node configuration in output.
        print(json.dumps({'ok':False,'error':str(e) if isinstance(e,ValueError) else 'exit_plan_failed'}));sys.exit(1)
