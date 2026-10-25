"""Private node import. Never adopt subscription routing, scripts or listeners."""
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs
import base64
import copy
import hashlib
import ipaddress
import json
import re
import socket
import subprocess
import threading
import time
import uuid
import yaml

LIMIT = 4 * 1024 * 1024
SUPPORTED = {'ss', 'vmess', 'vless', 'trojan'}
FIELDS = {'name', 'type', 'server', 'port', 'cipher', 'password', 'uuid', 'alterId', 'tls', 'servername', 'sni', 'alpn', 'client-fingerprint', 'fingerprint', 'network', 'flow', 'reality-opts', 'ws-opts', 'grpc-opts', 'http-opts', 'h2-opts', 'plugin', 'plugin-opts'}


def b64(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4)).decode()


def uri(line):
    if line.startswith('vmess://'):
        d = json.loads(b64(line[8:])); n = {'name': d.get('ps', 'VMess'), 'type': 'vmess', 'server': d['add'], 'port': int(d['port']), 'uuid': d['id'], 'alterId': int(d.get('aid', 0)), 'cipher': d.get('scy', 'auto'), 'tls': d.get('tls') == 'tls', 'network': d.get('net', 'tcp')}
        if d.get('sni'): n['servername'] = d['sni']
        if n['network'] == 'ws': n['ws-opts'] = {'path': d.get('path', '/'), 'headers': {'Host': d.get('host', d['add'])}}
        if n['network'] == 'grpc': n['grpc-opts'] = {'grpc-service-name': d.get('path', '')}
        return n
    u = urlsplit(line); q = {k: v[0] for k, v in parse_qs(u.query).items()}
    if u.scheme == 'ss' and not u.hostname:
        return uri('ss://' + b64(line[5:].split('#')[0]) + ('#' + u.fragment if u.fragment else ''))
    if u.scheme == 'ss' and '@' not in u.netloc:
        return uri('ss://' + b64(u.netloc) + ('#' + u.fragment if u.fragment else ''))
    n = {'name': unquote(u.fragment) or u.scheme.upper(), 'type': u.scheme, 'server': u.hostname, 'port': u.port or 443}
    if u.scheme == 'ss':
        auth = unquote(u.username or '')
        if u.password is None:
            cipher, password = b64(auth).split(':', 1)
        else: cipher, password = auth, unquote(u.password)
        n.update(cipher=cipher, password=password)
        if q.get('plugin'):
            items = q['plugin'].split(';'); n['plugin'] = {'obfs-local': 'obfs'}.get(items[0], items[0]); n['plugin-opts'] = {}
            for item in items[1:]:
                k, _, v = item.partition('='); n['plugin-opts'][{'obfs': 'mode', 'obfs-host': 'host'}.get(k, k)] = v or True
    elif u.scheme in ('vless', 'trojan'):
        n['uuid' if u.scheme == 'vless' else 'password'] = unquote(u.username or '')
        n.update(tls=u.scheme == 'trojan' or q.get('security') in ('tls', 'reality'), network=q.get('type', 'tcp'))
        if q.get('sni'): n['servername' if u.scheme == 'vless' else 'sni'] = q['sni']
        if q.get('fp'): n['client-fingerprint'] = q['fp']
        if q.get('flow'): n['flow'] = q['flow']
        if q.get('pbk'): n['reality-opts'] = {'public-key': q['pbk'], 'short-id': q.get('sid', '')}
        if n['network'] == 'ws': n['ws-opts'] = {'path': q.get('path', '/'), 'headers': {'Host': q.get('host', u.hostname)}}
        if n['network'] == 'grpc': n['grpc-opts'] = {'grpc-service-name': q.get('serviceName', '')}
    return n


def normalize(n):
    if not isinstance(n, dict) or not isinstance(n.get('server'), str): raise ValueError('subscription_invalid')
    n = {k: copy.deepcopy(v) for k, v in n.items() if k in FIELDS}
    n['port'] = int(n.get('port', 0)); n['name'] = str(n.get('name') or n.get('type'))[:160]
    if not 1 <= n['port'] <= 65535 or len(n['server']) > 253 or not re.fullmatch(r'[a-zA-Z0-9.:-]+', n['server']): raise ValueError('subscription_invalid')
    # File-backed certificates, external commands and routing chains are never imported.
    def check(v, depth=0):
        if depth > 5: raise ValueError('subscription_invalid')
        if isinstance(v, dict):
            for k, x in v.items():
                if not isinstance(k, str) or any(s in k.lower() for s in ('file', 'path-to', 'command', 'script', 'private-key')): raise ValueError('subscription_invalid')
                check(x, depth+1)
        elif isinstance(v, list):
            if len(v)>50: raise ValueError('subscription_invalid')
            for x in v: check(x, depth+1)
        elif not isinstance(v, (str, int, float, bool, type(None))): raise ValueError('subscription_invalid')
    check(n)
    if len(json.dumps(n)) > 16384: raise ValueError('subscription_invalid')
    n['udp'] = False
    n['_supported'] = n.get('type') in SUPPORTED and n.get('network', 'tcp') in ('tcp', 'ws', 'grpc', 'http', 'h2') and n.get('plugin', 'obfs') in ('obfs', 'v2ray-plugin')
    return n


def parse(raw):
    if not isinstance(raw, str) or len(raw.encode()) > LIMIT: raise ValueError('subscription_invalid')
    raw = raw.strip()
    if not raw: raise ValueError('subscription_invalid')
    if '://' not in raw and not raw.startswith(('{', 'proxies:', 'payload:')):
        try: raw = b64(raw)
        except Exception: pass
    if any(raw.startswith(s + '://') for s in ('ss','vmess','vless','trojan','hysteria2','hy2','tuic')):
        nodes = [uri(line.strip()) for line in raw.splitlines() if line.strip() and not line.startswith('#')]
    else:
        if any(isinstance(t, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for t in yaml.scan(raw)): raise ValueError('subscription_invalid')
        d = yaml.safe_load(raw)
        nodes = d.get('proxies', d.get('payload')) if isinstance(d, dict) else d
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 2000: raise ValueError('subscription_invalid')
    return [normalize(n) for n in nodes]


def resolve(host):
    ips = sorted({a[4][0] for a in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)})
    if not ips or any(not ipaddress.ip_address(x).is_global for x in ips): raise ValueError('public_endpoint_required')
    return ips


def fetch(url):
    # Pin a validated public IP; reject redirects rather than forwarding credentials.
    u = urlsplit(url)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment or '\n' in url or '\r' in url: raise ValueError('https_subscription_required')
    ips = resolve(u.hostname); port = u.port or 443
    cfg = f'url = {json.dumps(url)}\n'
    p = subprocess.run(['curl', '--config', '-', '--silent', '--show-error', '--fail', '--proto', '=https', '--max-time', '20', '--max-filesize', str(LIMIT), '--noproxy', '*', '--resolve', f'{u.hostname}:{port}:{ips[0]}', '--user-agent', 'ClashMeta'], input=cfg.encode(), capture_output=True, timeout=25)
    if p.returncode or len(p.stdout)>LIMIT: raise ValueError('subscription_fetch_failed')
    return p.stdout.decode('utf-8-sig')


class Subscriptions:
    def __init__(self, config):
        self.config = config
        self.folder = Path(config['state_dir']) / 'subscriptions'; self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.cache = None

    def write(self, path, data):
        t = path.with_suffix('.tmp'); t.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8'); t.chmod(0o600); t.replace(path)

    def ssh(self, command, data=None, timeout=20):
        profile = self.config.get('router')
        if not profile: raise ValueError('router_profile_required')
        p = subprocess.run(profile['ssh'] + [command], input=data, capture_output=True, timeout=timeout)
        if p.returncode: raise ValueError('router_unavailable')
        return p.stdout

    def current_config(self):
        if self.config.get('router',{}).get('executor_ssh'):
            from .router_remote import call
            return call(self.config,{'action':'current'})['config']
        return yaml.safe_load(self.ssh('cat /etc/mm-proxy/config.yaml'))

    def snapshot(self):
        result = {'sources': [], 'nodes': [], 'current': {'available': False}}
        with self.lock:
            files = list(self.folder.glob('source-*.json'))
            for p in files:
                d = json.loads(p.read_text(encoding='utf-8')); result['sources'].append({**{k:d[k] for k in ('id','name','count','updated_at')}, 'refreshable': bool(d.get('url'))})
                result['nodes'].extend({'id': n['_id'], 'name': n['name'], 'type': n.get('type'), 'supported': n['_supported'], 'source': d['id']} for n in d['nodes'])
        try:
            if not self.cache or time.monotonic()-self.cache[0]>15:
                d=self.current_config(); g=d['proxy-groups'][0]
                self.cache=(time.monotonic(),{'available':True,'mode':g['type'],'nodes':g['proxies'],'selected':None})
                cmd="s=$(cat /etc/mm-proxy/controller.secret); printf '%s\\r\\n' 'GET /proxies HTTP/1.0' 'Host: 127.0.0.1' \"Authorization: Bearer $s\" 'Connection: close' '' | nc 127.0.0.1 9090"
                if not self.config.get('router',{}).get('executor_ssh'):
                    raw=self.ssh(cmd).decode().replace('\r\n','\n').split('\n\n',1)[1]
                    self.cache[1]['selected']=json.loads(raw)['proxies'][g['name']].get('now')
            result['current']=self.cache[1]
        except Exception: pass
        result['plans'] = []
        for p in sorted(self.folder.glob('plan-*.json'), key=lambda x:x.stat().st_mtime, reverse=True)[:5]:
            d=json.loads(p.read_text(encoding='utf-8')); report=self.folder/f'apply-{d["id"]}.json'
            result['plans'].append({'id':d['id'],'created_at':d['created_at'],'names':d['names'],
                                   'result':json.loads(report.read_text(encoding='utf-8')) if report.exists() else None,
                                   'command':f'./bin/starport apply-exit {d["id"]}'})
        return result

    def import_nodes(self, data, source_id=None):
        url = data.get('url', '')
        raw = fetch(url) if url else data.get('content', '')
        try: nodes=parse(raw)
        except Exception: raise ValueError('subscription_invalid') from None
        sid = source_id or uuid.uuid4().hex
        for n in nodes: n['_id'] = hashlib.sha256((sid+json.dumps(n,sort_keys=True)).encode()).hexdigest()[:24]
        d={'id':sid,'name':str(data.get('name') or '订阅')[:100], 'url':url,'nodes':nodes,'count':len(nodes),'updated_at':time.time()}
        with self.lock: self.write(self.folder/f'source-{sid}.json',d)
        return {'ok':True,'id':sid,'nodes':len(nodes)}

    def refresh(self, sid):
        if not re.fullmatch('[a-f0-9]{32}',sid or ''): raise ValueError('invalid_subscription')
        d=json.loads((self.folder/f'source-{sid}.json').read_text(encoding='utf-8'))
        if not d.get('url'): raise ValueError('pasted_subscription_has_no_url')
        return self.import_nodes(d,sid)

    def preview(self, data):
        ids=data.get('nodes'); mode=data.get('mode')
        # Existing endpoint guard requires three labelled slots, retained as-is.
        if not isinstance(ids,list) or len(ids)!=3 or len(set(ids))!=3 or mode not in ('fallback','select'): raise ValueError('choose_three_nodes')
        nodes={n['_id']:n for p in self.folder.glob('source-*.json') for n in json.loads(p.read_text(encoding='utf-8'))['nodes']}
        selected=[]
        for id in ids:
            if id not in nodes or not nodes[id]['_supported']: raise ValueError('unsupported_node')
            selected.append(copy.deepcopy(nodes[id]))
        old=self.current_config(); candidate=copy.deepcopy(old); aliases=[n['name'] for n in old['proxies']]
        if len(aliases)!=3 or len(old['proxy-groups'])!=1: raise ValueError('router_profile_mismatch')
        endpoints=[]
        for alias,n in zip(aliases,selected):
            host=n['server']; ips=resolve(host); n['server']=ips[0]; n['name']=alias
            if n.get('tls') and not n.get('servername') and not n.get('sni'):
                n['sni' if n['type']=='trojan' else 'servername']=host
            n['interface-name']=old.get('interface-name','eth3')
            for k in list(n):
                if k.startswith('_'): del n[k]
            endpoints.append({'node':alias,'ip':ips[0],'port':n['port'],'protocol':'tcp'})
        if len({(e['ip'],e['port']) for e in endpoints})<3: raise ValueError('three_distinct_endpoints_required')
        candidate['proxies']=selected; group=candidate['proxy-groups'][0];group['type']=mode;group['proxies']=aliases
        if mode=='select': group['proxies']=aliases[:1]
        previous_ips={n['server']+'/32' for n in old['proxies']}
        exclude=[n for n in old['tun'].get('route-exclude-address',[]) if n not in previous_ips]
        candidate['tun']['route-exclude-address']=list(dict.fromkeys(exclude+[e['ip']+'/32' for e in endpoints]))
        pid=uuid.uuid4().hex;plan={'id':pid,'created_at':time.time(),'base_sha256':hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest(),'candidate':candidate,'endpoints':endpoints,'names':[nodes[i]['name'] for i in ids]}
        self.write(self.folder/f'plan-{pid}.json',plan)
        return {'id':pid,'summary':' → '.join(plan['names']),'impact':'保存并应用这三个节点，保留独立防泄漏规则。切换可能使已有网络连接重连；不同出口端点需要本机管理员授权。'}

    def apply(self, pid):
        if not re.fullmatch('[a-f0-9]{32}',pid or ''): raise ValueError('invalid_plan')
        plan=json.loads((self.folder/f'plan-{pid}.json').read_text(encoding='utf-8'))
        if time.time()-plan['created_at']>1800: raise ValueError('plan_expired')
        current=self.current_config()
        if current==plan['candidate']:
            result={'ok':True,'unchanged':True,'checked_at':time.time()}
            self.write(self.folder/f'apply-{pid}.json',result)
            return result
        if hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()!=plan['base_sha256']: raise ValueError('router_changed_preview_again')
        # Privileged network updates require an explicit local desktop approval,
        # never a root password or reusable elevated capability in the browser.
        return {'ok':False,'requires_local_admin':True,'command':f'python starport.py apply-exit {pid}'}
