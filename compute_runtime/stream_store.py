"""Sequence-addressed recent log plus bounded compressed durable segments."""
import contextlib
import codecs
import fcntl
import gzip
import json
import os
import base64
from pathlib import Path

RECENT = 16 * 1024 * 1024
SEGMENT = 4 * 1024 * 1024
ARCHIVE_LIMIT = 256 * 1024 * 1024


@contextlib.contextmanager
def lock(root):
    root = Path(root)
    (root/'logs').mkdir(exist_ok=True)
    with (root/'logs/lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield root/'logs'


def metadata(d):
    p=d/'index.json'
    return json.loads(p.read_text()) if p.exists() else {'cursor':0,'base':0,'segment':0,'archive_bytes':0,'archive_capped':False}


def save_metadata(d,value):
    temp=d/'index.new'
    temp.write_text(json.dumps(value));temp.replace(d/'index.json')


def append(root, data):
    if not data:return
    with lock(root) as d:
        m=metadata(d);recent=d/'recent.log'
        end=m['cursor']+len(data);tail=data[-RECENT:];position=(end-len(tail))%RECENT
        with recent.open('r+b' if recent.exists() else 'w+b') as f:
            first=min(len(tail),RECENT-position);f.seek(position);f.write(tail[:first])
            if first<len(tail):f.seek(0);f.write(tail[first:])
        m['cursor']=end;m['base']=max(0,end-RECENT)
        if not m['archive_capped']:
            part=d/'current.log'
            with part.open('ab') as f:f.write(data)
            if part.stat().st_size>=SEGMENT:
                packed=gzip.compress(part.read_bytes(),compresslevel=3)
                if m['archive_bytes']+len(packed)<=ARCHIVE_LIMIT:
                    (d/f"part-{m['segment']:05d}.log.gz").write_bytes(packed)
                    m['segment']+=1;m['archive_bytes']+=len(packed)
                else:m['archive_capped']=True
                part.unlink()
        save_metadata(d,m)


def read(root, cursor=None, limit=RECENT):
    with lock(root) as d:
        m=metadata(d);p=d/'recent.log'
        start=m['base'] if cursor is None else max(m['base'],min(cursor,m['cursor']))
        if not p.exists():raw=b''
        else:
            size=min(RECENT,limit,m['cursor']-start);position=start%RECENT
            with p.open('rb') as f:
                f.seek(position);raw=f.read(min(size,RECENT-position))
                if len(raw)<size:f.seek(0);raw+=f.read(size-len(raw))
        decoder=codecs.getincrementaldecoder('utf-8')('replace');text=decoder.decode(raw)
        pending=decoder.getstate()[0]
        return {'text':text,'cursor':start+len(raw)-len(pending),
                'base_cursor':max(m['base'],m['cursor']-RECENT),'truncated':cursor is not None and cursor<m['base'],
                'archive_capped':m['archive_capped'],'recent_limit_bytes':RECENT}


def chunk(root, cursor, limit=256*1024):
    with lock(root) as d:
        m=metadata(d);start=max(m['base'],min(cursor,m['cursor']))
        size=min(limit,m['cursor']-start);position=start%RECENT
        if size:
            with (d/'recent.log').open('rb') as f:
                f.seek(position);raw=f.read(min(size,RECENT-position))
                if len(raw)<size:f.seek(0);raw+=f.read(size-len(raw))
        else:raw=b''
        return {'start':start,'end':start+len(raw),'total':m['cursor'],
                'data':base64.b64encode(raw).decode(),'truncated':cursor<m['base'],
                'archive_capped':m['archive_capped']}


def mirror(root, value):
    """Apply remote byte offsets idempotently, including after a local crash."""
    raw=base64.b64decode(value['data'],validate=True)
    start=value['start'];end=value['end']
    if (type(start) is not int or type(end) is not int or start<0 or
            end-start!=len(raw) or len(raw)>256*1024):raise ValueError('invalid_log_chunk')
    with lock(root) as d:
        m=metadata(d);cursor=m['cursor']
        if end<=cursor:return cursor
        if start<cursor:raw=raw[cursor-start:];start=cursor
        if start>cursor:m['base']=start;m['gap']=True
        position=start%RECENT;p=d/'recent.log'
        with p.open('r+b' if p.exists() else 'w+b') as f:
            first=min(len(raw),RECENT-position);f.seek(position);f.write(raw[:first])
            if first<len(raw):f.seek(0);f.write(raw[first:])
            f.flush();os.fsync(f.fileno())
        m.update(cursor=end,base=max(m['base'],end-RECENT),
                 archive_capped=bool(value.get('archive_capped')))
        save_metadata(d,m)
        return end


def cursor(root):
    with lock(root) as d:return metadata(d)['cursor']


def finish(root):
    with lock(root) as d:
        m=metadata(d);p=d/'current.log'
        if p.exists() and p.stat().st_size:
            packed=gzip.compress(p.read_bytes(),compresslevel=3)
            if m['archive_bytes']+len(packed)<=ARCHIVE_LIMIT:
                (d/f"part-{m['segment']:05d}.log.gz").write_bytes(packed)
                m['segment']+=1;m['archive_bytes']+=len(packed)
            else:m['archive_capped']=True
            p.unlink()
        save_metadata(d,m)
