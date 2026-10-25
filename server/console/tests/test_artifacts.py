import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT.parents[1] / 'client/starship'))
from artifact_view import document_view
import artifact_reader as reader


def document(xml, **extra):
    return {'available': True, 'id': 'abcdefghijklmnopqrst', 'title': 'Example', 'xml': xml,
            'official_url': 'https://claude.ai/artifact/abcdefghijklmnopqrst', **extra}


class DocumentViewTests(unittest.TestCase):
    def test_preserves_headings_tables_math_and_never_emits_script_tags(self):
        value = document("<doc><paragraph heading='1'>Title</paragraph><paragraph>Hello <text><bold>bold</bold></text></paragraph><table><row><cell header='true'><paragraph>A</paragraph></cell></row></table><codeBlock language='latex'>x^2</codeBlock><script>alert(1)</script><link href='javascript:alert(1)'>bad</link></doc>")
        result = document_view(value)
        nodes = result['content']['children']
        self.assertEqual(nodes[0]['tag'], 'h1'); self.assertEqual(nodes[2]['tag'], 'table')
        self.assertEqual(nodes[3]['tag'], 'math'); self.assertEqual(nodes[4]['tag'], 'span')
        self.assertEqual(nodes[5]['href'], ''); self.assertEqual(len(result['toc']), 1)
        self.assertTrue(result['missing'])

    def test_entity_and_deep_xml_rejected(self):
        with self.assertRaises(ValueError): document_view(document('<!DOCTYPE doc [<!ENTITY x "oops">]><doc>&x;</doc>'))
        with self.assertRaises(ValueError): document_view(document('<doc>' + '<text>' * 42 + 'x' + '</text>' * 42 + '</doc>'))
        with self.assertRaisesRegex(ValueError,'partial'): document_view(document('<doc parent="table"><table/></doc>'))

    def test_images_verified_and_author_ids_not_exported(self):
        raw = b'\x89PNG\r\n\x1a\nexample'
        image = {'data': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest(), 'stale': True}
        result = document_view(document("<doc><mention user='private-id' name='Author'/><embed ref='node/a' caption='Figure'/></doc>",images={'node/a': image}))
        self.assertNotIn('private-id', json.dumps(result))
        self.assertTrue(result['content']['children'][1]['image']['stale'])
        with self.assertRaises(ValueError): document_view(document('<doc/>', images={'node/a': {**image,'sha256':'wrong'}}))


class ArtifactReaderTests(unittest.TestCase):
    def test_only_exact_official_links_are_registered(self):
        rows=[('',[{'type':'text','text':'[Report](https://claude.ai/artifact/abcdefghijklmnopqrst) https://claude.ai.evil/artifact/abcdefghijklmnopqrst'}],Path('/unused'))]
        found,_=reader.artifact_links(rows)
        self.assertEqual(list(found),['abcdefghijklmnopqrst']); self.assertEqual(found['abcdefghijklmnopqrst']['title'],'Report')

    def test_request_scope_and_unknown_session_fail_before_files(self):
        with self.assertRaisesRegex(ValueError, 'action_blocked'):
            reader.read_artifacts({'action':'read','session_id':'anything','path':'/etc/passwd'})
        with patch.object(reader,'native_rpc',return_value={'sessions':[]}):
            with self.assertRaisesRegex(ValueError,'session_unavailable'):
                reader.resolve_session('cse_1234567890123456')

    def test_other_artifact_cannot_be_requested_by_guessing(self):
        with patch.object(reader,'resolve_session',return_value=({'path':'/unused','cwd':'/p'},{})), patch.object(reader,'rows_from',return_value=([],False)), patch.object(reader,'remote_rows',return_value=([],False)):
            with self.assertRaisesRegex(ValueError,'not_in_session'):
                reader.read_artifacts({'action':'read','session_id':'cse_1234567890123456','artifact_id':'abcdefghijklmnopqrst'})

    def test_partial_updates_and_other_artifacts_do_not_replace_full_document(self):
        def call(tid,slug,kind='view'):
            return {'type':'tool_use','id':tid,'input':{'container':{'id':slug},'engine':'prose','ref':{'id':'n'},'payload':{'kind':kind}}}
        def response(tid,xml):
            return {'type':'tool_result','tool_use_id':tid,'content':json.dumps({'data':{'xml':xml}})}
        rows=[('1',[call('a','allowed'),response('a',"<doc node='n' rev='2'><paragraph>complete</paragraph></doc>")],Path('/unused')),
              ('2',[call('b','allowed'),response('b',"<doc op='0'><paragraph>partial</paragraph></doc>")],Path('/unused')),
              ('3',[call('c','other'),response('c',"<doc node='n' rev='3'><paragraph>other</paragraph></doc>")],Path('/unused'))]
        result=reader.extract_document(rows,'allowed')
        self.assertEqual(result['revision'],2); self.assertIn('complete',result['xml']); self.assertEqual(result['available_figures'],0)

    def test_snapshot_paths_cannot_escape_the_transcript_result_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'session.jsonl';output=root/'session/tool-results';output.mkdir(parents=True)
            png=b'\x89PNG\r\n\x1a\nexample';outside=root/'outside.png';outside.write_bytes(png)
            def block(p):return {'content':[{'type':'text','text':f'[Image: source: {p}]'}]}
            self.assertIsNone(reader.screenshot(block(outside),path))
            linked=output/'link.png';linked.symlink_to(outside)
            self.assertIsNone(reader.screenshot(block(linked),path))
            inside=output/'figure.png';inside.write_bytes(png)
            self.assertEqual(reader.screenshot(block(inside),path),png)

    def test_history_reader_ignores_api_request_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'session.jsonl'
            p.write_text('\n'.join(json.dumps({'type':kind,'message':{'content':[{'type':'text','text':kind}]}}) for kind in ['assistant','api-request-blob','user']), encoding='utf-8')
            rows,limited=reader.rows_from(p)
            self.assertEqual(len(rows),2);self.assertFalse(limited)

    def test_remote_only_session_and_registered_remote_artifact(self):
        sid='cse_1234567890123456'
        rows=[('1',[{'type':'text','text':'[Report](https://claude.ai/artifact/abcdefghijklmnopqrst)'}],None)]
        with patch.object(reader,'resolve_session',return_value=(None,{})), patch.object(reader,'remote_rows',return_value=(rows,False)), patch.object(reader,'rows_from',side_effect=AssertionError('no local files')):
            result=reader.read_artifacts({'action':'list','session_id':sid,'full_history':True})
        self.assertEqual(result['artifacts'][0]['title'],'Report')
        self.assertEqual(result['record_source'],'remote_control')

    def test_remote_images_require_verified_chunks_and_never_read_source_paths(self):
        raw=b'\x89PNG\r\n\x1a\nexample';sid='cse_1234567890123456';mid='a'*64
        def rpc(request):
            if 'offset' not in request:return {'media':{'mime':'image/png','size':len(raw)}}
            return {'media':{'offset':0,'data':base64.b64encode(raw).decode(),'chunk_sha256':hashlib.sha256(raw).hexdigest()}}
        with patch.object(reader,'native_rpc',side_effect=rpc):
            self.assertEqual(reader.remote_image(sid,mid),raw)
        with patch.object(reader,'native_rpc',side_effect=lambda r: {'media':{'mime':'image/png','size':len(raw)}} if 'offset' not in r else {'media':{'offset':0,'data':base64.b64encode(raw).decode(),'chunk_sha256':'wrong'}}):
            with self.assertRaisesRegex(ValueError,'integrity'):reader.remote_image(sid,mid)
        with patch.object(reader,'safe_file',side_effect=AssertionError('no arbitrary path')):
            self.assertIsNone(reader.screenshot({'content':[{'type':'text','text':'[Image: source: /private/file.png]'}]},None))

    def test_gap_and_since_revision_are_not_complete_snapshots(self):
        for xml in ["<doc node='n' rev='12'><gap blocks='44'/></doc>","<doc node='n' rev='12' sinceRev='10'><paragraph>partial</paragraph></doc>","<doc node='n' rev='12' outline='true'><paragraph>heading</paragraph></doc>","<doc node='n' rev='12' parent='table'><table/></doc>"]:
            rows=[('1',[{'type':'tool_use','id':'a','input':{'container':{'id':'c'},'engine':'prose','ref':{'id':'n'},'payload':{'kind':'view'}}},{'type':'tool_result','tool_use_id':'a','content':json.dumps({'data':{'xml':xml}})}],None)]
            self.assertIsNone(reader.extract_document(rows,'c'))

    def test_full_history_is_streamed_and_filters_unrelated_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'s.jsonl'
            rows=[{'type':'assistant','message':{'content':[{'type':'tool_use','name':'Artifact','id':'t','input':{}}]}}, {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'t','content':'result'}]}}, {'type':'assistant','message':{'content':[{'type':'text','text':'unrelated'}]}}]
            p.write_text('\n'.join(map(json.dumps,rows)), encoding='utf-8')
            result,limited=reader.rows_from(p,True)
            self.assertEqual(len(result),2);self.assertFalse(limited)

    def test_original_html_requires_a_matching_publication_and_scoped_file(self):
        aid='a'*20
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);file=root/'index.html';file.write_text('<html>Example</html>', encoding='utf-8')
            rows=[('1',[{'type':'tool_use','id':'t','name':'Artifact','input':{'file_path':str(file)}},{'type':'tool_result','tool_use_id':'t','content':'Published '+str(file)+' at https://claude.ai/artifact/'+aid+' (Version 1)'}],None)]
            self.assertIsNone(reader.published_html(rows,aid))
            value=reader.published_html(rows,aid,(root,));self.assertEqual(value['sha256'],hashlib.sha256(file.read_bytes()).hexdigest())
            self.assertIsNone(reader.published_html(rows,'b'*20,(root,)))
            file.unlink();file.symlink_to(root/'other.html');(root/'other.html').write_text('outside', encoding='utf-8')
            self.assertIsNone(reader.published_html(rows,aid,(root,)))


if __name__=='__main__':unittest.main()
