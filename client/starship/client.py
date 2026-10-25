"""Local session runner with a bounded Unix-socket control protocol."""
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import signal
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from rich_content import MediaStore, blocks_to_events, bounded_events

BASE = Path.home() / '.local/share/starship'
STATE = BASE / 'state'
SOCKET = STATE / 'control.sock'
LOCK = threading.RLock()
RUNS = {}
INDEX = {}
INDEX_AT = 0.0
INDEX_LOCK = threading.RLock()
MAX_REQUEST = 128 * 1024
MEDIA = MediaStore()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(path)


def clean(text, limit=12000):
    text = str(text)
    text = re.sub(r'(?i)(Bearer\s+)[^\s"\x27]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(?:sk-(?:ant-)?|github_pat_|ghp_)[A-Za-z0-9_-]{16,}', '[REDACTED]', text)
    return text[:limit]


def cli():
    configured = os.environ.get('STARSHIP_CLAUDE_BIN')
    if configured:
        executable = Path(configured).expanduser().resolve()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError('runner_unavailable')
        return str(executable)
    root = Path.home() / 'Library/Application Support/Claude/claude-code'
    versions = sorted((p for p in (root.iterdir() if root.is_dir() else []) if re.fullmatch(r'\d+\.\d+\.\d+', p.name)),
                      key=lambda p: tuple(map(int, p.name.split('.'))), reverse=True)
    for version in versions:
        paths = list(version.glob('*/claude.app/Contents/MacOS/claude'))
        if len(paths) == 1 and paths[0].is_file():
            return str(paths[0])
    standalone = shutil.which('claude')
    if standalone:
        return standalone
    raise ValueError('runner_unavailable')


def safe_directory(value):
    path = Path(value)
    home = Path.home()
    if not path.is_absolute() or home not in path.parents or not path.is_dir():
        raise ValueError('project_unavailable')
    for item in [path, *path.parents]:
        if item.is_symlink():
            raise ValueError('linked_project_blocked')
        if item == home:
            break
    return path


def transcript_rows(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024 * 1024:
        raise ValueError('transcript_unavailable')
    with path.open('rb') as stream:
        head = stream.read(1024 * 1024)
        if path.stat().st_size > len(head):
            stream.seek(max(len(head), path.stat().st_size - 2 * 1024 * 1024))
            tail = stream.read().split(b'\n', 1)[-1]
        else:
            tail = b''
    rows = []
    for line in (head + b'\n' + tail).splitlines():
        if len(line) > 2 * 1024 * 1024:
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except (ValueError, UnicodeError):
            pass
    return rows


def desktop_index(force=False):
    with INDEX_LOCK:
        return _desktop_index(force)


def _desktop_index(force=False):
    global INDEX, INDEX_AT
    if not force and INDEX_AT and time.monotonic() - INDEX_AT < 15:
        return INDEX
    root = Path.home() / '.claude/projects'
    found = {}
    if root.is_symlink():
        raise ValueError('linked_transcript_store_blocked')
    paths = []
    for folder in root.iterdir() if root.exists() else []:
        if folder.is_dir() and not folder.is_symlink():
            paths.extend(folder.glob('*.jsonl'))
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[:80]:
        if not re.fullmatch(r'[0-9a-f-]{36}', path.stem):
            continue
        try:
            rows = transcript_rows(path)
            cwd = None
            title = None
            first = None
            for item in rows:
                cwd = item.get('cwd') or cwd
                title = item.get('customTitle') or title
                msg = item.get('message', {})
                if not first and item.get('type') == 'user' and isinstance(msg, dict):
                    content = msg.get('content')
                    if isinstance(content, str):
                        first = content[:70]
            directory = safe_directory(cwd or '')
            if STATE in directory.parents or directory == STATE:
                continue
            if path.stem not in found:
                found[path.stem] = {'id': path.stem, 'kind': 'desktop', 'title': clean(title or first or directory.name, 120),
                                    'project': directory.name, 'cwd': str(directory), 'path': str(path),
                                    'updated': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                                    'status': 'history', 'can_send': False}
        except (ValueError, OSError, TypeError):
            continue
    INDEX, INDEX_AT = found, time.monotonic()
    return found


def public_session(item):
    return {k: v for k, v in item.items() if k not in {'cwd', 'path', 'resume_source', 'operation_keys', 'create_digest'}}


def session_file(sid):
    if not re.fullmatch(r'[0-9a-f-]{36}', sid):
        raise ValueError('invalid_session')
    return STATE / (sid + '.json')


def read_session(sid):
    path = session_file(sid)
    if not path.is_file():
        raise ValueError('session_not_found')
    return json.loads(path.read_text())


def save_session(item):
    item['updated'] = now()
    write_json(session_file(item['id']), item)


def add_event(sid, role, text='', **extra):
    with LOCK:
        item = read_session(sid)
        sequence = item.get('sequence', 0) + 1
        row = {'seq': sequence, 'role': role, 'text': clean(text, 64000), 'time': now(), **extra}
        with (STATE / (sid + '.events.jsonl')).open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        item['sequence'] = sequence
        save_session(item)
        return row


def set_status(sid, status, **extra):
    with LOCK:
        item = read_session(sid)
        item.update(status=status, **extra)
        save_session(item)


def auth_status():
    result = subprocess.run([cli(), 'auth', 'status'], capture_output=True, text=True, timeout=15)
    try:
        value = json.loads(result.stdout)
        return bool(value.get('loggedIn'))
    except ValueError:
        return False


def existing_process(sid):
    result = subprocess.run(['ps', '-axo', 'command='], capture_output=True, text=True, timeout=5)
    return any(sid in line and ('/claude' in line or 'claude --' in line) for line in result.stdout.splitlines())


from remote_control import Controller
REMOTE = Controller(desktop_index)


class Run:
    def __init__(self, session):
        self.session = session
        self.sid = session['id']
        self.process = None
        self.write_lock = threading.Lock()
        self.ready = threading.Event()
        self.pending = {}
        self.partial = ''
        self.cancelled = False

    def write(self, value):
        with self.write_lock:
            if self.process is None or self.process.poll() is not None:
                raise ValueError('runner_not_running')
            self.process.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
            self.process.stdin.flush()

    def start(self):
        if self.cancelled:
            raise ValueError('session_stopped_before_launch')
        args = [cli(), '-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
                '--verbose', '--include-partial-messages', '--replay-user-messages',
                '--permission-mode', 'manual', '--permission-prompt-tool', 'stdio',
                '--model', self.session.get('model', 'opus'), '--effort', self.session.get('effort', 'xhigh'),
                '--name', self.session['title']]
        if self.session.get('resume_source') and not self.session.get('started'):
            args += ['--resume', self.session['resume_source'], '--fork-session', '--session-id', self.sid]
        elif self.session.get('started'):
            args += ['--resume', self.sid]
        else:
            args += ['--session-id', self.sid]
        environment = {'HOME': str(Path.home()), 'USER': Path.home().name, 'LOGNAME': Path.home().name,
                       'PATH': str(Path.home() / '.local/bin') + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                       'LANG': 'en_US.UTF-8', 'TZ': 'UTC', 'CLAUDE_CODE_ENTRYPOINT': 'sdk-py'}
        self.process = subprocess.Popen(args, cwd=str(safe_directory(self.session['cwd'])), env=environment,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, bufsize=1, start_new_session=True)
        if self.cancelled:
            self.stop()
            raise ValueError('session_stopped_before_launch')
        threading.Thread(target=self.read, daemon=True).start()
        threading.Thread(target=self.read_errors, daemon=True).start()
        self.write({'type': 'control_request', 'request_id': 'init-' + self.sid,
                    'request': {'subtype': 'initialize', 'hooks': {}}})
        if not self.ready.wait(40):
            self.stop()
            raise ValueError('runner_initialization_timeout')
        if self.process.poll() is not None:
            raise ValueError('runner_start_failed')

    def read_errors(self):
        for line in self.process.stderr:
            if re.search(r'error|failed|not logged|authentication', line, re.I):
                add_event(self.sid, 'notice', clean(line, 1200))

    def read(self):
        try:
            for line in self.process.stdout:
                if len(line) > 4 * 1024 * 1024:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                kind = item.get('type')
                if kind == 'control_response':
                    if item.get('response', {}).get('request_id') == 'init-' + self.sid:
                        self.ready.set()
                elif kind == 'control_request':
                    request = item.get('request', {})
                    rid = item.get('request_id')
                    if request.get('subtype') == 'can_use_tool' and isinstance(rid, str):
                        if len(json.dumps(request.get('input', {}))) > 48000:
                            self.write({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': rid,
                                        'response': {'behavior': 'deny', 'message': 'Permission request exceeds the review limit. Split the operation.'}}})
                            add_event(self.sid, 'notice', 'An oversized permission request was declined without execution')
                            continue
                        self.pending[rid] = request
                        set_status(self.sid, 'waiting_approval')
                        add_event(self.sid, 'permission', '', request_id=rid, tool=clean(request.get('tool_name', ''), 100),
                                  input=request.get('input', {}))
                    else:
                        self.write({'type': 'control_response', 'response': {'subtype': 'error', 'request_id': rid,
                                                                          'error': 'Unsupported control request'}})
                elif kind == 'stream_event':
                    event = item.get('event', {})
                    delta = event.get('delta', {})
                    if delta.get('type') == 'text_delta':
                        self.partial = (self.partial + delta.get('text', ''))[-24000:]
                elif kind in {'assistant', 'user'}:
                    message = item.get('message', {})
                    roots = [Path(self.session['cwd']), Path.home() / 'Starship-Training']
                    for row in blocks_to_events(message.get('content', []), kind, MEDIA, self.sid, roots):
                        if row['role'] == 'assistant':
                            self.partial = ''
                        add_event(self.sid, row.pop('role'), row.pop('text'), **row)
                elif kind == 'result':
                    self.partial = ''
                    self.pending.clear()
                    set_status(self.sid, 'error' if item.get('is_error') else 'idle', started=True,
                               last_result={'error': bool(item.get('is_error')), 'duration_ms': item.get('duration_ms')})
                    if item.get('is_error'):
                        add_event(self.sid, 'notice', item.get('result') or json.dumps(item.get('errors', [])))
                    add_event(self.sid, 'result', 'Turn complete' if not item.get('is_error') else 'Turn failed')
                elif kind == 'system' and item.get('subtype') == 'init':
                    set_status(self.sid, read_session(self.sid)['status'], started=True)
        finally:
            self.ready.set()
            self.pending.clear()
            with LOCK:
                session = read_session(self.sid)
                if session['status'] in {'running', 'starting', 'waiting_approval'}:
                    set_status(self.sid, 'stopped' if self.cancelled else 'interrupted')
                    add_event(self.sid, 'notice', 'Stopped by request' if self.cancelled else 'Runner disconnected; check the last result before sending again')

    def stop(self):
        self.cancelled = True
        if self.process and self.process.poll() is None:
            try:
                self.write({'type': 'control_request', 'request_id': 'interrupt-' + str(uuid.uuid4()),
                            'request': {'subtype': 'interrupt'}})
            except (ValueError, OSError):
                pass
            os.killpg(self.process.pid, signal.SIGTERM)
        set_status(self.sid, 'stopped')


def list_sessions():
    output = [public_session(x) for x in desktop_index().values()]
    for file in STATE.glob('*.json'):
        if re.fullmatch(r'[0-9a-f-]{36}\.json', file.name):
            item = json.loads(file.read_text())
            output = [x for x in output if x['id'] != item['id']]
            output.append(public_session(item))
    remote = REMOTE.list_public()
    replaced = {x['local_id'] for x in remote}
    output = [x for x in output if x['id'] not in replaced] + remote
    return sorted(output, key=lambda x: x.get('updated') or '', reverse=True)


def dispatch(data):
    if re.fullmatch(r'cse_[A-Za-z0-9]{16,64}', str(data.get('session_id', ''))):
        return REMOTE.dispatch(data)
    if data.get('action') in {'create', 'stop', 'permission'}:
        with LOCK:
            return _dispatch(data)
    return _dispatch(data)


def _dispatch(data):
    action = data.get('action')
    if action == 'health':
        remote = REMOTE.list_public()
        return {'ok': True, 'authenticated': auth_status(), 'running': sum(r.process is not None and r.process.poll() is None for r in RUNS.values()),
                'remote_control_connected': sum(x.get('connected', False) for x in remote),
                'remote_control_total': len(remote), 'remote_control_error': REMOTE.last_error}
    if action == 'list':
        return {'sessions': list_sessions()}
    if action == 'projects':
        projects = {hashlib.sha256(x['cwd'].encode()).hexdigest()[:16]: {'id': hashlib.sha256(x['cwd'].encode()).hexdigest()[:16],
                    'name': x['project']} for x in desktop_index().values()}
        return {'projects': list(projects.values())}
    if action == 'read':
        sid = data.get('session_id', '')
        try:
            session = read_session(sid)
        except ValueError:
            session = desktop_index().get(sid)
            if session is None:
                raise ValueError('session_not_found')
            events = []
            for item in transcript_rows(Path(session['path'])):
                msg = item.get('message', {})
                if not isinstance(msg, dict):
                    continue
                roots = [Path(session['cwd']), Path.home() / 'Starship-Training']
                for row in blocks_to_events(msg.get('content', []), msg.get('role', item.get('type', 'notice')), MEDIA, sid, roots):
                    events.append({'seq': len(events) + 1, 'time': item.get('timestamp'), **row})
            return {'session': public_session(session), 'events': bounded_events(events[-200:]), 'partial': '', 'pending': [],
                    'source_process_present': existing_process(sid)}
        path = STATE / (sid + '.events.jsonl')
        events = deque(maxlen=200)
        if path.exists():
            with path.open() as stream:
                for line in stream:
                    row = json.loads(line)
                    if row['seq'] > int(data.get('after', 0)):
                        events.append(row)
        run = RUNS.get(sid)
        pending = [{'request_id': rid, 'tool': req.get('tool_name'), 'input': req.get('input', {})}
                   for rid, req in list(run.pending.items())] if run else []
        return {'session': public_session(session), 'events': bounded_events(list(events)), 'partial': clean(run.partial, 24000) if run else '', 'pending': pending}
    if action == 'media':
        sid = data.get('session_id', '')
        try:
            session = read_session(sid)
        except ValueError:
            session = desktop_index().get(sid)
            if not session:
                raise ValueError('session_not_found')
        roots = [Path(session['cwd']), Path.home() / 'Starship-Training']
        return {'media': MEDIA.read(sid, data.get('media_id'), roots, data.get('offset', 0), data.get('length', 0))}
    if action == 'create':
        title = data.get('title', '')
        if not isinstance(title, str) or not 1 <= len(title) <= 100 or any(ord(c) < 32 for c in title):
            raise ValueError('invalid_title')
        operation = data.get('operation_id', '')
        try:
            operation = str(uuid.UUID(operation))
        except (ValueError, TypeError):
            raise ValueError('invalid_operation')
        sid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'session-create:' + operation))
        signature = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        if session_file(sid).exists():
            previous = read_session(sid)
            if previous.get('create_digest') != signature:
                raise ValueError('operation_id_conflict')
            return {'session': public_session(previous), 'duplicate': True}
        source = data.get('source_session')
        source_item = desktop_index(force=True).get(source) if source else None
        if source and not source_item:
            raise ValueError('source_unavailable')
        if source and existing_process(source):
            raise ValueError('source_session_still_running')
        project_id = data.get('project_id')
        if source_item:
            cwd = safe_directory(source_item['cwd'])
        elif project_id:
            candidates = [x for x in desktop_index().values() if hashlib.sha256(x['cwd'].encode()).hexdigest()[:16] == project_id]
            if not candidates:
                raise ValueError('project_unavailable')
            cwd = safe_directory(candidates[0]['cwd'])
        else:
            root = Path.home() / 'Starship-Projects'
            root.mkdir(exist_ok=True, mode=0o700)
            cwd = root / str(uuid.uuid4())
            cwd.mkdir(mode=0o700)
        model, effort = data.get('model', 'opus'), data.get('effort', 'xhigh')
        if model not in {'opus', 'sonnet', 'haiku'} or effort not in {'low', 'medium', 'high', 'xhigh', 'max'}:
            raise ValueError('invalid_model_settings')
        item = {'id': sid, 'kind': 'managed', 'title': title, 'cwd': str(cwd), 'project': cwd.name,
                'status': 'idle', 'model': model, 'effort': effort, 'sequence': 0, 'can_send': True,
                'created': now(), 'operation_keys': [], 'started': False, 'create_digest': signature}
        if source:
            item['resume_source'] = source
        save_session(item)
        return {'session': public_session(item)}
    if action == 'send':
        sid = data.get('session_id', '')
        message = data.get('message', '')
        op = data.get('operation_id', '')
        if not isinstance(message, str) or not 1 <= len(message) <= 24000 or not re.fullmatch(r'[0-9a-f-]{36}', op):
            raise ValueError('invalid_message')
        with LOCK:
            session = read_session(sid)
            if op in session['operation_keys']:
                return {'accepted': True, 'duplicate': True, 'operation_id': op}
            if session['status'] in {'running', 'starting', 'waiting_approval'}:
                raise ValueError('session_busy')
            if sid not in RUNS and existing_process(sid):
                raise ValueError('session_process_already_exists')
            if session.get('resume_source') and not session.get('started') and existing_process(session['resume_source']):
                raise ValueError('source_session_still_running')
            if not auth_status():
                raise ValueError('login_required')
            session['operation_keys'] = (session['operation_keys'] + [op])[-2000:]
            session['status'] = 'starting'
            save_session(session)
            run = RUNS.get(sid)
            needs_start = run is None or run.process is None or run.process.poll() is not None
            if needs_start:
                run = Run(session)
                RUNS[sid] = run
        add_event(sid, 'user', message, operation_id=op)
        def launch():
            try:
                if needs_start:
                    run.start()
                with LOCK:
                    if run.cancelled or read_session(sid)['status'] == 'stopped':
                        return
                    set_status(sid, 'running')
                    run.write({'type': 'user', 'session_id': sid, 'message': {'role': 'user', 'content': message},
                               'parent_tool_use_id': None, 'uuid': op})
            except Exception as exc:
                if not run.cancelled:
                    set_status(sid, 'error')
                    add_event(sid, 'notice', str(exc))
        threading.Thread(target=launch, daemon=True).start()
        return {'accepted': True, 'operation_id': op}
    if action == 'stop':
        sid = data.get('session_id', '')
        read_session(sid)
        run = RUNS.get(sid)
        if run:
            run.stop()
        else:
            set_status(sid, 'stopped')
        return {'stopped': True}
    if action == 'permission':
        run = RUNS.get(data.get('session_id'))
        rid = data.get('request_id')
        if not run or rid not in run.pending:
            raise ValueError('permission_no_longer_pending')
        request = run.pending[rid]
        decision = data.get('decision')
        if decision not in {'allow', 'deny'}:
            raise ValueError('invalid_decision')
        original = request.get('input', {})
        updated = original
        if request.get('tool_name') == 'AskUserQuestion' and decision == 'allow':
            answers = data.get('answers', {})
            if not isinstance(answers, dict):
                raise ValueError('invalid_answers')
            if set(answers) != {q.get('question') for q in original.get('questions', [])}:
                raise ValueError('invalid_answer_targets')
            for q in original.get('questions', []):
                if q.get('question') not in answers or not isinstance(answers[q['question']], str):
                    raise ValueError('answer_required')
            updated = {**original, 'answers': answers}
        response = {'behavior': 'allow', 'updatedInput': updated} if decision == 'allow' else {'behavior': 'deny', 'message': 'Declined by the user'}
        run.write({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': rid, 'response': response}})
        run.pending.pop(rid, None)
        set_status(run.sid, 'running')
        add_event(run.sid, 'notice', 'Permission allowed' if decision == 'allow' else 'Permission declined')
        return {'submitted': True}
    raise ValueError('unknown_action')


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(60)
        raw = self.rfile.readline(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            return
        try:
            data = json.loads(raw)
            result = {'ok': True, **dispatch(data)}
        except Exception as exc:
            result = {'ok': False, 'error': clean(str(exc), 300)}
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b'\n')


def main():
    os.umask(0o077)
    if len(sys.argv) > 1 and sys.argv[1] == 'rpc':
        raw = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise SystemExit('Request too large')
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(65)
            connection.connect(str(SOCKET))
            connection.sendall(raw.rstrip(b'\n') + b'\n')
            connection.shutdown(socket.SHUT_WR)
            total = 0
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > 4 * 1024 * 1024:
                    raise SystemExit('Response too large')
                sys.stdout.buffer.write(chunk)
        return
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    guard = (STATE / 'service.lock').open('a')
    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for file in STATE.glob('*.json'):
        if re.fullmatch(r'[0-9a-f-]{36}\.json', file.name):
            item = json.loads(file.read_text())
            if item['status'] in {'running', 'starting', 'waiting_approval'}:
                set_status(item['id'], 'interrupted')
    SOCKET.unlink(missing_ok=True)
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    with Server(str(SOCKET), Handler) as server:
        os.chmod(SOCKET, 0o600)
        server.serve_forever()


if __name__ == '__main__':
    main()
