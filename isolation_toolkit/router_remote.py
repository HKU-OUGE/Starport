"""Optional Linux gateway executor, reachable from any Starport host OS."""
import json,re,subprocess


def call(config,request):
    ssh=config.get('router',{}).get('executor_ssh')
    if not isinstance(ssh,list) or not ssh or not all(isinstance(x,str) for x in ssh):raise ValueError('router_executor_missing')
    result=subprocess.run(ssh+['sudo -n starport-router-executor'],input=json.dumps(request).encode(),capture_output=True,timeout=160)
    if result.returncode or len(result.stdout)>2*1024*1024:raise ValueError('router_executor_unavailable')
    value=json.loads(result.stdout)
    if not value.get('ok'):raise ValueError(value.get('error','router_executor_failed'))
    return value
