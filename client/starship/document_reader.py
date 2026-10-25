"""Read registered PDF attachments without restarting the session daemon.

The caller supplies a session ID and opaque file ID, never a path or URL.
Native account credentials and device metadata never leave this helper.
"""
from pathlib import Path
import json
import os
import re
import sys
from urllib.parse import unquote

from artifact_reader import native_rpc
from rich_content import MediaStore, open_regular, clean

HOME = Path.home()
BASE = HOME / '.local/share/starship'
SID = re.compile(r'(?:cse_[A-Za-z0-9]{16,64}|[a-f0-9-]{36})')
LOCAL_ID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
LINK = re.compile(r'!?\[[^\]\n]*\]\((?:<([^<>\n]+)>|([^\s()]+))(?:\s+"[^"\n]*")?\)')
MAX_PDF = 512 * 1024 * 1024


def load_json(path):
    with os.fdopen(open_regular(path), 'rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('document_metadata_limit')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('document_metadata_invalid')
    return value


def resolve_project(sid, session, index, home=HOME):
    """Resolve exact bridge IDs, not a potentially duplicated session title."""
    local = index.get(session.get('local_id') or sid)
    if local:
        return Path(local['cwd'])
    if not sid.startswith('cse_'):
        if session.get('kind') != 'managed' or not LOCAL_ID.fullmatch(sid):
            return None
        saved = load_json(home / '.local/share/starship/state' / (sid + '.json'))
        if saved.get('id') != sid:
            raise ValueError('document_session_unavailable')
        return Path(saved['cwd'])
    bridge_ids = {sid, 'session_' + sid[4:]}
    root = home / 'Library/Application Support/Claude/claude-code-sessions'
    matches = {}
    for count, path in enumerate(root.glob('*/*/local_*.json')):
        if count >= 512:
            raise ValueError('document_metadata_limit')
        try:
            saved = load_json(path)
            bridges = saved.get('bridgeSessionIds', [])
            if not isinstance(bridges, list) or not any(x in bridge_ids for x in bridges if isinstance(x, str)):
                continue
            local_id = saved.get('cliSessionId', '')
            item = index.get(local_id)
            if not item or not LOCAL_ID.fullmatch(local_id):
                continue
            project = Path(saved.get('cwd', ''))
            current = Path(item.get('cwd', ''))
            # A session can cd into a report folder without changing its project.
            # Only accept the exact native bridge with a transcript inside that root.
            if not project.is_absolute() or (current != project and project not in current.parents):
                continue
            matches[local_id] = project
        except (OSError, ValueError, TypeError):
            continue
    if len(matches) > 1:
        raise ValueError('document_binding_ambiguous')
    return next(iter(matches.values()), None)


def context(sid):
    if not isinstance(sid, str) or not SID.fullmatch(sid):
        raise ValueError('invalid_session')
    current = native_rpc({'action': 'list'})
    session = next((s for s in current.get('sessions', []) if s.get('id') == sid), None)
    if not session:
        raise ValueError('document_session_unavailable')
    from client import desktop_index, safe_directory
    project = resolve_project(sid, session, desktop_index())
    if project is None:
        return []
    project = safe_directory(str(project))
    return [project, HOME / 'Starship-Training']


def local_pdf(reference):
    if not isinstance(reference, str) or not 1 <= len(reference) <= 4096:
        return None
    if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', reference) or '\x00' in reference:
        return None
    path = unquote(reference.split('#', 1)[0])
    return path if Path(path).suffix.lower() == '.pdf' else None


def references(events):
    found = []
    for event in events[-200:]:
        if event.get('role') == 'tool' and str(event.get('tool_name') or event.get('text', '')).split('__')[-1] == 'SendUserFile':
            value = event.get('input') or {}
            files = value.get('files', []) if isinstance(value, dict) else []
            if isinstance(files, list):
                found.extend(p for p in files[:24] if local_pdf(p))
        elif event.get('role') == 'assistant':
            for match in LINK.finditer(str(event.get('text', ''))[:64000]):
                ref = match.group(1) or match.group(2)
                if local_pdf(ref):
                    found.append(ref)
    return list(dict.fromkeys(found))[-64:]


def dispatch(request):
    action = request.get('action')
    allowed = {'action', 'session_id'} if action == 'list' else {'action', 'session_id', 'media_id', 'offset', 'length'}
    if action not in {'list', 'media'} or set(request) - allowed:
        raise ValueError('document_action_blocked')
    sid = request.get('session_id')
    roots = context(sid)
    store = MediaStore(BASE)
    if action == 'media':
        if not roots:
            raise ValueError('document_not_on_device')
        info = store.read(sid, request.get('media_id'), roots, request.get('offset', 0), request.get('length', 0))
        if info['mime'] != 'application/pdf' or info['size'] > MAX_PDF:
            raise ValueError('document_type_blocked')
        return {'ok': True, 'media': info}
    value = native_rpc({'action': 'read', 'session_id': sid})
    files = []
    for ref in references(value.get('events', [])):
        path = local_pdf(ref)
        item = {'reference': ref, 'name': clean(Path(path).name, 160),
                'mime': 'application/pdf', 'source': 'document', 'available': False}
        try:
            if not roots:
                raise ValueError('document_not_on_device')
            registered = store.register_file(sid, path, roots)
            if registered['size'] > MAX_PDF:
                raise ValueError('document_size_limit')
            item.update(registered)
        except (OSError, ValueError) as exc:
            reason = str(exc)
            item['error'] = reason if reason in {'document_not_on_device', 'document_size_limit'} else 'document_file_unavailable'
        files.append(item)
    return {'ok': True, 'session_id': sid, 'files': files}


def main():
    os.umask(0o077)
    try:
        raw = sys.stdin.buffer.readline(4097)
        if len(raw) > 4096:
            raise ValueError('document_request_limit')
        result = dispatch(json.loads(raw))
    except Exception as exc:
        allowed = {'invalid_session', 'document_action_blocked', 'document_session_unavailable',
                   'document_not_on_device', 'document_binding_ambiguous', 'document_request_limit',
                   'media_changed', 'media_not_found', 'media_path_blocked', 'media_range_invalid'}
        error = str(exc) if isinstance(exc, ValueError) and str(exc) in allowed else 'document_read_unavailable'
        result = {'ok': False, 'error': error}
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
