"""Run official, non-inference authentication diagnostics in the user session."""
from pathlib import Path
import fcntl,json,os,subprocess,sys,time
BASE=Path.home()/'.local/share/starship'
sys.path.insert(0,str(BASE))
from client import cli
STATE=BASE/'state/auth-maintenance'

def metadata():
 r=subprocess.run(['/usr/bin/security','find-generic-password','-s','Claude Code-credentials','-w'],capture_output=True,timeout=10)
 if r.returncode:return {'available':False,'exit':r.returncode}
 try:
  d=json.loads(r.stdout).get('claudeAiOauth',{})
  return {'available':bool(d.get('accessToken')),'expired':d.get('expiresAt',0)<=time.time()*1000,'remaining_seconds':int((d.get('expiresAt',0)-time.time()*1000)/1000),'refresh_present':bool(d.get('refreshToken'))}
 except Exception:return {'available':False}

def main():
 os.umask(0o077);STATE.mkdir(mode=0o700,exist_ok=True)
 with (STATE/'probe.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  out={'started_at':time.time(),'before':metadata(),'model_messages_sent':0}
  needed=out['before'].get('refresh_present') and out['before'].get('remaining_seconds',999999)<600
  out['renewal_checked']=bool(needed)
  if needed:
   try:
    r=subprocess.run([cli(),'doctor'],capture_output=True,cwd=STATE,timeout=30)
    out['doctor_exit']=r.returncode
   except subprocess.TimeoutExpired:out['doctor_timeout']=True
  out['after']=metadata()
  out['finished_at']=time.time();p=STATE/'status.json';p.write_text(json.dumps(out));p.chmod(0o600)
if __name__=='__main__':main()
