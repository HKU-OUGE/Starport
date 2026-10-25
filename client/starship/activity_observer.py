from bootstrap import RPCServer,ipc_stdio,secure_dir
"""Read-only activity observer. No message content or account material is returned."""
from pathlib import Path
from bootstrap import fcntl
import hashlib
import json
import os
import plistlib
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time

from activity_state import Activity
from remote_control import Controller
from artifact_reader import native_rpc

BASE = Path.home() / '.local/share/starship'
SOCKET = BASE / 'state/activity.rpc'
SID = re.compile(r'cse_[A-Za-z0-9]{16,64}')


class ReadOnlyController(Controller):
    def request(self, path, data=None, stream=False):
        if data is not None:
            raise ValueError('activity_read_only')
        return super().request(path, stream=stream)


class Watch:
    def __init__(self, controller, sid):
        self.controller, self.sid = controller, sid
        self.lock = threading.RLock()
        self.value = Activity()
        self.touched = time.monotonic()
        self.stop = threading.Event()
        self.stream = None
        self.source = 'remote_control_events'
        self.seen = set()
        self.seen_order = []
        self.counter = 0
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            item, _ = self.controller.get(self.sid, fresh=True)
            rows = self.controller.request('sessions/' + self.sid + '/events?limit=120&sort_order=desc').get('data', [])
            if not isinstance(rows, list):
                raise ValueError('activity_unavailable')
            with self.lock:
                for row in sorted(rows, key=lambda r: int(r.get('sequence_num') or 0)):
                    self.value.ingest(row)
                self.value.metadata(item)
            threading.Thread(target=self.follow, daemon=True).start()
            while time.monotonic() - self.touched < 90 and not self.stop.wait(5):
                try:
                    item, _ = self.controller.get(self.sid, fresh=True)
                    with self.lock:
                        self.value.metadata(item)
                except Exception:
                    with self.lock:
                        self.value.error = True
        except Exception:
            # A locked keychain must not require stopping the active service.
            # Reuse its authorized read protocol; never copy account material.
            self.source = 'native_service_events'
            self.value = Activity()
            while time.monotonic() - self.touched < 90 and not self.stop.is_set():
                try:
                    snapshot = native_rpc({'action': 'read', 'session_id': self.sid})
                    with self.lock:
                        self.ingest_native(snapshot)
                except Exception:
                    with self.lock:
                        self.value.error = True
                self.stop.wait(3)
        finally:
            self.stop.set()

    def ingest_native(self, snapshot):
        if snapshot.get('session', {}).get('id') != self.sid:
            raise ValueError('activity_session_mismatch')
        for row in snapshot.get('events', []):
            key = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            if key in self.seen:
                continue
            self.seen.add(key); self.seen_order.append(key)
            if len(self.seen_order) > 1024:
                self.seen.discard(self.seen_order.pop(0))
            role = row.get('role')
            if role in ('user', 'assistant'):
                payload = {'type': role, 'message': {'content': [{'type': 'text'}]}}
            elif role == 'tool':
                payload = {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': row.get('tool_id'), 'name': row.get('tool_name')}]}}
            elif role == 'tool_result':
                payload = {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': row.get('tool_id')}]}}
            elif role == 'result':
                payload = {'type': 'result', 'is_error': row.get('text') == 'Turn failed'}
            else:
                continue
            self.counter += 1
            self.value.ingest({'sequence_num': self.counter, 'created_at': row.get('time'), 'payload': payload})
        session = snapshot['session']
        self.value.waiting = {p['request_id']: p.get('tool', 'Tool') for p in snapshot.get('pending', []) if isinstance(p.get('request_id'), str)}
        if session.get('status') == 'running' and snapshot.get('partial'):
            self.value.phase = 'writing'
        self.value.stream_connected = bool(snapshot.get('stream_connected'))
        self.value.metadata({'connection_status': 'connected' if session.get('connected') else 'disconnected',
            'worker_status': {'running': 'running', 'idle': 'idle', 'waiting_approval': 'requires_action'}.get(session.get('status'), 'unknown')})
        # Completed text timestamps omit the earlier generation interval.
        self.value.basis = 'at_least'

    def follow(self):
        while not self.stop.is_set():
            try:
                with self.lock:
                    seq = self.value.sequence
                with self.controller.request('sessions/' + self.sid + '/events/stream?from_sequence_num=' + str(seq), stream=True) as stream:
                    with self.lock:
                        self.value.stream_connected = True
                    frame, size = [], 0
                    while not self.stop.is_set():
                        line = stream.readline(1024 * 1024 + 1)
                        if not line:
                            break
                        size += len(line)
                        if len(line) > 1024 * 1024 or size > 4 * 1024 * 1024:
                            raise ValueError('activity_frame_limit')
                        if line.startswith(b'data:'):
                            frame.append(line[5:].strip())
                        elif not line.strip():
                            if frame:
                                try:
                                    envelope = json.loads(b'\n'.join(frame))
                                    with self.lock:
                                        self.value.ingest(envelope, live=True)
                                except (ValueError, TypeError, KeyError):
                                    pass
                            frame, size = [], 0
            except Exception:
                pass
            finally:
                with self.lock:
                    self.value.stream_connected = False
            self.stop.wait(2)


class Observer:
    def __init__(self):
        self.controller = ReadOnlyController(lambda: {})
        self.watches = {}
        self.lock = threading.Lock()

    def read(self, request):
        if not isinstance(request, dict) or set(request) != {'action', 'session_id'} or request['action'] != 'read':
            raise ValueError('activity_request_invalid')
        sid = request['session_id']
        if not isinstance(sid, str) or not SID.fullmatch(sid):
            raise ValueError('activity_session_invalid')
        with self.lock:
            for key, watch in list(self.watches.items()):
                if watch.stop.is_set():
                    del self.watches[key]
            if sid not in self.watches:
                if len(self.watches) >= 8:
                    raise ValueError('activity_busy')
                self.watches[sid] = Watch(self.controller, sid)
            watch = self.watches[sid]
            watch.touched = time.monotonic()
        with watch.lock:
            return {'ok': True, 'session_id': sid, 'activity': {**watch.value.snapshot(), 'source': watch.source}}


def main():
    os.umask(0o077)
    mode=sys.argv[1] if len(sys.argv)==2 else ''
    if mode=='rpc':return ipc_stdio(SOCKET,max_request=4096,timeout=5,limit=16384)
    if mode!='serve':raise ValueError('invalid_service_mode')
    secure_dir(BASE/'state')
    guard=(BASE/'state/activity.lock').open('a+')
    fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    observer=Observer()
    def handle(data):
        return observer.read(data)
    with RPCServer(SOCKET,handle,max_request=4096,timeout=5) as listener:listener.serve_forever()

if __name__=='__main__':main()
