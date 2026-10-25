import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1] / 'client/starship'))
import document_reader as reader
from rich_content import MediaStore, blocks_to_events

SID = 'cse_' + 'A' * 24
LID = '22222222-2222-4222-8222-222222222222'


class DocumentTests(unittest.TestCase):
    def test_only_explicit_local_pdf_references_are_registered(self):
        events = [
            {'role':'tool','tool_name':'SendUserFile','input':{'files':['results/report.pdf','https://private.invalid/a.pdf','/etc/passwd']}},
            {'role':'assistant','text':'[Report](results/report.pdf) [Spaced](<results/two words.PDF>) [Web](https://example.com/a.pdf)'},
            {'role':'tool','tool_name':'Bash','input':{'command':'cat /private/secret.pdf'}},
            {'role':'user','text':'[try](/private/secret.pdf)'}]
        self.assertEqual(reader.references(events), ['results/report.pdf','results/two words.PDF'])
        self.assertIsNone(reader.local_pdf('file:///etc/report.pdf'))
        self.assertIsNone(reader.local_pdf('data:application/pdf;base64,AAAA'))

    def test_exact_bridge_mapping_not_title_and_ambiguity_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td);project=home/'project';project.mkdir()
            manifests=home/'Library/Application Support/Claude/claude-code-sessions/account/org';manifests.mkdir(parents=True)
            index={LID:{'cwd':str(project)}}
            path=manifests/('local_'+LID+'.json')
            path.write_text(json.dumps({'title':'Same title','cliSessionId':LID,'cwd':str(project),'bridgeSessionIds':['session_'+SID[4:]]}))
            self.assertEqual(reader.resolve_project(SID,{},index,home),project)
            index[LID]['cwd']=str(project/'results/reports')
            self.assertEqual(reader.resolve_project(SID,{},index,home),project)
            index[LID]['cwd']=str(home/'unrelated')
            self.assertIsNone(reader.resolve_project(SID,{},index,home))
            index[LID]['cwd']=str(project)
            self.assertIsNone(reader.resolve_project('cse_'+'B'*24,{'title':'Same title'},index,home))
            other='11111111-1111-4111-8111-111111111111';index[other]={'cwd':str(project)}
            (manifests/('local_'+other+'.json')).write_text(json.dumps({'cliSessionId':other,'cwd':str(project),'bridgeSessionIds':[SID]}))
            with self.assertRaisesRegex(ValueError,'ambiguous'):reader.resolve_project(SID,{},index,home)

    def test_download_range_hash_and_project_session_boundaries(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);project=root/'project';project.mkdir();service=root/'service'
            path=project/'report.pdf';body=b'%PDF-1.4\n'+b'example'*90;path.write_bytes(body)
            events=[{'role':'tool','tool_name':'SendUserFile','input':{'files':[str(path)]}}]
            with patch.object(reader,'BASE',service),patch.object(reader,'context',return_value=[project]),patch.object(reader,'native_rpc',return_value={'events':events}):
                value=reader.dispatch({'action':'list','session_id':SID});item=value['files'][0]
                self.assertTrue(item['available']);self.assertNotIn('path',item)
                data=reader.dispatch({'action':'media','session_id':SID,'media_id':item['id'],'offset':5,'length':35})['media']
                self.assertEqual(base64.b64decode(data['data']),body[5:40])
                self.assertEqual(data['chunk_sha256'],hashlib.sha256(body[5:40]).hexdigest())
                with self.assertRaisesRegex(ValueError,'not_found'):
                    reader.dispatch({'action':'media','session_id':'cse_'+'B'*24,'media_id':item['id']})
                path.write_bytes(body+b'changed')
                with self.assertRaisesRegex(ValueError,'changed'):
                    reader.dispatch({'action':'media','session_id':SID,'media_id':item['id']})
                with self.assertRaisesRegex(ValueError,'action_blocked'):
                    reader.dispatch({'action':'list','session_id':SID,'path':'/private/secret.pdf'})
                with patch.object(reader,'context',return_value=[]):
                    result=reader.dispatch({'action':'list','session_id':SID})
                    self.assertEqual(result['files'][0]['error'],'document_not_on_device')

    def test_fake_pdf_symlink_and_outside_file_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);project=root/'project';project.mkdir();store=MediaStore(root/'service')
            path=project/'report.pdf';path.write_bytes(b'<html>not a PDF</html>')
            with self.assertRaisesRegex(ValueError,'type_mismatch'):store.register_file(SID,str(path),[project])
            outside=root/'outside.pdf';outside.write_bytes(b'%PDF-1.4\n')
            with self.assertRaisesRegex(ValueError,'path_blocked'):store.register_file(SID,str(outside),[project])
            link=project/'linked.pdf';link.symlink_to(outside)
            with self.assertRaises(OSError):store.register_file(SID,str(link),[project])
            rows=blocks_to_events([{'type':'tool_use','name':'SendUserFile','input':{'files':[str(outside)]}}],'assistant',store,SID,[project])
            self.assertFalse(rows[1]['attachments'][0]['available'])


if __name__=='__main__':unittest.main()
