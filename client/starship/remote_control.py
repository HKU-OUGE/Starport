"""Account-scoped controller for existing Remote Control sessions.

Native account material stays on this machine. The caller supplies reviewed
messages and opaque operation identifiers, never HTTP headers or endpoint URLs.
This protocol is an unsupported compatibility interface and fails closed.
"""
from collections import deque
from pathlib import Path
import hashlib
import json
import re
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from rich_content import MediaStore, blocks_to_events, bounded_events

BASE = Path.home() / '.local/share/starship'
API = 'https://api.anthropic.com/v1/code/'
MAX_BODY = 8 * 1024 * 1024
ID = re.compile(r'cse_[A-Za-z0-9]{16,64}')


def clean(value, limit=12000):
    text = str(value)
    text = re.sub(r'(?i)(Bearer\s+)[^\s"\x27]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(?:sk-(?:ant-)?|github_pat_|ghp_)[A-Za-z0-9_-]{16,}', '[REDACTED]', text)
    return text[:limit]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('remote_redirect_blocked')


class Controller:
    def __init__(self, local_index):
        self.local_index = local_index
        self.lock = threading.RLock()
        self.list_lock = threading.Lock()
        self.auth_lock = threading.Lock()
        self.cached_auth = None
        self.auth_at = 0
        self.list_at = 0
        self.sessions = {}
        self.streams = {}
        self.last_error = None
        self.media = MediaStore()

    def media_roots(self, sid):
        bound = self.bindings().get(sid, {})
        local = self.local_index().get(bound.get('local_id'), {})
        return [Path(local['cwd']), Path.home() / 'Starship-Training'] if local.get('cwd') else []

    def bindings(self):
        path = BASE / 'remote-control-bindings.json'
        if not path.is_file() or path.is_symlink():
            return {}
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or len(value) > 30:
            raise ValueError('remote_binding_invalid')
        local = self.local_index()
        return {key: item for key, item in value.items()
                if ID.fullmatch(key) and isinstance(item, dict) and item.get('local_id') in local}

    def credentials(self):
        with self.auth_lock:
            if self.cached_auth and time.monotonic() - self.auth_at < 20:
                return self.cached_auth
            candidates = []
            result = subprocess.run(['/usr/bin/security', 'find-generic-password',
                                     '-s', 'Claude Code-credentials', '-w'],
                                    capture_output=True, timeout=8)
            if result.returncode == 0:
                try:
                    candidates.append(json.loads(result.stdout).get('claudeAiOauth', {}))
                except ValueError:
                    pass
            path = Path.home() / '.claude/.credentials.json'
            if path.is_file() and not path.is_symlink():
                try:
                    candidates.append(json.loads(path.read_text()).get('claudeAiOauth', {}))
                except ValueError:
                    pass
            candidates = [x for x in candidates if isinstance(x, dict) and x.get('accessToken')]
            if not candidates:
                raise ValueError('remote_login_required')
            item = max(candidates, key=lambda x: x.get('expiresAt', 0))
            if item.get('expiresAt', 0) <= time.time() * 1000:
                raise ValueError('remote_login_expired')
            account = json.loads((Path.home() / '.claude.json').read_text()).get('oauthAccount', {})
            org = account.get('organizationUuid')
            if not org:
                raise ValueError('remote_login_required')
            self.cached_auth = (item['accessToken'], org)
            self.auth_at = time.monotonic()
            return self.cached_auth

    def request(self, path, data=None, stream=False):
        if not re.fullmatch(r'sessions(?:\?limit=\d+(?:&cursor=[A-Za-z0-9_%.-]+)?|/cse_[A-Za-z0-9]{16,64}(?:/events(?:/stream)?(?:\?[A-Za-z0-9_=&]+)?)?)?', path):
            raise ValueError('remote_endpoint_blocked')
        token, org = self.credentials()
        headers = {'Authorization': str.format('Bearer {}', token), 'x-organization-uuid': org,
                   'anthropic-version': '2023-06-01', 'User-Agent': 'Starship/1.0',
                   'Accept': 'text/event-stream' if stream else 'application/json'}
        if data is not None:
            # This is the controller event channel used by the web surface.
            # Do not forge server-asserted origin or device attestation fields.
            headers.update({'Content-Type': 'application/json', 'anthropic-client-platform': 'web_claude_ai'})
        raw = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        req = urllib.request.Request(API + path, data=raw, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        for attempt in range(2 if data is None and not stream else 1):
            try:
                response = opener.open(req, timeout=45 if stream else 12)
                if stream:
                    return response
                with response:
                    body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise ValueError('remote_response_exceeds_limit')
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise ValueError('remote_protocol_changed')
                return value
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    self.cached_auth = None
                    raise ValueError('remote_login_or_device_verification_required') from None
                if data is None and exc.code in (429, 502, 503, 504) and attempt == 0:
                    time.sleep(0.4)
                    continue
                raise ValueError('remote_http_' + str(exc.code)) from None
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError):
                if data is None and not stream and attempt == 0:
                    continue
                raise ValueError('remote_read_unavailable' if data is None else 'remote_send_outcome_unknown') from None
            except (json.JSONDecodeError, UnicodeError):
                raise ValueError('remote_protocol_changed') from None

    def listing(self, fresh=False):
        with self.list_lock:
            if not fresh and self.list_at and time.monotonic() - self.list_at < 10:
                return dict(self.sessions)
            rows, cursor = [], None
            for page in range(10):
                query = 'sessions?limit=100' + ('&cursor=' + urllib.parse.quote(str(cursor), safe='') if cursor else '')
                value = self.request(query)
                if not isinstance(value.get('data'), list):
                    raise ValueError('remote_protocol_changed')
                rows.extend(value['data'])
                cursor = value.get('next_cursor')
                if not cursor:
                    break
            else:
                raise ValueError('remote_session_list_exceeds_limit')
            self.sessions = {x['id']: x for x in rows if isinstance(x, dict) and ID.fullmatch(str(x.get('id', '')))
                             and x.get('environment_kind') == 'bridge' and x.get('status') == 'active'}
            self.list_at = time.monotonic()
            self.last_error = None
            return dict(self.sessions)

    def public(self, item, bound):
        connected = item.get('connection_status') == 'connected'
        state = {'running': 'running', 'idle': 'idle', 'requires_action': 'waiting_approval'}.get(item.get('worker_status'), 'starting')
        meta = item.get('external_metadata') or {}
        return {'id': item['id'], 'kind': 'remote', 'local_id': bound.get('local_id'),
                'title': clean(item.get('title') or bound.get('title') or 'Remote session', 120), 'project': bound.get('project', ''),
                'updated': item.get('last_event_at') or item.get('updated_at'),
                'status': state if connected else ('connecting' if item.get('connection_status') == 'connecting' else 'disconnected'), 'can_send': connected,
                'connected': connected, 'model': clean(meta.get('model', ''), 80),
                'effort': clean(meta.get('effort_level', ''), 30),
                # The folded flag_settings field also includes rejected changes.
                # It is not proof of the worker's live Ultracode setting.
                'ultracode': None, 'ultracode_source': 'not_reported',
                'official_url': 'https://claude.ai/code/' + item['id']}

    def list_public(self):
        bound = self.bindings()
        try:
            rows = self.listing()
            return [self.public(item, bound.get(key, {})) for key, item in rows.items()]
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            self.last_error = str(exc) if isinstance(exc, ValueError) else 'remote_read_unavailable'
            known = {sid: {**bound.get(sid, {}), 'title': item.get('title') or 'Remote session'} for sid, item in self.sessions.items()}
            if not known:
                known = bound
            return [{'id': sid, 'kind': 'remote', 'local_id': row.get('local_id'), 'title': clean(row['title'], 120),
                     'project': row.get('project', ''), 'status': 'disconnected', 'can_send': False,
                     'connected': False, 'notice': self.last_error} for sid, row in known.items()]

    def get(self, sid, fresh=False):
        if not ID.fullmatch(sid):
            raise ValueError('invalid_session')
        rows = self.listing()
        if sid not in rows:
            rows = self.listing(fresh=True)
        if sid not in rows:
            raise ValueError('remote_session_not_found')
        bound = self.bindings().get(sid, {})
        if not fresh:
            return rows[sid], bound
        value = self.request('sessions/' + sid)
        item = value.get('response_shape', value.get('session', value))
        if item.get('id') != sid or item.get('environment_kind') != 'bridge' or item.get('status') != 'active':
            raise ValueError('remote_protocol_changed')
        return item, bound

    def snapshot(self, sid):
        item, bound = self.get(sid)
        with self.lock:
            state = self.streams.get(sid)
            if state is None:
                state = StreamState(self, sid)
                self.streams[sid] = state
            state.touched = time.monotonic()
        state.seed()
        state.start()
        with state.lock:
            events = list(state.events)
            pending = [dict(x) for x in state.pending.values()] if item.get('worker_status') == 'requires_action' else []
            partial = state.partial
        visible = bounded_events(events[-200:])
        return {'session': self.public(item, bound), 'events': visible, 'partial': partial,
                'pending': pending, 'stream_connected': state.connected,
                'notice': state.error, 'source_process_present': True, 'history_limited': len(visible) < len(events)}

    def post(self, sid, payload, op):
        try:
            op = str(uuid.UUID(op))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('invalid_operation') from None
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.lock:
            with sqlite3.connect(BASE / 'state/remote-operations.sqlite') as db:
                db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,digest TEXT,status TEXT,result TEXT)')
                old = db.execute('SELECT digest,status,result FROM operations WHERE id=?', (op,)).fetchone()
                if old:
                    if old[0] != digest:
                        raise ValueError('operation_id_conflict')
                    if old[1] == 'accepted':
                        return {**json.loads(old[2]), 'duplicate': True}
                    raise ValueError('remote_send_outcome_unknown')
                db.execute('INSERT INTO operations VALUES(?,?,?,NULL)', (op, digest, 'pending'))
            result = self.request('sessions/' + sid + '/events', {'session_id': sid, 'events': [{'payload': {**payload, 'uuid': op}}]})
            if result.get('attestation_feedback'):
                raise ValueError('remote_device_verification_required')
            acknowledgements = result.get('results')
            if not isinstance(acknowledgements, list) or len(acknowledgements) != 1:
                raise ValueError('remote_send_outcome_unknown')
            ack = acknowledgements[0]
            public = {'accepted': True, 'delivery': 'queued', 'operation_id': op,
                      'event_id': ack.get('event_id'), 'sequence_num': ack.get('sequence_num'),
                      'duplicate': bool(ack.get('duplicate'))}
            with sqlite3.connect(BASE / 'state/remote-operations.sqlite') as db:
                db.execute('UPDATE operations SET status=?,result=? WHERE id=?', ('accepted', json.dumps(public), op))
            self.list_at = 0
            return public

    def dispatch(self, data):
        sid, action = data.get('session_id', ''), data.get('action')
        if action == 'read':
            return self.snapshot(sid)
        if action == 'media':
            if sid not in self.sessions and sid not in self.bindings():
                self.get(sid)
            return {'media': self.media.read(sid, data.get('media_id'), self.media_roots(sid), data.get('offset', 0), data.get('length', 0))}
        item, bound = self.get(sid, fresh=True)
        if item.get('connection_status') != 'connected':
            raise ValueError('remote_session_disconnected')
        op = data.get('operation_id', '')
        if action == 'send':
            text = data.get('message')
            if not isinstance(text, str) or not 1 <= len(text) <= 24000:
                raise ValueError('invalid_message')
            return self.post(sid, {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]}}, op)
        if action == 'stop':
            return self.post(sid, {'type': 'control_request', 'request_id': 'console-' + op,
                                   'request': {'subtype': 'interrupt'}}, op)
        if action == 'permission':
            snapshot = self.snapshot(sid)
            request = next((x for x in snapshot['pending'] if x['request_id'] == data.get('request_id')), None)
            if request is None:
                raise ValueError('permission_no_longer_pending')
            decision = data.get('decision')
            if decision not in ('allow', 'deny'):
                raise ValueError('invalid_decision')
            if decision == 'allow' and not request.get('reviewable', True):
                raise ValueError('remote_permission_requires_official_ui')
            original = request['input']
            if request['tool'] == 'AskUserQuestion' and decision == 'allow':
                answers = data.get('answers', {})
                if not isinstance(answers, dict) or set(answers) != {q.get('question') for q in original.get('questions', [])}:
                    raise ValueError('invalid_answer_targets')
                original = {**original, 'answers': answers}
            response = {'behavior': 'allow', 'updatedInput': original} if decision == 'allow' else {'behavior': 'deny', 'message': 'Declined by the user'}
            return self.post(sid, {'type': 'control_response', 'response': {'subtype': 'success', 'request_id': request['request_id'], 'response': response}}, op)
        raise ValueError('remote_action_blocked')


class StreamState:
    def __init__(self, controller, sid):
        self.controller, self.sid = controller, sid
        self.lock = threading.RLock()
        self.seed_lock = threading.Lock()
        self.thread = None
        self.events = deque(maxlen=200)
        self.pending = {}
        self.partial = ''
        self.sequence = 0
        self.seeded = False
        self.connected = False
        self.error = None
        self.touched = time.monotonic()

    def add(self, sequence, role, text, stamp, **extra):
        if text or extra.get('attachments'):
            self.events.append({'seq': sequence, 'role': role, 'text': clean(text, 64000), 'time': stamp, **extra})

    def ingest(self, envelope, ephemeral=False):
        p = envelope.get('payload', envelope)
        if not isinstance(p, dict):
            return
        sequence = int(envelope.get('sequence_num') or 0)
        with self.lock:
            if sequence and sequence <= self.sequence:
                return
            if sequence:
                self.sequence = sequence
            stamp = envelope.get('created_at') or p.get('timestamp')
            kind = p.get('type')
            if kind in ('user', 'assistant'):
                message = p.get('message') or {}
                blocks = message.get('content', [])
                store = self.controller.media if self.controller else None
                roots = self.controller.media_roots(self.sid) if self.controller else []
                for row in blocks_to_events(blocks, kind, store, self.sid, roots):
                    self.add(sequence, row.pop('role'), row.pop('text'), stamp, **row)
                if kind == 'assistant':
                    self.partial = ''
            elif kind == 'result':
                self.partial = ''
                self.pending.clear()
                self.add(sequence, 'result', 'Turn failed' if p.get('is_error') else 'Turn complete', stamp)
            elif kind == 'stream_event':
                event = p.get('event', {})
                delta = event.get('delta', {})
                if delta.get('type') == 'text_delta':
                    self.partial = clean((self.partial + delta.get('text', ''))[-24000:], 24000)
            elif kind == 'control_request' and envelope.get('source') == 'worker':
                request = p.get('request', {})
                rid = p.get('request_id')
                if request.get('subtype') == 'can_use_tool' and isinstance(rid, str):
                    raw = json.dumps(request.get('input', {}), ensure_ascii=False)
                    safe = clean(raw, 48000)
                    reviewable = raw == safe
                    self.pending[rid] = {'request_id': rid, 'tool': clean(request.get('tool_name', ''), 100),
                                         'input': request.get('input', {}) if reviewable else {'notice': 'Open the official session to review this request.'},
                                         'reviewable': reviewable}
            elif kind == 'control_response':
                response = p.get('response', {})
                self.pending.pop(response.get('request_id'), None)
                if response.get('subtype') == 'error':
                    self.add(sequence, 'notice', response.get('error', 'Control request failed'), stamp)
            elif kind == 'control_cancel_request':
                self.pending.pop(p.get('request_id'), None)
            elif kind == 'system' and p.get('subtype') == 'informational':
                self.add(sequence, 'notice', p.get('content', ''), stamp)

    def seed(self):
        with self.seed_lock:
            if self.seeded:
                return
            result = self.controller.request('sessions/' + self.sid + '/events?limit=120&sort_order=desc')
            rows = result.get('data')
            if not isinstance(rows, list):
                raise ValueError('remote_protocol_changed')
            for row in sorted(rows, key=lambda r: int(r.get('sequence_num') or 0)):
                self.ingest(row)
            self.seeded = True

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self.follow, daemon=True)
            self.thread.start()

    def follow(self):
        while time.monotonic() - self.touched < 60:
            try:
                with self.controller.request('sessions/' + self.sid + '/events/stream?from_sequence_num=' + str(self.sequence), stream=True) as stream:
                    self.connected, self.error = True, None
                    frame, count = [], 0
                    while time.monotonic() - self.touched < 60:
                        line = stream.readline(1024 * 1024 + 1)
                        if not line:
                            break
                        count += len(line)
                        if len(line) > 1024 * 1024 or count > 4 * 1024 * 1024:
                            raise ValueError('remote_stream_frame_exceeds_limit')
                        if line.startswith(b'data:'):
                            frame.append(line[5:].strip())
                        elif line.strip() == b'':
                            if frame:
                                try:
                                    self.ingest(json.loads(b'\n'.join(frame)), ephemeral=True)
                                except (ValueError, TypeError):
                                    pass
                            frame, count = [], 0
            except (ValueError, OSError, urllib.error.URLError):
                self.error = 'remote_stream_reconnecting'
            finally:
                self.connected = False
            time.sleep(2)
