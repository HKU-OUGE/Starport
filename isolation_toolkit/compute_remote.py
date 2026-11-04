"""Trusted SSH probe. Only explicit capacities and task-scoped cleanup may change."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time
import uuid


def atomic(path, value):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.new')
    temp.write_text(json.dumps(value)); temp.chmod(0o600); temp.replace(path)


def proxy_https(config, budget_seconds=12):
    """Bounded HTTPS retries through the existing proxy, with no direct fallback."""
    deadline = time.monotonic() + budget_seconds
    context = ssl.create_default_context()

    def remaining():
        value = min(4, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('proxy_probe_deadline')
        return value

    targets = [('www.gstatic.com', '/generate_204'), ('example.com', '/'),
               ('www.gstatic.com', '/generate_204')]
    for host, path in targets:
        if time.monotonic() >= deadline:
            break
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(remaining()); conn.connect(config['proxy_socket'])
                conn.sendall(f'CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n'.encode())
                header = b''
                while not header.endswith(b'\r\n\r\n') and len(header) < 8192:
                    conn.settimeout(remaining()); chunk = conn.recv(1)
                    if not chunk:
                        raise RuntimeError('proxy_closed')
                    header += chunk
                if not header.endswith(b'\r\n\r\n') or b' 200 ' not in header.split(b'\r\n', 1)[0]:
                    raise RuntimeError('proxy_rejected')
                conn.settimeout(remaining())
                with context.wrap_socket(conn, server_hostname=host) as secure:
                    secure.settimeout(remaining())
                    secure.sendall(f'HEAD {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode())
                    secure.settimeout(remaining())
                    if not secure.recv(1024).startswith(b'HTTP/'):
                        raise RuntimeError('https_failed')
                return True
        except (OSError, ValueError, RuntimeError):
            pass
    return False


def probe(config):
    cg = Path('/sys/fs/cgroup')
    cpu = len(os.sched_getaffinity(0))
    if (cg/'cpu.max').exists():
        quota, period = (cg/'cpu.max').read_text().split()
        if quota != 'max': cpu = min(cpu, int(quota)//int(period))
    elif (cg/'cpu/cpu.cfs_quota_us').exists():
        quota = int((cg/'cpu/cpu.cfs_quota_us').read_text())
        if quota > 0: cpu = min(cpu, quota//int((cg/'cpu/cpu.cfs_period_us').read_text()))
    meminfo = {line.split(':')[0]: int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:', 'MemAvailable:'))}
    memory = meminfo['MemTotal']
    for p in [cg/'memory.max', cg/'memory/memory.limit_in_bytes']:
        if p.exists():
            value = p.read_text().strip()
            if value.isdigit() and 0 < int(value) < 2**60: memory = int(value)
            break
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=index', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=8)
        if r.returncode: raise RuntimeError('gpu_probe_failed')
        gpus = len([line for line in r.stdout.splitlines() if line.strip().isdigit()])
    except (FileNotFoundError, PermissionError):
        gpus = 0
    paths = [Path(p) for p in config['runtimes']]
    runtime = all((p/'resources.py').is_file() and (p/'runner_impl.py').is_file() for p in paths)
    leases = paths[0].parent/'dsw-leases'
    reserved = [json.loads(p.read_text()) for p in leases.glob('*.json')]
    result = {'ssh': True, 'runtime': runtime, 'proxy': False,
              'hardware': {'gpus': gpus, 'cpus': cpu, 'memory_gib': memory//2**30},
              'capacities': [json.loads((p/'capacity.json').read_text()) if (p/'capacity.json').exists() else None for p in paths],
              'reservations': [{k: d.get(k) for k in ('id', 'phase', 'gpus', 'cpus', 'memory_gib')} for d in reserved]}
    if not runtime: result['error'] = 'runtime_unavailable'
    result['proxy'] = proxy_https(config)
    if not result['proxy']: result['error'] = 'proxy_unavailable'
    return result


def limits(config, capacity):
    if set(capacity) != {'gpus', 'cpus', 'memory_gib'} or any(type(v) is not int or v < 0 for v in capacity.values()):
        raise ValueError('invalid_capacity')
    changed = []
    for location in config['runtimes']:
        runtime = Path(location); leases = runtime.parent/'dsw-leases'; leases.mkdir(exist_ok=True)
        with (leases/'allocation.lock').open('a') as lock:
            deadline = time.monotonic()+5
            while True:
                try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
                except BlockingIOError:
                    if time.monotonic() > deadline: raise TimeoutError('allocation_busy')
                    time.sleep(.1)
            path = runtime/'capacity.json'
            before = json.loads(path.read_text()) if path.exists() else None
            if before != capacity:
                if before is not None: atomic(runtime/'capacity.before-auto-sync.json', before)
                atomic(path, capacity); changed.append(runtime.name)
    return {'updated': len(changed), 'capacity': capacity}


def inspect_tasks(config, task_ids):
    """Prove a stale controller record ended without touching a live runtime."""
    runtime = Path(config['runtimes'][0]); sys.path.insert(0, str(runtime))
    import resources
    import cleanup_recovery as cleanup
    ended = []
    for jid in task_ids[:16]:
        try:
            resources.valid(jid)
            job = cleanup.valid_job(resources.JOBS/jid)
            ctx = cleanup.load(job/'cleanup-context.private.json')
            receipt = cleanup.load(job/'cleanup.json')
            lifecycle = cleanup.load(job/'lifecycle-private.json')
            if not all(isinstance(value, dict) for value in (ctx, receipt, lifecycle)): continue
            if (ctx.get('version') != cleanup.VERSION or ctx.get('job_id') != jid or
                    receipt.get('version') != cleanup.VERSION or receipt.get('job_id') != jid or
                    not receipt.get('cleaned') or not receipt.get('runtime_registry_empty') or
                    type(lifecycle.get('returncode')) is not int):
                continue
            base = cleanup.base_location(ctx['base'])
            if receipt.get('base') != str(base): continue
            with resources.transaction():
                lease_path = resources.LEASES/(jid+'.json')
                allocation = cleanup.load(lease_path) if lease_path.exists() else None
            if allocation and allocation.get('id') != jid: continue
            with cleanup.allocation_locks(resources.LEASES, allocation) if allocation else contextlib.nullcontext():
                if base.exists() or base.is_symlink() or cleanup.associated_processes(job, base): continue
                alternative = False
                for other in Path('/tmp').glob('isolated-sandbox-*'):
                    try: cleanup.binding(job, other); alternative = True; break
                    except (OSError, ValueError, KeyError): pass
                if alternative: continue
                reason = lifecycle.get('stop_reason')
                if reason not in ('control_heartbeat_timeout', 'training_timeout', 'cancelled'):
                    reason = 'remote_task_ended'
                ended.append({'id': jid, 'verified_ended': True,
                              'exit_code': lifecycle['returncode'], 'reason': reason,
                              'finished_at': receipt.get('checked_at'),
                              'result_bundle_present': (job/'result.zip').exists()})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {'ended': ended}


def recover(config, task_ids):
    runtime = Path(config['runtimes'][0]); sys.path.insert(0, str(runtime))
    import resources
    import cleanup_recovery as cleanup
    recovered = []
    for jid in task_ids[:16]:
        resources.valid(jid); job = resources.JOBS/jid
        if not (resources.LEASES/(jid+'.json')).exists(): recovered.append(jid); continue
        if resources.release(jid, abort=True): recovered.append(jid); continue
        # A restart can remove /tmp before the supervisor writes its final receipt.
        # Never infer release from SSH EOF: require a bound, absent or stopped
        # runtime, no associated processes, and free allocation locks.
        try:
            job = cleanup.valid_job(job)
            with (resources.LEASES/(jid+'.cleanup.lock')).open('a') as serial:
                fcntl.flock(serial, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with resources.transaction():
                    allocation = cleanup.load(resources.LEASES/(jid+'.json'))
                if allocation['phase'] != 'running': continue
                # Keep the task's allocation locks, not the global admission lock,
                # while checking or retiring its stopped runtime.
                with cleanup.allocation_locks(resources.LEASES, allocation):
                    ctx = cleanup.load(job/'cleanup-context.private.json')
                    if ctx.get('version') != cleanup.VERSION or ctx.get('job_id') != jid: continue
                    base = cleanup.base_location(ctx['base'])
                    if base.is_symlink() or cleanup.associated_processes(job, base): continue
                    if base.exists():
                        cleanup.binding(job, base)
                        import hashlib
                        if hashlib.sha256((base/'config.json').read_bytes()).hexdigest() != ctx.get('config_sha256'): continue
                        rows = cleanup.listing(base)
                        if not cleanup.stopped(rows): continue
                        ok, history = cleanup.remove_runtime(base, stopped_only=True, attempts=1)
                        if not ok or cleanup.associated_processes(job, base): continue
                        cleanup.receipt(job, base, history, recovered=True)
                    else:
                        alternative = False
                        for other in Path('/tmp').glob('isolated-sandbox-*'):
                            try: cleanup.binding(job, other); alternative = True; break
                            except (OSError, ValueError, KeyError): pass
                        if alternative: continue
                        cleanup.save(job/'cleanup.json', {'cleaned': True, 'version': cleanup.VERSION, 'job_id': jid,
                            'base': str(base), 'runtime_directory_absent': True, 'runtime_registry_empty': True,
                            'verified_no_task_controller': True, 'verified_allocation_locks_free': True,
                            'checked_at': time.time(), 'automatic_reconciliation': True})
            if resources.release(jid): recovered.append(jid)
        except (OSError, ValueError, KeyError): continue
    return {'released': recovered}


if __name__ == '__main__':
    os.umask(0o077)
    try:
        request = json.load(sys.stdin); config = request['config']
        op = request['op']
        value = probe(config) if op == 'probe' else limits(config, request['capacity']) if op == 'limits' else recover(config, request['task_ids']) if op == 'recover' else inspect_tasks(config, request['task_ids']) if op == 'inspect_tasks' else None
        if value is None: raise ValueError('unsupported_operation')
        print(json.dumps({'ok': True, 'result': value}))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': 'remote_probe_failed', 'error_type': type(exc).__name__}))
