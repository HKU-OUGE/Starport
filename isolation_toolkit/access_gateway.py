"""Access-verified loopback ingress for an existing console, without restarting it."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib import request
import http.client
import ipaddress
import json
import re
import threading
import time

from .cloudflare_api import NoRedirect


def private_origin(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'http' or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('private_console_origin_required')
    host = parsed.hostname
    if host == 'localhost':
        host = '127.0.0.1'
    try:
        ip = ipaddress.ip_address(host)
        if ip.version != 4 or not any(ip in ipaddress.ip_network(n) for n in ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
            raise ValueError()
        port = parsed.port
        if not port or not 1024 <= port <= 65535:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('private_console_origin_required') from None
    return 'http://' + parsed.netloc


class AccessVerifier:
    def __init__(self, issuer, audience, emails):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa  # Require the RS256 backend before publishing.
        if not re.fullmatch(r'https://[a-z0-9-]+\.cloudflareaccess\.com', issuer) or not audience or not emails:
            raise ValueError('invalid_access_configuration')
        self.jwt = jwt
        self.issuer, self.audience, self.emails = issuer, audience, emails
        self.keys, self.expires, self.lock, self.last_refresh = [], 0, threading.Lock(), 0

    def signing_key(self, kid):
        with self.lock:
            now = time.monotonic()
            matches = [k for k in self.keys if k.get('kid') == kid]
            if now >= self.expires or not matches and now - self.last_refresh > 10:
                self.last_refresh = now
                opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
                with opener.open(self.issuer + '/cdn-cgi/access/certs', timeout=5) as response:
                    raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError('invalid_access_keys')
                self.keys = json.loads(raw)['keys']
                self.expires = now + 300
                matches = [k for k in self.keys if k.get('kid') == kid]
            if len(matches) != 1 or matches[0].get('kty') != 'RSA':
                raise ValueError('invalid_access_key')
            return self.jwt.PyJWK.from_dict(matches[0], algorithm='RS256').key

    def verify(self, token):
        if not isinstance(token, str) or len(token) > 16384:
            raise ValueError('access_authentication_required')
        header = self.jwt.get_unverified_header(token)
        if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str):
            raise ValueError('access_authentication_required')
        payload = self.jwt.decode(token, self.signing_key(header['kid']), algorithms=['RS256'],
            audience=self.audience, issuer=self.issuer, leeway=5,
            options={'require': ['exp', 'iat', 'sub', 'email']})
        if not isinstance(payload['email'], str) or payload['email'].lower() not in self.emails:
            raise ValueError('access_denied')
        # Identity and Cloudflare metadata are intentionally not forwarded.


class AccessGateway:
    def __init__(self, receipt, verifier=None):
        self.receipt = receipt
        self.upstream = private_origin(receipt['upstream'])
        self.verifier = verifier or AccessVerifier(receipt['issuer'], receipt['resources']['app']['aud'], receipt['emails'])
        self.last_authenticated = None
        self.server = None

    def start(self):
        gateway = self
        target = urlsplit(self.upstream)
        expected = 'https://' + self.receipt['hostname']

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            server_version = 'Starport'
            sys_version = ''

            def log_message(self, *_):
                pass

            def deny(self, code, text):
                body = json.dumps({'error': text}).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(body)
                self.close_connection = True

            def proxy(self):
                sent = False
                conn = None
                try:
                    self.connection.settimeout(60)
                    if self.client_address[0] != '127.0.0.1' or self.headers.get_all('Host', []) != [gateway.receipt['hostname']]:
                        return self.deny(403, 'public_host_required')
                    origins = self.headers.get_all('Origin', [])
                    if origins and origins != [expected] or self.command == 'POST' and origins != [expected]:
                        return self.deny(403, 'public_origin_required')
                    tokens = self.headers.get_all('Cf-Access-Jwt-Assertion', [])
                    try:
                        if len(tokens) != 1:
                            raise ValueError()
                        gateway.verifier.verify(tokens[0])
                    except Exception:
                        return self.deny(401, 'access_authentication_required')
                    gateway.last_authenticated = time.time()
                    lengths = self.headers.get_all('Content-Length', [])
                    if self.headers.get('Transfer-Encoding') or len(lengths) > 1 or (lengths and not re.fullmatch(r'[0-9]{1,12}', lengths[0])):
                        return self.deny(400, 'invalid_request_framing')
                    length = int(lengths[0]) if lengths else 0
                    if length > 1024 ** 3 or self.command in ('GET', 'HEAD') and length:
                        return self.deny(413, 'request_too_large')
                    if not self.path.startswith('/') or self.path.startswith('//') or any(c in self.path for c in '\r\n'):
                        return self.deny(400, 'invalid_request_path')
                    # Only application headers cross this hop. Strip IP, Access,
                    # authorization, referer and unrelated browser cookies.
                    keep = ('Content-Type', 'Range', 'If-Range', 'If-None-Match', 'If-Modified-Since',
                            'Accept', 'Accept-Encoding', 'X-Console-CSRF')
                    headers = {k: self.headers[k] for k in keep if k in self.headers}
                    headers.update(Host=target.netloc, Connection='close')
                    if origins:
                        headers['Origin'] = gateway.upstream
                    cookies = [c.strip() for c in self.headers.get('Cookie', '').split(';') if c.strip().startswith('console_session=')]
                    if len(cookies) == 1:
                        headers['Cookie'] = cookies[0]
                    if lengths:
                        headers['Content-Length'] = str(length)
                    conn = http.client.HTTPConnection(target.hostname, target.port, timeout=60)
                    conn.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
                    for key, value in headers.items():
                        conn.putheader(key, value)
                    conn.endheaders()
                    remaining = length
                    while remaining:
                        part = self.rfile.read(min(65536, remaining))
                        if not part:
                            raise OSError('upload_interrupted')
                        conn.send(part)
                        remaining -= len(part)
                    response = conn.getresponse()
                    self.send_response(response.status)
                    for key, value in response.getheaders():
                        if key.lower() in ('connection', 'transfer-encoding', 'server', 'date', 'keep-alive'):
                            continue
                        if key.lower() == 'set-cookie':
                            if not value.startswith('console_session='):
                                continue
                            if not re.search(r';\s*secure(?:;|$)', value, re.I):
                                value += '; Secure'
                        if key.lower() == 'location' and value.startswith(gateway.upstream + '/'):
                            value = expected + value[len(gateway.upstream):]
                        self.send_header(key, value)
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    sent = True
                    if self.command != 'HEAD':
                        while True:
                            part = response.read1(65536)
                            if not part:
                                break
                            self.wfile.write(part)
                            self.wfile.flush()
                except (OSError, ValueError, http.client.HTTPException):
                    if not sent:
                        try:
                            self.deny(502, 'console_origin_unavailable')
                        except OSError:
                            pass
                finally:
                    self.close_connection = True
                    if conn:
                        conn.close()

            do_GET = do_POST = do_HEAD = proxy

        self.server = ThreadingHTTPServer(('127.0.0.1', self.receipt['port']), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.server = None
