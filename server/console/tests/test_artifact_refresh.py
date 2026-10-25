import hashlib,json,tempfile,threading,time,unittest,zipfile
from pathlib import Path
from artifact_refresh import RefreshManager,build_snapshot
from artifact_exports import latest_export,export_bundle
SID='cse_1234567890123456';A='a'*20;B='b'*20

def rpc(request,**kwargs):
 if request['action']=='list' and 'session_id' not in request:return {'sessions':[{'id':SID,'title':'Example'}]}
 if request['action']=='list':return {'artifacts':[{'id':a,'title':'Report '+a[0],'official_url':'https://claude.ai/artifact/'+a} for a in [A,B]]}
 aid=request['artifact_id']
 return {'artifact':{'id':aid,'title':'Report '+aid[0],'official_url':'https://claude.ai/artifact/'+aid,'available':True,'xml':'<doc><paragraph heading="1">Report</paragraph><paragraph>Verified content '+aid[0]+'</paragraph></doc>','revision':1,'captured_at':'2026-10-24T00:00:00Z'}}
def sync(request):return {'sync':{'state':'complete','sessions':[]}}

class RefreshTests(unittest.TestCase):
 def test_new_reused_archives_and_failed_refresh_preserves_latest(self):
  with tempfile.TemporaryDirectory() as d:
   state=Path(d);first=build_snapshot(state,'1'*32,rpc,sync,lambda **kw:None)
   self.assertEqual(first['changes']['new'],2)
   first_bytes=export_bundle(state,first['id'])
   second=build_snapshot(state,'2'*32,rpc,sync,lambda **kw:None)
   self.assertEqual(second['changes']['reused'],2);self.assertEqual(second['changes']['new'],0)
   self.assertEqual(export_bundle(state,first['id']),first_bytes)
   def failed(request,**kwargs):
    if request['action']=='read':raise ValueError('starship_connection_unavailable')
    return rpc(request,**kwargs)
   with self.assertRaises(ValueError):build_snapshot(state,'3'*32,failed,sync,lambda **kw:None)
   self.assertEqual(latest_export(state)['id'],second['id']);self.assertFalse((state/'artifact-exports'/('.staging-'+'3'*32)).exists())
   def missing(request,**kwargs):
    if request['action']=='read':return {'artifact':{'id':request['artifact_id'],'available':False}}
    return rpc(request,**kwargs)
   third=build_snapshot(state,'4'*32,missing,sync,lambda **kw:None)
   self.assertEqual(third['html_count'],2);self.assertEqual(third['changes']['retained'],2)
   with zipfile.ZipFile(state/'artifact-exports'/third['id']/'artifacts.zip') as z:
    for entry in third['artifacts']:self.assertEqual(hashlib.sha256(z.read(entry['file'])).hexdigest(),entry['sha256'])
 def test_duplicate_clicks_and_cross_process_lock_share_one_job(self):
  with tempfile.TemporaryDirectory() as d:
   state=Path(d);gate=threading.Event();started=threading.Event();calls=[]
   def worker(state,jid,*args):calls.append(jid);started.set();gate.wait(3);raise ValueError('source_sync_failed')
   one=RefreshManager(state,rpc,sync,build=worker);two=RefreshManager(state,rpc,sync,build=worker)
   first=one.start();self.assertTrue(started.wait(1))
   repeat=two.start();self.assertEqual(repeat['id'],first['id']);self.assertTrue(repeat['reused']);self.assertEqual(len(calls),1)
   gate.set()
   until=time.time()+2
   while one.status()['state']=='running' and time.time()<until:time.sleep(.01)
   self.assertEqual(one.status()['state'],'failed');self.assertEqual(one.status()['error'],'source_sync_failed')
 def test_failed_native_sync_does_not_publish(self):
  with tempfile.TemporaryDirectory() as d:
   def bad(req):return {'sync':{'state':'complete','sessions':[{'state':'failed'}]}}
   with self.assertRaisesRegex(ValueError,'source_sync_failed'):build_snapshot(Path(d),'5'*32,rpc,bad,lambda **kw:None)
   self.assertIsNone(latest_export(Path(d)))

class InterruptedStatusTests(unittest.TestCase):
 def test_missing_worker_lock_reports_interrupted_even_if_pid_is_alive(self):
  import os
  from artifact_refresh import atomic_json
  with tempfile.TemporaryDirectory() as d:
   state=Path(d);m=RefreshManager(state,rpc,sync)
   atomic_json(m.path,{'state':'running','owner_pid':os.getpid(),'id':'a'*32})
   self.assertEqual(m.status()['state'],'interrupted')

class SharedReferenceTests(unittest.TestCase):
 def test_reference_only_conversation_does_not_hide_full_source(self):
  remote='cse_9999999999999999'
  def read(request,**kwargs):
   if request['action']=='list' and 'session_id' not in request:return {'sessions':[{'id':remote,'title':'Reference'},{'id':SID,'title':'Original','local_id':'local'}]}
   if request['action']=='read' and request['session_id']==remote:return {'artifact':{'id':request['artifact_id'],'available':False}}
   return rpc(request,**kwargs)
  with tempfile.TemporaryDirectory() as d:
   m=build_snapshot(Path(d),'6'*32,read,sync,lambda **kw:None)
   self.assertEqual(m['html_count'],2);self.assertEqual(m['changes']['retained'],0)
   self.assertTrue(all(a['session']=='Original' for a in m['artifacts']))
   self.assertTrue(all(set(a['linked_sessions'])=={'Original','Reference'} for a in m['artifacts']))
