from pathlib import Path
import base64,hashlib,importlib.util,json,os,platform,socket,subprocess,sys,tempfile,threading,unittest
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'client/starship'))
from starport_common.runtime import bounded_process,secure_dir,windows_open_regular,WINDOWS,fcntl
from starport_common.ipc import RPCServer,request
from starport_common.services import definition
from starport_common.transport import command
from uploads import Uploads,filename
from rich_content import open_regular


class PlatformTests(unittest.TestCase):
    def test_authenticated_ipc_and_foreign_token_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'private/control.rpc'
            seen=[]
            with RPCServer(path,lambda d:seen.append(d) or {'ok':True,'value':d}) as server:
                thread=threading.Thread(target=server.serve_forever);thread.start()
                try:
                    self.assertEqual(request(path,{'hello':'附件'})['value'],{'hello':'附件'})
                    with socket.create_connection(server.server_address) as s:
                        s.sendall(b'{"token":"wrong","request":{"command":"blocked"}}\n')
                        self.assertFalse(json.loads(s.recv(4096))['ok'])
                    self.assertEqual(len(seen),1)
                finally:server.shutdown();thread.join()
            self.assertFalse(path.exists())

    def test_pipe_transport_drains_stderr_and_bounds_output(self):
        code='import sys;sys.stderr.write("x"*200000);sys.stdout.buffer.write(sys.stdin.buffer.read())'
        self.assertEqual(bounded_process([sys.executable,'-c',code],b'attachment',5,100),b'attachment')
        with self.assertRaisesRegex(ValueError,'response_exceeds_limit'):
            bounded_process([sys.executable,'-c','print("x"*200000)'],b'',5,100)
        with self.assertRaisesRegex(ValueError,'outcome_unknown'):
            bounded_process([sys.executable,'-c','import time;time.sleep(4)'],b'',.1,100)

    def test_user_service_definitions_on_three_platforms(self):
        with tempfile.TemporaryDirectory(prefix='starport space ') as folder:
            for system in ('Linux','Darwin','Windows'):
                name,value=definition('starport-console',[sys.executable,Path(folder)/'entry.py','argument with spaces'],folder,system)
                if system=='Linux':self.assertIn('WorkingDirectory='+folder,value)
                elif system=='Darwin':self.assertEqual(value['ProgramArguments'][2],'argument with spaces')
                else:
                    root=ET.fromstring(value);ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
                    self.assertEqual(root.find('.//t:LogonType',ns).text,'InteractiveToken')
                    self.assertEqual(root.find('.//t:RunLevel',ns).text,'LeastPrivilege')

    def test_read_regular_unicode_and_reject_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve() if platform.system()=='Darwin' else Path(folder)
            path=root/'資料 檔.txt';path.write_bytes(b'example')
            with os.fdopen(open_regular(path),'rb') as f:self.assertEqual(f.read(),b'example')
            link=root/'link.txt'
            try:link.symlink_to(path)
            except OSError as exc:self.skipTest('Symlink creation unavailable: '+str(exc))
            with self.assertRaises((ValueError,OSError)):open_regular(link)

    def test_ssh_commands_cannot_include_arbitrary_endpoint(self):
        self.assertIn('rpc.cmd',command('session','windows'))
        self.assertIn('$HOME',command('session','posix'))
        with self.assertRaises(ValueError):command('session & whoami','windows')

    @unittest.skipUnless(WINDOWS,'Windows command parser check')
    def test_windows_command_resolves_userprofile_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='starship space ') as folder:
            home=Path(folder);entry=home/'.local/share/starship/rpc.cmd';entry.parent.mkdir(parents=True)
            entry.write_text('@echo off\necho %1\n')
            result=subprocess.run(command('session','windows'),capture_output=True,text=True,env={**os.environ,'USERPROFILE':str(home)},timeout=5)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'session')

    @unittest.skipUnless(WINDOWS,'Windows child lifecycle check')
    def test_windows_job_closes_only_its_child(self):
        from starport_common.runtime import child_job,process_options
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],**process_options())
        try:
            close=child_job(process);close();process.wait(timeout=5)
        finally:
            if process.poll() is None:process.kill();process.wait()


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.project=self.root/'project';self.project.mkdir();self.sid='cse_'+'a'*24;self.uid='1'*32
        self.store=Uploads(self.root/'uploads',lambda sid:self.project,lambda sid:None)
    def tearDown(self):self.temp.cleanup()
    def send(self,op,**kwargs):return self.store.dispatch({'operation':op,'session_id':self.sid,'id':self.uid,**kwargs})
    def chunk(self,body,offset=0):return self.send('chunk',offset=offset,data=base64.b64encode(body).decode(),sha256=hashlib.sha256(body).hexdigest())
    def upload(self,body,name):
        self.send('begin',name=name,size=len(body));self.chunk(body);self.send('finish')
    def test_retry_chunks_finish_and_publish_without_spool_duplicate(self):
        self.send('begin',name='data.txt',size=6);self.chunk(b'abc');self.chunk(b'abc')
        self.assertEqual(self.send('status')['attachment']['offset'],3)
        self.chunk(b'def',3);self.send('finish');self.send('finish')
        content=self.store.content(self.sid,[self.uid],'Read this')
        file=self.project/content[1]['text'].removeprefix('Attached file: ')
        self.assertEqual(file.read_bytes(),b'abcdef')
        self.assertFalse((self.store.folder(self.sid,self.uid)/'content').exists())
        self.assertEqual(self.store.content(self.sid,[self.uid],'Read this'),content)
    def test_picture_is_native_image_block(self):
        body=b'\x89PNG\r\n\x1a\nexample';self.upload(body,'picture.png')
        block=self.store.content(self.sid,[self.uid],'')[0]
        self.assertEqual(block['type'],'image');self.assertEqual(base64.b64decode(block['source']['data']),body)
    def test_traversal_foreign_session_corruption_and_incomplete_fail(self):
        for name in ('../file','a\\b','CON','bad\nname'):
            with self.assertRaises(ValueError):filename(name)
        self.send('begin',name='file.txt',size=3)
        with self.assertRaisesRegex(ValueError,'integrity'):
            self.send('chunk',offset=0,data='YWJj',sha256='0'*64)
        with self.assertRaisesRegex(ValueError,'incomplete'):self.send('finish')
        self.chunk(b'abc');self.send('finish')
        with self.assertRaisesRegex(ValueError,'not_found'):self.store.content('cse_'+'b'*24,[self.uid],'')
    def test_unknown_fields_never_become_paths_or_commands(self):
        with self.assertRaises(ValueError):self.send('begin',name='x',size=1,path='outside')
    def test_files_on_other_remote_devices_report_clear_reason(self):
        def unavailable(sid):raise ValueError('attachment_requires_local_project')
        self.store.project=unavailable
        with self.assertRaisesRegex(ValueError,'requires_local_project'):self.send('begin',name='file.txt',size=3)
        self.send('begin',name='picture.png',size=3)


if __name__=='__main__':unittest.main()
