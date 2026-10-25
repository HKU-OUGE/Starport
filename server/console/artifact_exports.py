"""Serve only locally prepared, integrity-checked artifact export bundles."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

MAX_BUNDLE = 64 * 1024 * 1024


def regular_bytes(path, limit):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('artifact_export_invalid')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('artifact_export_invalid')
        raw = file.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('artifact_export_invalid')
    return raw


def latest_export(state):
    root = state / 'artifact-exports'
    if not (root / 'latest.json').exists():
        return None
    latest = json.loads(regular_bytes(root / 'latest.json', 16384))
    eid = latest.get('id')
    if not isinstance(eid, str) or not re.fullmatch(r'[a-f0-9]{32}', eid):
        raise ValueError('artifact_export_invalid')
    return export_manifest(state, eid)


def export_manifest(state, eid):
    if not re.fullmatch(r'[a-f0-9]{32}', eid):
        raise ValueError('artifact_export_not_found')
    value = json.loads(regular_bytes(state / 'artifact-exports' / eid / 'manifest.json', 512 * 1024))
    if value.get('id') != eid:
        raise ValueError('artifact_export_invalid')
    return {k: value.get(k) for k in ('id', 'created_at', 'session_count', 'artifact_count', 'html_count', 'link_only_count', 'bytes', 'sha256', 'sessions')}


def export_bundle(state, eid):
    manifest = export_manifest(state, eid)
    raw = regular_bytes(state / 'artifact-exports' / eid / 'artifacts.zip', MAX_BUNDLE)
    if len(raw) != manifest['bytes'] or hashlib.sha256(raw).hexdigest() != manifest['sha256']:
        raise ValueError('artifact_export_integrity')
    return raw
