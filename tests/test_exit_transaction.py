import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import yaml

spec=importlib.util.spec_from_file_location('apply_exit',Path(__file__).resolve().parents[1]/'apply_exit.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class TransactionTests(unittest.TestCase):
    def test_owned_table_reset_removes_stale_sets_and_is_idempotent(self):
        text='add table inet mm_exit\nflush table inet mm_exit\nadd table bridge mm_exit\nflush table bridge mm_exit\ntable inet mm_exit { }\ntable bridge mm_exit { }\n'
        result=module.reset_guard_tables(text)
        self.assertEqual(result.count('delete table'),2)
        self.assertNotIn('flush table',result)
        self.assertEqual(module.reset_guard_tables(result),result)
        with self.assertRaisesRegex(ValueError,'unowned_guard_table'):
            module.reset_guard_tables('add table inet another\nflush table inet another\n')
        with self.assertRaisesRegex(ValueError,'guard_table_creation_missing'):
            module.reset_guard_tables('flush table inet mm_exit\n')

    def test_newline_delimited_guard_keeps_every_other_rule(self):
        text='set tcp_endpoints {\n type ipv4_addr . inet_service\n elements = { 192.0.2.1 . 443 }\n}\nchain forward { policy drop; }'
        changed=module.replace_set(text,'tcp_endpoints',[('192.0.2.2',8443)])
        self.assertIn('192.0.2.2 . 8443',changed)
        self.assertEqual(changed.replace('192.0.2.2 . 8443','192.0.2.1 . 443'),text)

    def exercise(self,fail_health=False,check_only=False,retry=False):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'state';host=root/'host';state.mkdir();host.mkdir();(state/'subscriptions').mkdir()
            nodes=[{'name':f'SLOT-{i}','type':'ss','server':ip,'port':443,'password':'EXAMPLE_ONLY','cipher':'aes-128-gcm','udp':False,'interface-name':'eth3'} for i,ip in enumerate(['1.1.1.1','8.8.8.8','9.9.9.9'])]
            old={'proxies':nodes,'proxy-groups':[{'name':'EXIT','type':'fallback','proxies':[x['name'] for x in nodes]}],'ipv6':False,'interface-name':'eth3','tun':{'strict-route':True,'route-exclude-address':[]},'rules':['NETWORK,udp,REJECT','MATCH,EXIT']}
            candidate=copy.deepcopy(old);candidate['proxy-groups'][0]['type']='select';candidate['proxy-groups'][0]['proxies']=[nodes[0]['name']]
            eps=[{'node':x['name'],'ip':x['server'],'port':443,'protocol':'tcp'} for x in nodes]
            pid='a'*32;plan={'id':pid,'created_at':time.time(),'base_sha256':hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest(),'candidate':candidate,'endpoints':eps}
            (state/'subscriptions'/f'plan-{pid}.json').write_text(json.dumps(plan))
            guard='set tcp_endpoints { type ipv4_addr . inet_service; elements = { 192.0.2.1 . 443 }; } chain output { policy drop; }'
            (host/'host-guard.nft').write_text(guard);(host/'endpoints.json').write_text(json.dumps({'endpoints':eps}));(host/'host-routes.py').write_text('# fixture')
            if retry:
                previous=host/'toolkit-backups'/pid;previous.mkdir(parents=True)
                (previous/'config.yaml').write_text('previous attempt')
            commands=[]
            def run(argv,data=None,timeout=30):
                commands.append(argv)
                if argv[0]=='ssh':
                    cmd=argv[-1]
                    if cmd=='cat /etc/mm-proxy/config.yaml':return yaml.safe_dump(old).encode()
                    if cmd=='cat /etc/mm-proxy/guard.nft':return guard.replace('tcp_endpoints','nodes').encode()
                    if cmd.startswith('s=$(cat /etc/mm-proxy/controller.secret)'):
                        if fail_health:raise ValueError('fixture_health_failure')
                        return b'HTTP/1.0 200 OK\r\n\r\n{"delay": 120}'
                return b''
            with patch.object(module.os,'geteuid',return_value=0),patch.object(module,'secure_host_config'),patch.object(module,'run',side_effect=run),patch.object(module.time,'sleep'),patch.object(module.os,'chown'):
                result=module.apply({'state_dir':str(state),'router':{'ssh':['ssh'],'host_exit_dir':str(host)}},pid,check_only)
            if check_only:
                self.assertEqual((host/'host-guard.nft').read_text(),guard)
                self.assertFalse(any('close' in x or '/etc/init.d/mm-proxy stop' in x for x in commands))
                self.assertTrue(result['validated'])
            elif fail_health:
                self.assertFalse(result['ok']);self.assertEqual(result['error'],'apply_failed_previous_restored')
                self.assertEqual((host/'host-guard.nft').read_text(),guard)
                self.assertGreaterEqual(sum('close' in x for x in commands),2)
            else:
                self.assertTrue(result['ok'])
                close=next(i for i,x in enumerate(commands) if 'close' in x)
                load=next(i for i,x in enumerate(commands) if x[:2]==['nft','-f'])
                self.assertLess(close,load)
            self.assertFalse(any('flush ruleset' in ' '.join(x) for x in commands))
            if retry:
                self.assertEqual((previous/'config.yaml').read_text(),'previous attempt')
                self.assertEqual(len(list((host/'toolkit-backups').iterdir())),2)
    def test_dry_run_does_not_switch(self):self.exercise(check_only=True)
    def test_apply_closes_egress_before_changing_guard(self):self.exercise()
    def test_failed_https_restores_previous_configuration(self):self.exercise(fail_health=True)
    def test_retry_keeps_previous_backup(self):self.exercise(retry=True)

    def test_health_requires_successful_https_measurement(self):
        for raw in (b'HTTP/1.0 504 Timeout\r\n\r\n{"delay": 120}',b'HTTP/1.0 200 OK\r\n\r\n{"error":"failed"}',b'HTTP/1.0 200 OK\r\n\r\n{"delay":false}'):
            with self.subTest(raw=raw),self.assertRaisesRegex(ValueError,'new_exit_https_failed'):
                module.verify_exit(lambda *a,**k:raw,'EXIT')


if __name__=='__main__':unittest.main()
