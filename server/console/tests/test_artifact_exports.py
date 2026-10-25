import hashlib,json,tempfile,unittest
from pathlib import Path
from artifact_exports import latest_export,export_bundle
class BundleTests(unittest.TestCase):
 def test_integrity_manifest_and_path_limits(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);eid='a'*32;folder=root/'artifact-exports'/eid;folder.mkdir(parents=True)
   raw=b'zip-test';(folder/'artifacts.zip').write_bytes(raw)
   manifest={'id':eid,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'html_count':2}
   (folder/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8');(folder.parent/'latest.json').write_text(json.dumps({'id':eid}), encoding='utf-8')
   self.assertEqual(latest_export(root)['html_count'],2);self.assertEqual(export_bundle(root,eid),raw)
   (folder/'artifacts.zip').write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'integrity'):export_bundle(root,eid)
   with self.assertRaisesRegex(ValueError,'not_found'):export_bundle(root,'../../outside')
   (folder/'artifacts.zip').unlink();(folder/'artifacts.zip').symlink_to(folder/'manifest.json')
   with self.assertRaisesRegex(ValueError,'invalid'):export_bundle(root,eid)
