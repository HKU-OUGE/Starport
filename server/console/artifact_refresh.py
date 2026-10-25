from bootstrap import fcntl
"""Background, read-only refresh of authenticated artifact download snapshots."""
from pathlib import Path
import base64,datetime,hashlib,io,json,os,re,shutil,subprocess,threading,time,uuid,zipfile
from artifact_exports import latest_export,export_bundle,regular_bytes,MAX_BUNDLE
from artifact_view import document_view
BASE=Path(__file__).resolve().parent

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def atomic_json(path,value):
 path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2), encoding='utf-8');os.chmod(tmp,0o600);os.replace(tmp,path)

def render(value):
 result=subprocess.run([shutil.which('node') or 'node',str(BASE/'web/artifact-render-cli.mjs')],input=json.dumps(value,ensure_ascii=False).encode(),capture_output=True,timeout=20)
 if result.returncode or len(result.stdout)>24*1024*1024:raise ValueError('snapshot_render_failed')
 return result.stdout

def previous_files(state):
 meta=latest_export(state)
 if not meta:return {},{}
 root=state/'artifact-exports'/meta['id'];manifest=json.loads(regular_bytes(root/'manifest.json',512*1024));files={}
 with zipfile.ZipFile(io.BytesIO(export_bundle(state,meta['id']))) as archive:
  total=0
  for item in manifest.get('artifacts',[]):
   file=item.get('file')
   if not file:continue
   if Path(file).is_absolute() or '..' in Path(file).parts:raise ValueError('previous_export_invalid')
   info=archive.getinfo(file);total+=info.file_size
   if info.file_size>24*1024*1024 or total>MAX_BUNDLE:raise ValueError('snapshot_size_limit')
   body=archive.read(file)
   if hashlib.sha256(body).hexdigest()!=item.get('sha256'):raise ValueError('previous_export_invalid')
   files[item['id']]=body
 return {a['id']:a for a in manifest.get('artifacts',[])},files

def build_snapshot(state,job_id,rpc,sync_rpc,progress):
 start=time.monotonic();deadline=start+900
 def check():
  if time.monotonic()>deadline:raise ValueError('snapshot_timeout')
 progress(stage='sync',message='正在同步远端成品记录')
 sync=sync_rpc({'action':'start','job_id':job_id})['sync'];native_id=sync.get('job_id')
 until=time.monotonic()+240
 while sync.get('state') in ('queued','running'):
  if time.monotonic()>until:raise ValueError('source_sync_timeout')
  time.sleep(1)
  sync=sync_rpc({'action':'status'})['sync']
  if native_id and sync.get('job_id')!=native_id:raise ValueError('source_sync_changed')
 if sync.get('state')!='complete' or any(s.get('state') in ('failed','bounded') for s in sync.get('sessions',[])):raise ValueError('source_sync_failed')
 progress(stage='list',message='正在检查 Remote Control 会话')
 sessions=[s for s in rpc({'action':'list'}).get('sessions',[]) if re.fullmatch(r'cse_[A-Za-z0-9]{16,64}',str(s.get('id','')))]
 if len(sessions)>50:raise ValueError('snapshot_session_limit')
 old,old_files=previous_files(state);entries=[];seen=set();counts={'new':0,'updated':0,'reused':0,'retained':0};catalog={}
 for i,session in enumerate(sessions):
  check();progress(stage='list',message='正在查找成品',session_title=session.get('title'),sessions_done=i,sessions_total=len(sessions))
  result=rpc({'action':'list','session_id':session['id'],'full_history':True},artifact=True)
  for item in result.get('artifacts',[]):
   aid=item.get('id','')
   if not re.fullmatch(r'[A-Za-z0-9_-]{12,80}',aid):raise ValueError('snapshot_record_invalid')
   seen.add(aid);catalog.setdefault(aid,[]).append((session,item))
 if len(catalog)>200:raise ValueError('snapshot_artifact_limit')
 root=state/'artifact-exports';stage=root/('.staging-'+job_id);stage.mkdir(parents=True,mode=0o700)
 try:
  files={}
  for i,(aid,providers) in enumerate(catalog.items()):
   # A reference-only link in a new conversation must not displace the
   # originating conversation's full document or figures.
   providers.sort(key=lambda pair:(not bool(pair[0].get('local_id')),pair[0].get('title')!=old.get(aid,{}).get('session')))
   for session,item in providers:
    check();progress(stage='read',message='正在读取正文与插图',session_title=session.get('title'),artifact_title=item.get('title'),artifacts_done=i,artifacts_total=len(catalog),**counts)
    data=rpc({'action':'read','session_id':session['id'],'artifact_id':aid,'full_history':True},artifact=True)['artifact']
    view=document_view(data)
    if view.get('available') or data.get('html_source'):break
   entry={'id':item['id'],'session_id':session['id'],'session':session.get('title','Session'),'title':item.get('title','Artifact'),'official_url':item['official_url'],'status':'link_only','reason':'没有完整正文或原始 HTML 文件'}
   entry['linked_sessions']=[pair[0].get('title','Session') for pair in providers]
   body=None
   if view.get('available'):
    body=render({'action':'document','artifact':view});entry.update(status='document',reason='',revision=view.get('revision'),captured_at=view.get('captured_at'),figures=view.get('figure_count'),available_figures=view.get('available_figures'),missing=view.get('missing',[]),stale_figures=view.get('stale_figures',0),snapshot_outdated=bool(view.get('snapshot_outdated')),latest_observed_revision=view.get('latest_observed_revision'))
   elif data.get('html_source'):
    source=data['html_source'];body=base64.b64decode(source['data'],validate=True)
    if len(body)!=source['bytes'] or hashlib.sha256(body).hexdigest()!=source['sha256']:raise ValueError('snapshot_source_integrity')
    body.decode('utf-8');entry.update(status='original_html',captured_at=source.get('captured_at'),reason='原始互动 HTML；保留原有脚本'+('；附加资源未包含' if source.get('dependency_count') else ''),dependency_count=source.get('dependency_count',0))
   elif item['id'] in old_files:
    entry={**old[item['id']],'retained':True,'retained_reason':view.get('reason'),'linked_sessions':entry['linked_sessions']};body=old_files[item['id']];counts['retained']+=1
   if body is not None:
    sha=hashlib.sha256(body).hexdigest();previous=old.get(item['id'],{})
    name=re.sub(r'[\\/:*?"<>|\x00-\x1f]','-',entry['title'])[:50] or 'Artifact'
    entry.update(file='documents/'+item['id'][:12]+'-'+name+'.html',sha256=sha,bytes=len(body))
    files[entry['file']]=body
    counts['reused' if previous.get('sha256')==sha else 'updated' if previous.get('file') else 'new']+=1
   entries.append(entry)
  # Previously exported files are retained even if a source disappears or a
  # session is no longer listed. The manifest makes this provenance explicit.
  for aid,item in old.items():
   if aid not in seen and aid in old_files:
    entry={**item,'retained':True};entries.append(entry);files[entry['file']]=old_files[aid];counts['reused']+=1;counts['retained']+=1
  manifest={'id':job_id,'created_at':now(),'session_count':len(sessions),'artifact_count':len(entries),'html_count':sum(bool(e.get('file')) for e in entries),'link_only_count':sum(not e.get('file') for e in entries),'sessions':[{'title':s.get('title'),'artifacts':sum(e.get('session_id')==s['id'] for e in entries)} for s in sessions],'artifacts':entries,'changes':counts}
  if not manifest['html_count'] and not entries:raise ValueError('snapshot_empty')
  progress(stage='package',message='正在校验并生成下载包',artifacts_done=len(catalog),artifacts_total=len(catalog),**counts)
  index=render({'action':'index','manifest':manifest});uncompressed=sum(map(len,files.values()))+len(index)
  if uncompressed>MAX_BUNDLE:raise ValueError('snapshot_size_limit')
  archive_path=stage/'artifacts.zip'
  with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
   archive.writestr('index.html',index);archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
   for name,body in files.items():archive.writestr(name,body)
  with zipfile.ZipFile(archive_path) as archive:
   if archive.testzip() is not None:raise ValueError('snapshot_archive_integrity')
   for entry in entries:
    if entry.get('file') and hashlib.sha256(archive.read(entry['file'])).hexdigest()!=entry['sha256']:raise ValueError('snapshot_archive_integrity')
  raw=archive_path.read_bytes()
  if len(raw)>MAX_BUNDLE:raise ValueError('snapshot_size_limit')
  manifest.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
  atomic_json(stage/'manifest.json',manifest)
  os.rename(stage,root/job_id)
  atomic_json(root/'latest.json',{'id':job_id})
  return manifest
 finally:
  if stage.exists():shutil.rmtree(stage)

class RefreshManager:
 def __init__(self,state,rpc,sync_rpc,on_complete=lambda:None,build=build_snapshot):
  self.state=state;self.rpc=rpc;self.sync_rpc=sync_rpc;self.on_complete=on_complete;self.build=build;self.lock=threading.Lock()
  self.path=state/'artifact-exports/refresh.json'
 def status(self):
  if not self.path.exists():return {'state':'idle'}
  value=json.loads(regular_bytes(self.path,65536))
  if value.get('state')=='running':
   with (self.path.parent/'refresh.lock').open('a') as guard:
    try:
     fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
     value.update(state='interrupted',message='更新已中断，上一版仍可下载')
    except BlockingIOError:pass
  return {k:v for k,v in value.items() if k!='owner_pid'}
 def start(self):
  with self.lock:
   self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
   guard=(self.path.parent/'refresh.lock').open('a')
   try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:guard.close();return {**self.status(),'reused':True}
   jid=uuid.uuid4().hex
   status={'id':jid,'state':'running','stage':'starting','message':'准备更新全部快照','started_at':now(),'owner_pid':os.getpid()}
   atomic_json(self.path,status)
   def progress(**values):
    status.update(values,updated_at=now());atomic_json(self.path,status)
   def work():
    try:
     result=self.build(self.state,jid,self.rpc,self.sync_rpc,progress)
     self.on_complete();progress(state='complete',stage='complete',message='快照已更新',export_id=result['id'],finished_at=now(),**result.get('changes',{}))
    except Exception as exc:
     allowed={'source_sync_timeout','source_sync_changed','source_sync_failed','snapshot_timeout','snapshot_size_limit','snapshot_empty','snapshot_source_integrity','snapshot_archive_integrity','snapshot_render_failed','starship_connection_unavailable'}
     progress(state='failed',message='更新失败，上一版仍可下载',error=str(exc) if str(exc) in allowed else 'snapshot_update_failed',finished_at=now())
    finally:fcntl.flock(guard,fcntl.LOCK_UN);guard.close()
   threading.Thread(target=work,name='artifact-refresh',daemon=True).start()
   return self.status()
