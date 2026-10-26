import base64
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from isolation_toolkit.operations import Manager, inventory
from isolation_toolkit.registry import CONTROL, in_scope
from isolation_toolkit.subscriptions import parse, normalize, resolve, endpoint_hosts, Subscriptions


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

    def test_exact_hosts_alias_is_private_node_metadata(self):
        nodes=parse('''proxies:
  - {name: sample, type: trojan, server: entry.example.invalid, port: 443, password: EXAMPLE_ONLY}
hosts:
  entry.example.invalid: middle.example.invalid
  middle.example.invalid: final.example.invalid
  unrelated.example.invalid: 127.0.0.1
rules: [MATCH,DIRECT]
dns: {enable: true}
''')
        self.assertEqual(nodes[0]['server'],'entry.example.invalid')
        self.assertEqual(nodes[0]['_endpoint_hosts'],['final.example.invalid'])
        self.assertNotIn('hosts',nodes[0]);self.assertNotIn('dns',nodes[0])
        self.assertIsNone(endpoint_hosts('other.example.invalid',{'*.example.invalid':'1.1.1.1'}))

    def test_alias_cycles_depth_and_nonpublic_targets_are_rejected(self):
        for mapping in ({'a.invalid':'b.invalid','b.invalid':'a.invalid'},
                        {'a.invalid':'127.0.0.1'}, {'a.invalid':['1.1.1.1','10.0.0.1']},
                        {'a.invalid':'198.18.0.1'}, {'a.invalid':['b.invalid']},
                        {'a.invalid':'::1'}, {'a.invalid':[]},
                        {f'n{i}.invalid':f'n{i+1}.invalid' for i in range(10)}):
            server='n0.invalid' if 'n0.invalid' in mapping else 'a.invalid'
            with self.subTest(mapping=mapping),self.assertRaises(ValueError): endpoint_hosts(server,mapping)
        self.assertEqual(endpoint_hosts('a.invalid',{'a.invalid':['8.8.8.8','1.1.1.1']}),['8.8.8.8','1.1.1.1'])

    def test_preview_pins_alias_and_keeps_original_tls_name(self):
        with tempfile.TemporaryDirectory() as d:
            s=Subscriptions({'state_dir':d})
            content='proxies:\n'+''.join(f'  - {{name: node{i}, type: trojan, server: entry.example.invalid, port: {443+i}, tls: true, password: EXAMPLE_ONLY}}\n' for i in range(3))+'hosts: {entry.example.invalid: final.example.invalid}\n'
            s.import_nodes({'content':content})
            source=json.loads(next(s.folder.glob('source-*.json')).read_text())
            old={'proxies':[{'name':f'SLOT-{i}','server':'9.9.9.9'} for i in range(3)],'proxy-groups':[{'name':'EXIT','type':'fallback'}],'tun':{'route-exclude-address':['9.9.9.9/32','192.0.2.0/24']},'interface-name':'eth3','ipv6':False}
            with patch.object(s,'current_config',return_value=copy.deepcopy(old)),patch('isolation_toolkit.subscriptions.resolve',return_value=['1.1.1.1']) as resolver:
                result=s.preview({'nodes':[n['_id'] for n in source['nodes']],'mode':'fallback'})
                self.assertEqual([c.args[0] for c in resolver.call_args_list],['final.example.invalid']*3)
            plan=json.loads((s.folder/f'plan-{result["id"]}.json').read_text())
            for n in plan['candidate']['proxies']:
                self.assertEqual(n['server'],'1.1.1.1');self.assertEqual(n['sni'],'entry.example.invalid')
                self.assertFalse(any(k.startswith('_') for k in n))
            self.assertEqual(plan['candidate']['tun']['route-exclude-address'],['192.0.2.0/24','1.1.1.1/32'])
            raw=json.dumps(s.snapshot())
            self.assertNotIn('final.example.invalid',raw);self.assertNotIn('EXAMPLE_ONLY',raw)


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
