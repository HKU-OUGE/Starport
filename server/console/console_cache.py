"""Bounded, memory-only read snapshots and content-based event cursors."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import time


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


class ReadCache:
    """Slow remote reads never block a browser's other selected session.

    Only callers supply fixed read RPCs. Mutations must not use this cache.
    Refresh errors preserve the last snapshot, except explicit lost access.
    """
    def __init__(self, reader, max_entries=32, max_bytes=32 * 1024 * 1024, workers=4):
        self.reader = reader
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.entries = OrderedDict()
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='console-read')
        self.slots = threading.BoundedSemaphore(workers)

    def get(self, key, request, ttl):
        with self.lock:
            now = time.monotonic()
            if key not in self.entries:
                self._trim(reserve=1)
                if len(self.entries) >= self.max_entries:
                    return None, {'refreshing': True, 'age_seconds': None}
                self.entries[key] = {'value': None, 'at': 0, 'saved_at': None, 'restored': False, 'retry': 0, 'error': '',
                                     'inflight': False, 'generation': 0, 'size': 0}
            entry = self.entries[key]
            self.entries.move_to_end(key)
            if now - entry['at'] >= ttl and now >= entry['retry'] and not entry['inflight']:
                if self.slots.acquire(blocking=False):
                    entry['inflight'] = True
                    self.pool.submit(self._refresh, key, entry, dict(request), entry['generation'])
            return entry['value'], self._sync(entry)

    def _sync(self, entry):
        stamp = entry.get('saved_at')
        age = max(0, time.time() - stamp) if stamp else time.monotonic() - entry['at'] if entry['at'] else None
        return {'refreshing': entry['inflight'], 'error': entry['error'],
                'age_seconds': round(age, 1) if age is not None else None,
                'updated_at': stamp, 'restored': entry.get('restored', False)}

    def peek(self, key):
        """Read a snapshot without triggering any network work."""
        with self.lock:
            entry = self.entries.get(key)
            return (entry['value'], self._sync(entry)) if entry else (None, {})

    def seed(self, key, value, saved_at):
        with self.lock:
            size = len(encoded(value))
            if size > self.max_bytes:
                return
            self._trim(reserve=1)
            if len(self.entries) >= self.max_entries:
                return
            self.entries[key] = {'value': value, 'at': 0, 'saved_at': saved_at, 'restored': True,
                'retry': 0, 'error': '', 'inflight': False, 'generation': 0, 'size': size}

    def discard(self, key):
        with self.lock:
            self.entries.pop(key, None)

    def summary(self):
        with self.lock:
            return {'entries': len(self.entries), 'bytes': sum(e['size'] for e in self.entries.values()),
                    'inflight': sum(e['inflight'] for e in self.entries.values())}

    def _refresh(self, key, entry, request, generation):
        try:
            value = self.reader(request)
            size = len(encoded(value))
            if size > self.max_bytes:
                raise ValueError('remote_response_exceeds_limit')
            with self.lock:
                if self.entries.get(key) is entry and generation == entry['generation']:
                    entry.update(value=value, at=time.monotonic(), saved_at=time.time(), restored=False, failures=0, error='', retry=0, size=size)
        except Exception as exc:
            with self.lock:
                if self.entries.get(key) is entry and generation == entry['generation']:
                    error = str(exc)[:300] if isinstance(exc, ValueError) else 'starship_connection_unavailable'
                    failures = min(5, entry.get('failures', 0) + 1)
                    entry.update(error=error, failures=failures, retry=time.monotonic() + min(30, 3 * 2**(failures-1)))
                    if error in {'session_not_found', 'remote_session_not_found', 'login_required',
                                 'remote_login_required', 'remote_login_expired'}:
                        entry.update(value=None, size=0, at=0)
        finally:
            with self.lock:
                entry['inflight'] = False
                self._trim()
            self.slots.release()

    def _trim(self, reserve=0):
        for key, value in list(self.entries.items()):
            if len(self.entries) + reserve <= self.max_entries and sum(e['size'] for e in self.entries.values()) <= self.max_bytes:
                break
            if not value['inflight']:
                del self.entries[key]

    def invalidate(self):
        with self.lock:
            for entry in self.entries.values():
                entry.update(at=0, retry=0, generation=entry['generation'] + 1)


class DeltaStore:
    """Retain only bounded metadata/ID revisions, never duplicate event bodies."""
    def __init__(self, max_sessions=32, revisions=8, max_bytes=8 * 1024 * 1024):
        self.max_sessions, self.revisions, self.max_bytes = max_sessions, revisions, max_bytes
        self.sessions = OrderedDict()
        self.lock = threading.RLock()

    def response(self, sid, value, cursor=None):
        if value.get('session', {}).get('id') != sid:
            raise ValueError('remote_session_mismatch')
        metadata = {k: v for k, v in value.items() if k not in {'events', 'sync'}}
        events, counts = [], {}
        for raw in value.get('events', []):
            event = {k: v for k, v in raw.items() if k != '_event_id'}
            digest = hashlib.sha256(encoded(event)).hexdigest()[:32]
            count = counts.get(digest, 0)
            counts[digest] = count + 1
            events.append({**event, '_event_id': f'{digest}:{count}'})
        order = [e['_event_id'] for e in events]
        revision = {'metadata': metadata, 'order': order}
        revision_bytes = encoded(revision)
        latest = hashlib.sha256(revision_bytes).hexdigest()
        with self.lock:
            history = self.sessions.setdefault(sid, OrderedDict())
            self.sessions.move_to_end(sid)
            previous = history.get(cursor)
            history[latest] = (revision, len(revision_bytes))
            history.move_to_end(latest)
            while len(history) > self.revisions:
                history.popitem(last=False)
            while len(self.sessions) > self.max_sessions or sum(n for h in self.sessions.values() for _, n in h.values()) > self.max_bytes:
                self.sessions.popitem(last=False)
        base = {'delta': True, 'session_id': sid, 'cursor': latest, 'base_cursor': cursor,
                'reset': previous is None, 'unchanged': cursor == latest, 'events': [],
                'removed': [], 'changes': {}, 'removed_fields': []}
        if previous is None:
            return {**base, 'unchanged': False, 'events': events, 'order': order, 'changes': metadata}
        if cursor == latest:
            return base
        before = previous[0]
        old_ids, new_ids = set(before['order']), set(order)
        return {**base, 'order': order, 'events': [e for e in events if e['_event_id'] not in old_ids],
                'removed': [k for k in before['order'] if k not in new_ids],
                'changes': {k: v for k, v in metadata.items() if k not in before['metadata'] or v != before['metadata'][k]},
                'removed_fields': [k for k in before['metadata'] if k not in metadata]}
