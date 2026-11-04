import json
import contextlib
from pathlib import Path
import tempfile
import time
import unittest
import types
from unittest.mock import patch
from isolation_toolkit import admission_recovery as admission
from isolation_toolkit.compute_sync import atomic, load
from test_compute_sync import Fixture

TERMINAL = {'completed', 'failed', 'cancelled'}
OLD_JOB = {'id': 'a'*32, 'status': 'running', 'created_at': 1, 'last_heartbeat_at': 2}


class StartupPauseTests(unittest.TestCase):
    def test_owned_pause_resumes_only_after_all_records_settle(self):
        with tempfile.TemporaryDirectory() as root:
            admission.guard_startup(root, [OLD_JOB], TERMINAL)
            record = admission.read(root)
            self.assertFalse(record['accepting_new_jobs'])
            self.assertFalse(admission.resume_startup(root, [OLD_JOB], TERMINAL, False, record['recovery_id']))
            settled = dict(OLD_JOB, status='failed', allocation_released=True)
            self.assertFalse(admission.resume_startup(root, [settled], TERMINAL, True, record['recovery_id']))
            self.assertTrue(admission.resume_startup(root, [settled], TERMINAL, False, record['recovery_id']))
            self.assertTrue(admission.read(root)['accepting_new_jobs'])

    def test_repeated_start_preserves_owner_but_manual_pause_revokes_it(self):
        with tempfile.TemporaryDirectory() as root:
            admission.guard_startup(root, [OLD_JOB], TERMINAL)
            record = admission.read(root)
            admission.guard_startup(root, [OLD_JOB], TERMINAL)
            self.assertEqual(admission.read(root), record)
            admission.set_admission(root, False)
            self.assertFalse(admission.resume_startup(root, [], TERMINAL, False, record['recovery_id']))
            admission.guard_startup(root, [OLD_JOB], TERMINAL)
            self.assertEqual(admission.read(root), {'accepting_new_jobs': False})

    def test_old_owner_cannot_resume_new_pause(self):
        with tempfile.TemporaryDirectory() as root:
            admission.guard_startup(root, [OLD_JOB], TERMINAL)
            old = admission.read(root)['recovery_id']
            admission.set_admission(root, True)
            admission.guard_startup(root, [OLD_JOB], TERMINAL)
            self.assertFalse(admission.resume_startup(root, [], TERMINAL, False, old))

    def test_no_unsettled_records_does_not_pause(self):
        with tempfile.TemporaryDirectory() as root:
            admission.guard_startup(root, [dict(OLD_JOB, status='completed', allocation_released=True)], TERMINAL)
            self.assertTrue(admission.read(root)['accepting_new_jobs'])


class OrphanRecoveryTests(unittest.TestCase):
    def fixture(self, root):
        f = Fixture(root); f.version = 2
        path = f.state/'jobs'/OLD_JOB['id']/'state.json'
        atomic(path, OLD_JOB)
        proof = {'id': OLD_JOB['id'], 'verified_ended': True, 'exit_code': 255,
                 'reason': 'control_heartbeat_timeout', 'finished_at': 100,
                 'result_bundle_present': False}
        ops = []
        def remote(node, op, **payload):
            ops.append(op)
            if op == 'inspect_tasks': return {'ended': [proof]}
            if op == 'recover': return {'released': [OLD_JOB['id']]}
            raise AssertionError(op)
        w = f.worker(); w.remote = remote
        return f, w, path, proof, ops

    def test_proven_remote_end_releases_and_preserves_original_record(self):
        with tempfile.TemporaryDirectory() as root:
            f,w,path,proof,ops = self.fixture(root)
            recovered = w.recover_orphans(f.node, {'busy': False})
            self.assertEqual(recovered, [OLD_JOB['id']])
            self.assertEqual(ops, ['inspect_tasks', 'recover'])
            state = load(path)
            self.assertEqual(state['error'], 'control_heartbeat_timeout')
            self.assertEqual(state['finished_at'], 100)
            self.assertTrue(state['allocation_released'])
            backup = w.state/'recovered-records'/'compute-a'/(OLD_JOB['id']+'.json')
            self.assertEqual(load(backup), OLD_JOB)

    def test_busy_worker_recent_record_or_no_proof_never_releases(self):
        for mode in ('busy', 'recent', 'heartbeat_recent', 'no_proof'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                f,w,path,proof,ops = self.fixture(root)
                if mode == 'recent': atomic(path, dict(OLD_JOB, created_at=time.time()))
                if mode == 'heartbeat_recent': atomic(path, dict(OLD_JOB, last_control_heartbeat_at=time.time()))
                if mode == 'no_proof': proof['verified_ended'] = False
                before = path.read_bytes()
                self.assertEqual(w.recover_orphans(f.node, {'busy': mode == 'busy'}), [])
                self.assertNotIn('recover', ops)
                self.assertEqual(path.read_bytes(), before)

    def test_uncollected_result_bundle_stays_pending(self):
        with tempfile.TemporaryDirectory() as root:
            f,w,path,proof,ops = self.fixture(root)
            proof['result_bundle_present'] = True
            self.assertEqual(w.recover_orphans(f.node, {'busy': False}), [])
            self.assertNotIn('recover', ops)
            self.assertEqual(load(path), OLD_JOB)

    def test_changed_record_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            f,w,path,proof,ops = self.fixture(root)
            remote = w.remote
            def race(node, op, **payload):
                result = remote(node, op, **payload)
                if op == 'recover': atomic(path, dict(OLD_JOB, status='completed', allocation_released=True))
                return result
            w.remote = race
            self.assertEqual(w.recover_orphans(f.node, {'busy': False}), [])
            self.assertEqual(load(path)['status'], 'completed')

    def test_busy_after_remote_probe_prevents_release(self):
        with tempfile.TemporaryDirectory() as root:
            f,w,path,proof,ops = self.fixture(root)
            f.busy = True
            self.assertEqual(w.recover_orphans(f.node, {'busy': False}), [])
            self.assertNotIn('recover', ops)

    def test_manual_pause_never_requests_automatic_resume(self):
        with tempfile.TemporaryDirectory() as root:
            f,w,path,proof,ops = self.fixture(root)
            admission.set_admission(f.state, False)
            w.api = lambda *args: self.fail('Manual pause must not call resume')
            self.assertFalse(w.resume_startup(f.node, {'busy':False, 'active_jobs':0, 'startup_recovery_version':1}))

    def test_staged_recovery_upgrade_waits_for_active_workers(self):
        with tempfile.TemporaryDirectory() as root:
            f,w,path,proof,ops = self.fixture(root)
            p=f.dir/'api.py';p.write_text(p.read_text()+"\nVERSION={'startup_recovery_version':1}\n")
            health=f.api(f.node,'/health');health.update(busy=True,active_jobs=1)
            self.assertFalse(w.api_current(f.node,health))
            self.assertEqual(w.activate_idle_api(f.node,health),health)
            self.assertEqual(f.restarts,0)
            health['startup_recovery_version']=1
            self.assertTrue(w.api_current(f.node,health))

    def test_inflight_request_prevents_reload_and_restores_own_pause(self):
        with tempfile.TemporaryDirectory() as root:
            f,w,path,proof,ops = self.fixture(root)
            p=f.dir/'api.py';p.write_text(p.read_text()+"\nVERSION={'startup_recovery_version':1}\n")
            w.reload_quiescent=lambda _:False
            w.activate_idle_api(f.node,f.api(f.node,'/health'))
            self.assertEqual(f.restarts,0)
            self.assertTrue(admission.read(f.state)['accepting_new_jobs'])


class RemoteProofTests(unittest.TestCase):
    def fixture(self, root, mode):
        from isolation_toolkit import compute_remote
        root=Path(root); runtime=root/'runtime';runtime.mkdir()
        leases=root/'leases';leases.mkdir();jobs=root/'jobs'
        jid=OLD_JOB['id'];job=jobs/jid;job.mkdir(parents=True)
        base=root/'scratch'
        atomic(job/'cleanup-context.private.json',{'version':'v1','job_id':jid,'base':str(base)})
        atomic(job/'cleanup.json',{'version':'v1','job_id':jid,'base':str(base),
               'cleaned':True,'runtime_registry_empty':True,'checked_at':100})
        atomic(job/'lifecycle-private.json',{'returncode':255,'stop_reason':'control_heartbeat_timeout'})
        atomic(leases/(jid+'.json'),{'id':jid,'phase':'running'})
        if mode=='wrong_id': atomic(job/'cleanup.json',{'version':'v1','job_id':'b'*32,'cleaned':True})
        if mode=='runtime_exists': base.mkdir()
        if mode=='result': (job/'result.zip').write_bytes(b'kept')
        if mode=='missing_receipt': (job/'cleanup.json').unlink()
        if mode=='unfinished': atomic(job/'lifecycle-private.json',{'stopped':False})
        @contextlib.contextmanager
        def locks(*args):
            if mode=='locked': raise BlockingIOError()
            yield
        def binding(*args):raise ValueError()
        r=types.SimpleNamespace(JOBS=jobs,LEASES=leases,valid=lambda _:None,transaction=contextlib.nullcontext)
        c=types.SimpleNamespace(VERSION='v1',valid_job=lambda p:p,load=load,
            base_location=Path,associated_processes=lambda *args:[123] if mode=='live' else [],
            binding=binding,allocation_locks=locks)
        with patch.dict('sys.modules',{'resources':r,'cleanup_recovery':c}):
            result=compute_remote.inspect_tasks({'runtimes':[str(runtime)]},[jid])
        self.assertTrue((leases/(jid+'.json')).exists())
        if mode=='result':self.assertEqual((job/'result.zip').read_bytes(),b'kept')
        return result

    def test_live_locked_ambiguous_or_unfinished_tasks_have_no_proof(self):
        for mode in ('live','locked','wrong_id','runtime_exists','missing_receipt','unfinished'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                self.assertEqual(self.fixture(root,mode),{'ended':[]})

    def test_confirmed_end_and_result_presence_are_reported_without_mutation(self):
        for mode in ('ended','result'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                rows=self.fixture(root,mode)['ended']
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['reason'],'control_heartbeat_timeout')
                self.assertEqual(rows[0]['result_bundle_present'],mode=='result')


if __name__ == '__main__':
    unittest.main()
