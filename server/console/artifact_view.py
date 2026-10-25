import shutil
"""Convert document snapshots to a small presentation tree, never executable HTML."""
import base64
from collections import Counter
import hashlib
import re
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET


def document_view(value):
    result = {k: value.get(k) for k in ('id', 'title', 'official_url', 'available', 'reason', 'captured_at',
                                      'revision', 'snapshot', 'figure_count', 'available_figures', 'stale_figures', 'snapshot_outdated', 'latest_observed_revision')}
    if not value.get('available'):
        return result
    images, missing, toc, stats = {}, [], [], Counter()
    total = 0
    for ref, item in value.get('images', {}).items():
        raw = base64.b64decode(item.get('data', ''), validate=True)
        total += len(raw)
        mime=item.get('mime','image/png')
        valid = (mime=='image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or (mime=='image/jpeg' and raw.startswith(b'\xff\xd8\xff'))
        if len(raw) > 4 * 1024 * 1024 or total > 12 * 1024 * 1024 or not valid or hashlib.sha256(raw).hexdigest() != item.get('sha256'):
            raise ValueError('artifact_image_invalid')
        images[ref] = {'src': 'data:'+mime+';base64,' + base64.b64encode(raw).decode(),
                       'sha256': item['sha256'], 'captured_at': item.get('captured_at'), 'stale': bool(item.get('stale')), 'render_warning':bool(item.get('render_warning'))}

    if value.get('replay'):
        # A fixed parser consumes data over stdin; no artifact code is executed.
        script = Path(__file__).resolve().parent / 'web/artifact-replay-cli.mjs'
        for trim in range(min(12,len(value['replay']))):
            records=value['replay'][:-trim] if trim else value['replay']
            process = subprocess.run([shutil.which('node') or 'node', str(script)], input=json.dumps({'value': {'replay': records}, 'images': images}).encode(), capture_output=True, timeout=15)
            if process.returncode or len(process.stdout) > 20 * 1024 * 1024:
                raise ValueError('artifact_replay_unavailable')
            replay = json.loads(process.stdout)
            if replay.get('ok'):
                return {**result, **replay['value'], 'snapshot_outdated':bool(trim), 'unapplied_operations':trim,
                        'latest_observed_revision':max(x.get('revision') or 0 for x in value['replay'])}
        return {**result, 'available': False, 'reason': 'artifact_replay_incomplete', 'replay_error': replay.get('error')}

    xml = value.get('xml', '')
    if not isinstance(xml, str) or len(xml.encode()) > 512 * 1024 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('artifact_document_invalid')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError('artifact_document_invalid') from None
    if root.tag != 'doc' or len(list(root.iter())) > 20000:
        raise ValueError('artifact_document_invalid')
    if any(key in root.attrib for key in ('parent','sinceRev','outline','op')) or any(x.tag in ('gap','gone') for x in root.iter()):
        raise ValueError('artifact_document_partial')

    def text(value):
        return {'tag': 'text', 'text': value}

    def convert(node, depth=0):
        if depth > 40:
            raise ValueError('artifact_document_invalid')
        stats[node.tag] += 1
        children = [text(node.text)] if node.text else []
        for child in node:
            children.append(convert(child, depth + 1))
            if child.tail:
                children.append(text(child.tail))
        tag = node.tag
        if tag == 'paragraph':
            tag = 'p'
            if node.get('heading') in ('1', '2', '3', '4', '5', '6'):
                tag = 'h' + node.get('heading')
                anchor = 'section-' + str(len(toc))
                toc.append({'id': anchor, 'level': int(node.get('heading')), 'title': ''.join(node.itertext())})
                return {'tag': tag, 'id': anchor, 'children': children}
        if tag in ('embed', 'media'):
            ref = node.get('ref') or node.get('blob', '')
            image = images.get(ref)
            if not image:
                missing.append(node.get('caption') or node.get('alt', 'Figure'))
            return {'tag': 'figure', 'caption': node.get('caption') or node.get('alt', ''), 'image': image}
        if tag == 'codeBlock':
            return {'tag': 'math' if node.get('language') in ('latex', 'tex', 'math') else 'pre',
                    'text': ''.join(node.itertext()), 'language': node.get('language', '')[:50]}
        if tag == 'list':
            tag = 'ol' if node.get('kind') == 'ordered' else 'ul'
        tag = {'doc': 'article', 'text': 'span', 'bold': 'strong', 'italic': 'em', 'strikethrough': 's',
               'listItem': 'li', 'row': 'tr', 'cell': 'th' if node.get('header') == 'true' else 'td',
               'quote': 'blockquote', 'hardBreak': 'br', 'lineBreak': 'br'}.get(tag, tag)
        if node.tag == 'date':
            return text(node.get('value', ''))
        if node.tag == 'mention':
            return text(node.get('name', ''))
        if node.tag == 'pending':
            return {'tag': 'placeholder', 'text': node.get('intent', 'Section not yet available')}
        if node.tag == 'link':
            url = node.get('href') or node.get('url') or ''
            return {'tag': 'a' if re.match(r'^(https?://|mailto:|#)', url, re.I) else 'span', 'href': url if re.match(r'^(https?://|mailto:|#)', url, re.I) else '', 'children': children}
        if tag not in {'article', 'p', 'span', 'strong', 'em', 's', 'u', 'code', 'ul', 'ol', 'li', 'table', 'tr', 'th', 'td', 'blockquote', 'br'}:
            missing.append('Unsupported element: ' + node.tag[:50])
            tag = 'span'
        return {'tag': tag, 'children': children}

    result.update(content=convert(root), toc=toc, missing=missing, stats=dict(stats))
    return result
