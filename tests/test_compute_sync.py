import ast
import contextlib
import json
import os
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import patch
from isolation_toolkit.compute_availability import resolve_nodes, service_capacity, transition
from isolation_toolkit.compute_sync import Reconciler, atomic, load, patch_api


API_SOURCE = '''import json,time
def profile():return json.loads((BASE/'profile.json').read_text())
def example(d,g):
    h={'ready':profile()['validated'],'max_parallel':3,'cpu_task_limit':None,'cpu_budget':41,'memory_budget_gib':889}
    cpu=41*max(1,g)//3 if d.get('cpus') is None else d['cpus'];mem=889*max(1,g)//3 if d.get('memory_gib') is None else d['memory_gib']
    if type(cpu)!=int or not 1<=cpu<=41 or type(mem)!=int or not 2<=mem<=889:raise ValueError('invalid_resource_request')
    return h,cpu,mem
'''


class Fixture:
    def __init__(self, root):
        self.root=Path(root); self.dir=self.root/'worker'; self.dir.mkdir()
        self.state=self.root/'jobs-state'; self.state.mkdir()
        self.online=True; self.proxy=True; self.busy=False; self.version=1
        self.hardware={'gpus':4,'cpus':128,'memory_gib':624}; self.restarts=0; self.operations=[]
        self.human_pause_during_reload=False
        self.capacity={'gpus':3,'cpus':41,'memory_gib':889}
        atomic(self.root/'dsw-pool.json',{'nodes':[{'name':'compute-a','enabled':True,'port':19001,**self.capacity}]})
        atomic(self.dir/'profile.json',{'validated':True,'max_gpus':3,'max_parallel':3,'cpu_budget':41,'memory_budget_gib':889})
        atomic(self.dir/'capacity.json',self.capacity)
        (self.dir/'api.py').write_text(API_SOURCE)
        atomic(self.state/'management.json',{'accepting_new_jobs':True})
        self.node={'name':'compute-a','directory':'worker','port':19001,'state_root':str(self.state)}
        self.config={'service_root':str(self.root),'state_dir':str(self.root/'sync'),'nodes':[self.node], 'max_stale_seconds':120}
    def remote(self,node,op,**kwargs):
        self.operations.append(op)
        if not self.online: raise RuntimeError('ssh_unavailable')
        if op=='probe':return {'ssh':True,'runtime':True,'proxy':self.proxy,'hardware':self.hardware,'reservations':[],'error':None if self.proxy else 'proxy_unavailable'}
        if op=='limits':self.capacity=kwargs['capacity'];return {'capacity':self.capacity}
        if op=='recover':raise AssertionError('No terminal task exists in this fixture')
        raise AssertionError(op)
    def api(self,node,path,data=None,managed=False):
        if path=='/health':
            p=load(self.dir/'profile.json')
            return {'ready':p['validated'],'active_jobs':int(self.busy),'busy':self.busy,'accepting_new_jobs':load(self.state/'management.json')['accepting_new_jobs'],
                    'resource_profile_version':self.version,'max_gpus':p['max_gpus'],
                    'cpu_budget':p['cpu_budget'] if self.version==2 else 41,'memory_budget_gib':p['memory_budget_gib'] if self.version==2 else 889}
        if path.startswith('/management/'):
            value={'accepting_new_jobs':path.endswith('resume')};atomic(self.state/'management.json',value);return value
        raise AssertionError(path)
    def restart(self,node):
        assert not self.busy
        self.restarts+=1;self.version=2
        if self.human_pause_during_reload:
            time.sleep(.002);atomic(self.state/'management.json',{'accepting_new_jobs':False})
    def worker(self):return Reconciler(self.config,self.remote,self.api,self.restart)


class ComputeSyncTests(unittest.TestCase):
    def test_real_reconnect_and_resize_sequence(self):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);w=f.worker()
            self.assertEqual(w.check(f.node)['state'],'online')
            self.assertTrue(load(f.root/'dsw-pool.json')['nodes'][0]['enabled'])
            # State persists across the background service's own restart.
            w=f.worker();self.assertEqual(w.check(f.node)['state'],'online')
            self.assertEqual(f.capacity,{'gpus':4,'cpus':126,'memory_gib':616})
            self.assertEqual(f.restarts,1)
            f.busy=True;f.hardware['memory_gib']=256
            self.assertEqual(w.check(f.node)['capacity']['memory_gib'],248)
            self.assertEqual(f.restarts,1)
            f.online=False;self.assertEqual(w.check(f.node)['state'],'reconnecting')
            self.assertFalse(load(f.root/'dsw-pool.json')['nodes'][0]['enabled'])
            self.assertEqual(w.check(f.node)['state'],'offline')
            f.online=True;self.assertEqual(w.check(f.node)['state'],'reconnecting')
            self.assertEqual(w.check(f.node)['state'],'online')
            self.assertFalse(any(op in f.operations for op in ['cancel','stop','kill']))
    def test_busy_old_api_waits_for_idle_and_clamps_advertised_cpu(self):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.busy=True;w=f.worker();w.check(f.node);s=w.check(f.node)
            self.assertEqual(s['target_capacity']['cpus'],126)
            self.assertEqual(s['capacity']['cpus'],41);self.assertTrue(s['capacity_pending']);self.assertEqual(f.restarts,0)
            f.busy=False;s=w.check(f.node);self.assertEqual(s['capacity']['cpus'],126);self.assertEqual(f.restarts,1)
    def test_manual_pause_survives_reconnect_and_api_reload(self):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);atomic(f.state/'management.json',{'accepting_new_jobs':False})
            w=f.worker();w.check(f.node);self.assertEqual(w.check(f.node)['state'],'paused')
            self.assertFalse(load(f.state/'management.json')['accepting_new_jobs'])
    def test_later_human_pause_is_not_resumed(self):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.human_pause_during_reload=True;w=f.worker();w.check(f.node)
            self.assertFalse(load(f.state/'management.json')['accepting_new_jobs'])
    def test_interrupted_maintenance_resumes_only_its_own_pause(self):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);w=f.worker();f.version=2
            atomic(f.state/'management.json',{'accepting_new_jobs':False})
            marker=w.state/'compute-a.reload.json'
            atomic(marker,{'resume':True,'pause_stamp':(f.state/'management.json').stat().st_mtime_ns})
            w.recover_reload(f.node)
            self.assertTrue(load(f.state/'management.json')['accepting_new_jobs'])
            self.assertFalse(marker.exists())
    def test_operator_disabled_node_stays_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);r=load(f.root/'dsw-pool.json');r['nodes'][0]['operator_enabled']=False;atomic(f.root/'dsw-pool.json',r)
            w=f.worker();w.check(f.node);self.assertEqual(w.check(f.node)['state'],'disabled')
            self.assertFalse(load(f.root/'dsw-pool.json')['nodes'][0]['enabled'])
    def test_proxy_failure_closes_admission(self):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);w=f.worker();w.check(f.node);w.check(f.node);f.proxy=False
            self.assertEqual(w.check(f.node)['state'],'degraded');self.assertFalse(load(f.root/'dsw-pool.json')['nodes'][0]['enabled'])
    def test_stale_snapshot_never_claims_online(self):
        registry={'nodes':[{'name':'compute-a','enabled':True,'auto_managed':True,'operator_enabled':True}]}
        snap={'max_stale_seconds':120,'nodes':{'compute-a':{'state':'online','checked_at':100}}}
        self.assertTrue(resolve_nodes(registry,snap,200)[0]['enabled'])
        self.assertFalse(resolve_nodes(registry,snap,221)[0]['enabled'])
        self.assertFalse(resolve_nodes(registry,{},200)[0]['enabled'])
        self.assertFalse(resolve_nodes(registry,[],200)[0]['enabled'])
        self.assertFalse(resolve_nodes(registry,snap,90)[0]['enabled'])
    def test_existing_service_budget_cap_is_preserved(self):
        self.assertEqual(service_capacity({'gpus':8,'cpus':250,'memory_gib':1250},{'max_cpus':196,'max_memory_gib':1024}),{'gpus':8,'cpus':196,'memory_gib':1024})
    def test_malformed_node_state_closes_admission_without_hiding_registry(self):
        registry={'nodes':[{'name':'compute-a','enabled':True,'auto_managed':True}]}
        for row in (None, [], 'invalid', {'state':'online','checked_at':'100'},
                    {'state':'online','checked_at':float('nan')}, {'state':[],'checked_at':100}):
            with self.subTest(row=row):
                nodes=resolve_nodes(registry,{'nodes':{'compute-a':row}},110)
                self.assertEqual(len(nodes),1)
                self.assertFalse(nodes[0]['enabled'])
                self.assertEqual(nodes[0]['connection_state'],'stale')
        self.assertFalse(resolve_nodes(registry,{'nodes':{},'max_stale_seconds':'120'},110)[0]['enabled'])
    def test_api_patch_validates_current_profile_not_hardcoded_numbers(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'api.py';p.write_text(API_SOURCE);atomic(Path(root)/'profile.json',{'validated':True,'max_gpus':4,'max_parallel':4,'cpu_budget':126,'memory_budget_gib':616})
            self.assertTrue(patch_api(p,Path(root)/'backups'));self.assertFalse(patch_api(p,Path(root)/'backups'))
            ns={'BASE':Path(root)};exec(compile(p.read_text(),str(p),'exec'),ns)
            self.assertEqual(ns['example']({'cpus':100,'memory_gib':500},4)[1:],(100,500))
            with self.assertRaises(ValueError):ns['example']({'cpus':127,'memory_gib':500},4)
            cfg=load(Path(root)/'profile.json');cfg.update(auto_managed=True,last_checked_at=time.time()-121,max_stale_seconds=120);atomic(Path(root)/'profile.json',cfg)
            self.assertFalse(ns['profile']()['validated'])


@unittest.skipUnless(os.name == 'posix', 'Linux executor uses POSIX resource locks')
class RecoveryBoundaryTests(unittest.TestCase):
    def fixture(self, root, live=False, locked=False, runtime_exists=False, wrong_id=False):
        from isolation_toolkit import compute_remote
        root=Path(root);runtime=root/'runtime';runtime.mkdir();leases=root/'dsw-leases';leases.mkdir()
        jid='a'*32;job=root/'dsw-jobs'/jid;job.mkdir(parents=True)
        base=root/'scratch'
        if runtime_exists:base.mkdir()
        atomic(leases/(jid+'.json'),{'id':jid,'phase':'running','gpu_indices':[],'cpu_indices':[1]})
        atomic(job/'cleanup-context.private.json',{'version':'test','job_id':'b'*32 if wrong_id else jid,'base':str(base)})
        (job/'result.txt').write_text('preserve')
        @contextlib.contextmanager
        def allocation_locks(*_):
            if locked:raise BlockingIOError()
            yield
        def binding(*_):raise ValueError()
        r=types.SimpleNamespace(JOBS=root/'dsw-jobs',LEASES=leases,valid=lambda s:None,
            transaction=contextlib.nullcontext,release=lambda *args,**kw:(job/'cleanup.json').exists())
        c=types.SimpleNamespace(VERSION='test',valid_job=lambda p:p,load=load,save=atomic,
            allocation_locks=allocation_locks,base_location=lambda p:Path(p),binding=binding,
            associated_processes=lambda *args:[123] if live else [])
        with patch.dict('sys.modules',{'resources':r,'cleanup_recovery':c}):
            result=compute_remote.recover({'runtimes':[str(runtime)]},[jid])
        self.assertEqual((job/'result.txt').read_text(),'preserve')
        if runtime_exists:self.assertTrue(base.exists())
        return result
    def test_absent_runtime_free_locks_can_release(self):
        with tempfile.TemporaryDirectory() as root:self.assertEqual(len(self.fixture(root)['released']),1)
    def test_live_process_preserves_reservation(self):
        with tempfile.TemporaryDirectory() as root:self.assertEqual(self.fixture(root,live=True)['released'],[])
    def test_locked_resource_preserves_reservation(self):
        with tempfile.TemporaryDirectory() as root:self.assertEqual(self.fixture(root,locked=True)['released'],[])
    def test_existing_runtime_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as root:self.assertEqual(self.fixture(root,runtime_exists=True)['released'],[])
    def test_mismatched_binding_cannot_release(self):
        with tempfile.TemporaryDirectory() as root:self.assertEqual(self.fixture(root,wrong_id=True)['released'],[])


if __name__=='__main__':unittest.main()
