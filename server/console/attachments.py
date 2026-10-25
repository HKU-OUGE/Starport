"""Typed relay for user-selected attachments; no server filesystem API."""
import base64,hashlib,re


def request(data,session_id):
    operation=data.get('operation')
    fields={'operation','session_id','id'}|({'name','size'} if operation=='begin' else {'offset','data'} if operation=='chunk' else set())
    if set(data)-fields or operation not in {'begin','chunk','status','finish','remove'}:raise ValueError('attachment_invalid_request')
    uid=data.get('id')
    if not isinstance(uid,str) or not re.fullmatch('[a-f0-9]{32}',uid):raise ValueError('attachment_invalid_id')
    clean={'operation':operation,'session_id':session_id(data.get('session_id')),'id':uid}
    if operation=='begin':
        name=data.get('name');size=data.get('size')
        if not isinstance(name,str) or not 1<=len(name)<=180 or type(size) is not int or not 0<=size<=128*1024*1024:raise ValueError('attachment_file_limit')
        clean.update(name=name,size=size)
    if operation=='chunk':
        raw=base64.b64decode(data.get('data',''),validate=True);offset=data.get('offset')
        if not 0<len(raw)<=256*1024 or type(offset) is not int or offset<0:raise ValueError('attachment_chunk_limit')
        clean.update(offset=offset,data=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
    return {'action':'attachment','request':clean}


def identifiers(value):
    if not isinstance(value,list) or len(value)>8 or any(not isinstance(x,str) or not re.fullmatch('[a-f0-9]{32}',x) for x in value) or len(set(value))!=len(value):
        raise ValueError('attachment_invalid_id')
    return value
