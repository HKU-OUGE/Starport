"""Bounded read-only background synchronization, independent of browser lifetime."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time

SID = r'(?:cse_[A-Za-z0-9]{16,64}|[a-f0-9-]{36})'
KEY = re.compile(r'(?:sessions|health|(?:session|activity):' + SID + r')')
FILE = re.compile(r'[a-f0-9]{64}\.json')
ACTIVE = {'running', 'starting', 'waiting_approval'}


def valid(key, value):
    if not isinstance(key, str) or not KEY.fullmatch(key) or not isinstance(value, dict):
        return False
    if key == 'sessions':
        return isinstance(value.get('sessions'), list) and len(value['sessions']) <= 256 and all(isinstance(s, dict) and re.fullmatch(SID, str(s.get('id', ''))) for s in value['sessions'])
    if key == 'health':
        return isinstance(value.get('ok'), bool)
    kind, sid = key.split(':', 1)
    return (isinstance(value.get('events'), list) and len(value['events']) <= 200 and value.get('session', {}).get('id') == sid) if kind == 'session' else value.get('session_id') == sid and isinstance(value.get('activity'), dict)


def restored_value(key, value):
    """Restored content is readable, but never authority to send or approve."""
    value = {**value}
    def pending_session(session):
        return {**session, 'status': 'connecting', 'can_send': False, 'connected': False}
    if key == 'sessions':
        value['sessions'] = [pending_session(s) if s.get('kind') in ('remote', 'managed') else s for s in value['sessions']]
    elif key == 'health':
        value.update(ok=False, authenticated=False, remote_control_connected=0)
    elif key.startswith('session:'):
        value.update(session=pending_session(value['session']), pending=[], partial='')
    elif key.startswith('activity:'):
        value['activity'] = {**value['activity'], 'fresh': False, 'timer_running': False, 'active': False}
    return value


class SnapshotStore:
    def __init__(self, root, max_bytes=64*1024*1024, max_file_bytes=4*1024*1024, max_files=74, ttl=86400):
        self.root = Path(root)
        self.max_bytes, self.max_file_bytes, self.max_files, self.ttl = max_bytes, max_file_bytes, max_files, ttl
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or self.root.stat().st_uid != os.getuid():
            raise ValueError('invalid_snapshot_directory')
        self.root.chmod(0o700)
        self.hashes = {}

    def path(self, key):
        if not KEY.fullmatch(key):
            raise ValueError('invalid_snapshot_key')
        return self.root / (hashlib.sha256(key.encode()).hexdigest()+'.json')

    def files(self):
        result = []
        for p in self.root.iterdir():
            if not FILE.fullmatch(p.name):
                continue
            try:
                st = p.lstat()
                if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid():
                    result.append((p, st))
            except FileNotFoundError:
                pass
        return result

    def prune(self):
        files = sorted(self.files(), key=lambda row: row[1].st_mtime)
        size, count = sum(st.st_size for _, st in files), len(files)
        for p, st in files:
            if size > self.max_bytes or count > self.max_files or time.time()-st.st_mtime > self.ttl:
                p.unlink(missing_ok=True);self.hashes.pop(p.name, None)
                size -= st.st_size;count -= 1
        return {'bytes': size, 'files': count}

    def save(self, key, value, saved_at):
        if not valid(key, value) or not isinstance(saved_at, (float, int)):
            return
        p = self.path(key)
        if p.is_symlink():
            raise ValueError('invalid_snapshot_file')
        raw = json.dumps({'version': 1, 'key': key, 'value': value}, ensure_ascii=False, separators=(',', ':')).encode()
        if len(raw) > min(self.max_file_bytes, self.max_bytes):
            return
        digest = hashlib.sha256(raw).hexdigest()
        if self.hashes.get(p.name) == digest and p.exists():
            os.utime(p, (saved_at, saved_at), follow_symlinks=False)
        else:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix='.pending-', delete=False) as out:
                temporary = Path(out.name);out.write(raw)
            try:
                temporary.chmod(0o600);os.replace(temporary, p);os.utime(p, (saved_at, saved_at), follow_symlinks=False)
                self.hashes[p.name] = digest
            finally:
                temporary.unlink(missing_ok=True)
        self.prune()

    def load(self):
        self.prune()
        result = []
        for p, st in self.files():
            if st.st_size > self.max_file_bytes or st.st_mtime > time.time()+60:
                continue
            try:
                fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, 'rb') as stream:
                    raw = stream.read(self.max_file_bytes+1)
                data = json.loads(raw)
                key, value = data.get('key'), data.get('value')
                if len(raw) <= self.max_file_bytes and data.get('version') == 1 and valid(key, value) and p == self.path(key):
                    p.chmod(0o600);self.hashes[p.name] = hashlib.sha256(raw).hexdigest()
                    result.append((key, value, st.st_mtime))
            except (ValueError, OSError, AttributeError):
                continue
        return result

    def discard(self, key):
        p = self.path(key)
        if not p.is_symlink():
            p.unlink(missing_ok=True);self.hashes.pop(p.name, None)


class BackgroundSync:
    def __init__(self, reads, activities, root, max_sessions=16, max_activities=8):
        self.reads, self.activities = reads, activities
        self.store = SnapshotStore(root)
        self.max_sessions, self.max_activities = max_sessions, max_activities
        self.members, self.writers = {}, 0
        self.lock = threading.RLock()
        self.stopped = threading.Event();self.thread = None
        self.last_cycle = None;self.last_error = '';self.last_flush = 0
        self.disk = self.store.prune();self.restored_count = 0
        self.guard = None

    def restore(self):
        for key, value, stamp in self.store.load():
            value = restored_value(key, value)
            if key in ('sessions', 'health'):
                self.reads.seed(key, value, stamp)
            else:
                kind, sid = key.split(':', 1)
                (self.activities if kind == 'activity' else self.reads).seed(sid if kind == 'activity' else ('read', sid), value, stamp)
            self.restored_count += 1

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.guard = (self.store.root/'worker.lock').open('a')
        fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.restore()
        self.thread = threading.Thread(target=self.run, daemon=True, name='session-snapshot-sync');self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread:self.thread.join(timeout=3)
        self.flush()
        if self.guard:self.guard.close()

    def begin_write(self):
        with self.lock:self.writers += 1

    def end_write(self):
        with self.lock:self.writers = max(0, self.writers-1)

    def tick(self):
        with self.lock:
            if self.writers:return
        self.reads.get('sessions', {'action':'list'}, 10)
        self.reads.get('health', {'action':'health'}, 15)
        listing, sync = self.reads.peek('sessions')
        if listing and not sync.get('restored') and not sync.get('error'):
            rows = [s for s in listing.get('sessions', []) if isinstance(s, dict) and re.fullmatch(SID, str(s.get('id',''))) and (s.get('kind') == 'remote' or s.get('kind') == 'managed' and s.get('status') in ACTIVE | {'idle'})]
            rows.sort(key=lambda s:(s.get('status') not in ACTIVE, s.get('kind')!='remote', s.get('id')))
            members = {s['id']:s for s in rows[:self.max_sessions]}
            with self.lock:
                removed = set(self.members)-set(members);self.members = members
            for sid in removed:
                self.reads.discard(('read',sid));self.activities.discard(sid)
                self.store.discard('session:'+sid);self.store.discard('activity:'+sid)
        with self.lock:rows = list(self.members.values())
        # Oldest-due first prevents busy sessions from starving newly discovered ones.
        rows.sort(key=lambda s:self.reads.peek(('read',s['id']))[1].get('updated_at') or 0)
        inflight = sum(self.reads.peek(('read',s['id']))[1].get('refreshing',False) for s in rows)
        for s in rows:
            if inflight>=2:break
            sid=s['id'];value,meta=self.reads.peek(('read',sid))
            ttl=3 if s.get('status') in ACTIVE else 20
            if not meta.get('refreshing') and (value is None or meta.get('restored') or (meta.get('age_seconds') or 0)>=ttl):
                _,meta=self.reads.get(('read',sid),{'action':'read','session_id':sid,'after':0},ttl)
                inflight+=bool(meta.get('refreshing'))
        active_rows=sorted((s for s in rows if s.get('kind')=='remote'), key=lambda s:(s.get('status') not in ACTIVE,s['id']))[:self.max_activities]
        for s in active_rows:
            self.activities.get(s['id'],{'action':'read','session_id':s['id']},3 if s.get('status') in ACTIVE else 15)
        self.last_cycle=time.time()
        if time.monotonic()-self.last_flush>=15:self.flush()

    def flush(self):
        with self.lock:sids=list(self.members)
        items=[('sessions',self.reads.peek('sessions')),('health',self.reads.peek('health'))]
        for sid in sids:
            items.extend([('session:'+sid,self.reads.peek(('read',sid))),('activity:'+sid,self.activities.peek(sid))])
        for key,(value,sync) in items:
            if value is not None and not sync.get('restored') and not sync.get('error'):
                self.store.save(key,value,sync.get('updated_at'))
        self.disk=self.store.prune();self.last_flush=time.monotonic()

    def run(self):
        while not self.stopped.is_set():
            try:self.tick();self.last_error=''
            except Exception:self.last_error='background_sync_retrying'
            self.stopped.wait(1)

    def status(self):
        with self.lock:sids=list(self.members);writers=self.writers
        count=sum(self.reads.peek(('read',sid))[0] is not None for sid in sids)
        return {'running':bool(self.thread and self.thread.is_alive()), 'sessions':len(sids),'ready_sessions':count,
            'activity_sessions':sum(self.activities.peek(sid)[0] is not None for sid in sids),
            'last_cycle_at':self.last_cycle,'paused_for_send':bool(writers),'error':self.last_error,
            'memory_bytes':self.reads.summary()['bytes']+self.activities.summary()['bytes'],
            'disk_bytes':self.disk['bytes'],'disk_limit_bytes':self.store.max_bytes,'disk_files':self.disk['files'],
            'retention_hours':self.store.ttl//3600,'restored_snapshots':self.restored_count}
