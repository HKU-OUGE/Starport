from bootstrap import fcntl
"""One-shot, read-only artifact history download in the native user session.

Uses the existing controller for authentication. No credential is serialized.
It does not start, send messages to, stop or resume any Claude session.
"""
from pathlib import Path
import datetime,json,os,re,sys,time
os.umask(0o077)
BASE=Path.home()/'.local/share/starship';sys.path.insert(0,str(BASE))
from remote_control import Controller
from client import desktop_index
from artifact_reader import native_rpc,LINK
CACHE=BASE/'state/artifact-sources'
CACHE.mkdir(mode=0o700,exist_ok=True)

def atomic(path,value):
 raw=json.dumps(value,ensure_ascii=False).encode()
 if len(raw)>48*1024*1024:raise ValueError('artifact_source_limit')
 tmp=path.with_suffix('.next');tmp.write_bytes(raw);os.replace(tmp,path)

def main(job_id=None):
 lock=(CACHE/'sync.lock').open('w')
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 status={'job_id':job_id,'state':'running','started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sessions':[]}
 atomic(CACHE/'status.json',status)
 controller=Controller(desktop_index)
 sessions=native_rpc({'action':'list'}).get('sessions',[])
 # Local-mapped sessions are scanned from their full native transcripts.
 for session in sessions:
  sid=session.get('id','')
  if not re.fullmatch(r'cse_[A-Za-z0-9]{16,64}',sid) or session.get('local_id'):continue
  entry={'id':sid,'title':session.get('title'),'state':'reading','pages':0}
  status['sessions'].append(entry);atomic(CACHE/'status.json',status)
  try:
   controller.get(sid)
   old_path=CACHE/(sid+'.json')
   old=json.loads(old_path.read_text(encoding='utf-8')) if old_path.exists() and not old_path.is_symlink() else {}
   previous=int(old.get('last_sequence',0)) if old.get('history_complete') else 0
   events={};cursor=None;seen=set();complete=False
   for page in range(30):
    path='sessions/'+sid+'/events?limit=100&sort_order=desc'+('&cursor='+cursor if cursor else '')
    value=controller.request(path)
    rows=value.get('data')
    if not isinstance(rows,list):raise ValueError('artifact_protocol_changed')
    for row in rows:
     seq=int(row.get('sequence_num') or 0)
     if seq:events[seq]=row
    entry['pages']=page+1;entry['events']=len(events);atomic(CACHE/'status.json',status)
    if previous and any(int(r.get('sequence_num') or 0)<=previous for r in rows):complete=True;break
    nxt=value.get('next_cursor')
    if not nxt:complete=True;break
    nxt=str(nxt)
    if not re.fullmatch(r'[A-Za-z0-9_=-]{1,160}',nxt) or nxt in seen:raise ValueError('artifact_cursor_invalid')
    seen.add(nxt);cursor=nxt
    time.sleep(.1)
   output=list(old.get('rows',[])) if previous else []
   tool_ids={b.get('id') for row in output for b in row[1] if b.get('type')=='tool_use'}
   for sequence in sorted(events):
    if sequence<=previous:continue
    row=events[sequence];payload=row.get('payload',row)
    if not isinstance(payload,dict) or payload.get('type') not in ('assistant','user'):continue
    blocks=(payload.get('message') or {}).get('content',[])
    if isinstance(blocks,str):blocks=[{'type':'text','text':blocks}]
    selected=[]
    for block in blocks:
     if not isinstance(block,dict):continue
     kind=block.get('type');data=block.get('input')
     if kind=='tool_use' and (block.get('name')=='Artifact' or isinstance(data,dict) and ('engine' in data or 'container' in data)):
      tool_ids.add(block.get('id'));selected.append(block)
     elif kind=='tool_result' and block.get('tool_use_id') in tool_ids:selected.append(block)
     elif kind=='text' and LINK.search(block.get('text','')):selected.append(block)
    if selected:output.append([row.get('created_at') or payload.get('timestamp') or '',selected,None])
   result={'session_id':sid,'fetched_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'history_complete':complete,'events_read':len(events),'last_sequence':max([previous,*events.keys()]),'rows':output}
   atomic(CACHE/(sid+'.json'),result)
   entry.update(state='complete' if complete else 'bounded',artifact_rows=len(output))
  except Exception as exc:entry.update(state='failed',error=str(exc)[:160])
  atomic(CACHE/'status.json',status)
 status.update(state='complete',finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
 atomic(CACHE/'status.json',status)

if __name__=='__main__':
 job_id=sys.argv[1] if len(sys.argv)==2 else None
 if job_id is not None and not re.fullmatch(r'[a-f0-9]{32}',job_id):raise SystemExit('invalid_job')
 try:main(job_id)
 except BlockingIOError:pass
 except Exception:
  atomic(CACHE/'status.json',{'job_id':job_id,'state':'failed','error':'source_sync_failed'})
  raise
