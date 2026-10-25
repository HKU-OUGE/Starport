#!/usr/bin/python3
"""Fixed gateway RPC. Installed on a Linux gateway, not on Starship."""
from pathlib import Path
import hashlib,json,os,re,sys,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from isolation_toolkit.config import load
from isolation_toolkit.subscriptions import Subscriptions


def execute(config,data):
    action=data.get('action')
    if action=='current' and set(data)=={'action'}:return {'ok':True,'config':Subscriptions(config).current_config()}
    if action=='status' and set(data)=={'action','internet'}:
        from isolation_toolkit.operations import Manager
        manager=Manager(config);manager.check_router(bool(data['internet']))
        while manager.router_status()['state']=='checking':time.sleep(.2)
        return {'ok':True,'status':manager.router_status()}
    if action not in ('apply','check') or set(data)!={'action','plan'}:raise ValueError('router_action_blocked')
    plan=data['plan']
    if not isinstance(plan,dict) or not re.fullmatch('[a-f0-9]{32}',str(plan.get('id',''))):raise ValueError('invalid_plan')
    if type(plan.get('created_at')) not in (int,float) or not 0<=time.time()-plan['created_at']<=1800:raise ValueError('plan_expired')
    folder=Path(config['state_dir'])/'subscriptions';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=folder/('plan-'+plan['id']+'.json')
    if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=plan:raise ValueError('plan_id_conflict')
    if path.is_symlink():raise ValueError('invalid_plan')
    path.write_text(json.dumps(plan), encoding='utf-8');path.chmod(0o600)
    from apply_exit import apply
    return apply(config,plan['id'],check=action=='check')


def main():
    os.umask(0o077)
    try:
        if not hasattr(os,'geteuid') or os.geteuid()!=0:raise ValueError('gateway_administrator_required')
        path=Path('/etc/starport-router/executor.json')
        if path.is_symlink() or path.stat().st_uid!=0 or path.stat().st_mode&0o022:raise ValueError('unsafe_gateway_configuration')
        config=load(path)
        raw=sys.stdin.buffer.readline(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('router_request_limit')
        value=execute(config,json.loads(raw))
    except Exception as exc:
        reason=str(exc);value={'ok':False,'error':reason if re.fullmatch('[a-z_]{1,100}',reason) else 'router_operation_failed'}
    print(json.dumps(value))


if __name__=='__main__':main()
