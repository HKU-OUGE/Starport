import base64
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import types
import zipfile
import threading
from unittest.mock import patch

RUNTIME=Path(__file__).resolve().parents[1]/'compute_runtime'
sys.path.insert(0,str(RUNTIME))
import stream_store
from isolation_toolkit import admission_recovery

RESOURCES='''import json,re,fcntl,contextlib
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
JOBS=BASE/'jobs';LEASES=BASE/'leases'
def valid(j):
 if not re.fullmatch('[a-f0-9]{32}',j):raise ValueError('invalid_job')
@contextlib.contextmanager
def transaction():
 with (LEASES/'lock').open('a') as f:
  fcntl.flock(f,fcntl.LOCK_EX);yield
def release(j,abort=False):
 with transaction():
  p=LEASES/(j+'.json')
  if not p.exists():return True
  if (JOBS/j/'cleanup.json').exists():p.unlink();return True
  return False
'''

RUNNER='''import sys,json,time,threading,base64,zipfile,hashlib,fcntl
from pathlib import Path
j=Path(sys.argv[1]);cfg=json.loads((j/'request.json').read_text())
(j/'runtime-directory').mkdir()
assert (j/'runtime-directory').stat().st_mode&0o777==0o755
with (j/'launch-count').open('a') as f:f.write('launch\\n')
(j/'launch-intent.json').write_text('{}')
last=[time.monotonic()];cancel=threading.Event()
def control():
 for line in sys.stdin:
  m=json.loads(line);last[0]=time.monotonic()
  if m['op']=='cancel':cancel.set()
threading.Thread(target=control,daemon=True).start()
def emit(m):print(json.dumps(m),flush=True)
emit({'op':'running'});start=time.monotonic();i=0
while time.monotonic()-start<cfg.get('seconds',2):
 if cancel.is_set():break
 if time.monotonic()-last[0]>7:raise SystemExit(77)
 emit({'op':'log','data':base64.b64encode(('tick-%d\\n'%i).encode()).decode()});i+=1;time.sleep(.1)
out=j/'result.zip'
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as z:z.writestr('result.json',json.dumps({'ticks':i}))
emit({'op':'artifact_bundle','bytes':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest()})
emit({'op':'finished','exit_code':0 if not cancel.is_set() else 125,'error':None if not cancel.is_set() else 'task_cancelled'})
(j/'cleanup.json').write_text('{"cleaned":true}')
'''


class RemoteControlTests(unittest.TestCase):
    def fixture(self,root,seconds=2):
        root=Path(root);runtime=root/'runtime';release=runtime/'durable-v1';release.mkdir(parents=True)
        for name in ('task_control.py','stream_store.py'):shutil.copyfile(RUNTIME/name,release/name)
        (runtime/'resources.py').write_text(RESOURCES);(runtime/'runner.py').write_text(RUNNER)
        leases=root/'leases';leases.mkdir();job=root/'jobs'/('a'*32);job.mkdir(parents=True)
        (leases/(job.name+'.json')).write_text(json.dumps({'id':job.name,'phase':'reserved'}))
        (job/'request.json').write_text(json.dumps({'timeout':60,'seconds':seconds}))
        return job,release/'task_control.py'

    def command(self,script,job,op='poll',cursor=0):
        p=subprocess.run([sys.executable,'-I',str(script),op,str(job),'--cursor',str(cursor)],capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr+p.stdout)
        return json.loads(p.stdout)

    def wait(self,script,job):
        end=time.monotonic()+15
        while time.monotonic()<end:
            v=self.command(script,job)
            if v['status']['phase'] in ('completed','failed','cancelled') and v['status'].get('allocation_released'):return v
            time.sleep(.1)
        self.fail('Task did not finish and release')

    def test_disconnected_starter_and_repeated_start_do_not_duplicate_training(self):
        with tempfile.TemporaryDirectory() as root:
            job,script=self.fixture(root,2)
            self.command(script,job,'start')
            with ThreadPoolExecutor(max_workers=5) as ex:
                results=list(ex.map(lambda _:self.command(script,job,'start'),range(10)))
            v=self.wait(script,job)
            self.assertEqual(v['status']['phase'],'completed')
            self.assertEqual((job/'launch-count').read_text(),'launch\n')
            self.assertIn(b'tick-',base64.b64decode(v['logs']['data']))
            self.assertTrue((job/'result.zip').exists())
            self.command(script,job,'start')
            self.assertEqual((job/'launch-count').read_text(),'launch\n')

    def test_remote_cancel_survives_control_request_exit(self):
        with tempfile.TemporaryDirectory() as root:
            job,script=self.fixture(root,30)
            self.command(script,job,'start');self.command(script,job,'cancel')
            v=self.wait(script,job)
            self.assertEqual(v['status']['phase'],'cancelled')
            self.assertTrue(v['status']['allocation_released'])

    def test_issued_task_is_not_restarted_after_supervisor_loss(self):
        with tempfile.TemporaryDirectory() as root:
            job,script=self.fixture(root)
            (job/'control-issued.json').write_text('{}');(job/'cleanup.json').write_text('{}')
            v=self.command(script,job,'start')
            self.assertEqual(v['status']['error'],'remote_supervisor_lost')
            self.assertFalse((job/'launch-count').exists())

    def test_job_outside_registered_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            job,script=self.fixture(root);other=Path(root)/('b'*32);other.mkdir()
            p=subprocess.run([sys.executable,'-I',str(script),'start',str(other)],capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0);self.assertIn('invalid_job',p.stdout)


class ReplicaTests(unittest.TestCase):
    def test_replayed_or_overlapping_chunk_is_exactly_once(self):
        with tempfile.TemporaryDirectory() as root:
            value={'start':0,'end':6,'data':base64.b64encode(b'hello\n').decode()}
            stream_store.mirror(root,value);stream_store.mirror(root,value)
            stream_store.mirror(root,{'start':3,'end':9,'data':base64.b64encode(b'lo\nbye').decode()})
            self.assertEqual(stream_store.read(root)['text'],'hello\nbye')
            self.assertEqual(stream_store.cursor(root),9)

    def test_retention_gap_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            stream_store.mirror(root,{'start':100,'end':104,'data':base64.b64encode(b'tail').decode()})
            v=stream_store.read(root,0)
            self.assertTrue(v['truncated']);self.assertEqual(v['text'],'tail')

    def test_attached_live_task_can_restore_only_owned_startup_pause(self):
        with tempfile.TemporaryDirectory() as root:
            job={'id':'a'*32,'status':'running','allocation_released':False}
            admission_recovery.guard_startup(root,[job],{'completed','failed','cancelled'})
            marker=admission_recovery.read(root)['recovery_id']
            self.assertTrue(admission_recovery.resume_startup(root,[job],{'completed','failed','cancelled'},True,marker,attached={job['id']}))
            admission_recovery.set_admission(root,False)
            self.assertFalse(admission_recovery.resume_startup(root,[job],{'completed','failed','cancelled'},True,marker,attached={job['id']}))


class ResultResumeTests(unittest.TestCase):
    def test_partial_result_resumes_and_completed_result_is_reused(self):
        import durable_backend
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);remote=root/'remote';remote.mkdir();local=root/'local';local.mkdir();(local/'result').mkdir()
            payload=os.urandom(2*1024*1024)
            with zipfile.ZipFile(remote/'result.zip','w') as z:z.writestr('weights.bin',payload)
            size=(remote/'result.zip').stat().st_size
            info={'bytes':size,'sha256':hashlib.sha256((remote/'result.zip').read_bytes()).hexdigest()}
            fail=[True];offsets=[]
            class Reader:
                def __init__(self,p):self.f=p.open('rb');self.count=0
                def __enter__(self):return self
                def __exit__(self,*args):self.f.close()
                def seek(self,n):offsets.append(n);return self.f.seek(n)
                def read(self,n):
                    if fail[0] and self.count:fail[0]=False;raise OSError('connection reset')
                    self.count+=1;return self.f.read(n)
            class SFTP:
                def get_channel(self):return types.SimpleNamespace(settimeout=lambda _:None)
                def stat(self,p):return (remote/Path(p).name).stat()
                def open(self,p,mode):return Reader(remote/Path(p).name)
                def close(self):pass
            def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
            def extract(src,dest,expected):
                assert digest(src)==expected['sha256']
                with zipfile.ZipFile(src) as z:
                    data=z.read('weights.bin');(dest/'weights.bin').write_bytes(data)
                    return [{'path':'weights.bin','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}]
            backend=types.SimpleNamespace(bulk_archive=types.SimpleNamespace(LIMIT=514*1024*1024,digest=digest,extract=extract))
            files=types.SimpleNamespace(path=str(remote),ssh=types.SimpleNamespace(open_sftp=lambda:SFTP()))
            with self.assertRaises(OSError):durable_backend.fetch_result(backend,local,files,info)
            partial=(local/'result.zip.partial').stat().st_size
            self.assertGreater(partial,0);self.assertLess(partial,size)
            result=durable_backend.fetch_result(backend,local,files,info)
            self.assertEqual(offsets,[0,partial]);self.assertEqual((local/'result/weights.bin').read_bytes(),payload)
            self.assertEqual(durable_backend.fetch_result(backend,local,files,info),result)
            self.assertEqual(offsets,[0,partial])


class ReconnectStatusTests(unittest.TestCase):
    def test_successful_reattachment_clears_old_warning_and_keeps_task_identity(self):
        import durable_backend
        with tempfile.TemporaryDirectory() as folder:
            job=Path(folder)/('a'*32);job.mkdir()
            (job/'durable-launch.private.json').write_text('{"version":"durable-task-v1"}')
            current={'id':job.name,'status':'running','started_at':123,
                     'control_connection':'reconnecting','control_message':'Previous interruption'}
            updates=[];operations=[];connections=[]
            def state(path,**change):
                self.assertEqual(path,job)
                current.update(change)
                if change:updates.append(dict(current))
                return dict(current)
            def connect(path):
                files=types.SimpleNamespace(ssh=types.SimpleNamespace(
                    get_transport=lambda:types.SimpleNamespace(sock=object())),close=lambda:None)
                connections.append(files);return files
            def rpc(files,backend,path,operation,cursor):
                operations.append(operation)
                if len(operations)==1:raise OSError('temporary transport loss')
                phase='running' if operation=='start' or len(operations)==3 else 'completed'
                return {'status':{'id':job.name,'version':durable_backend.MODE,
                    'phase':phase,'started_at':123,'last_supervisor_heartbeat_at':456,
                    'allocation_released':phase=='completed','cleanup_finished':phase=='completed',
                    'exit_code':0},'logs':{'start':0,'end':0,'total':0,'data':''}}
            backend=types.SimpleNamespace(ACTIVE=set(),ACTIVE_LOCK=threading.Lock(),
                CONFIG={'jobs_path':'/test/tasks'},SFTPFiles=connect,ERROR_MESSAGES={})
            with patch.object(durable_backend,'rpc',side_effect=rpc),patch.object(durable_backend.time,'sleep'):
                durable_backend.execute(backend,job,state)
            self.assertEqual(operations,['start','start','poll','poll'])
            self.assertEqual(len(connections),2)
            self.assertTrue(any(x.get('control_message') and x['control_connection']=='reconnecting' for x in updates))
            connected=[x for x in updates if x['control_connection']=='connected']
            self.assertTrue(connected)
            self.assertTrue(all(x.get('control_message') is None for x in connected))
            self.assertTrue(all(x['id']==job.name and x['started_at']==123 for x in updates))
            self.assertEqual(current['control_reconnect_count'],1)
            self.assertEqual(current['status'],'completed')
            self.assertFalse(backend.ACTIVE)


if __name__=='__main__':unittest.main()
