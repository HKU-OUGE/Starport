"""Fresh-home setup and pairing boundaries; no live services or credentials."""
from pathlib import Path
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


pair=module('pair_helper','tools/pair.py')
preflight=module('preflight_helper','tools/preflight.py')
locator=module('runner_locator_test','client/starship/runner_locator.py')


def sample_key(byte=1):
    raw=struct.pack('>I',11)+b'ssh-ed25519'+struct.pack('>I',32)+bytes([byte])*32
    return 'ssh-ed25519 '+base64.b64encode(raw).decode()+' example-only'


class PairingTests(unittest.TestCase):
    def fixture(self,folder):
        home=Path(folder).resolve();root=home/'.local/share/starship';root.mkdir(parents=True)
        (root/'rpc').write_text('example launcher');(root/'rpc.cmd').write_text('example launcher')
        return home

    def test_key_format_and_fingerprint(self):
        key,fingerprint=pair.public_key(sample_key())
        self.assertTrue(fingerprint.startswith('SHA256:'))
        self.assertNotIn('example-only',key)
        for value in ('ssh-rsa invalid','ssh-ed25519 !!!!',sample_key()+'\n'+sample_key(), 'not a public key'):
            with self.subTest(value=value[:20]),self.assertRaises(ValueError):pair.public_key(value)

    def test_preview_apply_is_idempotent_and_preserves_other_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            home=self.fixture(folder);path=home/'.ssh/authorized_keys'
            self.assertFalse(pair.authorize(sample_key(),home,'Darwin')['changed'])
            self.assertFalse(path.exists())
            path.parent.mkdir();before=sample_key(2)+'\n';path.write_text(before)
            self.assertTrue(pair.authorize(sample_key(),home,'Darwin',True)['changed'])
            first=path.read_text();self.assertTrue(first.startswith(before))
            self.assertIn('restrict,command=',first)
            self.assertFalse(pair.authorize(sample_key(),home,'Darwin',True)['changed'])
            self.assertEqual(path.read_text(),first)

    def test_existing_unrestricted_key_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            home=self.fixture(folder);path=home/'.ssh/authorized_keys';path.parent.mkdir()
            path.write_text(sample_key()+'\n');before=path.read_text()
            with self.assertRaises(ValueError):pair.authorize(sample_key(),home,'Darwin',True)
            self.assertEqual(path.read_text(),before)

    @unittest.skipIf(os.name=='nt','Symlink creation requires host policy on Windows')
    def test_linked_authorized_keys_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            home=self.fixture(folder);p=home/'.ssh';p.mkdir();other=home/'other';other.write_text('keep')
            (p/'authorized_keys').symlink_to(other)
            with self.assertRaises(ValueError):pair.authorize(sample_key(),home,'Darwin',True)
            self.assertEqual(other.read_text(),'keep')

    def test_pin_requires_independently_matching_host_key(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder).resolve();key,fp=pair.public_key(sample_key())
            scan=lambda *a,**kw:types.SimpleNamespace(stdout='192.168.50.2 '+key+'\n',returncode=0)
            self.assertTrue(pair.pin('192.168.50.2',fp,home=home,run=scan)['fingerprint_verified'])
            self.assertFalse((home/'.ssh').exists())
            bad=pair.public_key(sample_key(2))[1]
            with self.assertRaises(ValueError):pair.pin('192.168.50.2',bad,apply=True,home=home,run=scan)
            self.assertFalse((home/'.ssh').exists())
            self.assertTrue(pair.pin('192.168.50.2',fp,apply=True,home=home,run=scan)['changed'])
            self.assertFalse(pair.pin('192.168.50.2',fp,apply=True,home=home,run=scan)['changed'])

    def test_config_does_not_accept_command_or_public_endpoint_injection(self):
        value=pair.ssh_config('192.168.50.2','device-user')
        self.assertIn('StrictHostKeyChecking yes',value);self.assertIn('ForwardAgent no',value)
        self.assertIn('starport_known_hosts',value)
        for host,user in [('1.1.1.1','user'),('192.168.50.2\nHost *','user'),('192.168.50.2','user\nProxyCommand id')]:
            with self.assertRaises(ValueError):pair.ssh_config(host,user)


class RunnerTests(unittest.TestCase):
    def make_executable(self,path):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text('example');path.chmod(0o700)
        return path

    def test_mac_background_path_finds_user_native_install(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(locator.shutil,'which',return_value=None):
            home=Path(folder);binary=self.make_executable(home/'.local/bin/claude')
            self.assertEqual(locator.find_claude(home,'Darwin',{'PATH':''}),str(binary.absolute()))

    def test_windows_native_install_does_not_need_refreshed_terminal_path(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(locator.shutil,'which',return_value=None):
            home=Path(folder);binary=self.make_executable(home/'.local/bin/claude.exe')
            self.assertEqual(locator.find_claude(home,'Windows',{'PATH':''}),str(binary.absolute()))

    def test_saved_path_and_missing_saved_launcher_fallback(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(locator.shutil,'which',return_value=None):
            home=Path(folder);saved=self.make_executable(home/'custom/claude');native=self.make_executable(home/'.local/bin/claude')
            config=home/'.local/share/starship/runner.json';config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'claude_bin':str(saved)}))
            self.assertEqual(locator.find_claude(home,'Linux',{}),str(saved.absolute()))
            saved.unlink();self.assertEqual(locator.find_claude(home,'Linux',{}),str(native.absolute()))

    @unittest.skipIf(os.name=='nt','Symlink creation requires host policy on Windows')
    def test_native_update_symlink_is_not_frozen_to_version(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(locator.shutil,'which',return_value=None):
            home=Path(folder);version=self.make_executable(home/'versions/one');link=home/'.local/bin/claude'
            link.parent.mkdir(parents=True);link.symlink_to(version)
            self.assertEqual(locator.find_claude(home,'Linux',{}),str(link.absolute()))

    def test_invalid_explicit_override_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):locator.find_claude(Path(folder),'Linux',{'STARSHIP_CLAUDE_BIN':'relative/path'})


class InstallationTests(unittest.TestCase):
    def test_client_manual_install_writes_runner_but_never_registers(self):
        setup=module('client_manual_setup','client/install.py')
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder).resolve();binary=home/'claude';binary.write_text('example');binary.chmod(0o700)
            with patch.object(setup.Path,'home',return_value=home),patch.dict(sys.modules,{'client':types.SimpleNamespace(cli=lambda:str(binary))}),patch.object(setup.services,'register') as register,contextlib.redirect_stdout(io.StringIO()):
                setup.main(['--yes','--no-register'])
            register.assert_not_called()
            root=home/'.local/share/starship'
            self.assertEqual(json.loads((root/'runner.json').read_text())['claude_bin'],str(binary))
            self.assertEqual(len(list((root/'state/services').glob('*.json'))),4)
            self.assertTrue((root/'runner_locator.py').exists())

    def test_server_manual_install_does_not_register(self):
        setup=module('server_manual_setup','server/install.py')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();(root/'server/.venv').mkdir(parents=True)
            state=root/'state';config_file=root/'config/console.json'
            config={'state_dir':str(state)}
            def secure(p):p=Path(p);p.mkdir(parents=True,exist_ok=True);return p
            with patch.object(setup,'ROOT',root),patch.object(setup,'make_plan',return_value=(config,{},{})),patch.object(setup,'config_path',return_value=config_file),patch.object(setup,'secure_dir',side_effect=secure),patch.object(setup.shutil,'which',return_value='available'),patch.object(setup.subprocess,'check_output',return_value='v22.12.0'),patch.object(setup.subprocess,'run'),patch.object(setup.services,'prepare',side_effect=lambda name,*a,**kw:(name+'.fixture','fixture')),patch.object(setup.services,'register') as register,contextlib.redirect_stdout(io.StringIO()):
                setup.main(['--yes','--no-register'])
            register.assert_not_called();self.assertTrue(config_file.exists())

    def test_old_node_is_reported_without_running_installers(self):
        run=lambda *a,**kw:types.SimpleNamespace(stdout='v18.20.0',returncode=0)
        result=preflight.checks('server','Linux',which=lambda _: 'available',run=run)
        self.assertFalse(result['prerequisites_ready'])
        self.assertTrue(result['read_only'])
        self.assertTrue(any(x['check']=='node_22_12_or_newer' and x['status']=='missing' for x in result['checks']))


if __name__=='__main__':unittest.main()
