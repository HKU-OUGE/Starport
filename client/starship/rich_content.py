from bootstrap import WINDOWS,windows_open_regular
"""Bounded transcript presentation and session-scoped, read-only media."""
from pathlib import Path
from contextlib import contextmanager
import base64
import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
from urllib.parse import unquote

BASE = Path.home() / '.local/share/starship'
MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.gif': 'image/gif', '.webp': 'image/webp', '.mp4': 'video/mp4',
        '.m4v': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
        '.pdf': 'application/pdf'}
INLINE_MAX = 8 * 1024 * 1024
CHUNK_MAX = 1024 * 1024
DB_LOCK = threading.RLock()


def clean(value, limit=64000):
    text = str(value)
    text = re.sub(r'(?i)(Bearer\s+)[^\s"\x27]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(?:sk-(?:ant-)?|github_pat_|ghp_)[A-Za-z0-9_-]{16,}', '[REDACTED]', text)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', '[REDACTED]', text, flags=re.S)
    return text[:limit]


def safe_value(value, depth=0):
    if depth > 7:
        return '[Depth limit]'
    if isinstance(value, dict):
        return {clean(k, 100): '[REDACTED]' if re.search(r'(?i)^(?:password|secret|token|access.?token|refresh.?token|api.?key|authorization)$', str(k)) else safe_value(v, depth + 1)
                for k, v in list(value.items())[:80]}
    if isinstance(value, list):
        return [safe_value(x, depth + 1) for x in value[:80]]
    if isinstance(value, str):
        return clean(value, 24000)
    return value if value is None or isinstance(value, (int, float, bool)) else clean(value, 1000)


def mime_valid(mime, head):
    return ((mime == 'image/png' and head.startswith(b'\x89PNG\r\n\x1a\n')) or
            (mime == 'image/jpeg' and head.startswith(b'\xff\xd8\xff')) or
            (mime == 'image/gif' and head[:6] in (b'GIF87a', b'GIF89a')) or
            (mime == 'image/webp' and head[:4] == b'RIFF' and head[8:12] == b'WEBP') or
            (mime in ('video/mp4', 'video/quicktime') and head[4:8] == b'ftyp') or
            (mime == 'video/webm' and head.startswith(b'\x1aE\xdf\xa3')) or
            (mime == 'application/pdf' and re.match(rb'%PDF-[12]\.[0-9]', head) is not None))


def open_regular(path):
    if WINDOWS:return windows_open_regular(path)
    """Open every component without following symlinks, including the root."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('media_path_blocked')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        result = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(os.fstat(result).st_mode):
        os.close(result)
        raise ValueError('media_not_regular')
    return result


class MediaStore:
    def __init__(self, base=BASE):
        self.base = Path(base)

    @contextmanager
    def db(self):
        folder = self.base / 'state'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(folder / 'media.sqlite', timeout=10)
        try:
            db.row_factory = sqlite3.Row
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS media(sid TEXT,id TEXT,name TEXT,mime TEXT,path TEXT,size INTEGER,stamp TEXT,body BLOB,PRIMARY KEY(sid,id))')
                yield db
        finally:
            db.close()

    @staticmethod
    def allowed(path, roots):
        return any(root != Path.home() and root in path.parents for root in roots)

    def register_file(self, sid, value, roots):
        if '://' in str(value) or '\x00' in str(value):
            raise ValueError('media_path_blocked')
        path = Path(unquote(str(value)))
        if not path.is_absolute() and roots:
            path = roots[0] / path
        mime = MIME.get(path.suffix.lower())
        if not mime or not self.allowed(path, roots):
            raise ValueError('media_path_blocked')
        fd = open_regular(path)
        try:
            info = os.fstat(fd)
            if not mime_valid(mime, os.read(fd, 32)):
                raise ValueError('media_type_mismatch')
        finally:
            os.close(fd)
        stamp = json.dumps([info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])
        key = hashlib.sha256((sid + '\0' + str(path) + '\0' + stamp).encode()).hexdigest()
        name = clean(path.name, 160)
        with DB_LOCK, self.db() as db:
            db.execute('INSERT OR IGNORE INTO media VALUES(?,?,?,?,?,?,?,NULL)', (sid, key, name, mime, str(path), info.st_size, stamp))
        return {'id': key, 'name': name, 'mime': mime, 'size': info.st_size, 'available': True}

    def register_image(self, sid, source):
        mime = source.get('media_type')
        value = source.get('data', '')
        if source.get('type') != 'base64' or not isinstance(value, str) or len(value) > INLINE_MAX * 4 // 3 + 8:
            raise ValueError('media_inline_unavailable')
        try:
            body = base64.b64decode(value, validate=True)
        except ValueError:
            raise ValueError('media_inline_invalid') from None
        if mime not in set(MIME.values()) or not str(mime).startswith('image/') or not mime_valid(mime, body[:32]):
            raise ValueError('media_type_mismatch')
        key = hashlib.sha256(sid.encode() + b'\0' + body).hexdigest()
        name = 'image-' + key[:8] + next(k for k, v in MIME.items() if v == mime)
        with DB_LOCK, self.db() as db:
            old = db.execute('SELECT 1 FROM media WHERE sid=? AND id=?', (sid, key)).fetchone()
            if not old and db.execute('SELECT COALESCE(SUM(LENGTH(body)),0) FROM media').fetchone()[0] + len(body) > 128 * 1024 * 1024:
                raise ValueError('media_inline_cache_full')
            db.execute('INSERT OR IGNORE INTO media VALUES(?,?,?,?,NULL,?,NULL,?)', (sid, key, name, mime, len(body), body))
        return {'id': key, 'name': name, 'mime': mime, 'size': len(body), 'available': True}

    def read(self, sid, key, roots, offset=0, length=0):
        if not re.fullmatch(r'[a-f0-9]{64}', str(key)):
            raise ValueError('media_not_found')
        if type(offset) is not int or type(length) is not int or offset < 0 or not 0 <= length <= CHUNK_MAX:
            raise ValueError('media_range_invalid')
        with DB_LOCK, self.db() as db:
            row = db.execute('SELECT sid,id,name,mime,path,size,stamp,substr(body,?,?) AS chunk FROM media WHERE sid=? AND id=?', (offset + 1, length, sid, key)).fetchone()
        if row is None:
            raise ValueError('media_not_found')
        result = {k: row[k] for k in ('id', 'name', 'mime', 'size')}
        if offset > row['size']:
            raise ValueError('media_range_invalid')
        if row['path']:
            path = Path(row['path'])
            if not self.allowed(path, roots):
                raise ValueError('media_path_blocked')
            fd = open_regular(path)
            try:
                info = os.fstat(fd)
                if json.dumps([info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]) != row['stamp']:
                    raise ValueError('media_changed')
                os.lseek(fd, offset, os.SEEK_SET)
                body = os.read(fd, length)
            finally:
                os.close(fd)
        else:
            body = row['chunk'] or b''
        if length:
            result.update(data=base64.b64encode(body).decode(), offset=offset,
                          chunk_sha256=hashlib.sha256(body).hexdigest())
        return result


def attachment(store, sid, value, roots, image=False):
    try:
        return store.register_image(sid, value) if image else store.register_file(sid, value, roots)
    except (ValueError, OSError, sqlite3.Error):
        return {'name': 'Image' if image else clean(Path(str(value)).name, 160), 'available': False,
                'error': 'media_unavailable'}


def blocks_to_events(blocks, role, store=None, sid='', roots=()):
    if isinstance(blocks, str):
        blocks = [{'type': 'text', 'text': blocks}]
    out = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get('type')
        if kind == 'text':
            raw = block.get('text', '')
            if not isinstance(raw, str):
                continue
            event = {'role': role, 'text': clean(raw), 'truncated': len(raw) > 64000}
            media = []
            if store:
                for match in list(re.finditer(r'!?\[[^\]\n]*\]\(<?([^\s<>]+)>?\)', raw))[:16]:
                    ref = match.group(1)
                    if '://' not in ref and Path(ref).suffix.lower() in MIME:
                        media.append({**attachment(store, sid, ref, roots), 'reference': ref})
            if media:
                event['attachments'] = media
            out.append(event)
        elif kind == 'tool_use':
            value = safe_value(block.get('input', {}))
            raw = json.dumps(value, ensure_ascii=False)
            limited = len(raw) > 48000
            out.append({'role': 'tool', 'text': clean(block.get('name', ''), 100),
                        'tool_name': clean(block.get('name', ''), 100), 'tool_id': clean(block.get('id', ''), 120),
                        'input': {'preview': raw[:48000]} if limited else value, 'input_truncated': limited})
            if str(block.get('name', '')).split('__')[-1] == 'SendUserFile' and store:
                original = block.get('input', {})
                files = original.get('files', []) if isinstance(original, dict) else []
                if isinstance(files, list):
                    media = [attachment(store, sid, p, roots) for p in files[:24] if isinstance(p, str) and Path(p).suffix.lower() in MIME]
                    if media:
                        out.append({'role': 'assistant', 'text': clean(original.get('caption', ''), 12000), 'attachments': media})
        elif kind == 'tool_result':
            value = block.get('content', '')
            media = []
            if isinstance(value, list):
                if store:
                    media = [attachment(store, sid, b.get('source', {}), roots, True) for b in value if isinstance(b, dict) and b.get('type') == 'image']
                value = '\n'.join(b.get('text', '') for b in value if isinstance(b, dict) and b.get('type') == 'text')
            text = clean(value)
            match = re.match(r'Exit code\s+(-?\d+)', text)
            out.append({'role': 'tool_result', 'text': text, 'tool_id': clean(block.get('tool_use_id', ''), 120),
                        'is_error': bool(block.get('is_error')), 'exit_code': int(match.group(1)) if match else None,
                        'truncated': len(str(value)) > 64000, 'attachments': media})
        elif kind == 'image' and store:
            out.append({'role': role, 'text': '', 'attachments': [attachment(store, sid, block.get('source', {}), roots, True)]})
        # Thinking and arbitrary account/protocol fields never leave this parser.
    return out


def bounded_events(events, budget=2500000):
    selected, total = [], 0
    for event in reversed(events):
        size = len(json.dumps(event, ensure_ascii=False).encode())
        if total + size > budget:
            break
        selected.append(event)
        total += size
    return list(reversed(selected))
