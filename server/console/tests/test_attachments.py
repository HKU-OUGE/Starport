from pathlib import Path
import base64,hashlib,http.client,importlib.util,json,sys,tempfile,threading,unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from attachments import request,identifiers
spec=importlib.util.spec_from_file_location('attachment_console',ROOT/'server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
SID='cse_'+'a'*24;UID='1'*32


class AttachmentBoundaryTests(unittest.TestCase):
    def test_chunk_hash_is_computed_without_forwarding_extra_fields(self):
        data={'operation':'chunk','session_id':SID,'id':UID,'offset':0,'data':base64.b64encode(b'file').decode()}
        clean=request(data,server.checked_session_id)
        self.assertEqual(clean['request']['sha256'],hashlib.sha256(b'file').hexdigest())
        self.assertEqual(clean['action'],'attachment')
        with self.assertRaises(ValueError):request({**data,'path':'outside'},server.checked_session_id)
    def test_send_binds_ids_and_allows_image_only_without_preview(self):
        payload=server.action_payload({'action':'send','session_id':SID,'message':'','attachments':[UID]})
        self.assertEqual(payload['attachments'],[UID]);self.assertEqual(payload['message'],'')
        with self.assertRaises(ValueError):identifiers(['../secret'])
    def test_upload_requires_login_origin_and_csrf(self):
        server.KEY='example-only-attachment-access';server.SESSIONS.clear()
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        http.external_origin=f'http://127.0.0.1:{http.server_port}'
        thread=threading.Thread(target=http.serve_forever);thread.start()
        def post(path,data,headers=None):
            conn=http_client=http_module.HTTPConnection('127.0.0.1',http.server_port,timeout=3)
            try:
                conn.request('POST',path,json.dumps(data),{'Content-Type':'application/json','Origin':http.external_origin,**(headers or {})})
                reply=conn.getresponse();raw=reply.read();return reply.status,dict(reply.getheaders()),json.loads(raw)
            finally:conn.close()
        http_module=__import__('http.client',fromlist=['HTTPConnection'])
        try:
            data={'operation':'begin','session_id':SID,'id':UID,'name':'example.txt','size':4}
            with patch.object(server,'rpc',return_value={'ok':True,'attachment':{'id':UID}}) as rpc:
                self.assertEqual(post('/api/attachments',data)[0],403);rpc.assert_not_called()
                status,headers,body=post('/api/login',{'key':server.KEY});self.assertEqual(status,200)
                cookie=headers['Set-Cookie'].split(';')[0]
                self.assertEqual(post('/api/attachments',data,{'Cookie':cookie})[0],403)
                self.assertEqual(post('/api/attachments',data,{'Cookie':cookie,'X-Console-CSRF':body['csrf']})[0],200)
                self.assertEqual(rpc.call_args.args[0]['request']['name'],'example.txt')
        finally:http.shutdown();thread.join();http.server_close()


if __name__=='__main__':unittest.main()
