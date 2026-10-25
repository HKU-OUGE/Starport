import http.client
import importlib.util
import json
from pathlib import Path
import threading
import tempfile
import uuid
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tunnel_console_server', ROOT / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class TunnelBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.public_origin = 'https://console.example.invalid'
        cls.private = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.private.external_origin = f'http://127.0.0.1:{cls.private.server_port}'
        cls.tunnel = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.tunnel.external_origin = cls.public_origin
        cls.tunnel.tunnel_only = True
        for listener in (cls.private, cls.tunnel):
            threading.Thread(target=listener.serve_forever, daemon=True).start()
        server.KEY = 'example-only'

    @classmethod
    def tearDownClass(cls):
        for listener in (cls.private, cls.tunnel):
            listener.shutdown()
            listener.server_close()

    def request(self, listener, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', listener.server_port, timeout=3)
        try:
            conn.request(method, path, body, headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def public_headers(self):
        return {'Host': 'console.example.invalid', 'Origin': self.public_origin,
                'Content-Type': 'application/json'}

    def login(self, listener, headers):
        status, reply, body = self.request(listener, 'POST', '/api/login',
            json.dumps({'key': 'example-only'}), headers)
        self.assertEqual(status, 200)
        return reply['Set-Cookie'], json.loads(body)['csrf']

    def test_public_requires_exact_host_and_origin(self):
        for changes in ({'Host': 'evil.example'}, {'Host': 'console.example.invalid:443'},
                        {'Origin': 'http://console.example.invalid'}, {'Origin': 'null'},
                        {'Origin': 'https://console.example.invalid.evil.example'}):
            headers = {**self.public_headers(), **changes}
            status, _, _ = self.request(self.tunnel, 'POST', '/api/login', '{}', headers)
            self.assertEqual(status, 403)
        headers = self.public_headers(); headers.pop('Origin')
        status, _, _ = self.request(self.tunnel, 'POST', '/api/login', '{}', headers)
        self.assertEqual(status, 403)

    def test_forwarded_headers_cannot_promote_private_listener(self):
        headers = {**self.public_headers(), 'X-Forwarded-Host': 'console.example.invalid',
                   'X-Forwarded-Proto': 'https', 'CF-Connecting-IP': '127.0.0.1'}
        status, _, _ = self.request(self.private, 'POST', '/api/login', '{}', headers)
        self.assertEqual(status, 403)

    def test_tunnel_rejects_private_host_and_duplicate_hosts(self):
        status, _, _ = self.request(self.tunnel, 'GET', '/api/auth')
        self.assertEqual(status, 403)
        conn = http.client.HTTPConnection('127.0.0.1', self.tunnel.server_port, timeout=3)
        try:
            conn.putrequest('GET', '/api/auth', skip_host=True)
            conn.putheader('Host', 'console.example.invalid')
            conn.putheader('Host', 'evil.example')
            conn.endheaders()
            response = conn.getresponse(); response.read()
            self.assertEqual(response.status, 403)
        finally:
            conn.close()

    def test_https_cookie_and_csrf_remain_required(self):
        headers = self.public_headers()
        cookie, csrf = self.login(self.tunnel, headers)
        self.assertIn('; Secure', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        headers['Cookie'] = cookie.split(';')[0]
        body = json.dumps({'action': 'send', 'session_id': '00000000-0000-0000-0000-000000000001',
                           'message': '测试文本 /path password=example-only'})
        with patch.object(server, 'rpc', side_effect=AssertionError('preview must never send')):
            status, _, _ = self.request(self.tunnel, 'POST', '/api/preview', body, headers)
            self.assertEqual(status, 403)
            headers['X-Console-CSRF'] = csrf
            status, reply, output = self.request(self.tunnel, 'POST', '/api/preview', body, headers)
            self.assertEqual(status, 200)
            self.assertEqual(reply['Cache-Control'], 'no-store')
            self.assertEqual(json.loads(output)['payload']['message'], '測試文本 /path password=example-only')
            status, reply, _ = self.request(self.tunnel, 'POST', '/api/logout', '{}', headers)
            self.assertEqual(status, 200)
            self.assertIn('Max-Age=0; Secure', reply['Set-Cookie'])

    def test_model_settings_auth_csrf_and_typed_native_route(self):
        sid='cse_'+'a'*20;headers=self.public_headers()
        payload={'action':'model_settings','session_id':sid,'operation_id':str(uuid.uuid4()),'setting':'model','value':'sonnet','expected':{'model':'claude-opus-5-5','effort':'xhigh','ultracode':None}}
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'STATE',Path(folder)), patch.object(server,'rpc',return_value={'ok':True,'operation':{'state':'requested'}}) as rpc:
            with server.database() as db:db.execute('CREATE TABLE operations(id TEXT PRIMARY KEY,digest TEXT,action TEXT,session_id TEXT,status TEXT,created REAL,result TEXT,error TEXT)')
            status,_,_=self.request(self.tunnel,'GET','/api/model-settings/'+sid,headers=headers);self.assertEqual(status,401)
            cookie,csrf=self.login(self.tunnel,headers);headers['Cookie']=cookie.split(';')[0]
            status,_,_=self.request(self.tunnel,'POST','/api/action',json.dumps(payload),headers);self.assertEqual(status,403)
            rpc.assert_not_called();headers['X-Console-CSRF']=csrf
            status,_,_=self.request(self.tunnel,'POST','/api/action',json.dumps(payload),headers);self.assertEqual(status,200)
            rpc.assert_called_once_with(payload,model_settings=True)
            status,_,_=self.request(self.tunnel,'POST','/api/action',json.dumps(payload),headers);self.assertEqual(status,200);self.assertEqual(rpc.call_count,1)
            status,_,_=self.request(self.tunnel,'POST','/api/action',json.dumps({**payload,'permission_mode':'bypassPermissions'}),headers);self.assertEqual(status,409)

    def test_activity_is_authenticated_read_only_and_session_scoped(self):
        sid='cse_'+'a'*20
        headers={'Host':'console.example.invalid'}
        status, _, _=self.request(self.tunnel,'GET','/api/activity/'+sid,headers=headers)
        self.assertEqual(status,401)
        cookie,_=self.login(self.tunnel,self.public_headers());headers['Cookie']=cookie.split(';')[0]
        with patch.object(server.ACTIVITY_READS,'get',return_value=({'ok':True,'session_id':sid,'activity':{'phase':'working'}},{'age_seconds':0})) as get:
            status,reply,raw=self.request(self.tunnel,'GET','/api/activity/'+sid,headers=headers)
            self.assertEqual(status,200);self.assertEqual(reply['Cache-Control'],'no-store')
            self.assertEqual(json.loads(raw)['session_id'],sid)
            get.assert_called_once_with(sid,{'action':'read','session_id':sid},1)
            for invalid in ['../../etc/passwd','cse_short','00000000-0000-0000-0000-000000000001']:
                status,_,_=self.request(self.tunnel,'GET','/api/activity/'+invalid,headers=headers)
                self.assertEqual(status,404)

    def test_direct_message_converts_once_and_keeps_auth_and_idempotency(self):
        headers=self.public_headers();sid='cse_'+'a'*20
        body={'action':'send','session_id':sid,'operation_id':str(uuid.uuid4()),'message':'  测试继续训练。\npassword=example-only'}
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'STATE',Path(folder)):
            with server.database() as db:db.execute('CREATE TABLE operations(id TEXT PRIMARY KEY,digest TEXT,action TEXT,session_id TEXT,status TEXT,created REAL,result TEXT,error TEXT)')
            with patch.object(server,'rpc',return_value={'ok':True,'accepted':True}) as rpc:
                status,_,_=self.request(self.tunnel,'POST','/api/action',json.dumps(body),headers);self.assertEqual(status,403)
                cookie,csrf=self.login(self.tunnel,headers);headers['Cookie']=cookie.split(';')[0]
                status,_,_=self.request(self.tunnel,'POST','/api/action',json.dumps(body),headers);self.assertEqual(status,403)
                headers['X-Console-CSRF']=csrf
                bad={**headers,'Origin':'https://different.example.invalid'}
                status,_,_=self.request(self.tunnel,'POST','/api/action',json.dumps(body),bad);self.assertEqual(status,403)
                status,reply,_=self.request(self.tunnel,'POST','/api/action',json.dumps(body),headers);self.assertEqual(status,200);self.assertEqual(reply['Cache-Control'],'no-store')
                self.assertEqual(rpc.call_args.args[0],{**body,'message':'  測試繼續訓練。\npassword=example-only'})
                status,_,raw=self.request(self.tunnel,'POST','/api/action',json.dumps(body),headers);self.assertEqual(status,200);self.assertTrue(json.loads(raw)['duplicate']);self.assertEqual(rpc.call_count,1)
                for invalid in ({**body,'operation_id':'invalid'},{**body,'message':'  '},{**body,'action':'stop'},{**body,'action':'permission','decision':'allow','request_id':'example'}):
                    status,_,_=self.request(self.tunnel,'POST','/api/action',json.dumps(invalid),headers);self.assertEqual(status,409)
                self.assertEqual(rpc.call_count,1)

    def test_private_login_still_works_without_secure_cookie(self):
        cookie, _ = self.login(self.private, {'Origin': self.private.external_origin,
            'Content-Type': 'application/json'})
        self.assertNotIn('; Secure', cookie)


if __name__ == '__main__':
    unittest.main()
