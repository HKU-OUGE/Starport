"""Validate the small public settings form before the native control boundary."""
import re


def checked_settings(data):
    if set(data) - {'action', 'session_id', 'operation_id', 'setting', 'value', 'expected', 'ultracode'}:
        raise ValueError('invalid_model_settings')
    sid = data.get('session_id')
    if not isinstance(sid, str) or not re.fullmatch(r'cse_[A-Za-z0-9]{16,64}', sid):
        raise ValueError('model_settings_remote_only')
    setting = data.get('setting')
    choices = {'model': ('opus', 'sonnet', 'haiku', 'fable', 'default'), 'effort': ('low', 'medium', 'high', 'xhigh', 'max')}
    if setting not in choices or data.get('value') not in choices[setting]:
        raise ValueError('invalid_model_settings')
    expected = data.get('expected')
    if not isinstance(expected, dict) or set(expected) != {'model', 'effort', 'ultracode'}:
        raise ValueError('invalid_model_settings')
    if any(not isinstance(expected[k], str) or len(expected[k]) > 80 for k in ('model', 'effort')):
        raise ValueError('invalid_model_settings')
    if expected['ultracode'] is not None and type(expected['ultracode']) is not bool:
        raise ValueError('invalid_model_settings')
    result = {'action': 'model_settings', 'session_id': sid, 'setting': setting, 'value': data['value'], 'expected': expected}
    if setting == 'effort':
        if data.get('ultracode') not in ('preserve', 'on', 'off'):
            raise ValueError('invalid_model_settings')
        result['ultracode'] = data['ultracode']
    elif 'ultracode' in data:
        raise ValueError('invalid_model_settings')
    return result
