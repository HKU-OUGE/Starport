"""Cross-platform installation and transport contracts, with no live services."""
from pathlib import Path
import argparse
from starport_common.runtime import fcntl
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server/console'))
from settings import validate, load


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


server = module('starport_installer', 'server/install.py')
client = module('starship_installer', 'client/install.py')
gateway = module('starship_gateway', 'client/starship/gateway.py')


class PackagingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd validation unavailable')
    def test_generated_unit_accepts_paths_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='starport test ') as folder, patch.object(server,'ROOT',Path(folder)):
            path=Path(folder)/'starport-test.service'
            path.write_text(server.unit('Starport test',[sys.executable,'-c','pass']))
            result=subprocess.run(['systemd-analyze','--user','verify',str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_configuration_rejects_public_listeners_and_command_aliases(self):
        for bad in ({'host':'0.0.0.0'}, {'host':'8.8.8.8'}, {'host':'::1'}, {'host':'169.254.1.1'},
                    {'ssh_host':'-Fconfig'}, {'ssh_host':'ship;id'}, {'port':True},
                    {'public_origin':'https://example.invalid/path'}, {'management_url':'javascript:alert(1)'},
                    {'port':18883}, {'command':'sh'}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                validate(bad)
        self.assertEqual(validate({})['ssh_host'], 'starship')

    def test_configuration_paths_resolve_from_external_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'console.json'; path.write_text('{"state_dir":"private/state"}')
            self.assertEqual(load(path)['state_dir'],str(path.parent/'private/state'))

    def test_forced_command_accepts_only_six_literal_endpoints(self):
        for name, command in gateway.ENDPOINTS.items():
            self.assertEqual(gateway.resolve([name]),command)
            self.assertEqual(gateway.resolve([],gateway.COMMAND+name),command)
        for text in ('id', gateway.COMMAND+'session;id',gateway.COMMAND+'session extra','',gateway.COMMAND+'../client'):
            with self.assertRaises(ValueError):gateway.resolve([],text)
        with self.assertRaises(ValueError):gateway.resolve(['session'],gateway.COMMAND+'session')

    def test_mac_agent_paths_are_derived_and_round_trip_as_plist(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder); root,units=client.installation(home,Path(sys.executable))
            self.assertEqual(len(units),4)
            for name,data in units.items():
                self.assertTrue(name.startswith('io.starport.starship'))
                self.assertIn(str(root),data['ProgramArguments'][1])
                self.assertEqual(plistlib.loads(plistlib.dumps(data)),data)
                self.assertEqual(data['Umask'],0o077)
            self.assertEqual(units['io.starport.starship-auth.plist']['StartInterval'],300)

    def test_client_update_refuses_live_daemon_and_unfinished_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'state';state.mkdir()
            with (state/'service.lock').open('w') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(ValueError):client.check_inactive(root)
            session=state/('1'*36+'.json');session.write_text('{"status":"running"}')
            with self.assertRaises(ValueError):client.check_inactive(root)
            session.write_text('{"status":"idle"}');client.check_inactive(root)

    def test_server_plan_registers_only_owned_units_and_keeps_external_state(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'XDG_CONFIG_HOME':folder+'/config','XDG_STATE_HOME':folder+'/state'}):
            args=argparse.Namespace(config=None,host=None,port=None,ssh_host=None,public_origin=None,
                management_url=None,panel_port=18882,legacy_root=None,allowed_subnet=None,router_profile=None)
            config,panel,units=server.make_plan(args)
            self.assertEqual(set(units),{'starport-console.service','starport-panel.service'})
            self.assertIsNone(panel['legacy_root'])
            self.assertTrue(config['state_dir'].startswith(folder))
            self.assertNotIn('stop ',''.join(units.values()))
            self.assertFalse(Path(folder,'config').exists())


if __name__=='__main__':unittest.main()
