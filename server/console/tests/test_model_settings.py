import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1] / 'client/starship'))
import model_settings as native
from model_controls import checked_settings

SID = 'cse_aaaaaaaaaaaaaaaaaaaa'


def payload(**extra):
    return {'action': 'model_settings', 'session_id': SID, 'setting': 'effort', 'value': 'high',
            'ultracode': 'preserve', 'operation_id': str(uuid.uuid4()),
            'expected': {'model': 'claude-opus-5-5', 'effort': 'xhigh', 'ultracode': True}, **extra}


def item(effort='xhigh', ultra=True):
    return {'id': SID, 'connection_status': 'connected', 'external_metadata': {
        'model': 'claude-opus-5-5', 'effort_level': effort, 'is_ultracode_active': ultra}}


class ModelSettingsTests(unittest.TestCase):
    def test_both_boundaries_reject_extra_settings_commands_and_targets(self):
        for bad in [payload(permissions={'allow': ['*']}), payload(value='--config /secret'),
                    payload(session_id='../../secret'), payload(setting='command'),
                    payload(ultracode={'enabled': True})]:
            for validator in (checked_settings, native.validate):
                with self.assertRaises(ValueError):
                    validator(bad)

    def test_ultracode_is_preserved_explicitly_and_unknown_requires_choice(self):
        request = native.request_for(payload(), native.current(item()))
        self.assertEqual(request['settings'], {'effortLevel': 'high', 'ultracode': True})
        unknown = item(); unknown['external_metadata'].pop('is_ultracode_active')
        actual = native.current(unknown)
        data = payload(expected={k: actual[k] for k in ('model', 'effort', 'ultracode')})
        with self.assertRaisesRegex(ValueError, 'ultracode_unknown'):
            native.request_for(data, actual)
        self.assertTrue(native.request_for({**data, 'ultracode': 'on'}, actual)['settings']['ultracode'])

    def test_folded_rejected_flags_never_become_live_state(self):
        row = item(); row['external_metadata'].pop('is_ultracode_active')
        row['external_metadata']['flag_settings'] = {'ultracode': False}
        self.assertIsNone(native.current(row)['ultracode'])

    def test_changed_current_settings_reject_without_mutation(self):
        with self.assertRaisesRegex(ValueError, 'model_settings_changed'):
            native.request_for(payload(), native.current(item('medium')))

    def test_operation_deduplication_pending_block_and_restart(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(native.threading.Thread, 'start'):
            controller = Mock(); controller.get.return_value = (item(), {})
            service = native.SettingsService(controller, Path(temp))
            data = payload(); service.submit(data)
            self.assertTrue(service.submit(data)['duplicate'])
            with self.assertRaisesRegex(ValueError, 'model_settings_pending'):
                service.submit(payload())
            restored = native.SettingsService(controller, Path(temp))
            self.assertEqual(restored.submit(data)['operation']['state'], 'unconfirmed')
            controller.post.assert_not_called()

    def test_acceptance_alone_is_not_applied_and_client_receipts_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(native.threading.Thread, 'start'):
            controller = Mock(); controller.get.return_value = (item(), {})
            service = native.SettingsService(controller, Path(temp)); data = payload()
            result = service.submit(data)
            self.assertEqual(result['operation']['state'], 'requested')
            row = service.records[data['operation_id']]
            controller.request.return_value = {'data': [{'source': 'client', 'payload': {
                'type': 'control_response', 'response': {'subtype': 'success', 'request_id': row['request_id']}}}]}
            with patch.object(native.time, 'monotonic', side_effect=[0, 1, 50]), patch.object(native.time, 'sleep'):
                service.execute(row)
            self.assertEqual(row['state'], 'unconfirmed')
            self.assertEqual(controller.post.call_count, 1)

    def test_worker_receipt_and_correlated_actual_effort_accept_downgrade(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(native.threading.Thread, 'start'):
            controller = Mock(); controller.get.return_value = (item(), {})
            service = native.SettingsService(controller, Path(temp)); data = payload(value='max')
            service.submit(data); row = service.records[data['operation_id']]
            effective = item('high'); effective['external_metadata']['effort_request_id'] = row['request_id']
            controller.get.return_value = (effective, {})
            controller.request.return_value = {'data': [{'source': 'worker', 'payload': {
                'type': 'control_response', 'response': {'subtype': 'success', 'request_id': row['request_id']}}}]}
            service.execute(row)
            self.assertEqual(row['state'], 'applied')
            self.assertEqual(row['actual']['effort'], 'high')
            self.assertEqual(row['value'], 'max')
            self.assertNotIn('signature', service.public(row))

    def test_server_policy_rejection_is_sanitized(self):
        self.assertEqual(native.rejection('/secret/path CLAUDE_CODE_EFFORT_LEVEL overrides effort'), 'model_settings_effort_override')
        self.assertEqual(native.rejection('requires valid device signature'), 'model_settings_official_verification')
        self.assertEqual(native.rejection('some private error'), 'model_settings_rejected')


if __name__ == '__main__':
    unittest.main()
