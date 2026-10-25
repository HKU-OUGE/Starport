"""HTTP representation helpers; API responses are not cached by browsers."""
from functools import lru_cache
import gzip
import hashlib
from pathlib import Path


def accepts_gzip(header):
    choices = {}
    for item in (header or '').lower().split(','):
        parts = [s.strip() for s in item.split(';')]
        quality = 1.0
        for part in parts[1:]:
            if part.startswith('q='):
                try:
                    quality = float(part[2:])
                except ValueError:
                    quality = 0
        choices[parts[0]] = quality
    return choices.get('gzip', choices.get('*', 0)) > 0


def compress(raw, content_type, accept):
    if accept and len(raw) >= 1024 and any(t in content_type for t in ('text/', 'json', 'javascript', 'svg')):
        compressed = gzip.compress(raw, compresslevel=5, mtime=0)
        if len(compressed) < len(raw):
            return compressed, 'gzip'
    return raw, None


@lru_cache(maxsize=32)
def asset_representation(path, mtime_ns, size, content_type, accept):
    raw, coding = compress(Path(path).read_bytes(), content_type, accept)
    return raw, coding, '"' + hashlib.sha256(raw).hexdigest() + '"'
