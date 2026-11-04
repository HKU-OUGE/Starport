"""Cloud enrollment transactions and a real local HTTP/Access boundary."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from isolation_toolkit.cloudflare_api import Enrollment, enrollment_input, private_read, private_write
from isolation_toolkit.access_gateway import AccessGateway, AccessVerifier, private_origin
from isolation_toolkit.publication import Publication, connector_command


class FakeCloud:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.fail_after_create = None
        self.config = None
        self.occupied = False

    def list(self, route):
        self.calls.append(('GET', route, None))
        if route.startswith('/zones?'):
            return [{'id': 'zone', 'name': 'example.invalid', 'account': {'id': 'account'}}]
        if route.endswith('/access/identity_providers'):
            return [{'id': 'email-otp'}]
        if '/dns_records?name=' in route:
            name = route.split('name=')[1]
            if self.occupied and name == 'console.example.invalid':
                return [{'name': name}]
            return [copy.deepcopy(r) for key, r in self.rows.items() if '/dns_records/' in key and r['name'] == name]
        if '/access/apps/' in route and route.endswith('/policies'):
            return [copy.deepcopy(r) for key, r in self.rows.items() if '/access/policies/' in key]
        return [copy.deepcopy(r) for key, r in self.rows.items() if key.rsplit('/', 1)[0] == route.split('?')[0]]

    def call(self, method, route, body=None):
        self.calls.append((method, route, copy.deepcopy(body)))
        if route.endswith('/access/organizations'):
            return {'result': {'auth_domain': 'team.cloudflareaccess.com'}}
        if route.endswith('/configurations'):
            if method == 'PUT':
                self.config = copy.deepcopy(body)
            return {'result': self.config}
        if route.endswith('/token'):
            return {'result': 'test-connector-secret'}
        if method == 'POST':
            ident = str(len(self.rows) + 1)
            value = {**copy.deepcopy(body), 'id': ident}
            if '/access/apps' in route:
                value['aud'] = 'test-audience'
            if '/access/policies' in route:
                value['reusable'] = True
            if '/cfd_tunnel' in route:
                value.update(status='healthy', credentials_file={'TunnelSecret': 'never-store-this'})
            self.rows[route + '/' + ident] = value
            if self.fail_after_create == route:
                self.fail_after_create = None
                raise ValueError('cloudflare_outcome_unknown')
            return {'result': copy.deepcopy(value)}
        if method == 'DELETE':
            self.rows.pop(route, None)
            return {'result': {}}
        if route not in self.rows:
            raise ValueError('cloudflare_http_404')
        return {'result': copy.deepcopy(self.rows[route])}


def plan(e, port=18884, upstream='http://127.0.0.1:18881'):
    return e.plan({'zone': 'example.invalid', 'prefix': 'console', 'emails': ['alice@example.invalid']}, upstream, port)


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = FakeCloud()
        self.e = Enrollment(Path(self.temp.name) / 'private', self.api)
    def tearDown(self):
        self.temp.cleanup()

    def test_plan_is_read_only_and_occupied_prefix_is_not_replaced(self):
        self.api.occupied = True
        r = plan(self.e)
        self.assertTrue(r['hostname'].startswith('console-'))
        self.assertFalse(any(c[0] != 'GET' for c in self.api.calls))
        self.assertFalse(self.api.rows)

    def test_publish_order_idempotence_and_secret_exclusion(self):
        plan(self.e)
        r = self.e.apply()
        posts = [c[1].rsplit('/', 1)[-1] for c in self.api.calls if c[0] == 'POST']
        self.assertEqual(posts, ['policies', 'apps', 'cfd_tunnel', 'dns_records'])
        self.assertEqual(r['stage'], 'configured')
        self.assertNotIn('never-store-this', self.e.file.read_text())
        self.assertNotIn('test-connector-secret', self.e.file.read_text())
        self.assertEqual(private_read(self.e.folder / 'tunnel-token'), 'test-connector-secret')
        count = len([c for c in self.api.calls if c[0] != 'GET'])
        self.e.apply()
        self.assertEqual(len([c for c in self.api.calls if c[0] != 'GET']), count)
        self.assertTrue(self.api.config['config']['ingress'][0]['originRequest']['access']['required'])

    def test_lost_ack_reconciles_instead_of_creating_duplicate(self):
        plan(self.e)
        route = '/accounts/account/access/policies'
        self.api.fail_after_create = route
        with self.assertRaisesRegex(ValueError, 'outcome_unknown'):
            self.e.apply()
        self.assertTrue(self.e.read()['pending'])
        self.e.apply()
        self.assertEqual(len([c for c in self.api.calls if c[0] == 'POST' and c[1] == route]), 1)

    def test_unresolved_creation_cannot_be_retried_or_deleted(self):
        plan(self.e)
        self.api.fail_after_create = '/accounts/account/access/policies'
        with self.assertRaises(ValueError):
            self.e.apply()
        self.api.rows.clear()
        for action in (self.e.apply, self.e.remove):
            with self.assertRaisesRegex(ValueError, 'outcome_unknown'):
                action()
        self.assertEqual(len([c for c in self.api.calls if c[0] == 'POST']), 1)

    def test_policy_drift_blocks_tunnel_and_dns(self):
        plan(self.e)
        original = self.api.list
        def changed(route):
            rows = original(route)
            if '/apps/' in route and route.endswith('/policies'):
                rows[0]['include'].append({'everyone': {}})
            return rows
        self.api.list = changed
        with self.assertRaisesRegex(ValueError, 'access_policy_mismatch'):
            self.e.apply()
        self.assertFalse(any('/cfd_tunnel' in c[1] and c[0] == 'POST' for c in self.api.calls))

    def test_domain_claimed_after_preview_blocks_publication(self):
        plan(self.e)
        self.api.occupied = True
        with self.assertRaisesRegex(ValueError, 'hostname_changed'):
            self.e.apply()
        self.assertFalse(any(c[0] == 'POST' for c in self.api.calls))

    def test_remove_refuses_changed_record_then_removes_only_owned_resources(self):
        plan(self.e)
        r = self.e.apply()
        dns = '/zones/zone/dns_records/' + r['resources']['dns']['id']
        self.api.rows[dns]['content'] = 'foreign.example.invalid'
        with self.assertRaisesRegex(ValueError, 'resource_changed'):
            self.e.remove()
        self.assertFalse(any(c[0] == 'DELETE' for c in self.api.calls))
        self.api.rows[dns]['content'] = r['resources']['tunnel']['id'] + '.cfargotunnel.com'
        self.api.rows['/unrelated/keep'] = {'id': 'keep', 'name': 'other'}
        self.assertEqual(self.e.remove()['stage'], 'removed')
        self.assertEqual(list(self.api.rows), ['/unrelated/keep'])
        self.assertFalse((self.e.folder / 'tunnel-token').exists())

    def test_partial_unconfigured_tunnel_can_be_safely_withdrawn(self):
        plan(self.e)
        original = self.api.call
        def fail_config(method, route, body=None):
            if method == 'PUT':
                raise ValueError('cloudflare_outcome_unknown')
            return original(method, route, body)
        self.api.call = fail_config
        with self.assertRaisesRegex(ValueError, 'outcome_unknown'):
            self.e.apply()
        self.assertEqual(self.e.remove()['stage'], 'removed')
        self.assertFalse(self.api.rows)

    def test_invalid_input_and_upstream_cannot_create_public_arbitrary_proxy(self):
        valid = {'zone': 'example.invalid', 'prefix': 'console', 'emails': 'alice@example.invalid', 'token': 'x' * 32}
        self.assertEqual(enrollment_input(valid)[0]['emails'], ['alice@example.invalid'])
        for change in ({'zone': 'https://example.invalid'}, {'prefix': '../name'}, {'emails': []}, {'upstream': 'http://example.invalid'}):
            with self.assertRaises(ValueError):
                enrollment_input({**valid, **change})
        for value in ('http://1.1.1.1:18881', 'http://user:secret@127.0.0.1:18881', 'http://127.0.0.1:18881/path'):
            with self.assertRaises(ValueError):
                private_origin(value)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.seen = []
        seen = self.seen
        class Origin(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                seen.append({'headers': dict(self.headers), 'body': body, 'path': self.path})
                data = b'video-data' if self.command != 'POST' else body
                self.send_response(206 if self.headers.get('Range') else 200)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Set-Cookie', 'console_session=test; HttpOnly; SameSite=Strict; Path=/')
                self.end_headers()
                if self.command != 'HEAD': self.wfile.write(data)
            do_POST = do_HEAD = do_GET
        self.origin = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
        self.worker = threading.Thread(target=self.origin.serve_forever)
        self.worker.start()
        class Verifier:
            def verify(self, token):
                if token != 'accepted': raise ValueError()
        self.gateway = AccessGateway({'hostname': 'console.example.invalid', 'port': 0,
            'upstream': 'http://127.0.0.1:' + str(self.origin.server_port)}, Verifier()).start()

    def tearDown(self):
        self.gateway.close()
        self.origin.shutdown(); self.origin.server_close(); self.worker.join()

    def call(self, method='GET', headers=None, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.gateway.server.server_port, timeout=3)
        try:
            c.request(method, '/api/example', body=body, headers={'Host': 'console.example.invalid', **(headers or {})})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def test_unauthenticated_or_foreign_origin_never_reaches_console(self):
        self.assertEqual(self.call()[0], 401)
        self.assertEqual(self.call(headers={'Cf-Access-Jwt-Assertion': 'accepted', 'Host': 'other.example.invalid'})[0], 403)
        self.assertEqual(self.call('POST', {'Cf-Access-Jwt-Assertion': 'accepted'}, b'data')[0], 403)
        self.assertEqual(self.call('POST', {'Cf-Access-Jwt-Assertion': 'accepted', 'Origin': 'https://other.example.invalid'}, b'data')[0], 403)
        self.assertFalse(self.seen)

    def test_authenticated_upload_strips_identity_and_preserves_csrf_and_bytes(self):
        data = b'x' * 200000
        status, headers, result = self.call('POST', {'Cf-Access-Jwt-Assertion': 'accepted',
            'Origin': 'https://console.example.invalid', 'X-Console-CSRF': 'check', 'CF-Connecting-IP': '198.51.100.2',
            'X-Forwarded-For': '198.51.100.2', 'Cookie': 'CF_Authorization=private; console_session=test'}, data)
        self.assertEqual((status, result), (200, data))
        forwarded = {k.lower(): v for k, v in self.seen[-1]['headers'].items()}
        self.assertNotIn('cf-access-jwt-assertion', forwarded)
        self.assertNotIn('cf-connecting-ip', forwarded)
        self.assertNotIn('x-forwarded-for', forwarded)
        self.assertEqual(forwarded['cookie'], 'console_session=test')
        self.assertEqual(forwarded['x-console-csrf'], 'check')
        self.assertEqual(forwarded['origin'], self.gateway.upstream)
        self.assertIn('; Secure', headers['Set-Cookie'])

    def test_range_download_remains_streamable(self):
        status, _, body = self.call(headers={'Cf-Access-Jwt-Assertion': 'accepted', 'Range': 'bytes=0-9'})
        self.assertEqual((status, body), (206, b'video-data'))
        self.assertEqual(self.seen[-1]['headers']['Range'], 'bytes=0-9')


class JwtTests(unittest.TestCase):
    def test_signature_audience_expiry_and_email_are_enforced(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        v = AccessVerifier('https://team.cloudflareaccess.com', 'audience', ['alice@example.invalid'])
        v.signing_key = lambda kid: key.public_key()
        payload = {'iss': v.issuer, 'aud': 'audience', 'sub': 'test-user', 'email': 'alice@example.invalid', 'iat': int(time.time()), 'exp': int(time.time()) + 60}
        encode = lambda p: jwt.encode(p, key, algorithm='RS256', headers={'kid': 'one'})
        v.verify(encode(payload))
        for update in ({'email': 'other@example.invalid'}, {'aud': 'wrong'}, {'exp': int(time.time()) - 60}, {'iss': 'https://other.cloudflareaccess.com'}):
            with self.assertRaises(Exception): v.verify(encode({**payload, **update}))
        with self.assertRaises(Exception): v.verify('not-a-jwt')


class ManagerTests(unittest.TestCase):
    def test_startup_choice_persists_without_cloud_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            api = FakeCloud()
            p = Publication({'state_dir': folder, 'links': {'console': 'http://127.0.0.1:18881'}}, lambda token: api)
            try:
                e = Enrollment(p.folder, api)
                plan(e)
                r = e.apply()
                before = len(api.calls)
                p.perform('startup', {'deployment': r['deployment'], 'enabled': True})
                self.assertTrue(p.enabled())
                p.perform('startup', {'deployment': r['deployment'], 'enabled': False})
                self.assertFalse(p.enabled())
                self.assertEqual(len(api.calls), before)
            finally:
                p.close()
    def test_snapshot_never_returns_tokens_and_background_action_has_state(self):
        with tempfile.TemporaryDirectory() as folder:
            api = FakeCloud()
            p = Publication({'state_dir': folder, 'links': {'console': 'http://127.0.0.1:18881'}}, lambda token: api)
            try:
                self.assertTrue(p.submit('plan', {'zone': 'example.invalid', 'prefix': 'console', 'emails': ['alice@example.invalid'], 'token': 'x' * 32})['accepted'])
                deadline = time.monotonic() + 5
                while p.lock.locked() and time.monotonic() < deadline: time.sleep(.01)
                s = p.snapshot()
                self.assertEqual(s['stage'], 'planned')
                self.assertEqual(s['operation']['phase'], 'idle')
                self.assertNotIn('x' * 32, json.dumps(s))
                with self.assertRaisesRegex(ValueError, 'changed_preview'):
                    p.perform('apply', {'deployment': 'wrong'})
            finally:
                p.close()

    def test_connector_token_is_file_only_and_configuration_is_dedicated(self):
        args = connector_command('cloudflared', Path('private'))
        self.assertIn('--token-file', args)
        self.assertNotIn('--token', args)
        self.assertIn('--config', args)


if __name__ == '__main__':
    unittest.main()
