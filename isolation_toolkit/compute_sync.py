"""Continuously reconcile registered compute nodes; never stop a training worker."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from .compute_availability import service_capacity, transition

VERSION = 1


def load(path, default=None):
    try: return json.loads(Path(path).read_text())
    except FileNotFoundError: return default


def atomic(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.new')
    with temp.open('x') as stream:
        os.chmod(temp, 0o600); json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    temp.replace(path)


@contextlib.contextmanager
def file_lock(path, blocking=True):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            if not stream.read(1): stream.write(b'0'); stream.flush()
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def patch_api(path, backup_dir):
    """Prepare profile-driven limits. Activation is separately gated on an idle API."""
    path = Path(path); old = path.read_text()
    if "'resource_profile_version':2" in old: return False
    new = old.replace("def profile():return json.loads((BASE/'profile.json').read_text())", """def profile():
 d=json.loads((BASE/'profile.json').read_text())
 if d.get('auto_managed'):
  age=time.time()-d.get('last_checked_at',0)
  d['validated']=bool(d.get('validated') and 0<=age<=d.get('max_stale_seconds',120))
 return d""")
    new = new.replace("'ready':profile()['validated']", "'ready':profile()['validated'],'resource_profile_version':2")
    for key in ('max_parallel', 'cpu_budget', 'memory_budget_gib'):
        new = re.sub(r"'"+key+r"':\d+", "'"+key+"':profile()['"+key+"']", new)
    lines = []
    for line in new.splitlines():
        if line.startswith('    ') and 'cpu=' in line and "d.get('cpus') is None" in line:
            line = "    limits=profile();ng=max(1,limits['max_gpus']);cpu=(min(limits['cpu_budget'],8) if not g else limits.get('default_cpus_per_gpu',limits['cpu_budget']//ng)*g) if d.get('cpus') is None else d['cpus'];mem=(min(limits['memory_budget_gib'],16) if not g else limits.get('default_memory_gib_per_gpu',limits['memory_budget_gib']//ng)*g) if d.get('memory_gib') is None else d['memory_gib']"
        if 'if type(cpu)' in line and "raise ValueError('invalid_resource_request')" in line:
            line = "    if type(cpu)!=int or not 1<=cpu<=profile()['cpu_budget'] or type(mem)!=int or not 2<=mem<=profile()['memory_budget_gib']:raise ValueError('invalid_resource_request')"
        lines.append(line)
    new = '\n'.join(lines)+'\n'; ast.parse(new)
    if "'resource_profile_version':2" not in new or 'def profile():\n' not in new:
        raise ValueError('unsupported_api_layout')
    backup = Path(backup_dir)/(path.parent.name+'-'+hashlib.sha256(old.encode()).hexdigest()[:12]+'.py')
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists(): backup.write_text(old); backup.chmod(0o600)
    if path.read_text() != old: raise RuntimeError('concurrent_api_edit')
    temp = path.with_name(path.name+'.compute-sync-new'); temp.write_text(new); temp.chmod(path.stat().st_mode & 0o777); temp.replace(path)
    return True


class Reconciler:
    def __init__(self, config, remote=None, api=None, restart=None):
        self.config = config
        self.base = Path(config['service_root'])
        self.registry = self.base/'dsw-pool.json'
        self.status = self.base/'node-status.json'
        self.state = Path(config['state_dir']); self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.mutex = threading.RLock()
        self.remote = remote or self.ssh
        self.api = api or self.http
        self.restart = restart or self.restart_service
        self.snapshot = load(self.status, {'version': VERSION, 'nodes': {}})
        self.snapshot['max_stale_seconds'] = config.get('max_stale_seconds', 120)
        self.script = Path(__file__).with_name('compute_remote.py').read_text()

    def http(self, node, path, data=None, managed=False):
        key = Path(self.config['management_token' if managed else 'api_token']).read_text().strip()
        req = urllib.request.Request('http://127.0.0.1:'+str(node['port'])+path,
              data=json.dumps(data).encode() if data is not None else None,
              headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=15) as response: return json.load(response)

    def ssh(self, node, op, **payload):
        conf = load(self.base/node['directory']/'operator.json')
        runtimes = [conf['runtime_path']]
        group = load(self.base/'multinode/operator.private.json', {}).get('nodes', {}).get(node['name'])
        if group and group['runtime'] not in runtimes: runtimes.append(group['runtime'])
        request = {'op':op,'config':{'runtimes':runtimes,'proxy_socket':conf['proxy_socket']},**payload}
        result = subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
             '-o','ConnectionAttempts=1','-o','ConnectTimeout=8',node['ssh_alias'],
             'python3 -I -c '+shlex.quote(self.script)],input=json.dumps(request),capture_output=True,text=True,timeout=30)
        if result.returncode:
            (self.state/(node['name']+'.private.log')).write_text(result.stderr[-8192:])
            raise RuntimeError('ssh_unavailable')
        value = json.loads(result.stdout)
        if not value.get('ok'): raise RuntimeError('remote_probe_failed')
        return value['result']

    def restart_service(self, node):
        command = node.get('restart_command')
        if not command: raise RuntimeError('api_reload_unavailable')
        subprocess.run(command, check=True, capture_output=True, timeout=20)

    def activate_idle_api(self, node, health):
        if health.get('resource_profile_version') == 2: return health
        if health.get('active_jobs') or health.get('busy'): return health
        management = Path(node['state_root'])/'management.json'
        before = load(management, {'accepting_new_jobs':True})
        paused = self.api(node, '/management/pause', {}, True)
        stamp = management.stat().st_mtime_ns
        marker = self.state/(node['name']+'.reload.json')
        atomic(marker, {'resume':before.get('accepting_new_jobs',True),'pause_stamp':stamp})
        try:
            checked = self.api(node, '/health')
            if checked.get('active_jobs') or checked.get('busy'): return checked
            self.restart(node)
            deadline = time.monotonic()+15
            while True:
                try:
                    checked = self.api(node, '/health')
                    if checked.get('resource_profile_version') == 2: return checked
                except Exception: pass
                if time.monotonic() >= deadline: raise RuntimeError('api_reload_failed')
                time.sleep(.25)
        finally:
            # A later human pause must not be undone, even if it wrote the same value.
            if before.get('accepting_new_jobs', True) and management.exists() and management.stat().st_mtime_ns == stamp:
                try:
                    checked = self.api(node, '/health')
                    if checked.get('resource_profile_version') == 2 or checked.get('active_jobs'):
                        self.api(node, '/management/resume', {}, True)
                        marker.unlink(missing_ok=True)
                except Exception: pass
            elif not before.get('accepting_new_jobs',True) or (management.exists() and management.stat().st_mtime_ns != stamp):
                marker.unlink(missing_ok=True)

    def recover_reload(self, node):
        marker = self.state/(node['name']+'.reload.json')
        record = load(marker)
        if not record: return
        management = Path(node['state_root'])/'management.json'
        if record.get('resume') and management.exists() and management.stat().st_mtime_ns == record['pause_stamp']:
            self.api(node, '/health')
            self.api(node, '/management/resume', {}, True)
        marker.unlink(missing_ok=True)

    def recover_terminal(self, node):
        root = Path(node['state_root'])/'jobs'
        candidates = {}
        for path in root.glob('*/state.json'):
            value = load(path, {})
            if value.get('status') in ('failed','cancelled','completed') and not value.get('allocation_released',False):
                candidates[path.parent.name] = path
        if not candidates: return []
        result = self.remote(node, 'recover', task_ids=list(candidates)[:16])
        for jid in result['released']:
            path = candidates.get(jid)
            if path is None: continue
            current = load(path, {})
            if current.get('status') not in ('failed','cancelled','completed'): continue
            current.update(allocation_released=True, cleanup_finished=True)
            atomic(path, current)
        return result['released']

    def publish(self, node, state, capacity=None):
        with self.mutex, file_lock(self.base/'pool-update.lock'):
            registry = load(self.registry)
            entry = next(n for n in registry['nodes'] if n['name']==node['name'])
            operator = entry.get('operator_enabled', node.get('operator_enabled', True))
            entry.update(auto_managed=True, operator_enabled=operator)
            ready = state['state'] in ('online','paused')
            entry['enabled'] = bool(operator and ready)
            if capacity:
                changed = any(entry.get(k) != v for k,v in capacity.items())
                entry.update(capacity)
                if changed:
                    for field in ('cpus','memory_gib'):
                        key = 'default_'+field+'_per_gpu'
                        if key in entry: entry[key] = max(1,capacity[field]//max(1,capacity['gpus']))
            state['state'] = state['state'] if operator else 'disabled'
            profile_path = self.base/node['directory']/'profile.json'
            profile = load(profile_path)
            profile.update(validated=bool(operator and ready), auto_managed=True,
                           last_checked_at=state['checked_at'],max_stale_seconds=self.snapshot['max_stale_seconds'])
            atomic(profile_path, profile)
            atomic(self.registry, registry)
            self.snapshot['nodes'][node['name']] = state
            self.snapshot['updated_at'] = time.time()
            atomic(self.status, self.snapshot)

    def check(self, node):
        with self.mutex: previous = dict(self.snapshot['nodes'].get(node['name'], {}))
        state = None
        try:
            observation = self.remote(node, 'probe')
            self.recover_reload(node)
            health = self.api(node, '/health')
            state = transition(previous, observation, time.time(), self.config.get('recovery_successes', 2))
            if not (observation.get('ssh') and observation.get('runtime') and observation.get('proxy')):
                self.publish(node, state); return state
            desired = service_capacity(observation['hardware'], node.get('policy', {}))
            if not observation.get('capacities') or any(c != desired for c in observation['capacities']):
                self.remote(node, 'limits', capacity=desired)
            with self.mutex, file_lock(self.base/'pool-update.lock'):
                directory = self.base/node['directory']
                old = load(directory/'capacity.json', {})
                profile = load(directory/'profile.json')
                profile.update(max_gpus=desired['gpus'], max_parallel=desired['gpus'],cpu_budget=desired['cpus'],memory_budget_gib=desired['memory_gib'])
                if old != desired:
                    for field in ('cpus','memory_gib'):
                        key = 'default_'+field+'_per_gpu'
                        if key in profile: profile[key] = max(1, desired[field]//max(1,desired['gpus']))
                atomic(directory/'capacity.json', desired); atomic(directory/'profile.json', profile)
                multi_path = self.base/'multinode/operator.private.json'
                multi = load(multi_path, {})
                if node['name'] in multi.get('nodes', {}):
                    multi['nodes'][node['name']].update(desired); atomic(multi_path,multi)
                patch_api(directory/'api.py', self.state/'api-backups')
            released = self.recover_terminal(node)
            health = self.activate_idle_api(node, self.api(node, '/health'))
            health = self.api(node, '/health')
            # Older busy APIs keep their workers. Advertise only limits they can accept.
            effective = {'gpus': min(desired['gpus'], health.get('max_gpus',0)),
                         'cpus': min(desired['cpus'], health.get('cpu_budget',0)),
                         'memory_gib': min(desired['memory_gib'], health.get('memory_budget_gib',0))}
            if effective['cpus'] < 1 or effective['memory_gib'] < 2: raise RuntimeError('api_capacity_unavailable')
            state.update(hardware=observation['hardware'], target_capacity=desired, capacity=effective,
                         capacity_pending=effective != desired, api_reload_pending=health.get('resource_profile_version') != 2,
                         recovered_tasks=released, accepting_new_jobs=bool(health.get('accepting_new_jobs')))
            if state['state']=='online' and not health.get('accepting_new_jobs'): state['state']='paused'
            self.publish(node, state, effective)
            return state
        except Exception as exc:
            error = str(exc) if str(exc) in ('ssh_unavailable','remote_probe_failed','api_reload_failed','api_reload_unavailable','api_capacity_unavailable','insufficient_instance_capacity') else 'status_check_failed'
            observation = {'ssh':False,'error':error}
            state = transition(previous, observation, time.time(), self.config.get('recovery_successes',2))
            for key in ('hardware','capacity','target_capacity'):
                if key in previous: state[key] = previous[key]
            self.publish(node, state)
            return state

    def once(self):
        with ThreadPoolExecutor(max_workers=min(8,len(self.config['nodes']))) as executor:
            futures = {executor.submit(self.check,node):node['name'] for node in self.config['nodes']}
            return {futures[f]:f.result() for f in as_completed(futures)}


def read_config(path):
    path = Path(path).expanduser().resolve(); config = load(path)
    for key in ('service_root','state_dir','api_token','management_token'):
        value = Path(config[key]).expanduser(); config[key] = str(value if value.is_absolute() else path.parent/value)
    for node in config['nodes']:
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', node['name']) or not re.fullmatch(r'[a-zA-Z0-9_-]+',node['directory']):
            raise ValueError('invalid_registered_node')
        state = Path(node['state_root']).expanduser(); node['state_root'] = str(state if state.is_absolute() else path.parent/state)
    return config


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--once',action='store_true');parser.add_argument('--install',action='store_true')
    args=parser.parse_args();os.umask(0o077);config=read_config(args.config)
    if args.install:
        from starport_common.services import definition, register
        root=Path(__file__).resolve().parents[1]
        name,unit=definition('starport-compute-sync',[sys.executable,'-m','isolation_toolkit.compute_sync','--config',str(Path(args.config).resolve())],root)
        register({name:unit});print(json.dumps({'service':'starport-compute-sync','registered':True}));return
    with file_lock(Path(config['state_dir'])/'sync.lock',blocking=False):
        worker=Reconciler(config)
        while True:
            started=time.monotonic(); result=worker.once()
            print(json.dumps({'checked_at':time.time(),'nodes':{k:{'state':v['state'],'error':v.get('error')} for k,v in result.items()}}),flush=True)
            if args.once: break
            time.sleep(max(1,config.get('interval_seconds',30)-(time.monotonic()-started)))


if __name__=='__main__': main()
