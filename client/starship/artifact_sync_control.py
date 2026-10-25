"""Fixed start/status interface for a one-shot, read-only history refresh."""
from pathlib import Path
import fcntl,json,os,plistlib,re,subprocess,sys,time
BASE=Path.home()/'.local/share/starship';CACHE=BASE/'state/artifact-sources';LABEL='io.starport.starship-artifacts'

def status():
 p=CACHE/'status.json'
 if not p.exists():return {'state':'idle'}
 if p.is_symlink() or p.stat().st_size>65536:raise ValueError('sync_status_invalid')
 value=json.loads(p.read_text())
 return {k:value.get(k) for k in ('job_id','state','started_at','finished_at','sessions','error')}

def dispatch(request):
 if set(request)-{'action','job_id'} or request.get('action') not in ('start','status'):raise ValueError('sync_action_blocked')
 if request['action']=='status':return {'ok':True,'sync':status()}
 jid=request.get('job_id')
 if not isinstance(jid,str) or not re.fullmatch(r'[a-f0-9]{32}',jid):raise ValueError('invalid_job')
 CACHE.mkdir(mode=0o700,exist_ok=True)
 with (CACHE/'control.lock').open('a') as control:
  fcntl.flock(control,fcntl.LOCK_EX)
  current=status()
  with (CACHE/'sync.lock').open('a') as active:
   try:fcntl.flock(active,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:return {'ok':True,'sync':current,'reused':True}
   p=CACHE/'status.json'
   if current.get('state')=='queued' and p.exists() and time.time()-p.stat().st_mtime<30:return {'ok':True,'sync':current,'reused':True}
   target='gui/'+str(os.getuid())
   subprocess.run(['/bin/launchctl','bootout',target+'/'+LABEL],capture_output=True,timeout=10)
   plist=CACHE/'launch-once.plist'
   plist.write_bytes(plistlib.dumps({'Label':LABEL,'ProgramArguments':[sys.executable,str(BASE/'artifact_native_sync.py'),jid],'RunAtLoad':True,'KeepAlive':False,'StandardErrorPath':str(CACHE/'last-error.log')}))
   queued={'state':'queued','job_id':jid,'sessions':[]};p.write_text(json.dumps(queued))
   # Release the worker lock before launch; the control lock serializes starts.
   fcntl.flock(active,fcntl.LOCK_UN)
   result=subprocess.run(['/bin/launchctl','bootstrap',target,str(plist)],capture_output=True,timeout=15)
   if result.returncode:
    p.write_text(json.dumps({'state':'failed','job_id':jid,'error':'native_sync_start_failed'}))
    raise ValueError('native_sync_start_failed')
   return {'ok':True,'sync':queued}

if __name__=='__main__':
 os.umask(0o077)
 try:
  raw=sys.stdin.buffer.readline(4097)
  if len(raw)>4096:raise ValueError('sync_request_limit')
  value=dispatch(json.loads(raw))
 except Exception as exc:
  allowed={'sync_action_blocked','invalid_job','native_sync_start_failed','sync_status_invalid','sync_request_limit'}
  value={'ok':False,'error':str(exc) if str(exc) in allowed else 'source_sync_unavailable'}
 print(json.dumps(value,ensure_ascii=False))
