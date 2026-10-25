import tempfile
from bootstrap import ipc_request,desktop_home
"""Read-only artifact snapshots from authorized session records.

No account credentials, arbitrary paths, URLs or commands are accepted.
This helper runs separately from the active session service.
"""
from pathlib import Path
import base64
import hashlib
import json
import os
import re
import socket
import sys
import xml.etree.ElementTree as ET

HOME = Path.home()
BASE = HOME / '.local/share/starship'
TAIL_BYTES = 16 * 1024 * 1024
MAX_IMAGE = 4 * 1024 * 1024
MAX_IMAGES = 12 * 1024 * 1024
LINK = re.compile(r'https://claude\.ai/(?:code/)?artifact/([A-Za-z0-9_-]{12,80})(?=$|[\s\"\x27<>)?#])')
SID = re.compile(r'(?:cse_[A-Za-z0-9]{16,64}|[a-f0-9-]{36})')


def native_rpc(data):
    value=ipc_request(BASE/'state/control.rpc',data,timeout=55)
    if not value.get('ok'):raise ValueError('artifact_session_unavailable')
    return value


def safe_file(path):
    from rich_content import open_regular
    return open_regular(path)


def rows_from(path, full=False):
    fd = safe_file(path)
    with os.fdopen(fd, 'rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > 512 * 1024 * 1024:
            raise ValueError('artifact_source_limit')
        start = 0 if full else max(0, size - TAIL_BYTES)
        stream.seek(start)
        if start:
            stream.readline()
        result, tool_ids = [], set()
        while stream.tell() < size:
            line = stream.readline(min(2 * 1024 * 1024 + 1, size - stream.tell()))
            if len(line) > 2 * 1024 * 1024:
                while not line.endswith(b'\n') and stream.tell() < size:
                    line = stream.readline(min(2 * 1024 * 1024, size - stream.tell()))
                continue
            try:
                row = json.loads(line)
                # API request blobs duplicate old messages and are not new events.
                if row.get('type') not in ('assistant', 'user'):
                    continue
                content = row.get('message', {}).get('content', [])
                if isinstance(content, str):
                    content = [{'type': 'text', 'text': content}]
                if not isinstance(content, list):
                    continue
                if full:
                    selected = []
                    for block in content:
                        kind = block.get('type')
                        if kind == 'tool_use' and (block.get('name') == 'Artifact' or isinstance(block.get('input'), dict) and ('engine' in block['input'] or 'container' in block['input'])):
                            tool_ids.add(block.get('id')); selected.append(block)
                        elif kind == 'tool_result' and block.get('tool_use_id') in tool_ids:
                            selected.append(block)
                        elif kind == 'text' and LINK.search(block.get('text', '')):
                            selected.append(block)
                    content = selected
                if content:
                    result.append((row.get('timestamp', ''), content, path))
            except (ValueError, AttributeError):
                continue
    return result, start > 0


def remote_rows(sid):
    """Use the active authenticated service; no independent credential access."""
    value = native_rpc({'action': 'read', 'session_id': sid})
    result = []
    for event in value.get('events', []):
        role = event.get('role')
        if role == 'tool':
            block = {'type': 'tool_use', 'id': event.get('tool_id'), 'name': event.get('tool_name'), 'input': event.get('input', {})}
        elif role == 'tool_result':
            content = [{'type': 'text', 'text': event.get('text', '')}]
            for image in event.get('attachments', []):
                if image.get('available') and image.get('mime') == 'image/png':
                    content.append({'type': 'remote_image', 'session_id': sid, 'media_id': image.get('id')})
            block = {'type': 'tool_result', 'tool_use_id': event.get('tool_id'), 'is_error': event.get('is_error', False), 'content': content}
        elif role in ('assistant', 'user'):
            block = {'type': 'text', 'text': event.get('text', '')}
        else:
            continue
        result.append((event.get('time') or '', [block], None))
    # The service provides a recent event window. Never claim account history completeness.
    return result, len(value.get('events', [])) >= 200 or bool(value.get('history_limited'))


def cached_native_rows(sid):
    path = BASE / 'state/artifact-sources' / (sid + '.json')
    if not path.exists():
        return [], False
    with os.fdopen(safe_file(path), 'rb') as stream:
        raw = stream.read(48 * 1024 * 1024 + 1)
    if len(raw) > 48 * 1024 * 1024:
        raise ValueError('artifact_source_limit')
    value = json.loads(raw)
    if value.get('session_id') != sid:
        raise ValueError('artifact_session_unavailable')
    return [(r[0], r[1], None) for r in value.get('rows', [])], bool(value.get('history_complete'))


def remote_image(sid, media_id):
    if not SID.fullmatch(str(sid)) or not re.fullmatch(r'[a-f0-9]{64}', str(media_id)):
        return None
    info = native_rpc({'action': 'media', 'session_id': sid, 'media_id': media_id})['media']
    size = info.get('size', 0)
    if info.get('mime') != 'image/png' or type(size) is not int or not 0 < size <= MAX_IMAGE:
        return None
    body = bytearray()
    while len(body) < size:
        length = min(1024 * 1024, size - len(body))
        value = native_rpc({'action': 'media', 'session_id': sid, 'media_id': media_id, 'offset': len(body), 'length': length})['media']
        chunk = base64.b64decode(value['data'], validate=True)
        if len(chunk) != length or value.get('offset') != len(body) or hashlib.sha256(chunk).hexdigest() != value.get('chunk_sha256'):
            raise ValueError('artifact_image_integrity')
        body.extend(chunk)
    return bytes(body) if body.startswith(b'\x89PNG\r\n\x1a\n') else None


def json_results(block):
    content = block.get('content', [])
    texts = [content] if isinstance(content, str) else [x.get('text', '') for x in content if isinstance(x, dict) and x.get('type') == 'text']
    output = []
    for text in texts:
        try:
            value, _ = json.JSONDecoder().raw_decode(text.lstrip())
            if isinstance(value, dict):
                output.append(value)
        except (ValueError, TypeError):
            pass
    return output


def artifact_links(rows):
    found, slugs = {}, {}
    for stamp, blocks, _ in rows:
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'text':
                text = block.get('text', '')
                for match in LINK.finditer(text):
                    aid = match.group(1)
                    found.setdefault(aid, {'id': aid, 'official_url': match.group(0), 'title': 'Artifact', 'observed_at': stamp})
                for match in re.finditer(r'\[([^\]\n]{1,200})\]\((https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]+)\)', text):
                    aid = match.group(2).rsplit('/', 1)[-1]
                    if aid in found:
                        found[aid]['title'] = match.group(1)
            elif block.get('type') == 'tool_result':
                for value in json_results(block):
                    frame = value.get('frame', {})
                    url = frame.get('artifactUrl') or frame.get('url') or ''
                    match = LINK.fullmatch(url)
                    if not match:
                        continue
                    aid = match.group(1)
                    item = found.setdefault(aid, {'id': aid, 'official_url': url, 'title': 'Artifact', 'observed_at': stamp})
                    name = value.get('value', {}).get('name') if isinstance(value.get('value'), dict) else None
                    if isinstance(name, str) and name:
                        item['title'] = name[:200]
                    if isinstance(frame.get('slug'), str):
                        slugs[aid] = frame['slug']
    return found, slugs


def screenshot(block, transcript):
    content = block.get('content', [])
    if not isinstance(content, list):
        return None
    for item in content:
        if item.get('type') == 'remote_image':
            try:
                body = remote_image(item.get('session_id'), item.get('media_id'))
                if body:
                    return body
            except (OSError, ValueError, KeyError):
                continue
        if item.get('type') == 'image':
            source = item.get('source', {})
            if source.get('type') == 'base64' and source.get('media_type') == 'image/png':
                try:
                    body = base64.b64decode(source.get('data', ''), validate=True)
                except ValueError:
                    continue
                if len(body) <= MAX_IMAGE and body.startswith(b'\x89PNG\r\n\x1a\n'):
                    return body
        if item.get('type') == 'text':
            if transcript is None:
                continue
            match = re.fullmatch(r'\[Image: source: ([^\n]+)\]', item.get('text', ''))
            if not match:
                continue
            path = Path(match.group(1))
            allowed = transcript.parent / transcript.stem / 'tool-results'
            if allowed not in path.parents or path.suffix.lower() != '.png' or '..' in path.parts:
                continue
            try:
                with os.fdopen(safe_file(path), 'rb') as file:
                    body = file.read(MAX_IMAGE + 1)
                if len(body) <= MAX_IMAGE and body.startswith(b'\x89PNG\r\n\x1a\n'):
                    return body
            except OSError:
                continue
    return None


def extract_document(rows, slug):
    calls, documents, images, publishes = {}, [], {}, {}
    seen = set()
    for stamp, blocks, path in rows:
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get('type')
            if kind == 'tool_use':
                data = block.get('input', {})
                if isinstance(data, dict) and data.get('container', {}).get('id') == slug:
                    calls.setdefault(block.get('id'), data)
            elif kind == 'tool_result':
                tid = block.get('tool_use_id')
                if tid in seen or tid not in calls or block.get('is_error'):
                    continue
                seen.add(tid)
                call = calls[tid]
                node = call.get('ref', {}).get('id')
                engine = call.get('engine')
                payload = call.get('payload', {})
                values = json_results(block)
                if engine == 'prose':
                    for value in values:
                        data = value.get('data', {})
                        xml = (data.get('xml') or value.get('xml', '')) if isinstance(data, dict) else ''
                        if not isinstance(xml, str) or len(xml.encode()) > 512 * 1024 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
                            continue
                        try:
                            root = ET.fromstring(xml)
                        except ET.ParseError:
                            continue
                        if (root.tag == 'doc' and root.get('node') == node and not root.get('op')
                                and set(root.attrib) <= {'node', 'rev'}
                                and not (set(payload) - {'kind', 'projection'})
                                and payload.get('projection') in (None, 'full', 'document')
                                and not root.get('sinceRev') and root.get('outline') != 'true'
                                and not any(part.tag in ('gap', 'gone') for part in root.iter())):
                            documents.append({'xml': xml, 'revision': int(root.get('rev', data.get('rev', 0))), 'captured_at': stamp})
                if engine == 'widget' and payload.get('kind') == 'screenshot':
                    valid = any(v.get('verdict') == 'allow' and v.get('value', {}).get('kind') == 'screenshot' and not v.get('value', {}).get('errors') for v in values)
                    body = screenshot(block, path) if valid else None
                    if body:
                        images[node] = {'mime': 'image/png', 'data': base64.b64encode(body).decode(), 'sha256': hashlib.sha256(body).hexdigest(), 'captured_at': stamp}
                if engine == 'widget' and any(v.get('outcome') == 'published' for v in values):
                    publishes[node] = max(stamp, publishes.get(node, ''))
    if not documents:
        return None
    document = max(documents, key=lambda x: (x['captured_at'], x['revision']))
    root = ET.fromstring(document['xml'])
    selected, total = {}, 0
    for embed in root.iter('embed'):
        ref = embed.get('ref', '')
        node = ref.removeprefix('node/')
        image = images.get(node)
        if image and total + len(image['data']) * 3 // 4 <= MAX_IMAGES:
            total += len(image['data']) * 3 // 4
            selected[ref] = {**image, 'stale': publishes.get(node, '') > image['captured_at']}
    return {**document, 'images': selected, 'figure_count': len(list(root.iter('embed'))),
            'available_figures': len(selected), 'stale_figures': sum(x['stale'] for x in selected.values()),
            'snapshot': True}


def replay_source(rows, slug, asset_roots=()):
    """Export acknowledged prose operations, never run generated widget code."""
    calls, seen, records, images, published, assets = {}, set(), [], {}, {}, {}
    def resolved(value, lids):
        if isinstance(value, str):
            return re.sub(r'\$lid:([A-Za-z0-9_-]+)', lambda m: str(lids.get(m[1], m[0])), value)
        if isinstance(value, list): return [resolved(x, lids) for x in value]
        if isinstance(value, dict): return {k: resolved(v, lids) for k,v in value.items()}
        return value
    def add_ops(call, ack, stamp, initial=False):
        xml = ack.get('xml', '')
        if not isinstance(xml, str) or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper(): return
        try: docs = list(ET.fromstring('<records>'+xml+'</records>'))
        except ET.ParseError: return
        ops = call.get('payload', {}).get('ops', [])
        for i, op in enumerate(ops):
            doc = next((d for d in docs if d.get('op', '0') == str(i)), None)
            if doc is None: continue
            prefix = ack.get('session', '')
            def canonical(value): return prefix + value if value.startswith('.') else value
            blocks = [{'id': canonical(b.get('id', '')), 'tag': b.tag, 'hash': b.get('h'),
                       'text': ''.join(b.itertext())} for b in doc]
            records.append({'op':op,'blocks':blocks,'revision':ack.get('rev'), 'captured_at':stamp,
                            'initial':initial,'node':call.get('ref',{}).get('id')})
    for stamp, blocks, path in rows:
        for block in blocks:
            if block.get('type') == 'tool_use': calls.setdefault(block.get('id'), block.get('input', {}))
            if block.get('type') != 'tool_result' or block.get('is_error'): continue
            tid = block.get('tool_use_id')
            if tid in seen or tid not in calls: continue
            call = calls[tid]
            # Only read images explicitly published as assets in this artifact's
            # history, with a matching successful upload receipt and byte count.
            if call.get('action') == 'publish' and call.get('asset') and LINK.fullmatch(call.get('url', '')):
                content = block.get('content', [])
                receipt = content if isinstance(content,str) else '\n'.join(x.get('text','') for x in content if x.get('type')=='text')
                uploads=[(*m.groups(),None) for m in re.finditer(r'"([^"\n]+)" \((\d+) bytes, (image/(?:png|jpeg))\) → "/_blob/([a-f0-9]{32})"', receipt)]
                single=re.search(r'Asset uploaded: "([^"\n]+)" \((\d+) bytes, (image/(?:png|jpeg)), sha256 ([a-f0-9]{64})\).*?"/_blob/([a-f0-9]{32})"',receipt,re.S)
                if single:
                    name,size,mime,digest,asset=single.groups()
                    matches=[f for f in call.get('file_paths',[call.get('file_path')]) if isinstance(f,str) and Path(f).name==Path(name).name]
                    if len(matches)==1:uploads.append((matches[0],size,mime,asset,digest))
                for file,size,mime,asset,digest in uploads:
                    p=Path(file)
                    approved = desktop_home(HOME)/'scratch-workspaces'
                    temporary = Path(tempfile.gettempdir())/('claude-'+str(getattr(os,'getuid',lambda:0)()))
                    if not any(root in p.parents for root in (approved, HOME/'.claude/projects', temporary,*asset_roots)) or '..' in p.parts:continue
                    if file not in call.get('file_paths',[call.get('file_path')]) or int(size)>MAX_IMAGE:continue
                    try:
                        with os.fdopen(safe_file(p),'rb') as stream:body=stream.read(MAX_IMAGE+1)
                        valid = body.startswith(b'\x89PNG\r\n\x1a\n') if mime=='image/png' else body.startswith(b'\xff\xd8\xff')
                        sha=hashlib.sha256(body).hexdigest()
                        if len(body)==int(size) and valid and (not digest or sha==digest):assets[asset]={'mime':mime,'data':base64.b64encode(body).decode(),'sha256':sha,'captured_at':stamp}
                    except (OSError,ValueError):pass
            for value in json_results(block):
                if value.get('verdict') != 'allow': continue
                container = call.get('container', {})
                if container.get('id') != slug and value.get('frame', {}).get('slug') != slug: continue
                seen.add(tid)
                init = container.get('create', {}).get('doc')
                if init and isinstance(init,dict) and isinstance(init.get('markdown'),str):
                    ack=next((x for x in value.get('acks',[]) if x.get('xml') and x.get('id')),None)
                    if ack:
                        c={'ref':{'id':ack['id']},'payload':{'ops':[{'op':'insert','target':{'kind':'root'},'side':'end','source':{'as':'markdown','from':{'kind':'inline','content':init['markdown']},'blocks':init.get('blocks',{})}}]}}
                        add_ops(c,ack,stamp,True)
                pairs = [(call,value)]
                if isinstance(call.get('batch'),list):
                    pairs = [(resolved(c,value.get('lids',{})),v) for c,v in zip(call['batch'],value.get('acks',[]))]
                for c,v in pairs:
                    if v.get('verdict') != 'allow': continue
                    if c.get('engine') == 'prose': add_ops(c,v,stamp)
                    node=c.get('ref',{}).get('id') or v.get('minted')
                    if c.get('engine')=='blob' and v.get('minted') and c.get('payload',{}).get('asset') in assets:
                        images['blob/'+v['minted']]=assets[c['payload']['asset']]
                    if c.get('engine') == 'widget':
                        if c.get('payload',{}).get('kind')=='screenshot' and v.get('value',{}).get('kind')=='screenshot':
                            body=screenshot(block,path)
                            if body: images['node/'+node]={'mime':'image/png','data':base64.b64encode(body).decode(),'sha256':hashlib.sha256(body).hexdigest(),'captured_at':stamp,'render_warning':bool(v.get('value',{}).get('errors'))}
                        if v.get('outcome')=='published': published['node/'+node]=stamp
    for ref,image in images.items(): image['stale']=published.get(ref,'')>image['captured_at']
    total=0;selected={}
    for ref,image in images.items():
        total+=len(image['data'])*3//4
        if total<=MAX_IMAGES: selected[ref]=image
    return {'records':records,'images':selected} if records else None


def published_html(rows, aid, asset_roots=()):
    """Read a successful, session-registered HTML publication as a download."""
    calls, candidates = {}, []
    for stamp, blocks, _ in rows:
        for block in blocks:
            if block.get('type')=='tool_use' and block.get('name')=='Artifact':
                calls.setdefault(block.get('id'),block.get('input',{}))
            if block.get('type')!='tool_result' or block.get('is_error'):continue
            call=calls.get(block.get('tool_use_id'),{})
            path=call.get('file_path','')
            content=block.get('content',[])
            text=content if isinstance(content,str) else '\n'.join(x.get('text','') for x in content if isinstance(x,dict) and x.get('type')=='text')
            if isinstance(path,str) and path.endswith('.html') and text.startswith('Published ') and any(m[1]==aid for m in LINK.finditer(text)):
                candidates.append((stamp,call))
    if not candidates:return None
    stamp,call=max(candidates,key=lambda x:x[0]);path=Path(call['file_path'])
    approved=(desktop_home(HOME)/'scratch-workspaces',Path(tempfile.gettempdir())/('claude-'+str(getattr(os,'getuid',lambda:0)())),*asset_roots)
    if not any(root in path.parents for root in approved) or '..' in path.parts:return None
    try:
        with os.fdopen(safe_file(path),'rb') as file:body=file.read(512*1024+1)
        if len(body)>512*1024:return None
        body.decode('utf-8')
    except (OSError,ValueError):return None
    return {'data':base64.b64encode(body).decode(),'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body),
            'captured_at':stamp,'dependency_count':len(call.get('files',{})),'title':call.get('title') or call.get('label')}


def resolve_session(sid):
    if not isinstance(sid, str) or not SID.fullmatch(sid):
        raise ValueError('invalid_session')
    current = native_rpc({'action': 'list'})
    session = next((x for x in current.get('sessions', []) if x.get('id') == sid), None)
    if session is None:
        raise ValueError('artifact_session_unavailable')
    import client
    index = client.desktop_index()
    local = index.get(session.get('local_id') or sid)
    return local, index


def read_artifacts(request):
    if request.get('action') not in ('list', 'read') or set(request) - {'action', 'session_id', 'artifact_id', 'full_history'} or type(request.get('full_history', False)) is not bool:
        raise ValueError('artifact_action_blocked')
    sid = request.get('session_id')
    local, index = resolve_session(sid)
    path = Path(local['path']) if local else None
    primary, limited = rows_from(path, request.get('full_history', False)) if path else ([], False)
    remote_limited = False
    if sid.startswith('cse_'):
        cached, complete = cached_native_rows(sid)
        primary.extend(cached)
        extra, remote_limited = remote_rows(sid)
        primary.extend(extra)
        remote_limited = remote_limited and not complete
    if not path:
        limited = remote_limited
    links, slugs = artifact_links(primary)
    if request['action'] == 'list':
        return {'ok': True, 'session_id': sid, 'artifacts': list(links.values())[-200:], 'history_limited': limited,
                'record_source': 'local_and_remote' if path else 'remote_control'}
    aid = request.get('artifact_id')
    if not isinstance(aid, str) or aid not in links:
        raise ValueError('artifact_not_in_session')
    slug = slugs.get(aid)
    rows = list(primary)
    # A linked fork can own figures referenced by this exact artifact. Read
    # only same-project transcript tails and extract only this artifact.
    related = sorted((x for x in index.values() if local and x.get('cwd') == local.get('cwd') and x.get('path') != str(path)), key=lambda x: x.get('updated', ''), reverse=True)[:6]
    for other in related:
        extra, _ = rows_from(Path(other['path']), request.get('full_history', False))
        refs, mapped = artifact_links(extra)
        if aid in refs:
            rows.extend(extra)
            slug = slug or mapped.get(aid)
    rows.sort(key=lambda x: x[0])
    value = extract_document(rows, slug) if slug else None
    replay = replay_source(rows, slug, (Path(local['cwd']),) if local else ()) if slug else None
    if value and replay:
        root=ET.fromstring(value['xml'])
        refs=[x.get('ref') or x.get('blob') for x in root.iter() if x.tag in ('embed','media')]
        all_images={**value['images'],**replay['images']}
        value['images']={ref:all_images[ref] for ref in refs if ref in all_images}
        value['figure_count']=len(refs);value['available_figures']=sum(ref in all_images for ref in refs)
        value['stale_figures']=sum(x.get('stale',False) for x in value['images'].values())
        value['latest_observed_revision']=max(x.get('revision') or 0 for x in replay['records'])
        value['snapshot_outdated']=value['latest_observed_revision']>value['revision']
    if not value and replay:
        value = {'replay': replay['records'], 'images': replay['images'], 'snapshot': True}
    html_source=published_html(rows,aid,(Path(local['cwd']),) if local else ()) if not value else None
    return {'ok': True, 'session_id': sid, 'artifact': {**links[aid], 'available': value is not None,
            **(value or {'reason': 'artifact_snapshot_unavailable'}), **({'html_source':html_source} if html_source else {})}, 'history_limited': limited,
            'record_source': 'local_and_remote' if path else 'remote_control'}


def main():
    os.umask(0o077)
    try:
        raw = sys.stdin.buffer.readline(4097)
        if len(raw) > 4096:
            raise ValueError('artifact_request_limit')
        result = read_artifacts(json.loads(raw))
        encoded = json.dumps(result, ensure_ascii=False).encode()
        if len(encoded) > 20 * 1024 * 1024:
            raise ValueError('artifact_response_limit')
    except Exception as exc:
        allowed = {'invalid_session', 'artifact_action_blocked', 'artifact_session_unavailable', 'artifact_local_history_unavailable',
                   'artifact_not_in_session', 'artifact_source_limit', 'artifact_request_limit', 'artifact_response_limit'}
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in allowed else 'artifact_read_unavailable'
        encoded = json.dumps({'ok': False, 'error': code}).encode()
    sys.stdout.buffer.write(encoded + b'\n')


if __name__ == '__main__':
    main()
