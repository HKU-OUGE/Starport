import shutil
from bootstrap import bounded_process,WINDOWS
from starport_common.transport import command as remote_command
from bootstrap import owned, secure_dir
"""Authenticated console. Browser input never becomes a local command."""
from model_controls import checked_settings
from attachments import request as attachment_request, identifiers as attachment_ids
from settings import load as load_settings, state_path, validate as validate_settings
from collections import defaultdict, deque
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, quote
import base64
import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import select
import socket
import sqlite3
import subprocess
import threading
import time
import uuid

BASE = Path(__file__).resolve().parent
STATE = state_path()
from opencc import OpenCC
from console_cache import ReadCache, DeltaStore
from console_sync import BackgroundSync
from console_http import accepts_gzip, compress, asset_representation
from artifact_view import document_view
from artifact_exports import latest_export, export_bundle
from artifact_refresh import RefreshManager

CONVERT = OpenCC('s2t')
SESSIONS = {}
ATTEMPTS = defaultdict(deque)
AUTH_LOCK = threading.RLock()
OP_LOCK = threading.RLock()
CACHE_LOCK = threading.RLock()
CACHE = {}
HOST, PORT = '127.0.0.1', 18881
SSH_HOST = 'starship'
CLIENT_PLATFORM = 'posix'
MANAGEMENT_URL = ''
KEY = ''
MEDIA_SLOTS = threading.BoundedSemaphore(4)
MEDIA_CHUNK = 1024 * 1024
READS = ReadCache(lambda request: rpc(request), max_entries=48, max_bytes=48 * 1024 * 1024)
SYNC = None
DELTAS = DeltaStore()
ARTIFACT_READS = ReadCache(lambda request: rpc(request, artifact=True), max_entries=12, max_bytes=64 * 1024 * 1024, workers=2)
DOCUMENT_READS = ReadCache(lambda request: rpc(request, document=True), max_entries=16, max_bytes=4 * 1024 * 1024, workers=2)
ACTIVITY_READS = ReadCache(lambda request: rpc(request, activity=True), max_entries=16, max_bytes=128 * 1024, workers=2)
ARTIFACT_REFRESH = RefreshManager(STATE, lambda *a, **k: rpc(*a, **k), lambda request: rpc(request, artifact_sync=True), ARTIFACT_READS.invalidate)


def media_range(header, size):
    if not header:
        return 0, size - 1, 200
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError('invalid_range')
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        count = int(last)
        if count <= 0:
            raise ValueError('invalid_range')
        start, end = max(0, size - count), size - 1
    if start >= size or start > end:
        raise ValueError('invalid_range')
    return start, end, 206


def verified_media_chunk(sid, key, offset, length, document=False):
    request = {'action': 'media', 'session_id': sid, 'media_id': key, 'offset': offset, 'length': length}
    value = (rpc(request, document=True) if document else rpc(request))['media']
    body = base64.b64decode(value['data'], validate=True)
    if len(body) != length or value.get('offset') != offset or hashlib.sha256(body).hexdigest() != value.get('chunk_sha256'):
        raise ValueError('media_integrity_failed')
    return body


@contextmanager
def database():
    connection = sqlite3.connect(STATE / 'operations.sqlite', timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def ssh_options(data, *, activity=False, document=False, artifact_sync=False):
    if WINDOWS:return ['-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2']
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', str(STATE))) / 'starport-ssh'
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    if runtime.is_symlink() or not owned(runtime.stat()):
        raise ValueError('invalid_transport_directory')
    secure_dir(runtime)
    group = ('activity' if activity else 'sync' if artifact_sync else 'files' if document or data.get('action') in ('media','attachment')
             else 'write' if data.get('action') in {'send', 'stop', 'permission', 'create', 'model_settings'} else 'read')
    return ['-o', 'ControlMaster=auto', '-o', 'ControlPersist=300', '-o', 'ControlPath=' + str(runtime / (group + '-%C')),
            '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2']


def rpc(data, *, artifact=False, artifact_sync=False, document=False, activity=False, model_settings=False):
    # Only the request object crosses the boundary. No browser headers,
    # client addresses, local environment, or local paths are forwarded.
    endpoint = ('settings' if model_settings else 'activity' if activity else 'documents' if document
                else 'artifact-sync' if artifact_sync else 'artifacts' if artifact else 'session')
    command = [shutil.which('ssh') or 'ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ForwardAgent=no', '-o', 'SendEnv=-*',
               *ssh_options(data, activity=activity, document=document, artifact_sync=artifact_sync),
               SSH_HOST, remote_command(endpoint,CLIENT_PLATFORM)]
    output=bounded_process(command,json.dumps(data,ensure_ascii=False).encode()+b'\n',12 if activity else 70,
                           16384 if activity else (20 if artifact else 4)*1024*1024)
    try:
        value = json.loads(output)
    except ValueError:
        raise ValueError('invalid_remote_response') from None
    if not isinstance(value, dict) or not value.get('ok'):
        raise ValueError(str(value.get('error', 'remote_operation_failed'))[:300])
    return value


def cached_rpc(key, data, ttl):
    with CACHE_LOCK:
        old = CACHE.get(key)
    if old and time.monotonic() - old[0] < ttl:
        return old[1]
    result = rpc(data)
    with CACHE_LOCK:
        CACHE[key] = (time.monotonic(), result)
        while len(CACHE) > 64:
            CACHE.pop(next(iter(CACHE)))
    return result


def checked_text(text, limit=24000):
    if not isinstance(text, str) or not text.strip() or len(text) > limit:
        raise ValueError('invalid_text_length')
    # User-entered console text is converted, not content-filtered.
    return CONVERT.convert(text)


def checked_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid_identifier') from None


def checked_session_id(value):
    if isinstance(value, str) and re.fullmatch(r'cse_[A-Za-z0-9]{16,64}', value):
        return value
    return checked_id(value)


def action_payload(data):
    action = data.get('action')
    if action == 'model_settings':
        return checked_settings(data)
    if action == 'create':
        result = {'action': action, 'title': checked_text(data.get('title'), 100),
                  'model': data.get('model', 'opus'), 'effort': data.get('effort', 'xhigh')}
        if result['model'] not in {'opus', 'sonnet', 'haiku'} or result['effort'] not in {'low', 'medium', 'high', 'xhigh', 'max'}:
            raise ValueError('invalid_model_settings')
        if data.get('source_session'):
            result['source_session'] = checked_id(data['source_session'])
        if data.get('project_id'):
            if not re.fullmatch(r'[0-9a-f]{16}', data['project_id']):
                raise ValueError('invalid_project')
            result['project_id'] = data['project_id']
        return result
    if action in {'send', 'stop', 'permission'}:
        result = {'action': action, 'session_id': checked_session_id(data.get('session_id'))}
        if action == 'send':
            ids=attachment_ids(data.get('attachments',[]))
            result['message'] = '' if ids and not data.get('message') else checked_text(data.get('message'))
            if ids:result['attachments']=ids
        if action == 'permission':
            if data.get('decision') not in {'allow', 'deny'}:
                raise ValueError('invalid_decision')
            rid = data.get('request_id')
            if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', rid):
                raise ValueError('invalid_permission_request')
            result.update(request_id=rid, decision=data['decision'])
            if data.get('answers'):
                if not isinstance(data['answers'], dict) or len(data['answers']) > 8:
                    raise ValueError('invalid_answers')
                result['answers'] = {str(k)[:2000]: checked_text(v, 4000) for k, v in data['answers'].items()}
        return result
    raise ValueError('unknown_action')


def perform(payload, operation_id):
    operation_id = checked_id(operation_id)
    signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with OP_LOCK, database() as db:
        row = db.execute('SELECT * FROM operations WHERE id=?', (operation_id,)).fetchone()
        if row:
            if row['digest'] != signature:
                raise ValueError('operation_id_conflict')
            if row['status'] == 'complete':
                return {**json.loads(row['result']), 'duplicate': True}
            raise ValueError('operation_pending_or_outcome_unknown')
        db.execute('INSERT INTO operations(id,digest,action,session_id,status,created) VALUES(?,?,?,?,?,?)',
                   (operation_id, signature, payload['action'], payload.get('session_id', ''), 'started', time.time()))
    if SYNC:
        SYNC.begin_write()
    try:
        started = time.monotonic()
        result = rpc({**payload, 'operation_id': operation_id}, model_settings=True) if payload['action'] == 'model_settings' else rpc({**payload, 'operation_id': operation_id})
        result = {**result, 'timing': {'starship_roundtrip_ms': round((time.monotonic() - started) * 1000)}}
    except ValueError as exc:
        with OP_LOCK, database() as db:
            known_rejections = {'login_required', 'session_busy', 'source_session_still_running', 'invalid_message', 'invalid_operation', 'session_not_found', 'invalid_model_settings', 'project_unavailable', 'permission_no_longer_pending', 'model_settings_changed', 'model_settings_pending', 'model_settings_ultracode_unknown', 'model_settings_remote_only'}
            known_rejections.update({'remote_session_disconnected', 'remote_session_not_bound', 'remote_session_not_found', 'remote_login_required', 'remote_login_expired', 'remote_login_or_device_verification_required', 'remote_permission_requires_official_ui'})
            db.execute('UPDATE operations SET status=?,error=? WHERE id=?', ('rejected' if str(exc) in known_rejections else 'unknown', str(exc), operation_id))
        raise
    finally:
        if SYNC:
            SYNC.end_write()
    with OP_LOCK, database() as db:
        db.execute('UPDATE operations SET status=?,result=? WHERE id=?', ('complete', json.dumps(result), operation_id))
    with CACHE_LOCK:
        CACHE.clear()
    READS.invalidate()
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = 'Starport'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(90)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, *_):
        pass

    def respond(self, status, value, cookie=None, content_type='application/json; charset=utf-8', cache_control='no-store', representation=None, download_name=None):
        if representation is None:
            raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
            raw, coding = compress(raw, content_type, accepts_gzip(self.headers.get('Accept-Encoding')))
            etag = None
        else:
            raw, coding, etag = representation
            if self.headers.get('If-None-Match') == etag:
                status = 304
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', cache_control)
        self.send_header('Vary', 'Accept-Encoding')
        if download_name:
            self.send_header('Content-Disposition', 'attachment; filename="'+download_name+'"')
        if coding:
            self.send_header('Content-Encoding', coding)
        if etag:
            self.send_header('ETag', etag)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; worker-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data: blob:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD' and status != 304:
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    def read_background(self, key, request, ttl):
        value, sync = READS.get(key, request, ttl)
        return {**(value or {'loading': True}), 'sync': sync}

    def origin_ok(self, mutation=False):
        # Each listener has exactly one allowed browser origin. Forwarded
        # headers never turn a private HTTP request into a trusted HTTPS one.
        expected_origin = getattr(self.server, 'external_origin', f'http://{HOST}:{PORT}')
        expected = urlsplit(expected_origin).netloc
        if getattr(self.server, 'tunnel_only', False) and self.client_address[0] != '127.0.0.1':
            return False
        if self.headers.get_all('Host', []) != [expected]:
            return False
        origins = self.headers.get_all('Origin', [])
        if origins and origins != [expected_origin]:
            return False
        return not mutation or origins == [expected_origin]

    def session_cookie(self, token, max_age=43200):
        origin = getattr(self.server, 'external_origin', f'http://{HOST}:{PORT}')
        secure = '; Secure' if origin.startswith('https://') else ''
        return f'console_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}{secure}'

    def identity(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookie['console_session'].value
        except Exception:
            return None
        with AUTH_LOCK:
            identity = SESSIONS.get(token)
            if identity and identity['expires'] > time.time():
                return identity
        return None

    def serve_media(self, path, query, head=False):
        document = path.startswith('/api/documents/')
        match = re.fullmatch(r'/api/(?:media|documents)/([^/]+)/([a-f0-9]{64})', path)
        if not match:
            return self.respond(404, {'error': 'media_not_found'})
        if not MEDIA_SLOTS.acquire(blocking=False):
            return self.respond(503, {'error': 'media_busy'})
        sent = False
        try:
            sid, key = checked_session_id(match[1]), match[2]
            request = {'action': 'media', 'session_id': sid, 'media_id': key}
            info = (rpc(request, document=True) if document else rpc(request))['media']
            size = info['size']
            try:
                start, end, status = media_range(self.headers.get('Range'), size)
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', 'bytes */' + str(size))
                self.send_header('Content-Length', '0')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return
            first = b'' if head or end < start else verified_media_chunk(sid, key, start, min(MEDIA_CHUNK, end - start + 1), document)
            self.send_response(status)
            self.send_header('Content-Type', info['mime'])
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            disposition = 'attachment' if parse_qs(query).get('download') == ['1'] else 'inline'
            self.send_header('Content-Disposition', disposition + "; filename*=UTF-8''" + quote(info['name'], safe=''))
            self.send_header('Cache-Control', 'private, no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "default-src 'none'; media-src 'self'; img-src 'self'; sandbox")
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            sent = True
            if head:
                return
            self.wfile.write(first)
            offset = start + len(first)
            while offset <= end:
                chunk = verified_media_chunk(sid, key, offset, min(MEDIA_CHUNK, end - offset + 1), document)
                self.wfile.write(chunk)
                offset += len(chunk)
        except (ValueError, OSError, KeyError):
            if not sent:
                self.respond(404, {'error': 'media_unavailable'})
            else:
                self.close_connection = True
        finally:
            MEDIA_SLOTS.release()

    def do_HEAD(self):
        return self.do_GET()

    def do_GET(self):
        if not self.origin_ok():
            return self.respond(403, {'error': 'invalid_origin'})
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == '/health':
            return self.respond(200, {'ok': True})
        if path.startswith('/api/'):
            identity = self.identity()
            if identity is None:
                return self.respond(401, {'error': 'authentication_required'})
            try:
                if path == '/api/me':
                    result = {'ok': True, 'csrf': identity['csrf'], 'management_url': MANAGEMENT_URL}
                elif path == '/api/status':
                    result = self.read_background('health', {'action': 'health'}, 10) if SYNC or query.get('background') == ['1'] else cached_rpc('health', {'action': 'health'}, 10)
                    if SYNC:
                        result['background_sync'] = SYNC.status()
                elif path == '/api/sessions':
                    result = self.read_background('sessions', {'action': 'list'}, 5) if SYNC or query.get('background') == ['1'] else cached_rpc('sessions', {'action': 'list'}, 3)
                elif path == '/api/projects':
                    result = cached_rpc('projects', {'action': 'projects'}, 15)
                elif path.startswith('/api/model-settings/'):
                    sid = checked_session_id(path.removeprefix('/api/model-settings/'))
                    if not sid.startswith('cse_'):
                        raise ValueError('model_settings_remote_only')
                    result = rpc({'action': 'read', 'session_id': sid}, model_settings=True)
                elif path.startswith('/api/activity/'):
                    match = re.fullmatch(r'/api/activity/(cse_[A-Za-z0-9]{16,64})', path)
                    if not match:
                        return self.respond(404, {'error': 'activity_not_supported'})
                    sid = checked_session_id(match[1])
                    value, sync = ACTIVITY_READS.get(sid, {'action': 'read', 'session_id': sid}, 1)
                    result = {**value, 'sync': sync} if value is not None else {'session_id': sid, 'loading': True, 'sync': sync}
                elif path.startswith('/api/documents/'):
                    match = re.fullmatch(r'/api/documents/([^/]+)', path)
                    if match:
                        sid = checked_session_id(match[1])
                        value, sync = DOCUMENT_READS.get(sid, {'action': 'list', 'session_id': sid}, 10)
                        result = {**value, 'sync': sync} if value is not None else {'session_id': sid, 'loading': True, 'sync': sync}
                    else:
                        return self.serve_media(path, parsed.query, head=self.command == 'HEAD')
                elif path == '/api/artifact-exports':
                    result = {'ok': True, 'export': latest_export(STATE), 'refresh': ARTIFACT_REFRESH.status()}
                elif path.startswith('/api/artifact-exports/'):
                    match = re.fullmatch(r'/api/artifact-exports/([a-f0-9]{32})/download', path)
                    if not match:
                        return self.respond(404, {'error': 'artifact_export_not_found'})
                    raw = export_bundle(STATE, match[1])
                    return self.respond(200, raw, content_type='application/zip', download_name='Claude-Artifacts-'+match[1][:8]+'.zip')
                elif path.startswith('/api/artifacts/'):
                    match = re.fullmatch(r'/api/artifacts/([^/]+)(?:/([A-Za-z0-9_-]{12,80}))?', path)
                    if not match:
                        return self.respond(404, {'error': 'artifact_not_found'})
                    sid = checked_session_id(match[1])
                    aid = match[2]
                    request = {'action': 'read' if aid else 'list', 'session_id': sid}
                    if aid:
                        request['artifact_id'] = aid
                    value, sync = ARTIFACT_READS.get((sid, aid), request, 30)
                    if value is None:
                        result = {'loading': True, 'sync': sync}
                    elif aid:
                        result = {'ok': True, 'session_id': sid, 'artifact': document_view(value['artifact']), 'sync': sync}
                    else:
                        result = {**value, 'sync': sync}
                elif path.startswith('/api/media/'):
                    return self.serve_media(path, parsed.query, head=self.command == 'HEAD')
                elif path.startswith('/api/session/'):
                    sid = checked_session_id(path.removeprefix('/api/session/'))
                    if query.get('delta') == ['1']:
                        cursor = query.get('cursor', [None])[0]
                        if cursor is not None and not re.fullmatch(r'[a-f0-9]{64}', cursor):
                            raise ValueError('invalid_cursor')
                        value, sync = READS.get(('read', sid), {'action': 'read', 'session_id': sid, 'after': 0}, 2)
                        result = {**DELTAS.response(sid, value, cursor), 'sync': sync} if value is not None else {'session_id': sid, 'loading': True, 'sync': sync}
                    else:
                        after = int(query.get('after', ['0'])[0])
                        result = cached_rpc(('read', sid, after), {'action': 'read', 'session_id': sid, 'after': max(0, after)}, 1)
                elif path == '/api/operations':
                    with database() as db:
                        rows = [dict(row) for row in db.execute('SELECT id,action,session_id,status,created,error,result FROM operations ORDER BY created DESC LIMIT 60')]
                    for row in rows:
                        payload = json.loads(row.pop('result') or '{}')
                        timing = payload.get('timing', {})
                        row['duration_ms'] = timing.get('starship_roundtrip_ms', timing.get('mac_roundtrip_ms'))
                    result = {'operations': rows}
                else:
                    return self.respond(404, {'error': 'not_found'})
                return self.respond(200, result)
            except (ValueError, OSError) as exc:
                return self.respond(503, {'error': str(exc)[:300]})
        if path == '/':
            file = BASE / 'web/dist/index.html'
        elif re.fullmatch(r'/assets/[A-Za-z0-9_.-]+', path):
            file = BASE / 'web/dist' / path.lstrip('/')
        elif re.fullmatch(r'/pdf-assets/6\.4\.299/(?:cmaps/[A-Za-z0-9_-]+\.bcmap|standard_fonts/[A-Za-z0-9_.-]+\.(?:pfb|ttf))', path):
            file = BASE / 'web/dist' / path.lstrip('/')
        else:
            return self.respond(404, {'error': 'not_found'})
        if not file.is_file() or file.is_symlink():
            return self.respond(404, {'error': 'not_found'})
        content_type = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        if path.startswith('/assets/') and re.search(r'-[A-Za-z0-9_-]{8,}\.[a-z0-9]+$', file.name):
            stat = file.stat()
            representation = asset_representation(str(file), stat.st_mtime_ns, stat.st_size, content_type, accepts_gzip(self.headers.get('Accept-Encoding')))
            return self.respond(200, b'', content_type=content_type, cache_control='public, max-age=31536000, immutable', representation=representation)
        return self.respond(200, file.read_bytes(), content_type=content_type)

    def do_POST(self):
        if not self.origin_ok(mutation=True):
            self.close_connection = True
            return self.respond(403, {'error': 'invalid_origin'})
        try:
            if self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                return self.respond(400, {'error': 'invalid_body'})
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= (512 * 1024 if self.path=='/api/attachments' else 128 * 1024) or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.close_connection = True
                return self.respond(400, {'error': 'invalid_body'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('invalid_body')
        except (ValueError, UnicodeError):
            self.close_connection = True
            return self.respond(400, {'error': 'invalid_body'})
        if self.path == '/api/login':
            with AUTH_LOCK:
                key = self.client_address[0]
                attempts = ATTEMPTS[key]
                while attempts and attempts[0] < time.time() - 60:
                    attempts.popleft()
                if len(attempts) >= 8:
                    return self.respond(429, {'error': 'too_many_attempts'})
                attempts.append(time.time())
                submitted = str(data.get('key', ''))[:200]
                if not hmac.compare_digest(submitted, KEY):
                    return self.respond(401, {'error': 'invalid_access_code'})
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
                SESSIONS[token] = {'csrf': csrf, 'expires': time.time() + 12 * 3600}
                for old in list(SESSIONS):
                    if SESSIONS[old]['expires'] <= time.time():
                        SESSIONS.pop(old, None)
                return self.respond(200, {'ok': True, 'csrf': csrf}, cookie=self.session_cookie(token))
        identity = self.identity()
        if not identity or not hmac.compare_digest(self.headers.get('X-Console-CSRF', ''), identity['csrf']):
            return self.respond(403, {'error': 'invalid_session'})
        try:
            if self.path == '/api/attachments':
                result=rpc(attachment_request(data,checked_session_id))
            elif self.path == '/api/artifact-exports/refresh':
                if data:
                    raise ValueError('invalid_body')
                result = {'ok': True, 'refresh': ARTIFACT_REFRESH.start()}
            elif self.path == '/api/preview':
                result = {'ok': True, 'payload': action_payload(data)}
            elif self.path == '/api/action':
                payload = action_payload(data)
                # The owner explicitly removed message previews. Other actions
                # still require confirmation; older message clients remain valid.
                needs_confirmation = payload['action'] not in ('send', 'model_settings') or 'confirmed_digest' in data
                if needs_confirmation and data.get('confirmed_digest') != hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest():
                    raise ValueError('preview_confirmation_required')
                result = perform(payload, data.get('operation_id'))
            elif self.path == '/api/logout':
                with AUTH_LOCK:
                    SESSIONS.pop(SimpleCookie(self.headers['Cookie'])['console_session'].value, None)
                return self.respond(200, {'ok': True}, cookie=self.session_cookie('', max_age=0))
            else:
                return self.respond(404, {'error': 'not_found'})
            if self.path == '/api/preview':
                result['digest'] = hashlib.sha256(json.dumps(result['payload'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            return self.respond(200, result)
        except ValueError as exc:
            return self.respond(409, {'error': str(exc)[:300]})


def main():
    global HOST, PORT, KEY, SYNC, SSH_HOST, STATE, ARTIFACT_REFRESH, MANAGEMENT_URL, CLIENT_PLATFORM
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', help='External Starport console configuration')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--public-origin', help='Exact HTTPS origin accepted only on the loopback tunnel listener')
    parser.add_argument('--tunnel-port', type=int)
    args = parser.parse_args()
    config = load_settings(args.config)
    config.update({k: getattr(args, k) for k in ('host', 'port', 'public_origin', 'tunnel_port') if getattr(args, k) is not None})
    config = validate_settings(config)
    HOST, PORT, SSH_HOST = config['host'], config['port'], config['ssh_host']
    CLIENT_PLATFORM=config['client_platform']
    MANAGEMENT_URL = config['management_url']
    STATE = Path(config['state_dir'])
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.is_symlink() or not owned(STATE.stat()):
        raise ValueError('invalid_state_directory')
    secure_dir(STATE)
    ARTIFACT_REFRESH = RefreshManager(STATE, lambda *a, **k: rpc(*a, **k), lambda request: rpc(request, artifact_sync=True), ARTIFACT_READS.invalidate)
    key_file = STATE / 'access.key'
    if key_file.is_symlink():
        raise ValueError('invalid_access_key_file')
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(24), encoding='utf-8')
    key_file.chmod(0o600)
    KEY = key_file.read_text(encoding='utf-8').strip()
    if len(KEY) < 24:
        raise ValueError('access_key_too_short')
    with database() as db:
        db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,digest TEXT,action TEXT,session_id TEXT,status TEXT,created REAL,result TEXT,error TEXT)')
    private = ThreadingHTTPServer((HOST, PORT), Handler)
    private.external_origin = f'http://{HOST}:{PORT}'
    tunnel = None
    SYNC = BackgroundSync(READS, ACTIVITY_READS, STATE / 'session-snapshots')
    SYNC.start()
    try:
        if config['public_origin']:
            # cloudflared validates the Access JWT before it reaches this
            # loopback listener. Both entries share sessions and operation IDs.
            tunnel = ThreadingHTTPServer(('127.0.0.1', config['tunnel_port']), Handler)
            tunnel.external_origin = config['public_origin']
            tunnel.tunnel_only = True
            threading.Thread(target=tunnel.serve_forever, daemon=True).start()
        private.serve_forever()
    finally:
        if SYNC:
            SYNC.stop()
        if tunnel:
            tunnel.shutdown()
            tunnel.server_close()
        private.server_close()


if __name__ == '__main__':
    main()
