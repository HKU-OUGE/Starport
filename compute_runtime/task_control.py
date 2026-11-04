"""Detached, task-scoped supervisor and idempotent SSH control operations."""
import argparse
import base64
import contextlib
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
import stream_store

VERSION='durable-task-v1'
TERMINAL={'completed','failed','cancelled'}


def atomic(path,value):
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.new')
    with temp.open('x') as f:
        os.chmod(temp,0o600);json.dump(value,f);f.flush();os.fsync(f.fileno())
    temp.replace(path)


def read(path,default=None):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return default


def environment(job):
    runtime=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(runtime))
    import resources
    path=Path(job);resources.valid(path.name)
    if path.is_symlink() or path.parent.resolve()!=resources.JOBS.resolve() or not path.is_dir():
        raise ValueError('invalid_job')
    # Outer control state must never be redirected into workload-owned files.
    for name in ('control-status.json','control-issued.json','control-cancel.json',
                 'control-start.lock','control-supervisor.lock','logs','request.json'):
        if (path/name).is_symlink():raise ValueError('invalid_job')
    return path,runtime,resources


def held(path):
    with path.open('a+b') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return True
        return False


def release(resources,job):
    try:return resources.release(job.name,abort=not (job/'launch-intent.json').exists()) is True
    except Exception:return False


def status(job,resources):
    value=read(job/'control-status.json',{})
    if not value:
        value={'version':VERSION,'id':job.name,'phase':'starting','allocation_released':False}
        if not (job/'control-issued.json').exists() or held(job/'control-supervisor.lock'):return value
    if value['phase'] not in TERMINAL and not held(job/'control-supervisor.lock'):
        # Never restart a task whose launch may already have happened.
        value=dict(value,phase='recovering',error='remote_supervisor_lost')
        if release(resources,job):
            value.update(phase='failed',allocation_released=True,cleanup_finished=True,finished_at=time.time())
            atomic(job/'control-status.json',value)
    elif value['phase'] in TERMINAL and not value.get('allocation_released'):
        if release(resources,job):
            value.update(allocation_released=True,cleanup_finished=True)
            atomic(job/'control-status.json',value)
    return value


def start(job,runtime,resources):
    with (job/'control-start.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if (job/'control-issued.json').exists() or held(job/'control-supervisor.lock'):
            return status(job,resources)
        # Only a still-reserved, matching lease may launch the first execution.
        with resources.transaction():
            lease=read(resources.LEASES/(job.name+'.json'),{})
        if lease.get('id')!=job.name or lease.get('phase')!='reserved':
            if not lease:
                return {'version':VERSION,'id':job.name,'phase':'failed','error':'reservation_unavailable',
                        'allocation_released':True,'cleanup_finished':True,'finished_at':time.time()}
            raise ValueError('reservation_unavailable')
        with (job/'control-private.log').open('ab') as err:
            subprocess.Popen([sys.executable,'-I','-u',str(Path(__file__).resolve()),
                              'supervise',str(job)],stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,stderr=err,close_fds=True,start_new_session=True)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if (job/'control-issued.json').exists():return status(job,resources)
            time.sleep(.05)
        # Ambiguous startup is retried by identity. The supervisor lock prevents
        # a second execution even if this SSH response is lost.
        return {'version':VERSION,'id':job.name,'phase':'starting','allocation_released':False}


def supervise(job,runtime,resources):
    with (job/'control-supervisor.lock').open('a+b') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        if (job/'control-issued.json').exists():return
        issued={'version':VERSION,'id':job.name,'issued_at':time.time()}
        atomic(job/'control-issued.json',issued)
        value={**issued,'phase':'provisioning','allocation_released':False,'cleanup_finished':False}
        atomic(job/'control-status.json',value)
        # No operator connection is inherited by the compute process.
        cfg=read(job/'request.json',{})
        deadline=time.monotonic()+min(86400,max(10,int(cfg.get('timeout',86400))))+3720
        proc=None;pending=b'';finished=None;last_save=0;cancel_sent=False;next_beat=0
        stopping=threading.Event()
        signal.signal(signal.SIGTERM,lambda *_:stopping.set())
        signal.signal(signal.SIGINT,lambda *_:stopping.set())
        signal.signal(signal.SIGHUP,signal.SIG_IGN)
        try:
            with (job/'control-runner.private.log').open('ab') as err:
                proc=subprocess.Popen([sys.executable,'-I','-u',str(runtime/'runner.py'),str(job)],
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,
                        close_fds=True,start_new_session=True,bufsize=0,umask=0o022)
                os.set_blocking(proc.stdin.fileno(),False)
                os.set_blocking(proc.stdout.fileno(),False)
                with selectors.DefaultSelector() as sel:
                    sel.register(proc.stdout,selectors.EVENT_READ)
                    while True:
                        now=time.monotonic()
                        cancel=(job/'control-cancel.json').exists() or stopping.is_set() or now>deadline
                        if now>=next_beat and proc.poll() is None:
                            try:
                                proc.stdin.write(b'{"op":"cancel"}\n' if cancel else b'{"op":"heartbeat"}\n')
                                if cancel:cancel_sent=True
                            except (OSError,BrokenPipeError):pass
                            next_beat=now+(1 if cancel else 5)
                        for key,_ in sel.select(.2):
                            data=os.read(key.fd,65536)
                            if not data:sel.unregister(key.fileobj);continue
                            pending+=data
                            while b'\n' in pending:
                                line,pending=pending.split(b'\n',1)
                                if len(line)>100000:raise ValueError('frame_limit')
                                msg=json.loads(line);op=msg.get('op')
                                if op=='running':value.update(phase='running',started_at=time.time())
                                elif op=='log':
                                    try:stream_store.append(job,base64.b64decode(msg['data'],validate=True))
                                    except OSError:value['log_storage_unavailable']=True
                                elif op=='diagnostic':
                                    path=job/'control-diagnostic.private.log'
                                    if not path.exists() or path.stat().st_size<1024*1024:
                                        with path.open('ab') as out:out.write(base64.b64decode(msg['data'],validate=True))
                                elif op=='artifact_bundle':value['artifact_bundle']={k:msg[k] for k in ('bytes','sha256')}
                                elif op=='finished':finished=msg
                            if len(pending)>100000:raise ValueError('frame_limit')
                        if now-last_save>=2:
                            value.update(last_supervisor_heartbeat_at=time.time(),cancel_requested=cancel_sent)
                            atomic(job/'control-status.json',value);last_save=now
                        if proc.poll() is not None and not sel.get_map():break
                        if now>deadline+30:
                            os.killpg(proc.pid,signal.SIGTERM)
                            try:proc.wait(timeout=10)
                            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                proc.wait()
            if finished:
                code=finished.get('error')
                code={'time_limit':'task_time_limit','task_cleanup_failed':'sandbox_cleanup_failed'}.get(code,code)
                exit_code=finished['exit_code']
                phase='cancelled' if cancel_sent else 'completed' if exit_code==0 and not code else 'failed'
                value.update(phase=phase,exit_code=exit_code,error='task_cancelled' if cancel_sent else code)
            else:value.update(phase='cancelled' if cancel_sent else 'failed',exit_code=proc.returncode,error='task_cancelled' if cancel_sent else 'remote_executor_failed')
        except Exception as exc:
            value.update(phase='failed',error='remote_supervisor_failed')
            with (job/'control-private.log').open('a') as out:out.write(type(exc).__name__+': '+str(exc)+'\n')
        finally:
            if proc:
                with contextlib.suppress(Exception):proc.stdin.close()
                # Losing this *remote* supervisor still retires its own runtime.
                if proc.poll() is None:
                    try:proc.wait(timeout=45)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGTERM)
                        try:proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            with contextlib.suppress(OSError):stream_store.finish(job)
            released=release(resources,job)
            value.update(allocation_released=released,cleanup_finished=released,finished_at=time.time(),last_supervisor_heartbeat_at=time.time())
            atomic(job/'control-status.json',value)


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('op',choices=['start','poll','cancel','supervise'])
    parser.add_argument('job');parser.add_argument('--cursor',type=int,default=0)
    args=parser.parse_args();job,runtime,resources=environment(args.job)
    if args.op=='supervise':supervise(job,runtime,resources);return
    if args.cursor<0:raise ValueError('invalid_cursor')
    if args.op=='start':value=start(job,runtime,resources)
    elif args.op=='cancel':
        atomic(job/'control-cancel.json',{'id':job.name,'requested_at':time.time()});value=status(job,resources)
    else:value=status(job,resources)
    log=stream_store.chunk(job,args.cursor)
    print(json.dumps({'ok':True,'status':value,'logs':log},separators=(',',':')))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc) if str(exc) in ('reservation_unavailable','invalid_job') else 'control_request_failed'}))
        sys.exit(1)
