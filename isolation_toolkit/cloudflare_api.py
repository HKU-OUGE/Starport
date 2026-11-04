"""Owned Cloudflare enrollment; resource writes are journaled before sending.

Adapted from Starport Pi's MIT-licensed enrollment workflow. No model/client
process receives operator tokens or the Cloudflare account identifiers.
"""
from pathlib import Path
from urllib import request, error, parse
import fnmatch
import json
import os
import re
import secrets
import time

from starport_common.runtime import secure_dir, owned, WINDOWS


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def private_write(path, value):
    path = Path(path)
    secure_dir(path.parent)
    if path.is_symlink() or (path.exists() and not owned(path.stat())):
        raise ValueError('private_file_required')
    data = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    temp = path.parent / ('.' + path.name + '.' + secrets.token_hex(8))
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def private_read(path):
    path = Path(path)
    st = path.lstat()
    if path.is_symlink() or not path.is_file() or not owned(st) or st.st_size > 1048576:
        raise ValueError('private_file_required')
    if not WINDOWS and st.st_mode & 0o077:
        raise ValueError('private_file_permissions_required')
    return path.read_text(encoding='utf-8')


def enrollment_input(data):
    if not isinstance(data, dict) or set(data) - {'zone', 'prefix', 'emails', 'token'}:
        raise ValueError('invalid_publication_configuration')
    zone = str(data.get('zone', '')).strip().lower()
    prefix = str(data.get('prefix', 'console')).strip().lower().replace('_', '-')
    if len(zone) > 190 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', zone):
        raise ValueError('invalid_zone')
    if not re.fullmatch(r'[a-z](?:[a-z0-9-]{0,33}[a-z0-9])?', prefix):
        raise ValueError('invalid_hostname_prefix')
    emails = data.get('emails', [])
    if isinstance(emails, str):
        emails = re.split(r'[\s,;]+', emails.strip())
    if not isinstance(emails, list) or not 1 <= len(emails) <= 100 or any(
        not isinstance(e, str) or len(e) > 254 or not re.fullmatch(r'[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+', e) for e in emails
    ):
        raise ValueError('explicit_login_emails_required')
    token = data.get('token', '')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,512}', token.strip()):
        raise ValueError('cloudflare_api_token_required')
    return {'zone': zone, 'prefix': prefix, 'emails': sorted(set(e.lower() for e in emails))}, token.strip()


class Cloudflare:
    def __init__(self, token):
        self.token = token
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    def call(self, method, route, body=None):
        if not route.startswith('/') or any(c in route for c in '\r\n'):
            raise ValueError('invalid_cloudflare_route')
        req = request.Request('https://api.cloudflare.com/client/v4' + route,
            data=None if body is None else json.dumps(body).encode(), method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        unknown = 'cloudflare_unreachable' if method == 'GET' else 'cloudflare_outcome_unknown'
        try:
            with self.opener.open(req, timeout=25) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError(unknown)
            data = json.loads(raw)
        except error.HTTPError as exc:
            # An interrupted mutation can have succeeded: never blindly repeat it.
            if exc.code >= 500 or exc.code in (408,):
                raise ValueError(unknown) from None
            raise ValueError('cloudflare_http_' + str(exc.code)) from None
        except (OSError, ValueError):
            raise ValueError(unknown) from None
        if not isinstance(data, dict) or data.get('success') is not True:
            raise ValueError(unknown)
        return data

    def list(self, route):
        rows = []
        for page in range(1, 101):
            result = self.call('GET', route + ('&' if '?' in route else '?') + f'per_page=100&page={page}')
            if not isinstance(result.get('result'), list):
                raise ValueError('cloudflare_invalid_response')
            rows.extend(result['result'])
            if page >= result.get('result_info', {}).get('total_pages', 1):
                return rows
        raise ValueError('cloudflare_list_too_large')


class Enrollment:
    def __init__(self, folder, api):
        self.folder = secure_dir(folder)
        self.file = self.folder / 'receipt.json'
        self.api = api

    def read(self):
        return json.loads(private_read(self.file))

    def save(self, value):
        private_write(self.file, value)
        return value

    def plan(self, data, upstream, port):
        if self.file.exists():
            raise ValueError('existing_publication_resume_or_remove')
        zones = self.api.list('/zones?name=' + parse.quote(data['zone']))
        if len(zones) != 1 or zones[0]['name'] != data['zone']:
            raise ValueError('exact_zone_not_available')
        zone = zones[0]
        base = '/accounts/' + zone['account']['id']
        org = self.api.call('GET', base + '/access/organizations')['result']
        if not re.fullmatch(r'[a-z0-9-]+\.cloudflareaccess\.com', org.get('auth_domain', '')):
            raise ValueError('configure_zero_trust_team_first')
        if not self.api.list(base + '/access/identity_providers'):
            raise ValueError('configure_access_login_method_first')
        apps = self.api.list(base + '/access/apps')
        # Check the remaining read scopes before any resources are created.
        self.api.list(base + '/access/policies')
        self.api.list(base + '/cfd_tunnel?is_deleted=false')
        deployment = secrets.token_hex(6)
        for attempt in range(5):
            suffix = '' if attempt == 0 else '-' + secrets.token_hex(3)
            hostname = data['prefix'] + suffix + '.' + data['zone']
            records = self.api.list('/zones/' + zone['id'] + '/dns_records?name=' + hostname)
            domains = [a.get('domain', '').split('/')[0] for a in apps]
            domains += [d.get('uri', '').split('/')[0] for a in apps for d in a.get('destinations', [])]
            if not records and not any(fnmatch.fnmatchcase(hostname, d) for d in domains if d):
                break
        else:
            raise ValueError('hostname_already_used')
        return self.save({'format': 1, 'stage': 'planned', 'deployment': deployment,
            'name': 'starport-' + deployment, 'hostname': hostname, 'zone_name': data['zone'],
            'prefix': data['prefix'], 'zone': zone['id'], 'account': zone['account']['id'],
            'issuer': 'https://' + org['auth_domain'], 'emails': data['emails'],
            'upstream': upstream, 'port': port, 'resources': {}, 'pending': None, 'created_at': time.time()})

    def matches(self, key, current, body):
        fields = {'policy': ('name', 'decision', 'include', 'require', 'exclude'),
                  'app': ('name', 'type', 'domain'), 'tunnel': ('name',),
                  'dns': ('name', 'type', 'content', 'proxied')}[key]
        return all(current.get(k, [] if k in ('require', 'exclude') else None) == body[k] for k in fields)

    def reconcile(self):
        r = self.read()
        if r.get('pending'):
            p = r['pending']
            rows = self.api.list(p['route'] + ('?name=' + r['hostname'] if p['key'] == 'dns' else ''))
            matches = [row for row in rows if self.matches(p['key'], row, p['body']) and not row.get('deleted_at')]
            if len(matches) == 1:
                r['resources'][p['key']] = self.reference(matches[0])
                r['pending'] = None
                self.save(r)
        return r

    @staticmethod
    def reference(value):
        # Tunnel responses may contain credentials; receipts contain identifiers only.
        return {k: value[k] for k in ('id', 'name', 'domain', 'type', 'aud') if k in value}

    def create(self, r, key, route, body):
        if key in r['resources']:
            return r['resources'][key]
        if r.get('pending'):
            raise ValueError('cloudflare_outcome_unknown_reconcile')
        r['pending'] = {'key': key, 'route': route, 'body': body}
        self.save(r)
        try:
            value = self.api.call('POST', route, body)['result']
        except ValueError as exc:
            if re.fullmatch(r'cloudflare_http_[34][0-9]{2}', str(exc)):
                r['pending'] = None
                self.save(r)
            raise
        if not isinstance(value.get('id'), str) or not value['id']:
            raise ValueError('cloudflare_outcome_unknown_reconcile')
        r['resources'][key] = self.reference(value)
        r['pending'] = None
        self.save(r)
        return r['resources'][key]

    def verify_access(self, r):
        base = '/accounts/' + r['account']
        app = self.api.call('GET', base + '/access/apps/' + r['resources']['app']['id'])['result']
        policies = self.api.list(base + '/access/apps/' + app['id'] + '/policies')
        expected = [{'email': {'email': e}} for e in r['emails']]
        if app.get('domain') != r['hostname'] or app.get('type') != 'self_hosted' or not app.get('aud'):
            raise ValueError('access_application_mismatch')
        if (len(policies) != 1 or policies[0].get('id') != r['resources']['policy']['id'] or
            policies[0].get('decision') != 'allow' or policies[0].get('include') != expected or
            policies[0].get('require') or policies[0].get('exclude') or policies[0].get('reusable') is False):
            raise ValueError('access_policy_mismatch')
        r['resources']['app'] = self.reference(app)
        self.save(r)
        return r

    @staticmethod
    def tunnel_config(r):
        return {'config': {'ingress': [{'hostname': r['hostname'], 'service': 'http://127.0.0.1:' + str(r['port']),
            'originRequest': {'httpHostHeader': r['hostname'], 'access': {'required': True,
                'teamName': r['issuer'].removeprefix('https://').removesuffix('.cloudflareaccess.com'),
                'audTag': [r['resources']['app']['aud']]}}}, {'service': 'http_status:404'}]}}

    def verify_tunnel(self, r, allow_unconfigured=False):
        route = '/accounts/' + r['account'] + '/cfd_tunnel/' + r['resources']['tunnel']['id']
        value = self.api.call('GET', route + '/configurations')['result'] or {}
        ingress = value.get('config', {}).get('ingress')
        initial = allow_unconfigured and ingress in (None, [], [{'service': 'http_status:404'}])
        if not initial and ingress != self.tunnel_config(r)['config']['ingress']:
            raise ValueError('tunnel_configuration_mismatch')
        return self.api.call('GET', route)['result'].get('status', 'unknown')

    def verify_dns(self, r):
        value = self.api.call('GET', '/zones/' + r['zone'] + '/dns_records/' + r['resources']['dns']['id'])['result']
        if (value.get('name') != r['hostname'] or value.get('type') != 'CNAME' or
            value.get('content') != r['resources']['tunnel']['id'] + '.cfargotunnel.com' or value.get('proxied') is not True):
            raise ValueError('dns_configuration_mismatch')

    def verify_hostname(self, r):
        dns = self.api.list('/zones/' + r['zone'] + '/dns_records?name=' + r['hostname'])
        owned_dns = r['resources'].get('dns', {}).get('id')
        if any(not owned_dns or row.get('id') != owned_dns for row in dns):
            raise ValueError('hostname_changed_preview_again')
        apps = self.api.list('/accounts/' + r['account'] + '/access/apps')
        for app in apps:
            if r['resources'].get('app', {}).get('id') and app.get('id') == r['resources']['app']['id']:
                continue
            domains = [app.get('domain', '').split('/')[0]] + [d.get('uri', '').split('/')[0] for d in app.get('destinations', [])]
            if any(fnmatch.fnmatchcase(r['hostname'], d) for d in domains if d):
                raise ValueError('hostname_changed_preview_again')

    def apply(self):
        r = self.reconcile()
        if r.get('pending'):
            raise ValueError('cloudflare_outcome_unknown_reconcile')
        if r['stage'] in ('removed', 'removing'):
            raise ValueError('publication_removed')
        self.verify_hostname(r)
        if r['stage'] == 'configured':
            self.verify_access(r)
            self.verify_tunnel(r)
            self.verify_dns(r)
            return r
        base = '/accounts/' + r['account']
        policy = self.create(r, 'policy', base + '/access/policies', {'name': r['name'], 'decision': 'allow',
            'include': [{'email': {'email': e}} for e in r['emails']], 'require': [], 'exclude': [], 'session_duration': '12h'})
        self.create(r, 'app', base + '/access/apps', {'name': r['name'], 'type': 'self_hosted', 'domain': r['hostname'],
            'policies': [{'id': policy['id'], 'precedence': 1}], 'session_duration': '12h',
            'app_launcher_visible': False, 'http_only_cookie_attribute': True})
        self.verify_access(r)
        r['stage'] = 'access_ready'
        self.save(r)
        tunnel = self.create(r, 'tunnel', base + '/cfd_tunnel', {'name': r['name'], 'config_src': 'cloudflare'})
        route = base + '/cfd_tunnel/' + tunnel['id']
        self.api.call('PUT', route + '/configurations', self.tunnel_config(r))
        self.verify_tunnel(r)
        token = self.api.call('GET', route + '/token')['result']
        if not isinstance(token, str) or not token or len(token) > 65536:
            raise ValueError('invalid_tunnel_token')
        private_write(self.folder / 'tunnel-token', token)
        self.create(r, 'dns', '/zones/' + r['zone'] + '/dns_records', {'name': r['hostname'],
            'type': 'CNAME', 'content': tunnel['id'] + '.cfargotunnel.com', 'proxied': True, 'ttl': 1})
        self.verify_dns(r)
        r['stage'] = 'configured'
        return self.save(r)

    def remove(self):
        r = self.reconcile()
        if r.get('pending'):
            raise ValueError('cloudflare_outcome_unknown_reconcile')
        r['stage'] = 'removing'
        self.save(r)
        base = '/accounts/' + r['account']
        routes = {'dns': '/zones/' + r['zone'] + '/dns_records', 'tunnel': base + '/cfd_tunnel',
                  'app': base + '/access/apps', 'policy': base + '/access/policies'}
        for key, route in routes.items():
            owned_resource = r['resources'].get(key)
            if not owned_resource or owned_resource.get('removed'):
                continue
            try:
                current = self.api.call('GET', route + '/' + owned_resource['id'])['result']
            except ValueError as exc:
                if str(exc) != 'cloudflare_http_404':
                    raise
            else:
                if (current.get('name') != (r['hostname'] if key == 'dns' else r['name']) or
                    key == 'dns' and current.get('content') != r['resources']['tunnel']['id'] + '.cfargotunnel.com' or
                    key == 'app' and current.get('domain') != r['hostname'] or
                    key == 'policy' and current.get('app_count', 0) > 0):
                    raise ValueError('resource_changed_do_not_delete')
                if key == 'tunnel':
                    self.verify_tunnel(r, allow_unconfigured=not r['resources'].get('dns'))
                self.api.call('DELETE', route + '/' + owned_resource['id'])
            owned_resource['removed'] = True
            self.save(r)
        r['stage'] = 'removed'
        r['edge_status'] = 'unchecked'
        r['checked_at'] = None
        self.save(r)
        for name in ('tunnel-token', 'api-token'):
            p = self.folder / name
            if p.exists():
                p.unlink()
        return r
