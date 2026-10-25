import base64
import hashlib
import gzip
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console_cache import ReadCache, DeltaStore
from console_http import accepts_gzip
spec = importlib.util.spec_from_file_location('incremental_server', ROOT / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def snapshot(events, status='idle', **extra):
    return {'ok': True, 'session': {'id': 'session-a', 'status': status}, 'events': events, **extra}


class DeltaTests(unittest.TestCase):
    def test_same_sequence_duplicate_append_edit_and_removal(self):
        store = DeltaStore()
        a = {'seq': 1, 'role': 'assistant', 'text': 'hello'}
        b = {'seq': 1, 'role': 'tool', 'tool_id': 'tool-1'}
        first = store.response('session-a', snapshot([a, a, b], partial='thinking'))
        self.assertEqual(len(set(first['order'])), 3)
        unchanged = store.response('session-a', snapshot([a, a, b], partial='thinking'), first['cursor'])
        self.assertTrue(unchanged['unchanged']); self.assertEqual(unchanged['events'], [])
        c = {'seq': 1, 'role': 'tool_result', 'text': 'done', 'tool_id': 'tool-1'}
        second = store.response('session-a', snapshot([a, a, b, c], 'running'), first['cursor'])
        self.assertEqual(len(second['events']), 1); self.assertIn('partial', second['removed_fields'])
        third = store.response('session-a', snapshot([{**a, 'text': 'edited'}, b, c]), second['cursor'])
        self.assertEqual(len(third['events']), 1); self.assertEqual(len(third['removed']), 2)

    def test_cursor_is_session_scoped_and_evicted_cursor_resets(self):
        store = DeltaStore(revisions=1, max_sessions=1)
        first = store.response('session-a', snapshot([]))
        store.response('session-a', snapshot([], 'running'))
        self.assertTrue(store.response('session-a', snapshot([]), first['cursor'])['reset'])
        other = store.response('session-b', {'session': {'id': 'session-b'}, 'events': []}, first['cursor'])
        self.assertTrue(other['reset']); self.assertEqual(len(store.sessions), 1)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            store.response('session-b', snapshot([]))


class ReadTests(unittest.TestCase):
    def test_slow_list_does_not_block_detail_and_singleflight(self):
        blocked, release = threading.Event(), threading.Event()
        calls = []
        def read(request):
            calls.append(request['action'])
            if request['action'] == 'list': blocked.set(); release.wait(2)
            return {'ok': True, 'request': request}
        cache = ReadCache(read, workers=2)
        try:
            self.assertIsNone(cache.get('list', {'action': 'list'}, 5)[0]); self.assertTrue(blocked.wait(1))
            for _ in range(10): cache.get('list', {'action': 'list'}, 5)
            cache.get('read', {'action': 'read'}, 5)
            deadline = time.monotonic() + 1
            while cache.get('read', {'action': 'read'}, 5)[0] is None and time.monotonic() < deadline: time.sleep(.005)
            self.assertIsNotNone(cache.get('read', {'action': 'read'}, 5)[0])
            self.assertEqual(calls.count('list'), 1)
        finally: release.set(); cache.pool.shutdown()

    def test_invalidation_discards_inflight_result_and_errors_keep_snapshot(self):
        gate = threading.Event()
        cache = ReadCache(lambda _: (gate.wait(1), {'ok': True})[1])
        cache.get('a', {}, 10); cache.invalidate(); gate.set(); cache.pool.shutdown()
        self.assertIsNone(cache.entries['a']['value'])
        cache = ReadCache(lambda _: (_ for _ in ()).throw(ValueError('starship_connection_unavailable')))
        cache.entries['a'] = {'value': {'events': [1]}, 'at': 0, 'retry': 0, 'error': '', 'inflight': False, 'generation': 0, 'size': 10}
        value, _ = cache.get('a', {}, 10)
        cache.pool.shutdown(); self.assertEqual(value['events'], [1])
        self.assertEqual(cache.entries['a']['error'], 'starship_connection_unavailable')

    def test_memory_bound(self):
        cache = ReadCache(lambda _: {'ok': True}, max_entries=2)
        for i in range(10):
            cache.get(i, {}, 10)
            time.sleep(.01)
        cache.pool.shutdown(); self.assertLessEqual(len(cache.entries), 2)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name); (root / 'web/dist/assets').mkdir(parents=True)
        (root / 'web/dist/index.html').write_text('<html>console</html>', encoding='utf-8')
        cls.asset = b'const example = "sample content";\n' * 3000
        (root / 'web/dist/assets/example-abcdefgh.js').write_bytes(cls.asset)
        cls.base = patch.object(server, 'BASE', root); cls.base.start()
        server.HOST = '127.0.0.1'; server.KEY = 'example-only'; server.SESSIONS.clear(); server.ATTEMPTS.clear()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        server.PORT = cls.http.server_port
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.base.stop(); cls.tmp.cleanup()

    def setUp(self):
        self.conn = http.client.HTTPConnection('127.0.0.1', server.PORT, timeout=3)

    def tearDown(self): self.conn.close()

    def request(self, method, path, body=None, headers=None):
        self.conn.request(method, path, body, headers or {})
        response = self.conn.getresponse(); raw = response.read()
        return response, raw

    def test_compressed_immutable_asset_etag_and_keepalive(self):
        path = '/assets/example-abcdefgh.js'
        response, raw = self.request('GET', path, headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(response.status, 200); self.assertEqual(response.version, 11)
        self.assertEqual(gzip.decompress(raw), self.asset); self.assertLess(len(raw), len(self.asset) // 5)
        self.assertIn('immutable', response.getheader('Cache-Control'))
        sock = self.conn.sock
        second, raw = self.request('GET', path, headers={'Accept-Encoding': 'gzip', 'If-None-Match': response.getheader('ETag')})
        self.assertEqual(second.status, 304); self.assertEqual(raw, b''); self.assertIs(self.conn.sock, sock)
        third, raw = self.request('GET', path, headers={'Accept-Encoding': 'gzip;q=0, *;q=1'})
        self.assertIsNone(third.getheader('Content-Encoding')); self.assertEqual(raw, self.asset)
        head, raw = self.request('HEAD', path, headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(head.status, 200); self.assertEqual(raw, b'')

    def test_auth_origin_no_store_and_no_rpc_without_auth(self):
        with patch.object(server, 'rpc', side_effect=AssertionError('unexpected RPC')):
            response, _ = self.request('GET', '/api/session/00000000-0000-0000-0000-000000000001?delta=1')
            self.assertEqual(response.status, 401); self.assertEqual(response.getheader('Cache-Control'), 'no-store')
            response, _ = self.request('GET', '/', headers={'Host': 'wrong'})
            self.assertEqual(response.status, 403)
        response, _ = self.request('GET', '/')
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        response, _ = self.request('POST', '/api/login', '{}', {'Content-Type': 'application/json'})
        self.assertEqual(response.status, 403); self.assertEqual(response.getheader('Connection'), 'close')

    def test_authenticated_delta_does_not_forward_cursor(self):
        sid = '00000000-0000-0000-0000-000000000001'
        origin = f'http://127.0.0.1:{server.PORT}'
        response, _ = self.request('POST', '/api/login', json.dumps({'key': 'example-only'}), {'Origin': origin, 'Content-Type': 'application/json'})
        cookie = response.getheader('Set-Cookie').split(';')[0]
        class Ready:
            def get(self, key, request, ttl):
                self.request = request
                return {'ok': True, 'session': {'id': sid}, 'events': [{'text': 'abc' * 1000}]}, {'refreshing': False}
        ready = Ready()
        with patch.object(server, 'READS', ready):
            response, raw = self.request('GET', f'/api/session/{sid}?delta=1', headers={'Cookie': cookie, 'Accept-Encoding': 'gzip'})
            self.assertEqual(response.status, 200); self.assertEqual(response.getheader('Cache-Control'), 'no-store')
            value = json.loads(gzip.decompress(raw)); self.assertTrue(value['reset'])
            response, raw = self.request('GET', f'/api/session/{sid}?delta=1&cursor={value["cursor"]}', headers={'Cookie': cookie})
            second = json.loads(raw); self.assertTrue(second['unchanged']); self.assertEqual(second['events'], [])
            self.assertNotIn('cursor', ready.request); self.assertLess(len(raw), 500)

    def test_negotiation(self):
        self.assertTrue(accepts_gzip('br, gzip;q=0.5'))
        self.assertFalse(accepts_gzip('gzip;q=0, *;q=1'))
        self.assertFalse(accepts_gzip(None))

    def test_document_download_range_and_fixed_rpc_boundary(self):
        sid='00000000-0000-0000-0000-000000000001';key='a'*64
        path=f'/api/documents/{sid}/{key}'
        body=b'%PDF-1.4\n'+b'page-data'*30
        with patch.object(server,'rpc',side_effect=AssertionError('unauthenticated RPC')):
            response,_=self.request('GET',path)
            self.assertEqual(response.status,401)
        origin=f'http://127.0.0.1:{server.PORT}'
        response,_=self.request('POST','/api/login',json.dumps({'key':'example-only'}),{'Origin':origin,'Content-Type':'application/json'})
        cookie=response.getheader('Set-Cookie').split(';')[0]
        calls=[]
        def read(request,**kwargs):
            calls.append((request,kwargs))
            self.assertEqual(kwargs,{'document':True})
            self.assertEqual(request['session_id'],sid)
            self.assertEqual(request['media_id'],key)
            start=request.get('offset',0);part=body[start:start+request.get('length',0)]
            return {'media':{'size':len(body),'mime':'application/pdf','name':'報告.pdf','offset':start,
                'data':base64.b64encode(part).decode(),'chunk_sha256':hashlib.sha256(part).hexdigest()}}
        with patch.object(server,'rpc',side_effect=read):
            response,raw=self.request('GET',path+'?download=1&path=/etc/passwd',headers={'Cookie':cookie})
            self.assertEqual(response.status,200);self.assertEqual(raw,body)
            self.assertEqual(response.getheader('Content-Type'),'application/pdf')
            self.assertTrue(response.getheader('Content-Disposition').startswith("attachment; filename*=UTF-8''"))
            self.assertIn('no-store',response.getheader('Cache-Control'))
            self.assertTrue(all('path' not in request for request,_ in calls))
            response,raw=self.request('GET',path,headers={'Cookie':cookie,'Range':'bytes=0-8'})
            self.assertEqual(response.status,206);self.assertEqual(raw,b'%PDF-1.4\n')
            self.assertEqual(response.getheader('Content-Range'),f'bytes 0-8/{len(body)}')
            response,raw=self.request('HEAD',path,headers={'Cookie':cookie})
            self.assertEqual(response.status,200);self.assertEqual(raw,b'')
            self.assertEqual(int(response.getheader('Content-Length')),len(body))
            response,_=self.request('GET',path,headers={'Cookie':cookie,'Range':'bytes=99999-'})
            self.assertEqual(response.status,416)

    def test_artifact_read_requires_login_and_does_not_accept_urls_or_commands(self):
        sid = '00000000-0000-0000-0000-000000000001'
        aid = 'abcdefghijklmnopqrst'
        response, _ = self.request('GET', f'/api/artifacts/{sid}/{aid}')
        self.assertEqual(response.status, 401)
        origin = f'http://127.0.0.1:{server.PORT}'
        response, _ = self.request('POST', '/api/login', json.dumps({'key': 'example-only'}), {'Origin': origin, 'Content-Type': 'application/json'})
        cookie = response.getheader('Set-Cookie').split(';')[0]
        class Ready:
            def get(self, key, request, ttl):
                self.request = request
                return {'ok': True, 'artifact': {'id': aid, 'title': 'Example', 'available': True, 'xml': '<doc><paragraph>Text</paragraph></doc>'}}, {}
        ready = Ready()
        with patch.object(server, 'ARTIFACT_READS', ready):
            response, raw = self.request('GET', f'/api/artifacts/{sid}/{aid}?url=https://example.invalid&command=bad', headers={'Cookie': cookie})
            self.assertEqual(response.status, 200)
            value = json.loads(raw)['artifact']; self.assertIn('content', value); self.assertNotIn('xml', value)
            self.assertEqual(ready.request, {'action':'read', 'session_id':sid, 'artifact_id':aid})
            self.assertEqual(response.getheader('Cache-Control'),'no-store')
            response, _ = self.request('GET', f'/api/artifacts/{sid}/../../etc/passwd', headers={'Cookie':cookie})
            self.assertEqual(response.status,404)

    def test_export_download_auth_disposition_and_head(self):
        eid='a'*32;url='/api/artifact-exports/'+eid+'/download'
        response,_=self.request('GET',url)
        self.assertEqual(response.status,401)
        origin=f'http://127.0.0.1:{server.PORT}'
        response,_=self.request('POST','/api/login',json.dumps({'key':'example-only'}),{'Origin':origin,'Content-Type':'application/json'})
        cookie=response.getheader('Set-Cookie').split(';')[0]
        with patch.object(server,'export_bundle',return_value=b'archive') as bundle:
            response,raw=self.request('GET',url,headers={'Cookie':cookie})
            self.assertEqual(response.status,200);self.assertEqual(raw,b'archive')
            self.assertEqual(response.getheader('Content-Type'),'application/zip')
            self.assertIn('attachment',response.getheader('Content-Disposition'))
            self.assertEqual(response.getheader('Cache-Control'),'no-store')
            response,raw=self.request('HEAD',url,headers={'Cookie':cookie});self.assertEqual(raw,b'')
            response,_=self.request('GET','/api/artifact-exports/../../file/download',headers={'Cookie':cookie})
            self.assertEqual(response.status,404);self.assertEqual(bundle.call_count,2)

    def test_snapshot_refresh_requires_csrf_and_accepts_no_paths(self):
        origin=f'http://127.0.0.1:{server.PORT}'
        response,raw=self.request('POST','/api/login',json.dumps({'key':'example-only'}),{'Origin':origin,'Content-Type':'application/json'})
        cookie=response.getheader('Set-Cookie').split(';')[0];csrf=json.loads(raw)['csrf']
        class Refresh:
            calls=0
            def start(self):self.calls+=1;return {'state':'running','id':'a'*32}
        refresh=Refresh();headers={'Cookie':cookie,'Origin':origin,'Content-Type':'application/json'}
        with patch.object(server,'ARTIFACT_REFRESH',refresh):
            response,_=self.request('POST','/api/artifact-exports/refresh','{}',headers);self.assertEqual(response.status,403)
            headers['X-Console-CSRF']=csrf
            response,_=self.request('POST','/api/artifact-exports/refresh','{"path":"/etc/passwd"}',headers);self.assertEqual(response.status,409)
            response,raw=self.request('POST','/api/artifact-exports/refresh','{}',headers);self.assertEqual(response.status,200);self.assertEqual(json.loads(raw)['refresh']['state'],'running')
            self.assertEqual(refresh.calls,1)


if __name__ == '__main__': unittest.main()
