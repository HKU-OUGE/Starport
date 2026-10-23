import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from isolation_toolkit.operations import Manager, inventory
from isolation_toolkit.registry import CONTROL, in_scope
from isolation_toolkit.subscriptions import parse, normalize, resolve, Subscriptions


class NodeTests(unittest.TestCase):
    def test_yaml_import_does_not_adopt_routing_or_unsafe_node_fields(self):
        nodes=parse('''proxies:
  - {name: sample, type: ss, server: proxy.example.invalid, port: 443, cipher: aes-128-gcm, password: EXAMPLE_ONLY, udp: true, dialer-proxy: DIRECT, certificate: private.pem}
rules: [MATCH,DIRECT]
external-controller: '0.0.0.0:9090'
''')
        self.assertEqual(len(nodes),1);n=nodes[0]
        self.assertFalse(n['udp']);self.assertNotIn('dialer-proxy',n);self.assertNotIn('certificate',n);self.assertTrue(n['_supported'])

    def test_uri_and_base64(self):
        auth=base64.urlsafe_b64encode(b'aes-128-gcm:EXAMPLE_ONLY').decode().rstrip('=')
        text=f'ss://{auth}@proxy.example.invalid:443#Sample'
        for source in (text,base64.b64encode(text.encode()).decode()):
            self.assertEqual(parse(source)[0]['password'],'EXAMPLE_ONLY')

    def test_vless_ws(self):
        n=parse('vless://00000000-0000-4000-8000-000000000000@proxy.example.invalid:443?security=tls&type=ws&path=%2Fws#Example')[0]
        self.assertEqual(n['ws-opts']['path'],'/ws');self.assertTrue(n['tls'])

    def test_alias_and_private_endpoint_rejected(self):
        with self.assertRaises(ValueError): parse('proxies: &a [*a]')
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',0))]):
            with self.assertRaises(ValueError): resolve('localhost')

    def test_udp_transport_is_not_enabled(self):
        n=normalize({'name':'sample','type':'hysteria2','server':'proxy.example.invalid','port':443,'password':'EXAMPLE_ONLY'})
        self.assertFalse(n['_supported']);self.assertFalse(n['udp'])

    def test_snapshot_does_not_return_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            s=Subscriptions({'state_dir':d})
            s.import_nodes({'name':'sample','content':'proxies: [{name: example, type: ss, server: proxy.example.invalid, port: 443, password: EXAMPLE_ONLY}]'})
            raw=json.dumps(s.snapshot())
            self.assertNotIn('EXAMPLE_ONLY',raw);self.assertNotIn('proxy.example.invalid',raw)
            self.assertEqual(len(s.snapshot()['nodes']),1)


class OperationsTests(unittest.TestCase):
    def setUp(self): self.m=Manager({'legacy_root':'.'})
    def test_discovery_never_grants_root_controls(self):
        self.assertTrue(in_scope('gpu-training.service'))
        self.assertNotIn('system:gpu-training.service',CONTROL)
        for key in ['system:macmini-router.service','user:unknown.service','user:mac-mini-smb.service;id']:
            with self.assertRaises(ValueError): self.m.preview(key,'restart')
    def test_compare_and_swap_refuses_changed_service(self):
        row={'id':'user:mac-mini-smb.service','registered':True,'active':'active','sub':'running','enabled':'enabled','pid':10,'note':'test','title':'test'}
        with patch('isolation_toolkit.operations.inventory',return_value={'services':[row]}): p=self.m.preview(row['id'],'restart')
        row['pid']=11
        with patch('isolation_toolkit.operations.inventory',return_value={'services':[row]}),patch('isolation_toolkit.operations.run') as run:
            with self.assertRaises(ValueError):self.m.execute(p['payload'],p['digest'])
            run.assert_not_called()
    def test_exact_argument_execution(self):
        row={'id':'user:mac-mini-smb.service','registered':True,'active':'inactive','sub':'dead','enabled':'disabled','pid':0,'note':'test','title':'test'}
        with patch('isolation_toolkit.operations.inventory',return_value={'services':[row]}),patch('isolation_toolkit.operations.run',return_value=(0,'')) as run:
            p=self.m.preview(row['id'],'enable');self.assertTrue(self.m.execute(p['payload'],p['digest'])['ok'])
            self.assertEqual(run.call_args.args[0][-2:],['enable','mac-mini-smb.service'])


class GuardTests(unittest.TestCase):
    def test_only_exact_endpoint_set_is_replaced(self):
        spec=importlib.util.spec_from_file_location('apply_exit',Path(__file__).resolve().parents[1]/'apply_exit.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        text='table inet example { set nodes { type ipv4_addr . inet_service; elements = { 192.0.2.1 . 443 }; } chain output { policy drop; } }'
        new=m.replace_set(text,'nodes',[('192.0.2.2',8443)])
        self.assertIn('policy drop',new);self.assertNotIn('192.0.2.1',new);self.assertIn('192.0.2.2 . 8443',new)
        with self.assertRaises(ValueError):m.replace_set(text,'missing',[('192.0.2.2',8443)])


if __name__=='__main__':unittest.main()
