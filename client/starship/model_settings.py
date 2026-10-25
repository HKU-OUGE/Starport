"""Session-only model controls. Native credentials never leave this process."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import plistlib
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid

from remote_control import Controller

BASE = Path.home() / '.local/share/starship'
ROOT = BASE / 'state/model-settings'
SOCKET = BASE / 'state/model-settings.sock'
MODELS = ('opus', 'sonnet', 'haiku', 'fable', 'default')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')


def validate(data):
    if not isinstance(data, dict) or set(data) - {'action', 'session_id', 'setting', 'value', 'ultracode', 'expected', 'operation_id'}:
        raise ValueError('invalid_model_settings')
    if not isinstance(data.get('session_id'), str) or not re.fullmatch(r'cse_[A-Za-z0-9]{16,64}', data['session_id']):
        raise ValueError('model_settings_remote_only')
    if data.get('action') == 'read' and set(data) == {'action', 'session_id'}:
        return dict(data)
    if data.get('action') != 'model_settings' or data.get('setting') not in ('model', 'effort'):
        raise ValueError('invalid_model_settings')
    choices = MODELS if data['setting'] == 'model' else EFFORTS
    if data.get('value') not in choices:
        raise ValueError('invalid_model_settings')
    if data['setting'] == 'model' and 'ultracode' in data:
        raise ValueError('invalid_model_settings')
    if data['setting'] == 'effort' and data.get('ultracode') not in ('preserve', 'on', 'off'):
        raise ValueError('invalid_model_settings')
    expected = data.get('expected')
    if not isinstance(expected, dict) or set(expected) != {'model', 'effort', 'ultracode'}:
        raise ValueError('invalid_model_settings')
    if any(not isinstance(expected[k], str) or len(expected[k]) > 80 for k in ('model', 'effort')):
        raise ValueError('invalid_model_settings')
    if expected['ultracode'] is not None and type(expected['ultracode']) is not bool:
        raise ValueError('invalid_model_settings')
    if 'operation_id' in data:
        try:
            if str(uuid.UUID(data['operation_id'])) != data['operation_id']:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('invalid_operation') from None
    return dict(data)


def current(item):
    meta = item.get('external_metadata') or {}
    # Only a worker-reported active flag is authoritative. Folded flag_settings
    # records rejected requests too and is deliberately never consulted.
    ultra = meta.get('is_ultracode_active')
    if ultra is None and 'is_ultracode_active' in meta:
        ultra = False
    return {'model': str(meta.get('model') or '')[:80], 'effort': str(meta.get('effort_level') or '')[:30],
            'ultracode': ultra if type(ultra) is bool else None,
            'connected': item.get('connection_status') == 'connected',
            'version': str(meta.get('container_cc_version') or '')[:30]}


def request_for(data, actual):
    if {k: actual[k] for k in ('model', 'effort', 'ultracode')} != data['expected']:
        raise ValueError('model_settings_changed')
    if not actual['connected']:
        raise ValueError('remote_session_disconnected')
    if data['setting'] == 'model':
        return {'subtype': 'set_model', 'model': data['value']}
    ultra = data['ultracode']
    if ultra == 'preserve' and actual['ultracode'] is None:
        raise ValueError('model_settings_ultracode_unknown')
    return {'subtype': 'apply_flag_settings', 'settings': {
        'effortLevel': data['value'], 'ultracode': actual['ultracode'] if ultra == 'preserve' else ultra == 'on'}}


def rejection(error):
    # Map protocol errors to fixed public reasons. Do not return raw settings,
    # hook output, paths, provider identifiers, or credential-bearing messages.
    text = str(error).lower()
    for fragment, code in [('signature', 'model_settings_official_verification'),
                           ('attestation', 'model_settings_official_verification'),
                           ('verification', 'model_settings_official_verification'),
                           ('env_override', 'model_settings_effort_override'),
                           ('overrides effort', 'model_settings_effort_override'),
                           ('ultracode', 'model_settings_ultracode_unavailable'),
                           ('unsupported', 'model_settings_unsupported'),
                           ('does not handle', 'model_settings_unsupported'),
                           ('not supported', 'model_settings_unsupported'),
                           ('not available', 'model_settings_unavailable'),
                           ('not allowed', 'model_settings_unavailable'),
                           ('unrecognized', 'model_settings_unavailable')]:
        if fragment in text:
            return code
    return 'model_settings_rejected'


class SettingsService:
    def __init__(self, controller=None, root=ROOT):
        self.controller = controller or Controller(lambda: {})
        self.root = Path(root)
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.lock = threading.RLock()
        self.active = set()
        self.records = {}
        for path in self.root.glob('*.json'):
            if re.fullmatch(r'[a-f0-9-]{36}\.json', path.name) and not path.is_symlink() and path.stat().st_size < 16384:
                row = json.loads(path.read_text())
                if row['state'] in ('requested', 'accepted'):
                    row.update(state='unconfirmed', error='model_settings_outcome_unknown')
                self.records[row['operation_id']] = row

    def save(self, row):
        with self.lock:
            row['updated_at'] = time.time()
            self.records[row['operation_id']] = row
            path = self.root / (row['operation_id'] + '.json')
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(row)); temporary.chmod(0o600); temporary.replace(path)
            old = sorted(self.records.values(), key=lambda x: x['updated_at'])
            for prior in old[:-256]:
                if prior['state'] not in ('requested', 'accepted', 'unconfirmed'):
                    (self.root / (prior['operation_id'] + '.json')).unlink(missing_ok=True)
                    self.records.pop(prior['operation_id'], None)

    @staticmethod
    def public(row):
        return {k: v for k, v in row.items() if k not in ('signature', 'request', 'request_id')}

    def read(self, sid):
        item, _ = self.controller.get(sid, fresh=True)
        with self.lock:
            rows = [r for r in self.records.values() if r['session_id'] == sid]
            latest = max(rows, key=lambda r: r['created_at']) if rows else None
            # After a transport gap only reconcile the existing operation.
            # No mutation is replayed, including after a process restart.
            if latest and latest['state'] == 'unconfirmed' and sid not in self.active:
                self.active.add(sid)
                threading.Thread(target=self.execute, args=(latest, False), daemon=True).start()
        return {'ok': True, 'session_id': sid, 'current': current(item), 'models': list(MODELS),
                'efforts': list(EFFORTS), 'catalog_source': 'standard_aliases',
                'operation': self.public(latest) if latest else None}

    def submit(self, data):
        data = validate(data)
        op = data.get('operation_id')
        if not op:
            raise ValueError('invalid_operation')
        signature = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        sid = data['session_id']
        with self.lock:
            old = self.records.get(op)
            if old:
                if old['signature'] != signature:
                    raise ValueError('operation_id_conflict')
                return {'ok': True, 'operation': self.public(old), 'duplicate': True}
            if sid in self.active or any(r['session_id'] == sid and r['state'] in ('requested', 'accepted', 'unconfirmed') for r in self.records.values()):
                raise ValueError('model_settings_pending')
            item, _ = self.controller.get(sid, fresh=True)
            actual = current(item)
            request = request_for(data, actual)
            row = {'operation_id': op, 'session_id': sid, 'setting': data['setting'], 'value': data['value'],
                   'signature': signature, 'request': request, 'request_id': 'settings-' + op,
                   'before': actual, 'state': 'requested', 'created_at': time.time()}
            if data['setting'] == 'effort':
                row['ultracode'] = request['settings']['ultracode']
            self.save(row)
            self.active.add(sid)
            threading.Thread(target=self.execute, args=(row, True), daemon=True).start()
        return {'ok': True, 'operation': self.public(row)}

    def execute(self, row, send=True):
        sid = row['session_id']
        try:
            if send:
                try:
                    self.controller.post(sid, {'type': 'control_request', 'request_id': row['request_id'], 'request': row['request']}, row['operation_id'])
                    row['state'] = 'accepted'; self.save(row)
                except Exception:
                    # The request may already be accepted. Resolve by receipt only.
                    row['state'] = 'unconfirmed'; self.save(row)
            deadline = time.monotonic() + (45 if send else 12)
            while time.monotonic() < deadline:
                rows = self.controller.request('sessions/' + sid + '/events?limit=120&sort_order=desc').get('data', [])
                reply = next((e.get('payload', {}).get('response') for e in rows
                              if e.get('source') == 'worker' and e.get('payload', {}).get('type') == 'control_response'
                              and e.get('payload', {}).get('response', {}).get('request_id') == row['request_id']), None)
                if reply:
                    if reply.get('subtype') == 'error':
                        row.update(state='rejected', error=rejection(reply.get('error')))
                    elif reply.get('subtype') == 'success':
                        item, _ = self.controller.get(sid, fresh=True)
                        actual = current(item)
                        meta = item.get('external_metadata') or {}
                        matches = (bool(actual['model']) if row['value'] == 'default' else
                                   actual['model'] == row['value'] or actual['model'].startswith('claude-' + row['value'] + '-')) if row['setting'] == 'model' else (
                                   meta.get('effort_request_id') == row['request_id'] and bool(actual['effort']))
                        if not matches:
                            row.update(state='accepted', receipt_received=True, actual=actual)
                            self.save(row); time.sleep(1.5); continue
                        row.update(state='applied', actual=actual, verified_by='worker_receipt_and_metadata', verified_at=time.time())
                        row.pop('error', None)
                    else:
                        raise ValueError('model_settings_protocol_changed')
                    self.save(row); return
                time.sleep(1.5)
            row.update(state='unconfirmed', error='model_settings_outcome_unknown'); self.save(row)
        except Exception:
            row.update(state='unconfirmed', error='model_settings_outcome_unknown'); self.save(row)
        finally:
            with self.lock:
                self.active.discard(sid)

    def dispatch(self, data):
        data = validate(data)
        return self.read(data['session_id']) if data['action'] == 'read' else self.submit(data)


def main():
    os.umask(0o077)
    mode = sys.argv[1] if len(sys.argv) == 2 else ''
    if mode == 'install':
        path = Path.home() / 'Library/LaunchAgents/io.starport.starship-model-settings.plist'
        if path.exists():
            raise ValueError('settings_agent_already_installed')
        path.write_bytes(plistlib.dumps({'Label': 'io.starport.starship-model-settings',
            'ProgramArguments': [sys.executable, str(BASE / 'model_settings.py'), 'serve'],
            'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 5,
            'StandardErrorPath': str(BASE / 'state/model-settings-agent.log')}))
        path.chmod(0o600)
        subprocess.run(['/bin/launchctl', 'bootstrap', 'gui/' + str(os.getuid()), str(path)], check=True)
        print(json.dumps({'ok': True})); return
    if mode == 'rpc':
        raw = sys.stdin.buffer.readline(4097)
        if len(raw) > 4096:
            raise ValueError('model_settings_request_limit')
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(50); connection.connect(str(SOCKET))
            connection.sendall(raw.rstrip(b'\n') + b'\n'); connection.shutdown(socket.SHUT_WR)
            body = bytearray()
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > 16384:
                    raise ValueError('model_settings_response_limit')
        sys.stdout.buffer.write(body); return
    if mode != 'serve':
        raise ValueError('model_settings_mode_invalid')
    ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    guard = (ROOT / 'agent.lock').open('a')
    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    service = SettingsService()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(50)
            try:
                raw = self.rfile.readline(4097)
                if len(raw) > 4096:
                    raise ValueError('model_settings_request_limit')
                value = service.dispatch(json.loads(raw))
            except Exception as exc:
                reason = str(exc)
                allowed = reason.startswith(('model_settings_', 'remote_login_', 'remote_session_', 'invalid_model_', 'invalid_operation', 'operation_id_conflict')) and re.fullmatch(r'[a-z_]{1,90}', reason)
                value = {'ok': False, 'error': reason if allowed else 'model_settings_unavailable'}
            self.wfile.write(json.dumps(value).encode() + b'\n')
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    SOCKET.unlink(missing_ok=True)
    with Server(str(SOCKET), Handler) as server:
        SOCKET.chmod(0o600); server.serve_forever()


if __name__ == '__main__':
    main()
