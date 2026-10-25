"""Session-scoped, resumable attachments. Uploaded bytes are never executed."""
from pathlib import Path
import base64,hashlib,json,os,re,secrets,threading,time
from bootstrap import secure_dir

CHUNK=256*1024
MAX_FILE=128*1024*1024
MAX_IMAGE=5*1024*1024
MAX_STAGING=1024*1024*1024
SID=re.compile(r'(?:cse_[A-Za-z0-9]{16,64}|[a-f0-9-]{36})')
ID=re.compile('[a-f0-9]{32}')
IMAGES={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp'}


def filename(value):
    if not isinstance(value,str) or not 1<=len(value)<=180 or value in ('.','..') or re.search(r'[\\/:*?"<>|\x00-\x1f]',value) or value.endswith((' ','.')):
        raise ValueError('attachment_invalid_name')
    if value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{x}' for x in range(1,10)],*[f'LPT{x}' for x in range(1,10)]}:
        raise ValueError('attachment_invalid_name')
    return value


def public(meta):return {k:meta[k] for k in ('id','session_id','name','size','offset','state','mime') if k in meta}


class Uploads:
    def __init__(self,root,project,session):
        self.root=Path(root);self.project=project;self.session=session;self.lock=threading.RLock()
    def folder(self,sid,uid):
        if not SID.fullmatch(str(sid)) or not ID.fullmatch(str(uid)):raise ValueError('attachment_invalid_id')
        return self.root/sid/uid
    def save(self,folder,meta):
        temp=folder/'meta.tmp';temp.write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8');temp.chmod(0o600);temp.replace(folder/'meta.json')
    def load(self,folder):
        p=folder/'meta.json'
        if p.is_symlink() or not p.is_file():raise ValueError('attachment_not_found')
        return json.loads(p.read_text(encoding='utf-8'))
    def dispatch(self,data):
        if not isinstance(data,dict):raise ValueError('attachment_invalid_request')
        action=data.get('operation');sid=data.get('session_id');uid=data.get('id')
        allowed={'operation','session_id','id'}|({'name','size'} if action=='begin' else {'offset','data','sha256'} if action=='chunk' else set())
        if set(data)-allowed or action not in {'begin','chunk','status','finish','remove'}:raise ValueError('attachment_invalid_request')
        folder=self.folder(sid,uid)
        with self.lock:
            if action=='begin':
                name=filename(data.get('name'));size=data.get('size')
                if type(size) is not int or not 0<=size<=MAX_FILE:raise ValueError('attachment_file_limit')
                mime=IMAGES.get(Path(name).suffix.lower(),'application/octet-stream')
                if mime.startswith('image/') and size>MAX_IMAGE:raise ValueError('attachment_image_limit')
                self.session(sid)
                if not mime.startswith('image/'):self.project(sid)
                if (folder/'meta.json').exists():
                    old=self.load(folder)
                    if old['name']!=name or old['size']!=size:raise ValueError('attachment_id_conflict')
                    return {'ok':True,'attachment':public(old)}
                total=0
                for path in self.root.glob('*/*/meta.json'):
                    if path.is_symlink():continue
                    meta=json.loads(path.read_text(encoding='utf-8'))
                    if meta.get('state') in ('uploading','ready') and time.time()-meta.get('touched',meta['created'])>86400:
                        (path.parent/'content').unlink(missing_ok=True);meta['state']='removed';self.save(path.parent,meta)
                    total+=meta['size'] if meta.get('state') not in ('removed','published') else 0
                if total+size>MAX_STAGING:raise ValueError('attachment_storage_limit')
                secure_dir(folder)
                meta={'id':uid,'session_id':sid,'name':name,'size':size,'offset':0,'state':'uploading','mime':mime,'created':time.time()}
                (folder/'content').touch(mode=0o600);self.save(folder,meta)
                return {'ok':True,'attachment':public(meta)}
            meta=self.load(folder)
            meta['touched']=time.time()
            if action=='status':
                self.save(folder,meta);return {'ok':True,'attachment':public(meta)}
            if action=='remove':
                if meta['state']=='published':raise ValueError('attachment_already_sent')
                (folder/'content').unlink(missing_ok=True);meta['state']='removed';self.save(folder,meta)
            elif action=='chunk':
                if meta['state']!='uploading':raise ValueError('attachment_not_uploading')
                offset=data.get('offset');raw=base64.b64decode(data.get('data',''),validate=True)
                if type(offset) is not int or not 0<=offset<=meta['size'] or not 0<len(raw)<=CHUNK or offset+len(raw)>meta['size']:
                    raise ValueError('attachment_chunk_limit')
                if hashlib.sha256(raw).hexdigest()!=data.get('sha256'):raise ValueError('attachment_integrity_failed')
                path=folder/'content'
                if path.is_symlink():raise ValueError('attachment_path_blocked')
                with path.open('r+b') as stream:
                    if offset<meta['offset']:
                        stream.seek(offset)
                        if stream.read(len(raw))!=raw:raise ValueError('attachment_chunk_conflict')
                    elif offset!=meta['offset']:raise ValueError('attachment_offset_mismatch')
                    else:
                        stream.seek(offset);stream.write(raw);stream.flush();os.fsync(stream.fileno())
                        meta['offset']+=len(raw);self.save(folder,meta)
            elif action=='finish':
                if meta['state'] in ('ready','published'):return {'ok':True,'attachment':public(meta)}
                if meta['state']!='uploading' or meta['offset']!=meta['size']:raise ValueError('attachment_incomplete')
                path=folder/'content';digest=hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda:stream.read(CHUNK),b''):digest.update(block)
                meta.update(state='ready',sha256=digest.hexdigest());self.save(folder,meta)
            return {'ok':True,'attachment':public(meta)}
    def content(self,sid,ids,text):
        if not isinstance(ids,list) or len(ids)>8 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):raise ValueError('attachment_count_limit')
        blocks=[{'type':'text','text':text}] if text else []
        with self.lock:
            for uid in ids:
                folder=self.folder(sid,uid);meta=self.load(folder)
                meta['touched']=time.time()
                if meta['state'] not in ('ready','published'):raise ValueError('attachment_not_ready')
                if meta['mime'].startswith('image/'):
                    body=(folder/'content').read_bytes()
                    if len(body)>MAX_IMAGE or hashlib.sha256(body).hexdigest()!=meta['sha256']:raise ValueError('attachment_integrity_failed')
                    from rich_content import mime_valid
                    if not mime_valid(meta['mime'],body[:32]):raise ValueError('attachment_image_invalid')
                    blocks.append({'type':'image','source':{'type':'base64','media_type':meta['mime'],'data':base64.b64encode(body).decode()}})
                    self.save(folder,meta)
                else:
                    project=self.project(sid)
                    destination=project/'.starship/attachments'/uid/meta['name']
                    for parent in (destination.parent,*destination.parent.parents):
                        if parent.is_symlink():raise ValueError('attachment_path_blocked')
                        if parent==project:break
                    secure_dir(destination.parent)
                    if destination.is_symlink():raise ValueError('attachment_path_blocked')
                    if meta['state']!='published':
                        if destination.exists():
                            digest=hashlib.sha256()
                            with destination.open('rb') as stream:
                                for chunk in iter(lambda:stream.read(CHUNK),b''):digest.update(chunk)
                            if digest.hexdigest()!=meta['sha256']:raise ValueError('attachment_destination_exists')
                            (folder/'content').unlink(missing_ok=True);meta['state']='published';self.save(folder,meta)
                    if meta['state']!='published':
                        # Copy across filesystems, verify before publishing, then remove the spool.
                        temp=destination.with_name('.incoming');digest=hashlib.sha256()
                        if temp.is_symlink():raise ValueError('attachment_path_blocked')
                        with (folder/'content').open('rb') as src,temp.open('wb') as dst:
                            for chunk in iter(lambda:src.read(CHUNK),b''):digest.update(chunk);dst.write(chunk)
                        if digest.hexdigest()!=meta['sha256']:temp.unlink();raise ValueError('attachment_integrity_failed')
                        temp.replace(destination);(folder/'content').unlink()
                        meta['state']='published';self.save(folder,meta)
                    blocks.append({'type':'text','text':'Attached file: '+destination.relative_to(project).as_posix()})
        return blocks
