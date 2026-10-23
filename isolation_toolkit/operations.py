"""Local adapters: bounded fixed commands; no credentials or arbitrary shell API."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections import Counter
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from .registry import CONTROL, TOOLS, GROUPS, describe, in_scope

PROPERTIES = ['Id', 'LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'FragmentPath', 'MainPID', 'Result', 'NRestarts', 'Restart', 'After', 'Requires', 'Wants', 'ActiveEnterTimestamp']


def run(argv, timeout=8):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env={**os.environ, 'LC_ALL': 'C'}, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 125, ''


def systemctl(scope, *args):
    return ['/usr/bin/systemctl', *(('--user',) if scope == 'user' else ()), '--no-pager', *args]


def inventory(scope):
    rc, raw = run(systemctl(scope, 'list-unit-files', '--no-legend', '--plain', '--type=service,timer'))
    if rc:
        return {'scope': scope, 'error': 'service_inventory_unavailable', 'services': []}
    names = sorted({x.split()[0] for x in raw.splitlines() if x.split() and in_scope(x.split()[0])})
    if not names:
        return {'scope': scope, 'services': []}
    rc, raw = run(systemctl(scope, 'show', *names, '--property=' + ','.join(PROPERTIES)))
    if rc:
        return {'scope': scope, 'error': 'service_details_unavailable', 'services': []}
    rows = []
    for block in raw.strip().split('\n\n'):
        d = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        name = d.get('Id', '')
        if not in_scope(name):
            continue
        active, state = d.get('ActiveState'), d.get('UnitFileState')
        health = 'attention' if active == 'failed' or d.get('SubState') == 'auto-restart' else 'running' if active == 'active' else 'starting' if active == 'activating' else 'scheduled' if state in ('static', 'indirect') else 'stopped'
        key = scope + ':' + name
        rows.append({**describe(name), 'id': key, 'unit': name, 'scope': scope, 'active': active, 'sub': d.get('SubState'), 'enabled': state,
                     'health': health, 'registered': bool(d.get('FragmentPath')), 'registration': ('user-unit/' if scope == 'user' else 'system-unit/') + name,
                     'pid': int(d.get('MainPID') or 0), 'result': d.get('Result'), 'restarts': int(d.get('NRestarts') or 0),
                     'restart_policy': d.get('Restart'), 'since': d.get('ActiveEnterTimestamp'), 'actions': list(CONTROL.get(key, ())),
                     'after': d.get('After', '').split(), 'requires': d.get('Requires', '').split()})
    return {'scope': scope, 'services': rows}


def monitor(config):
    # Fixed local read-only collector; URL is never provided by an HTTP client.
    port = int(config.get('monitor_port', 18880))
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/api/status', timeout=6) as r:
            raw = r.read(12 * 1024 * 1024 + 1)
        if len(raw) > 12 * 1024 * 1024:
            raise ValueError('too_large')
        d = json.loads(raw)
        jobs = d.get('jobs', [])
        terminal = {'completed', 'failed', 'cancelled', 'canceled', 'succeeded'}
        live = [j for j in jobs if j.get('status') not in terminal]
        latest = [j for j in jobs if j.get('status') in terminal][-60:]
        return {'available': True, **{k: d.get(k) for k in ['updated_at', 'error', 'nodes', 'transfers', 'storage', 'cleanup', 'standard']},
                'job_count': len(jobs), 'job_counts': dict(Counter(j.get('status', 'unknown') for j in jobs)), 'live_jobs': live,
                'recent_jobs': latest, 'datasets_count': len(d.get('datasets', [])), 'datasets': d.get('datasets', [])[-40:]}
    except (OSError, ValueError, TypeError):
        return {'available': False, 'error': 'monitor_unavailable'}


class Manager:
    def __init__(self, config):
        self.config = config
        self.cache = {}
        self.lock = threading.Lock()
        self.router_lock = threading.Lock()
        self.action_lock = threading.Lock()

    def overview(self):
        with self.lock:
            old = self.cache.get('overview')
            if old and time.monotonic() - old[0] < 8:
                return old[1]
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(inventory, x) for x in ('system', 'user')]
            info = [f.result() for f in futures]
        services = sorted([s for x in info for s in x['services']], key=lambda s: (s['group'], s['scope'], s['unit']))
        root = Path(self.config['legacy_root'])
        tools = [{**t, 'available': (root / t['path']).exists()} for t in TOOLS]
        disk = shutil.disk_usage(root if root.exists() else Path.home())
        rc, linger = run(['/usr/bin/loginctl', 'show-user', str(os.getuid()), '--property=Linger', '--value'])
        rc, raw = run(['/usr/sbin/ip', '-j', 'address', 'show'])
        try:
            network = [{'name': n['ifname'], 'state': n['operstate'], 'master': n.get('master'),
                        'addresses': [a['local'] + '/' + str(a['prefixlen']) for a in n.get('addr_info', []) if a.get('family') == 'inet']}
                       for n in json.loads(raw) if n['ifname'].startswith(('mm-', 'zt')) or n['ifname'] == self.config.get('client_interface')]
        except (ValueError, KeyError):
            network = []
        value = {'checked_at': time.time(), 'services': services, 'errors': [x for x in info if x.get('error')], 'tools': tools, 'groups': GROUPS,
                 'links': self.config.get('links', {}), 'network': network, 'user_linger': linger.strip() == 'yes',
                 'disk': {'total': disk.total, 'used': disk.used, 'free': disk.free}, 'router': self.router_status(),
                 'totals': dict(Counter(s['health'] for s in services))}
        with self.lock:
            self.cache['overview'] = (time.monotonic(), value)
        return value

    def router_status(self):
        with self.lock:
            return self.cache.get('router', {'state': 'not_checked', 'note': '尚未执行本轮路由探测'})

    def check_router(self, internet=False):
        if not self.router_lock.acquire(blocking=False):
            return self.router_status()
        with self.lock:
            self.cache['router'] = {'state': 'checking', 'started_at': time.time()}

        def worker():
            try:
                script = Path(self.config['legacy_root']) / 'router-status.sh'
                if not script.is_file() or script.is_symlink():
                    raise ValueError('router_adapter_missing')
                rc, raw = run([str(script), '--json', *(['--internet'] if internet else [])], timeout=105)
                try:
                    d = json.loads(raw)
                except ValueError:
                    d = {'error': 'router_check_failed', 'exit_code': rc}
                # Do not forward arbitrary command output or secrets.
                result = {'state': 'complete', 'checked_at': time.time(), 'internet': internet,
                          'all_checks_passed': d.get('all_checks_passed', False),
                          'checks': [{k: r.get(k) for k in ('check', 'passed', 'detail')} for r in d.get('checks', [])],
                          'limits': d.get('limits', []), 'error': d.get('error')}
            except Exception:
                result = {'state': 'error', 'error': 'router_check_unavailable'}
            finally:
                with self.lock:
                    self.cache['router'] = result
                    self.cache.pop('overview', None)
                self.router_lock.release()
        threading.Thread(target=worker, daemon=True).start()
        return self.router_status()

    def preview(self, key, action):
        if not isinstance(key, str) or action not in CONTROL.get(key, ()):
            raise ValueError('operation_not_allowed')
        scope, unit = key.split(':', 1)
        rows = inventory(scope)['services']
        row = next((r for r in rows if r['id'] == key), None)
        if not row or not row['registered']:
            raise ValueError('service_not_registered')
        state = {k: row[k] for k in ('id', 'active', 'sub', 'enabled', 'pid')}
        payload = {'service': key, 'action': action, 'expected': state}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return {'payload': payload, 'digest': digest, 'impact': row['note'], 'title': row['title']}

    def execute(self, payload, digest):
        with self.action_lock:
            p = self.preview(payload.get('service'), payload.get('action'))
            if p['payload'] != payload or p['digest'] != digest:
                raise ValueError('state_changed_preview_again')
            scope, unit = payload['service'].split(':', 1)
            rc, _ = run(systemctl(scope, '--no-ask-password', payload['action'], unit), timeout=20)
            with self.lock:
                self.cache.pop('overview', None)
            return {'ok': rc == 0, 'service': payload['service'], 'action': payload['action'], 'exit_code': rc,
                    'error': None if rc == 0 else 'service_action_failed', 'checked_at': time.time()}
