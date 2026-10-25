"""Bounded authenticated loopback JSON, common to Linux, macOS and Windows."""
from pathlib import Path
import hmac,json,secrets,socket,socketserver,threading
from .runtime import secure_dir


def request(path, value, timeout=65, limit=4*1024*1024):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>4096:raise ValueError('ipc_endpoint_invalid')
    config=json.loads(path.read_text(encoding='utf-8'))
    if type(config.get('port')) is not int or not 1024<=config['port']<=65535 or not isinstance(config.get('token'),str) or len(config['token'])!=64:
        raise ValueError('ipc_endpoint_invalid')
    with socket.create_connection(('127.0.0.1',config['port']),timeout=timeout) as connection:
        connection.sendall(json.dumps({'token':config['token'],'request':value},ensure_ascii=False).encode()+b'\n')
        with connection.makefile('rb') as stream:raw=stream.readline(limit+1)
    if len(raw)>limit:raise ValueError('ipc_response_limit')
    return json.loads(raw)


class RPCServer(socketserver.ThreadingTCPServer):
    daemon_threads=True
    def __init__(self,path,dispatch,max_request=1024*1024,timeout=65):
        self.path=Path(path);secure_dir(self.path.parent)
        self.token=secrets.token_hex(32);self.slots=threading.BoundedSemaphore(16)
        owner=self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(timeout)
                try:
                    raw=self.rfile.readline(max_request+1025)
                    if len(raw)>max_request+1024:raise ValueError('ipc_request_limit')
                    data=json.loads(raw)
                    if not isinstance(data,dict) or not isinstance(data.get('token'),str) or not hmac.compare_digest(data['token'],owner.token):
                        raise ValueError('ipc_authentication_required')
                    result=dispatch(data['request'])
                except Exception as exc:
                    reason=str(exc)
                    import re
                    result={'ok':False,'error':reason if re.fullmatch('[a-z_]{1,100}',reason) else 'ipc_operation_failed'}
                self.wfile.write(json.dumps(result,ensure_ascii=False).encode()+b'\n')
        super().__init__(('127.0.0.1',0),Handler)
        if self.path.is_symlink():self.server_close();raise ValueError('ipc_endpoint_invalid')
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps({'port':self.server_address[1],'token':self.token}), encoding='utf-8');temp.chmod(0o600);temp.replace(self.path)
    def verify_request(self,request,address):return address[0]=='127.0.0.1' and self.slots.acquire(False)
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()
    def server_close(self):
        super().server_close()
        if self.path.exists() and not self.path.is_symlink():
            try:
                if json.loads(self.path.read_text(encoding='utf-8')).get('token')==self.token:self.path.unlink()
            except (ValueError,OSError):pass


def stdio(path,max_request=1024*1024,timeout=65,limit=4*1024*1024):
    import sys
    raw=sys.stdin.buffer.readline(max_request+1)
    if len(raw)>max_request:raise ValueError('ipc_request_limit')
    result=request(path,json.loads(raw),timeout,limit)
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode()+b'\n')
