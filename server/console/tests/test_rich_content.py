import base64
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'remote'))
from rich_content import MediaStore, blocks_to_events, bounded_events


class RichContentTests(unittest.TestCase):
    def test_tool_command_and_matching_result_survive(self):
        rows = blocks_to_events([
            {'type': 'thinking', 'thinking': 'private reasoning'},
            {'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': 'python3 train.py --steps 2', 'description': 'Run a smoke test', 'api_key': 'private-value'}},
            {'type': 'tool_result', 'tool_use_id': 't1', 'is_error': True, 'content': 'Exit code 3\nFailed to read the file'}], 'assistant')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['input']['command'], 'python3 train.py --steps 2')
        self.assertEqual(rows[0]['input']['api_key'], '[REDACTED]')
        self.assertEqual(rows[0]['tool_id'], rows[1]['tool_id'])
        self.assertEqual(rows[1]['exit_code'], 3)

    def test_media_only_from_registered_session_and_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'; project.mkdir()
            path = project / 'clip.mp4'; path.write_bytes(b'\0\0\0\x20ftypisom' + bytes(range(100)))
            store = MediaStore(root / 'service')
            item = store.register_file('a', str(path), [project])
            out = store.read('a', item['id'], [project], 6, 20)
            self.assertEqual(base64.b64decode(out['data']), path.read_bytes()[6:26])
            self.assertEqual(out['chunk_sha256'], hashlib.sha256(path.read_bytes()[6:26]).hexdigest())
            with self.assertRaises(ValueError): store.read('b', item['id'], [project], 0, 1)
            with self.assertRaises(ValueError): store.register_file('a', str(path), [root / 'another'])
            with self.assertRaises(ValueError): store.read('a', item['id'], [project], 0, 2**22)
            with self.assertRaises(ValueError): store.register_file('a', 'https://private.invalid/a.mp4', [project])

    def test_link_substitution_and_changed_files_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / 'project'; project.mkdir()
            path = project / 'image.png'; path.write_bytes(b'\x89PNG\r\n\x1a\n123')
            store = MediaStore(root / 'service')
            item = store.register_file('a', str(path), [project])
            path.write_bytes(b'\x89PNG\r\n\x1a\nchanged')
            with self.assertRaisesRegex(ValueError, 'changed'): store.read('a', item['id'], [project], 0, 1)
            link = project / 'linked.png'; link.symlink_to(path)
            with self.assertRaises(OSError): store.register_file('a', str(link), [project])
            path.write_text('not an image')
            with self.assertRaisesRegex(ValueError, 'type_mismatch'): store.register_file('a', str(path), [project])

    def test_senduserfile_creates_media_card_and_inline_images_are_not_in_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / 'project'; project.mkdir()
            path = project / 'clip.mp4'; path.write_bytes(b'\0\0\0\x20ftypisom0000000')
            store = MediaStore(root / 'service')
            rows = blocks_to_events([{'type':'tool_use','id':'t','name':'SendUserFile','input':{'files':[str(path)],'caption':'A short video'}}], 'assistant', store, 'a', [project])
            self.assertTrue(rows[1]['attachments'][0]['available'])
            png = b'\x89PNG\r\n\x1a\nabc'
            rows = blocks_to_events([{'type':'image','source':{'type':'base64','media_type':'image/png','data':base64.b64encode(png).decode()}}], 'user', store, 'a', [project])
            item = rows[0]['attachments'][0]
            self.assertNotIn('data', item)
            self.assertEqual(base64.b64decode(store.read('a',item['id'],[],0,len(png))['data']),png)

    def test_budget_keeps_recent_events_without_oversized_rpc(self):
        rows = [{'seq':i,'text':'x'*200} for i in range(30)]
        picked = bounded_events(rows,1000)
        self.assertLess(len(picked),len(rows))
        self.assertEqual(picked[-1]['seq'],29)


if __name__ == '__main__': unittest.main()
