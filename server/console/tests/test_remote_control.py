import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1] / 'client/starship'))
spec = importlib.util.spec_from_file_location('rc', ROOT.parents[1] / 'client/starship/remote_control.py')
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)
SID = 'cse_abcdefghijklmnopqrstuvwx'


class RemoteTests(unittest.TestCase):
    def test_hidden_reasoning_and_account_fields_are_not_displayed(self):
        stream = rc.StreamState(None, SID)
        stream.ingest({'sequence_num': '4', 'sent_by_account_id': 'private-account',
                       'payload': {'type': 'assistant', 'message': {'content': [
                           {'type': 'thinking', 'thinking': 'hidden content'},
                           {'type': 'text', 'text': 'Visible answer'}]}}})
        visible = json.dumps(list(stream.events))
        self.assertIn('Visible answer', visible)
        self.assertNotIn('hidden content', visible)
        self.assertNotIn('private-account', visible)

    def test_reconnected_stream_deduplicates_and_drops_stale_events(self):
        stream = rc.StreamState(None, SID)
        event = {'sequence_num': '7', 'payload': {'type': 'assistant', 'message': {'content': 'One reply'}}}
        stream.ingest(event)
        stream.ingest(event)
        stream.ingest({**event, 'sequence_num': '6'})
        self.assertEqual(len(stream.events), 1)

    def test_permission_disappears_when_another_controller_answers(self):
        stream = rc.StreamState(None, SID)
        stream.ingest({'sequence_num': '1', 'source': 'worker', 'payload': {'type': 'control_request',
                      'request_id': 'p1', 'request': {'subtype': 'can_use_tool', 'tool_name': 'Write', 'input': {'file_path': 'demo.txt'}}}})
        self.assertIn('p1', stream.pending)
        stream.ingest({'sequence_num': '2', 'source': 'client', 'payload': {'type': 'control_response', 'response': {'request_id': 'p1'}}})
        self.assertNotIn('p1', stream.pending)

    def test_redacted_permission_cannot_be_approved_blindly(self):
        stream = rc.StreamState(None, SID)
        stream.ingest({'sequence_num': '1', 'source': 'worker', 'payload': {'type': 'control_request',
                      'request_id': 'p1', 'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash',
                                                   'input': {'command': 'Bearer ' + 'private-value'}}}})
        self.assertFalse(stream.pending['p1']['reviewable'])
        self.assertNotIn('private-value', json.dumps(stream.pending))

    def test_sessions_not_in_the_account_listing_are_not_controllable(self):
        controller = rc.Controller(lambda: {})
        with patch.object(controller, 'listing', return_value={}), patch.object(controller, 'request') as request:
            with self.assertRaisesRegex(ValueError, 'not_found'):
                controller.get(SID)
            request.assert_not_called()

    def test_discovery_includes_unbound_bridge_sessions_and_paginates(self):
        controller = rc.Controller(lambda: {})
        pages = [{'data': [{'id': SID, 'status': 'active', 'environment_kind': 'bridge'}], 'next_cursor': 'page-two'},
                 {'data': [{'id': 'cse_' + 'B' * 24, 'status': 'active', 'environment_kind': 'bridge'},
                           {'id': 'cse_' + 'C' * 24, 'status': 'archived', 'environment_kind': 'bridge'},
                           {'id': 'cse_' + 'D' * 24, 'status': 'active', 'environment_kind': 'cloud'}]}]
        with patch.object(controller, 'request', side_effect=pages) as request:
            result = controller.listing()
        self.assertEqual(set(result), {SID, 'cse_' + 'B' * 24})
        self.assertIn('cursor=page-two', request.call_args[0][0])

    def test_unbound_sessions_can_be_read_without_repeated_detail_requests(self):
        controller = rc.Controller(lambda: {})
        item = {'id': SID, 'status': 'active', 'environment_kind': 'bridge'}
        with patch.object(controller, 'listing', return_value={SID: item}), patch.object(controller, 'bindings', return_value={}), patch.object(controller, 'request') as request:
            self.assertEqual(controller.get(SID), (item, {}))
            request.assert_not_called()
            request.return_value = {'response_shape': item}
            self.assertEqual(controller.get(SID, fresh=True), (item, {}))
            request.assert_called_once_with('sessions/' + SID)

    def test_ultracode_is_unknown_even_when_rejected_folded_flags_exist(self):
        controller = rc.Controller(lambda: {})
        item = {'id': SID, 'connection_status': 'connected', 'worker_status': 'idle',
                'external_metadata': {'effort_level': 'xhigh', 'flag_settings': {'ultracode': False}}}
        public = controller.public(item, {})
        self.assertEqual(public['effort'], 'xhigh')
        self.assertIsNone(public['ultracode'])

    def test_disconnected_unbound_session_is_visible_but_cannot_send(self):
        controller = rc.Controller(lambda: {})
        item = {'id': SID, 'title': 'Another session', 'connection_status': 'disconnected'}
        with patch.object(controller, 'listing', return_value={SID: item}), patch.object(controller, 'bindings', return_value={}):
            public = controller.list_public()
        self.assertEqual(len(public), 1)
        self.assertEqual(public[0]['status'], 'disconnected')
        self.assertFalse(public[0]['can_send'])

    def test_operation_ack_is_durable_and_unknown_outcome_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(rc, 'BASE', Path(directory)):
            (Path(directory) / 'state').mkdir()
            controller = rc.Controller(lambda: {})
            payload = {'type': 'user', 'message': {'role': 'user', 'content': 'Please confirm this message.'}}
            op = str(uuid.uuid4())
            with patch.object(controller, 'request', return_value={'results': [{'event_id': 'x', 'sequence_num': '1'}]}) as request:
                controller.post(SID, payload, op)
                self.assertTrue(controller.post(SID, payload, op)['duplicate'])
                self.assertEqual(request.call_count, 1)
            op = str(uuid.uuid4())
            with patch.object(controller, 'request', side_effect=ValueError('remote_send_outcome_unknown')) as request:
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, 'unknown'):
                        controller.post(SID, payload, op)
                self.assertEqual(request.call_count, 1)

    def test_device_verification_feedback_is_not_bypassed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(rc, 'BASE', Path(directory)):
            (Path(directory) / 'state').mkdir()
            controller = rc.Controller(lambda: {})
            with patch.object(controller, 'request', return_value={'attestation_feedback': {'requires_verification': True}, 'results': []}):
                with self.assertRaisesRegex(ValueError, 'device_verification_required'):
                    controller.post(SID, {'type': 'user'}, str(uuid.uuid4()))


if __name__ == '__main__':
    unittest.main()
