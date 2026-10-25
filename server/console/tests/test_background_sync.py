import json,os,stat,tempfile,threading,time,unittest
from pathlib import Path
from console_cache import ReadCache
from console_sync import BackgroundSync, SnapshotStore, restored_value

A='cse_'+'a'*20;B='cse_'+'b'*20

def session(sid):return {'id':sid,'kind':'remote','status':'running','connected':True,'can_send':True}
def record(sid):return {'ok':True,'session':session(sid),'events':[{'seq':1,'role':'assistant','text':'A saved message'}],'pending':[{'request_id':'old'}],'partial':'old partial'}

def wait_until(predicate,seconds=4):
 end=time.monotonic()+seconds
 while not predicate():
  if time.monotonic()>end:raise AssertionError('condition not reached')
  time.sleep(.02)

class StoreTests(unittest.TestCase):
 def test_restore_is_private_bounded_and_does_not_grant_write_state(self):
  with tempfile.TemporaryDirectory() as d:
   store=SnapshotStore(Path(d)/'cache');store.save('session:'+A,record(A),time.time()-10)
   rows=store.load();self.assertEqual(len(rows),1);self.assertEqual(stat.S_IMODE(store.root.stat().st_mode),0o700)
   self.assertEqual(stat.S_IMODE(store.path('session:'+A).stat().st_mode),0o600)
   restored=restored_value(rows[0][0],rows[0][1]);self.assertFalse(restored['session']['can_send']);self.assertEqual(restored['pending'],[]);self.assertEqual(restored['events'],record(A)['events'])
 def test_symlinks_bad_ids_corruption_and_expiry_do_not_load(self):
  with tempfile.TemporaryDirectory() as d:
   store=SnapshotStore(Path(d)/'cache');store.save('session:'+A,record(A),time.time()-90000);self.assertEqual(store.load(),[])
   with self.assertRaises(ValueError):store.path('../../secrets')
   target=Path(d)/'private';target.write_text('retained', encoding='utf-8');p=store.path('session:'+A);p.symlink_to(target)
   with self.assertRaisesRegex(ValueError,'invalid_snapshot_file'):store.save('session:'+A,record(A),time.time())
   self.assertEqual(target.read_text(encoding='utf-8'),'retained');self.assertEqual(store.load(),[])
   p.unlink();p.write_text('{invalid', encoding='utf-8');self.assertEqual(store.load(),[])
 def test_capacity_recycles_only_cache_files(self):
  with tempfile.TemporaryDirectory() as d:
   store=SnapshotStore(Path(d)/'cache',max_files=1);unrelated=store.root/'keep.txt';unrelated.write_text('keep', encoding='utf-8')
   store.save('session:'+A,record(A),time.time()-2);store.save('session:'+B,record(B),time.time())
   self.assertFalse(store.path('session:'+A).exists());self.assertTrue(store.path('session:'+B).exists());self.assertEqual(unrelated.read_text(encoding='utf-8'),'keep')
 def test_unchanged_snapshot_refreshes_timestamp_without_rewrite(self):
  with tempfile.TemporaryDirectory() as d:
   store=SnapshotStore(Path(d)/'cache');stamp=time.time()-5;store.save('session:'+A,record(A),stamp);p=store.path('session:'+A);inode=p.stat().st_ino
   store.save('session:'+A,record(A),stamp+3);self.assertEqual(p.stat().st_ino,inode);self.assertAlmostEqual(p.stat().st_mtime,stamp+3,places=3)

class SyncTests(unittest.TestCase):
 def make(self,root,calls,gate=None):
  def read(request):
   calls.append(dict(request))
   self.assertIn(request['action'],{'list','health','read'})
   if request['action']=='list':return {'ok':True,'sessions':[session(A),session(B)]}
   if request['action']=='health':return {'ok':True,'authenticated':True}
   if gate and request['session_id']==A:gate.wait(3)
   return record(request['session_id'])
  reads=ReadCache(read);activity=ReadCache(lambda r:{'ok':True,'session_id':r['session_id'],'activity':{'fresh':True,'phase':'working','timer_running':True}})
  return BackgroundSync(reads,activity,root),reads,activity
 def test_background_populates_and_restores_without_browser_reads(self):
  with tempfile.TemporaryDirectory() as d:
   calls=[];sync,reads,activity=self.make(Path(d)/'cache',calls)
   try:
    sync.start();wait_until(lambda:sync.status()['ready_sessions']==2);sync.flush()
    self.assertTrue(any(x.get('session_id')==A for x in calls));self.assertTrue(any(x.get('session_id')==B for x in calls));self.assertGreater(sync.status()['disk_bytes'],0)
   finally:sync.stop();reads.pool.shutdown();activity.pool.shutdown()
   restored,reads2,act2=self.make(Path(d)/'cache',[])
   try:
    restored.restore();value,meta=reads2.peek(('read',A));self.assertTrue(meta['restored']);self.assertGreater(meta['updated_at'],0);self.assertEqual(value['events'],record(A)['events']);self.assertFalse(value['session']['can_send'])
    self.assertFalse(act2.peek(A)[0]['activity']['fresh'])
   finally:reads2.pool.shutdown();act2.pool.shutdown()
 def test_slow_session_does_not_block_another_and_writes_pause_new_work(self):
  with tempfile.TemporaryDirectory() as d:
   calls=[];gate=threading.Event();sync,reads,activity=self.make(Path(d)/'cache',calls,gate)
   try:
    sync.begin_write();sync.tick();self.assertEqual(calls,[]);sync.end_write();sync.start()
    wait_until(lambda:reads.peek(('read',B))[0] is not None)
    self.assertIsNone(reads.peek(('read',A))[0]);self.assertTrue(sync.status()['running'])
   finally:gate.set();sync.stop();reads.pool.shutdown();activity.pool.shutdown()
 def test_duplicate_background_owner_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   sync,reads,activity=self.make(Path(d)/'cache',[]);other,r2,a2=self.make(Path(d)/'cache',[])
   try:
    sync.start()
    with self.assertRaises(BlockingIOError):other.start()
   finally:
    sync.stop();other.stop()
    for cache in (reads,activity,r2,a2):cache.pool.shutdown()

if __name__=='__main__':unittest.main()
