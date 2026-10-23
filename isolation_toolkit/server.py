from collections import defaultdict, deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import hmac
import ipaddress
import json
import secrets
import sqlite3
import threading
import time
import uuid
from .config import ROOT
from .operations import Manager, monitor
from .subscriptions import Subscriptions


def serve(config):
    state = Path(config['state_dir']); state.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = Path(config['access_key_file']).read_text().strip()
    if len(key) < 24:
        raise ValueError('access_key_too_short')
    manager = Manager(config)
    subscriptions = Subscriptions(config)
    sessions, attempts = {}, defaultdict(deque)
    lock, action_lock = threading.RLock(), threading.Lock()
    hosts = {f'{h}:{config["port"]}' for h in config['listen']} | {f'localhost:{config["port"]}'}
    nets = [ipaddress.ip_network(n) for n in config.get('allowed_subnets', ['127.0.0.0/8'])]
    with sqlite3.connect(state / 'actions.sqlite') as db:
        db.execute('CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,digest TEXT,status TEXT,result TEXT,created REAL)')
    monitor_cache = [0, None]
    monitor_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'IsolationToolkit'
        sys_version = ''

        def log_message(self, *_):
            pass

        def respond(self, code, value, mime='application/json; charset=utf-8', cookie=None):
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            try:
                self.send_response(code)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                if cookie:
                    self.send_header('Set-Cookie', cookie)
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def boundary(self, mutation=False):
            peer = ipaddress.ip_address(self.client_address[0])
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            return host in hosts and any(peer in n for n in nets) and (origin == 'http://' + host if mutation else origin in (None, 'http://' + host))

        def identity(self):
            try:
                token = SimpleCookie(self.headers.get('Cookie', ''))['isolation_session'].value
            except (KeyError, TypeError):
                return None
            with lock:
                d = sessions.get(token)
                return d if d and d['expires'] > time.time() else None

        def do_GET(self):
            if not self.boundary():
                return self.respond(403, {'error': 'access_boundary_rejected'})
            path = urlsplit(self.path).path
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in files:
                file, mime = files[path]
                return self.respond(200, (ROOT / 'web' / file).read_bytes(), mime)
            if path == '/health':
                return self.respond(200, {'ok': True})
            ident = self.identity()
            if not ident:
                return self.respond(401, {'error': 'authentication_required'})
            try:
                if path == '/api/me':
                    return self.respond(200, {'csrf': ident['csrf']})
                if path == '/api/overview':
                    return self.respond(200, manager.overview())
                if path == '/api/router':
                    return self.respond(200, manager.router_status())
                if path == '/api/proxy':
                    return self.respond(200, subscriptions.snapshot())
                if path == '/api/monitor':
                    with monitor_lock:
                        if time.monotonic() - monitor_cache[0] > 10:
                            monitor_cache[:] = [time.monotonic(), monitor(config)]
                        return self.respond(200, monitor_cache[1])
                if path == '/api/actions':
                    with sqlite3.connect(state / 'actions.sqlite') as db:
                        rows = db.execute('SELECT id,status,result,created FROM actions ORDER BY created DESC LIMIT 30').fetchall()
                    return self.respond(200, {'actions': [{'id': r[0], 'status': r[1], 'result': json.loads(r[2]) if r[2] else None, 'created': r[3]} for r in rows]})
                if path.startswith('/api/docs/'):
                    name = path.removeprefix('/api/docs/')
                    if name not in ('GUIDE', 'SERVICES', 'NETWORK', 'INSTALL', 'SECURITY'):
                        return self.respond(404, {'error': 'not_found'})
                    return self.respond(200, {'name': name, 'text': (ROOT / 'docs' / (name + '.md')).read_text()})
                return self.respond(404, {'error': 'not_found'})
            except Exception:
                return self.respond(503, {'error': 'status_temporarily_unavailable'})

        def do_POST(self):
            if not self.boundary(mutation=True):
                return self.respond(403, {'error': 'access_boundary_rejected'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                maximum = 5 * 1024 * 1024 if self.path == '/api/proxy/import' else 16384
                if not 1 <= length <= maximum or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('invalid_body')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('invalid_body')
                if self.path == '/api/login':
                    with lock:
                        q = attempts[self.client_address[0]]
                        while q and time.time() - q[0] > 60:
                            q.popleft()
                        if len(q) >= 8:
                            return self.respond(429, {'error': 'too_many_attempts'})
                        q.append(time.time())
                    if not isinstance(data.get('key'), str) or not hmac.compare_digest(data['key'].encode(), key.encode()):
                        return self.respond(401, {'error': 'invalid_access_code'})
                    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
                    with lock:
                        for t in list(sessions):
                            if sessions[t]['expires'] < time.time():
                                del sessions[t]
                        sessions[token] = {'csrf': csrf, 'expires': time.time() + 43200}
                    return self.respond(200, {'csrf': csrf}, cookie=f'isolation_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200')
                ident = self.identity()
                if not ident:
                    return self.respond(401, {'error': 'authentication_required'})
                if not hmac.compare_digest(self.headers.get('X-Toolkit-CSRF', ''), ident['csrf']):
                    return self.respond(403, {'error': 'invalid_csrf'})
                if self.path == '/api/logout':
                    with lock:
                        for t in list(sessions):
                            if sessions[t] is ident:
                                del sessions[t]
                    return self.respond(200, {'ok': True}, cookie='isolation_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                if self.path == '/api/router/check':
                    return self.respond(202, manager.check_router(data.get('internet') is True))
                if self.path == '/api/proxy/import':
                    return self.respond(200, subscriptions.import_nodes(data))
                if self.path == '/api/proxy/refresh':
                    return self.respond(200, subscriptions.refresh(data.get('id')))
                if self.path == '/api/proxy/adopt':
                    current = subscriptions.current_config()
                    return self.respond(200, subscriptions.import_nodes({'name': '当前出口', 'content': json.dumps({'proxies': current['proxies']})}))
                if self.path == '/api/proxy/preview':
                    return self.respond(200, subscriptions.preview(data))
                if self.path == '/api/proxy/apply':
                    return self.respond(200, subscriptions.apply(data.get('id')))
                if self.path == '/api/action/preview':
                    return self.respond(200, manager.preview(data.get('service'), data.get('action')))
                if self.path == '/api/action':
                    operation_id = str(uuid.UUID(data.get('operation_id')))
                    payload, digest = data.get('payload'), data.get('digest')
                    if not isinstance(payload, dict) or not isinstance(digest, str):
                        raise ValueError('invalid_body')
                    expected = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                    if not hmac.compare_digest(expected, digest):
                        raise ValueError('invalid_preview')
                    with action_lock:
                        with sqlite3.connect(state / 'actions.sqlite') as db:
                            row = db.execute('SELECT digest,status,result FROM actions WHERE id=?', (operation_id,)).fetchone()
                            if row:
                                if row[0] != digest:
                                    raise ValueError('operation_id_conflict')
                                if row[1] in ('complete', 'failed'):
                                    return self.respond(200, {**json.loads(row[2]), 'duplicate': True})
                                raise ValueError('operation_outcome_unknown')
                            db.execute('INSERT INTO actions VALUES(?,?,?,?,?)', (operation_id, digest, 'started', None, time.time()))
                        try:
                            result = manager.execute(payload, digest)
                        except ValueError as e:
                            result = {'ok': False, 'error': str(e), 'service': payload.get('service'), 'action': payload.get('action')}
                        with sqlite3.connect(state / 'actions.sqlite') as db:
                            db.execute('UPDATE actions SET status=?,result=? WHERE id=?', ('complete' if result['ok'] else 'failed', json.dumps(result), operation_id))
                    return self.respond(200, result)
                return self.respond(404, {'error': 'not_found'})
            except (ValueError, TypeError, AttributeError) as exc:
                value = str(exc)
                import re
                return self.respond(409, {'error': value if re.fullmatch('[a-z_]{1,90}', value) else 'invalid_or_changed_operation'})
            except Exception:
                return self.respond(503, {'error': 'operation_outcome_unknown'})

    servers = [ThreadingHTTPServer((host, config['port']), Handler) for host in config['listen']]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()
