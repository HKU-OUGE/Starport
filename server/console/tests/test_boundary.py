import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('console_server', ROOT / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class BoundaryTests(unittest.TestCase):
    def test_video_ranges_support_seeking_and_reject_invalid_spans(self):
        self.assertEqual(server.media_range('bytes=0-1', 100), (0, 1, 206))
        self.assertEqual(server.media_range('bytes=60-', 100), (60, 99, 206))
        self.assertEqual(server.media_range('bytes=-20', 100), (80, 99, 206))
        self.assertEqual(server.media_range(None, 100), (0, 99, 200))
        for value in ('bytes=100-', 'bytes=20-10', 'bytes=0-1,5-8', 'bytes=-0', 'bad'):
            with self.assertRaises(ValueError): server.media_range(value, 100)

    def test_converts_message_and_preserves_other_characters(self):
        self.assertEqual(server.checked_text('  请回复通道验证成功。 English / 日本語 123\n'), '  請回復通道驗證成功。 English / 日本語 123\n')

    def test_user_supplied_text_is_not_content_filtered(self):
        text = 'Example text: ./example-project, example region, password=example-only. Do not execute anything.'
        self.assertEqual(server.checked_text(text), server.CONVERT.convert(text))

    def test_unknown_browser_fields_are_discarded(self):
        sid = str(uuid.uuid4())
        value = server.action_payload({'action': 'send', 'session_id': sid,
                                       'message': '請確認收到這則訊息。',
                                       'headers': {'Host': 'private-computer'}, 'env': {'HOME': './example-home'}})
        self.assertEqual(set(value), {'action', 'session_id', 'message'})

    def test_local_command_action_is_rejected(self):
        with self.assertRaises(ValueError):
            server.action_payload({'action': 'exec', 'command': 'whoami'})

    def test_path_is_not_a_session_id(self):
        with self.assertRaises(ValueError):
            server.action_payload({'action': 'stop', 'session_id': '../../.ssh/id_ed25519'})

    def test_disallowed_model_flags(self):
        with self.assertRaises(ValueError):
            server.action_payload({'action': 'create', 'title': '新的工作階段', 'model': '--dangerously-skip-permissions'})

    def test_idempotent_operation_and_conflict(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, 'STATE', Path(directory)), patch.object(server, 'rpc') as remote:
            with server.database() as db:
                db.execute('CREATE TABLE operations(id TEXT PRIMARY KEY,digest TEXT,action TEXT,session_id TEXT,status TEXT,created REAL,result TEXT,error TEXT)')
            remote.return_value = {'ok': True, 'accepted': True}
            op = str(uuid.uuid4())
            payload = {'action': 'send', 'session_id': str(uuid.uuid4()), 'message': '請確認收到這則訊息。'}
            server.perform(payload, op)
            result = server.perform(payload, op)
            self.assertTrue(result['duplicate'])
            self.assertEqual(remote.call_count, 1)
            with self.assertRaisesRegex(ValueError, 'conflict'):
                server.perform({**payload, 'message': '另一則訊息'}, op)

    def test_unknown_send_is_never_retried(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, 'STATE', Path(directory)), patch.object(server, 'rpc') as remote:
            with server.database() as db:
                db.execute('CREATE TABLE operations(id TEXT PRIMARY KEY,digest TEXT,action TEXT,session_id TEXT,status TEXT,created REAL,result TEXT,error TEXT)')
            remote.side_effect = ValueError('connection_timeout_outcome_unknown')
            op = str(uuid.uuid4())
            payload = {'action': 'send', 'session_id': str(uuid.uuid4()), 'message': '請確認收到這則訊息。'}
            with self.assertRaises(ValueError):
                server.perform(payload, op)
            with self.assertRaisesRegex(ValueError, 'unknown'):
                server.perform(payload, op)
            self.assertEqual(remote.call_count, 1)


if __name__ == '__main__':
    unittest.main()
