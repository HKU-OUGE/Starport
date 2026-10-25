import importlib.util,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
class MaintenanceTests(unittest.TestCase):
 def load(self):
  spec=importlib.util.spec_from_file_location('auth_check_test',ROOT.parents[1]/'client/starship/session_auth_check.py')
  module=importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules,{'client':types.SimpleNamespace(cli=lambda:'/official/claude')}):spec.loader.exec_module(module)
  return module
 def test_valid_credentials_do_not_start_cli(self):
  m=self.load();safe={'available':True,'expired':False,'remaining_seconds':20000,'refresh_present':True}
  with tempfile.TemporaryDirectory() as folder,patch.object(m,'STATE',Path(folder)),patch.object(m,'metadata',return_value=safe),patch.object(m.subprocess,'run') as run:
   m.main();run.assert_not_called();out=json.loads((Path(folder)/'status.json').read_text());self.assertFalse(out['renewal_checked'])
 def test_expiring_credentials_use_doctor_without_prompt(self):
  m=self.load();before={'available':True,'expired':True,'remaining_seconds':-1,'refresh_present':True};after={**before,'expired':False,'remaining_seconds':28800}
  with tempfile.TemporaryDirectory() as folder,patch.object(m,'STATE',Path(folder)),patch.object(m,'metadata',side_effect=[before,after]),patch.object(m.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
   m.main();self.assertEqual(run.call_args.args[0],['/official/claude','doctor']);self.assertNotIn('input',run.call_args.kwargs);out=json.loads((Path(folder)/'status.json').read_text());self.assertEqual(out['model_messages_sent'],0);self.assertFalse(out['after']['expired'])
if __name__=='__main__':unittest.main()
