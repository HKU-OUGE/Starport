"""Controller-side observer for detached, idempotently addressed remote tasks."""
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import threading
import time
import uuid

import stream_store

MODE='durable-task-v1'
TERMINAL={'completed','failed','cancelled'}


def atomic(path,value):
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.new')
    with temp.open('x') as f:
        os.chmod(temp,0o600);json.dump(value,f);f.flush();os.fsync(f.fileno())
    temp.replace(path)


def load(path,default=None):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return default


def rpc(files,backend,p,op,cursor=0):
    script=backend.CONFIG['runtime_path']+'/durable-v2/task_control.py'
    command='/usr/bin/python3 -I -u '+shlex.quote(script)+' '+op+' '+shlex.quote(files.path)+' --cursor '+str(cursor)
    stdin,stdout,stderr=files.ssh.exec_command(command,timeout=20)
    channel=stdout.channel
    try:
        stdin.close();raw=stdout.read(768*1024+1)
        if len(raw)>768*1024:raise ValueError('control_response_limit')
        value=json.loads(raw)
        if not value.get('ok'):
            raise RuntimeError(value.get('error','control_request_failed'))
        if value['status'].get('id')!=p.name or value['status'].get('version')!=MODE:
            raise ValueError('control_identity_mismatch')
        return value
    finally:channel.close()


def prepare_source(backend,p,state,files):
    try:files.mkdir()
    except OSError:
        import stat
        if not stat.S_ISDIR(files.writer.stat(files.path).st_mode):raise
    source=p/'source.zip';manifest=p/'source-manifest.private.json'
    info=load(manifest)
    if info is None:
        staged=p/('source.'+uuid.uuid4().hex+'.zip')
        info=backend.bulk_archive.pack(p/'code',staged)
        staged.replace(source);atomic(manifest,info)
    if source.stat().st_size!=info['bytes'] or backend.bulk_archive.digest(source)!=info['sha256']:
        raise ValueError('source_integrity')
    cfg=load(p/'request.json');cfg.update(proxy_socket=backend.CONFIG['proxy_socket'],source=info)
    def cancelled():
        if state(p).get('cancel'):raise RuntimeError('task_cancelled')
    def progress(attempt,maximum,delay,done,total):
        state(p,source_upload_attempt=attempt,source_upload_max_attempts=maximum,
              source_upload_retry_seconds=delay,source_upload_bytes=done,source_upload_total_bytes=total)
    files.put_bulk(source,'source.zip',cancelled,expected=info,request=cfg,notify=progress)
    state(p,source_upload_complete=True,source_upload_retry_seconds=0)


def fetch_result(backend,p,files,info):
    size=info.get('bytes');sha=info.get('sha256')
    if type(size) is not int or not 0<=size<=backend.bulk_archive.LIMIT or not isinstance(sha,str) or len(sha)!=64:
        raise ValueError('invalid_result_manifest')
    dest=p/'result.zip';part=p/'result.zip.partial';marker=p/'result-transfer.private.json'
    if load(marker)!=info:
        if part.exists():part.unlink()
        atomic(marker,info)
    if not dest.exists() or dest.stat().st_size!=size or backend.bulk_archive.digest(dest)!=sha:
        offset=part.stat().st_size if part.exists() else 0
        if offset>size:part.unlink();offset=0
        client=files.ssh.open_sftp();client.get_channel().settimeout(30)
        try:
            remote=files.path+'/result.zip'
            if client.stat(remote).st_size!=size:raise ValueError('result_size_changed')
            with client.open(remote,'rb') as src,part.open('ab') as out:
                src.seek(offset)
                while offset<size:
                    data=src.read(min(1024*1024,size-offset))
                    if not data:raise OSError('result_transfer_interrupted')
                    out.write(data);offset+=len(data)
                out.flush();os.fsync(out.fileno())
        finally:client.close()
        if backend.bulk_archive.digest(part)!=sha:
            part.unlink();raise ValueError('result_integrity')
        part.replace(dest)
    receipt=load(p/'result-receipt.private.json')
    def verified(root,items):
        return all((root/x['path']).is_file() and not (root/x['path']).is_symlink()
                   and (root/x['path']).stat().st_size==x['bytes']
                   and backend.bulk_archive.digest(root/x['path'])==x['sha256'] for x in items)
    if receipt and receipt.get('bundle')==info and verified(p/'result',receipt['artifacts']):
        return receipt['artifacts']
    staging=p/'durable-result-staging'
    prepared=load(p/'result-prepared.private.json')
    if prepared and prepared.get('bundle')==info:
        items=prepared['artifacts']
        if verified(p/'result',items):
            atomic(p/'result-receipt.private.json',prepared);return items
        if not verified(staging,items):prepared=None
    if not prepared:
        if staging.exists():shutil.rmtree(staging)
        staging.mkdir(mode=0o700)
        items=backend.bulk_archive.extract(dest,staging,info)
        prepared={'bundle':info,'artifacts':items};atomic(p/'result-prepared.private.json',prepared)
    result=p/'result'
    if result.exists():
        if any(result.iterdir()):raise ValueError('result_recovery_conflict')
        result.rmdir()
    staging.replace(result);atomic(p/'result-receipt.private.json',prepared)
    return items


def execute(backend,p,state):
    p=Path(p)
    with backend.ACTIVE_LOCK:
        if p.name in backend.ACTIVE:return
        backend.ACTIVE.add(p.name)
    files=None;errors=0;launched=bool(load(p/'durable-launch.private.json'))
    state(p,control_mode=MODE,control_connection='connecting')
    try:
        while True:
            try:
                if files is None:
                    state(p,control_connection='reconnecting' if errors or launched else 'connecting')
                    files=backend.SFTPFiles(backend.CONFIG['jobs_path']+'/'+p.name)
                    transport=files.ssh.get_transport().sock
                    if hasattr(transport,'process'):
                        pid=transport.process.pid
                        with contextlib.suppress(OSError):atomic(p/'transport.private.json',{'pid':pid,'start_ticks':Path('/proc',str(pid),'stat').read_text().split()[21]})
                if not launched:
                    if state(p).get('cancel'):
                        released=backend.release(p.name,abort=True)
                        state(p,status='cancelled',error='task_cancelled',allocation_released=released,cleanup_finished=released)
                        if released:break
                        time.sleep(5);continue
                    state(p,status='provisioning')
                    prepare_source(backend,p,state,files)
                    atomic(p/'durable-launch.private.json',{'version':MODE,'id':p.name,'created_at':time.time()})
                    launched=True
                # Idempotent start returns the existing supervisor even when an
                # earlier SSH response or the whole local process was lost.
                rpc(files,backend,p,'start',stream_store.cursor(p))
                state(p,control_connection='connected',last_control_heartbeat_at=time.time(),
                      control_reconnect_count=errors,control_message=None)
                while True:
                    op='cancel' if state(p).get('cancel') else 'poll'
                    response=rpc(files,backend,p,op,stream_store.cursor(p))
                    remote=response['status'];logs=response['logs']
                    stream_store.mirror(p,logs)
                    phase=remote['phase'];updates={
                        'control_connection':'connected','control_message':None,'last_control_heartbeat_at':time.time(),
                        'remote_phase':phase,'remote_supervisor_heartbeat_at':remote.get('last_supervisor_heartbeat_at'),
                        'remote_allocation_released':bool(remote.get('allocation_released')),
                        'allocation_released':bool(remote.get('allocation_released')),
                        'cleanup_finished':bool(remote.get('cleanup_finished')),
                        'log_archive_capped':bool(logs.get('archive_capped'))}
                    if phase in ('running','provisioning'):updates['status']=phase
                    if remote.get('started_at'):updates['started_at']=remote['started_at']
                    state(p,**updates)
                    if phase in TERMINAL:
                        if logs['end']<logs['total']:continue
                        items=[];info=remote.get('artifact_bundle')
                        if info:
                            state(p,status='fetching',control_connection='collecting_results')
                            items=fetch_result(backend,p,files,info)
                        error=remote.get('error')
                        if phase=='failed' and not error:error='task_exit_nonzero'
                        allowed=set(backend.ERROR_MESSAGES)|{'remote_supervisor_lost','remote_supervisor_failed','remote_executor_failed'}
                        if error and error not in allowed:error='dedicated_execution_failed'
                        released=bool(remote.get('allocation_released'))
                        state(p,status=phase,exit_code=remote.get('exit_code'),error=error,
                              error_message=backend.ERROR_MESSAGES.get(error,'The remote task supervisor reported an execution failure.') if error else None,
                              artifacts=items,allocation_released=released,cleanup_finished=released,
                              finished_at=remote.get('finished_at',time.time()),control_connection='connected',control_message=None)
                        if released:return
                    if logs['end']>=logs['total']:time.sleep(2)
            except Exception as exc:
                errors+=1
                with (p/'durable-error.private.log').open('a') as out:out.write(type(exc).__name__+': '+str(exc)+'\n')
                state(p,control_connection='reconnecting',control_reconnect_count=errors,
                      control_message='Control connection interrupted; remote execution is not cancelled. Reconnecting to the same task.')
                if files:
                    with contextlib.suppress(Exception):files.close()
                    files=None
                time.sleep(min(15,2**min(errors-1,4)))
    finally:
        if files:
            with contextlib.suppress(Exception):files.close()
        with backend.ACTIVE_LOCK:backend.ACTIVE.discard(p.name)


def recover_jobs(backend,root,state):
    for p in (Path(root)/'jobs').glob('*'):
        row=load(p/'state.json',{})
        if row.get('control_mode')!=MODE:continue
        if row.get('status') in TERMINAL and row.get('allocation_released'):continue
        threading.Thread(target=execute,args=(backend,p,state),daemon=True).start()


def attached_ids(backend,records):
    with backend.ACTIVE_LOCK:active=set(backend.ACTIVE)
    return {row['id'] for row in records if row['id'] in active and row.get('control_mode')==MODE
            and ((row.get('control_connection')=='connected' and
                  0<=time.time()-row.get('last_control_heartbeat_at',0)<30) or
                 (row.get('control_connection')=='collecting_results' and row.get('remote_allocation_released')))}
