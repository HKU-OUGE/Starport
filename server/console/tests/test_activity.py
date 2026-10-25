import importlib.util,json,sys,unittest
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parents[1]/'client/starship'))
from activity_state import Activity
from activity_observer import ReadOnlyController,Observer,Watch
class ActivityTests(unittest.TestCase):
 def setUp(self):
  self.now=1780000000000;self.a=Activity(lambda:self.now)
  self.a.metadata({'connection_status':'connected','worker_status':'idle'})
 def event(self,payload,seq=0,live=True,source='worker'):
  t=datetime.fromtimestamp(self.now/1000,timezone.utc).isoformat()
  self.a.ingest({'payload':payload,'sequence_num':seq,'created_at':t,'source':source},live)
 def user(self,n=1):self.event({'type':'user','uuid':'user-'+str(n),'message':{'content':[{'type':'text','text':'example'}]}},n)
 def delta(self,kind='thinking_delta',content='private reasoning must never leave'):
  self.event({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':kind,'text':content,'thinking':content}}})
 def test_true_thinking_is_flag_only(self):
  self.user();self.now+=1000;self.delta();out=self.a.snapshot()
  self.assertEqual(out['phase'],'thinking');self.assertTrue(out['active']);self.assertNotIn('private reasoning',json.dumps(out))
 def test_completed_thinking_block_is_not_live_thinking(self):
  self.user();self.event({'type':'assistant','message':{'content':[{'type':'thinking','thinking':'private'}]}},2,False)
  self.assertEqual(self.a.snapshot()['phase'],'working')
 def test_tool_lifecycle_and_queue_does_not_reset_clock(self):
  self.user();self.now+=1000;self.delta();start=self.a.started
  self.event({'type':'assistant','message':{'content':[{'type':'tool_use','id':'t1','name':'Bash','input':{'command':'secret path'}}]}},2)
  self.assertEqual(self.a.snapshot()['tool_name'],'Bash');self.assertNotIn('secret path',json.dumps(self.a.snapshot()))
  self.user(3);self.assertEqual(self.a.started,start);self.assertEqual(self.a.queued,1)
  self.event({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'t1','content':'private output'}]}},4)
  self.assertEqual(self.a.snapshot()['phase'],'working');self.assertIsNone(self.a.snapshot()['tool_name'])
 def test_reported_duration_stops(self):
  self.user();self.delta();self.now+=9000
  self.event({'type':'result','duration_ms':8500},3);one=self.a.snapshot();self.now+=7000;two=self.a.snapshot()
  self.assertEqual(one['elapsed_ms'],8500);self.assertEqual(two['elapsed_ms'],8500);self.assertFalse(two['timer_running'])
 def test_zero_completion_duration_does_not_erase_observed_time(self):
  self.user();self.delta();self.now+=9000
  self.event({'type':'result','duration_ms':0},3)
  self.assertEqual(self.a.snapshot()['elapsed_ms'],9000)
 def test_permission_and_question(self):
  self.user();self.delta()
  self.event({'type':'control_request','request_id':'p','request':{'subtype':'can_use_tool','tool_name':'AskUserQuestion','input':{'questions':'private'}}},2)
  self.a.metadata({'connection_status':'connected','worker_status':'requires_action'})
  self.assertEqual(self.a.snapshot()['phase'],'waiting_input');self.assertFalse(self.a.snapshot()['active'])
 def test_stale_and_disconnected_freeze(self):
  self.user();self.delta();self.now+=1000;self.a.metadata({'connection_status':'connected','worker_status':'running'})
  self.now+=26000;one=self.a.snapshot();self.now+=10000;two=self.a.snapshot()
  self.assertFalse(two['fresh']);self.assertFalse(two['timer_running']);self.assertEqual(one['elapsed_ms'],two['elapsed_ms'])
  self.a.metadata({'connection_status':'disconnected','worker_status':'running'});self.assertEqual(self.a.snapshot()['phase'],'disconnected')
 def test_idle_fallback_duration_is_stable(self):
  self.user();self.delta();self.now+=9000;self.a.metadata({'connection_status':'connected','worker_status':'idle'})
  one=self.a.snapshot();self.now+=5000;self.a.metadata({'connection_status':'connected','worker_status':'idle'});two=self.a.snapshot()
  self.assertEqual(one['elapsed_ms'],two['elapsed_ms']);self.assertEqual(two['phase'],'complete')
 def test_nested_agent_does_not_replace_main_phase(self):
  self.user();self.delta();self.event({'type':'assistant','parent_tool_use_id':'agent1','message':{'content':[{'type':'tool_use','name':'Nested','id':'n'}]}},3)
  self.assertEqual(self.a.snapshot()['phase'],'thinking')
 def test_duplicates_and_bad_ids_rejected(self):
  self.user();self.user();self.assertEqual(self.a.queued,1)
  o=Observer()
  for data in [{'action':'send','session_id':'cse_'+'a'*16},{'action':'read','session_id':'../../etc/passwd'},{'action':'read','session_id':'cse_'+'a'*16,'url':'https://example.invalid'}]:
   with self.assertRaises(ValueError):o.read(data)
 def test_controller_never_sends(self):
  with self.assertRaisesRegex(ValueError,'activity_read_only'):ReadOnlyController(lambda:{}).request('sessions',{})
 def test_idle_metadata_clears_historical_queue(self):
  self.user();self.delta();self.user(3);self.event({'type':'result'},4);self.now+=9000
  self.a.metadata({'connection_status':'connected','worker_status':'idle'})
  self.assertEqual(self.a.snapshot()['phase'],'complete');self.assertEqual(self.a.snapshot()['queued'],0)
 def test_native_snapshot_preserves_timer_and_tool_state(self):
  w=Watch.__new__(Watch);w.sid='cse_'+'a'*20;w.seen=set();w.seen_order=[];w.counter=0;w.value=self.a
  t=datetime.fromtimestamp(self.now/1000,timezone.utc).isoformat()
  rows=[{'seq':1,'role':'user','time':t,'text':'Example'},{'seq':2,'role':'tool','time':t,'tool_id':'t','tool_name':'Bash','text':'Bash'}]
  snap={'session':{'id':w.sid,'connected':True,'status':'running'},'events':rows,'stream_connected':True}
  w.ingest_native(snap);first=w.value.started;self.now+=3000;w.ingest_native(snap)
  self.assertEqual(w.value.started,first);self.assertEqual(w.value.snapshot()['tool_name'],'Bash');self.assertEqual(w.counter,2)
  snap['events']=rows+[{'seq':3,'role':'tool_result','tool_id':'t','time':t,'text':'done'},{'seq':4,'role':'result','time':t,'text':'Turn complete'}]
  snap['session']['status']='idle';w.ingest_native(snap)
  self.assertFalse(w.value.snapshot()['timer_running']);self.assertIsNone(w.value.snapshot()['tool_name'])
  snap['session']['id']='cse_'+'b'*20
  with self.assertRaisesRegex(ValueError,'mismatch'):w.ingest_native(snap)
if __name__=='__main__':unittest.main()
