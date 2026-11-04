"""Operator-only publication jobs and owned connector lifecycle."""
from pathlib import Path
import http.client
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

from starport_common.runtime import secure_dir, fcntl, process_options, stop_process, child_job
from .cloudflare_api import Cloudflare, Enrollment, enrollment_input, private_read, private_write
from .access_gateway import AccessGateway, private_origin


def find_cloudflared(config):
    override = config.get('cloudflared_binary')
    candidates = [override] if override else [shutil.which('cloudflared'),
        Path.home() / '.local/bin/cloudflared', Path.home() / '.local/bin/cloudflared.exe',
        Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Microsoft/WinGet/Links/cloudflared.exe',
        Path('/opt/homebrew/bin/cloudflared'), Path('/usr/local/bin/cloudflared')]
    for candidate in candidates:
        if candidate and Path(candidate).is_absolute() and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def connector_command(binary, folder):
    return [binary, 'tunnel', '--config', str(folder / 'connector.yml'), '--no-autoupdate',
            '--loglevel', 'error', 'run', '--token-file', str(folder / 'tunnel-token')]


class Publication:
    def __init__(self, config, api_factory=Cloudflare, gateway_factory=AccessGateway):
        self.config = config
        self.folder = secure_dir(Path(config['state_dir']) / 'publication')
        self.api_factory, self.gateway_factory = api_factory, gateway_factory
        self.lock, self.runtime_lock = threading.Lock(), threading.RLock()
        self.connector = self.gateway = self.close_job = None
        self.work = {'phase': 'idle', 'error': None}
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.maintain, daemon=True)
        self.thread.start()

    def receipt(self):
        path = self.folder / 'receipt.json'
        return json.loads(private_read(path)) if path.exists() else None

    def enabled(self):
        path = self.folder / 'startup.json'
        return path.exists() and json.loads(private_read(path)).get('enabled') is True

    def api(self):
        try:
            return self.api_factory(private_read(self.folder / 'api-token').strip())
        except FileNotFoundError:
            raise ValueError('cloudflare_api_token_required') from None

    def snapshot(self):
        r = self.receipt() or {}
        p = self.connector
        return {'stage': r.get('stage', 'not_configured'), 'operation': dict(self.work),
            'deployment': r.get('deployment'),
            'hostname': r.get('hostname'), 'url': 'https://' + r['hostname'] if r.get('hostname') else None,
            'zone': r.get('zone_name'), 'prefix': r.get('prefix'), 'emails': r.get('emails', []),
            'pending': r.get('pending', {}).get('key') if r.get('pending') else None,
            'resources': {key: not value.get('removed', False) for key, value in r.get('resources', {}).items()},
            'connector_running': p is not None and p.poll() is None,
            'connector_available': bool(find_cloudflared(self.config)),
            'jwt_available': importlib.util.find_spec('jwt') is not None and importlib.util.find_spec('cryptography') is not None,
            'resume_with_panel': self.enabled(), 'edge_status': r.get('edge_status', 'unchecked'),
            'checked_at': r.get('checked_at'),
            'last_authenticated_request': self.gateway.last_authenticated if self.gateway else None}

    def submit(self, action, data):
        if self.closed.is_set():
            raise ValueError('publication_stopping')
        if action not in ('plan', 'apply', 'refresh', 'start', 'stop', 'remove', 'discard', 'token', 'startup'):
            raise ValueError('unknown_publication_action')
        if not self.lock.acquire(blocking=False):
            raise ValueError('publication_operation_in_progress')
        self.work = {'phase': action, 'error': None}
        def run():
            try:
                with (self.folder / 'operation.lock').open('a+b') as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.perform(action, data)
            except Exception as exc:
                message = str(exc)
                self.work = {'phase': 'failed', 'error': message if re.fullmatch(r'[a-z0-9_]{1,100}', message) else 'publication_operation_failed'}
            else:
                self.work = {'phase': 'idle', 'error': None}
            finally:
                self.lock.release()
        threading.Thread(target=run, daemon=True).start()
        return {'accepted': True}

    def perform(self, action, data):
        if action == 'plan':
            settings, token = enrollment_input(data)
            upstream = private_origin(self.config.get('links', {}).get('console', ''))
            port = self.config.get('publication_port', 18884)
            if type(port) is not int or not 1024 <= port <= 65535:
                raise ValueError('invalid_publication_port')
            e = Enrollment(self.folder, self.api_factory(token))
            e.plan(settings, upstream, port)
            private_write(self.folder / 'api-token', token)
            return
        r = self.receipt()
        if not r:
            raise ValueError('publication_plan_required')
        if data.get('deployment') != r['deployment']:
            raise ValueError('publication_changed_preview_again')
        if action == 'startup':
            if r['stage'] != 'configured' or type(data.get('enabled')) is not bool:
                raise ValueError('invalid_startup_choice')
            private_write(self.folder / 'startup.json', {'enabled': data['enabled']})
            return
        if action == 'token':
            _, token = enrollment_input({'zone': r['zone_name'], 'prefix': r['prefix'], 'emails': r['emails'], 'token': data.get('token')})
            api = self.api_factory(token)
            api.call('GET', '/zones/' + r['zone'])
            private_write(self.folder / 'api-token', token)
            return
        if action == 'stop':
            private_write(self.folder / 'startup.json', {'enabled': False})
            self.stop()
            r['edge_status'] = 'unchecked'
            r['checked_at'] = None
            private_write(self.folder / 'receipt.json', r)
            return
        if action == 'discard':
            if any(not x.get('removed') for x in r.get('resources', {}).values()) or r.get('pending') or r['stage'] not in ('planned', 'removed'):
                raise ValueError('publication_has_resources_remove_first')
            for name in ('receipt.json', 'api-token', 'startup.json'):
                path = self.folder / name
                if path.exists():
                    path.unlink()
            return
        e = Enrollment(self.folder, self.api())
        if action == 'remove':
            private_write(self.folder / 'startup.json', {'enabled': False})
            self.stop()
            e.remove()
            return
        if action == 'apply':
            self.prerequisites(r)
            r = e.apply()
        if action in ('apply', 'start'):
            if r['stage'] != 'configured':
                raise ValueError('publication_apply_required')
            e.verify_access(r)
            e.verify_dns(r)
            r['edge_status'] = e.verify_tunnel(r)
            r['checked_at'] = time.time()
            e.save(r)
            self.start(r)
            private_write(self.folder / 'startup.json', {'enabled': data.get('resume_with_panel') is True})
        if action == 'refresh':
            r = e.reconcile()
            if r['stage'] == 'configured':
                e.verify_access(r)
                r['edge_status'] = e.verify_tunnel(r)
                e.verify_dns(r)
                r['checked_at'] = time.time()
                e.save(r)

    def prerequisites(self, r):
        binary = find_cloudflared(self.config)
        if not binary:
            raise ValueError('cloudflared_install_required')
        try:
            version = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=5, check=True)
            match = re.search(r'\b(\d{4})\.(\d+)\.(\d+)\b', version.stdout + version.stderr)
            if not match or tuple(map(int, match.groups())) < (2025, 4, 0):
                raise ValueError('cloudflared_update_required')
        except (OSError, subprocess.SubprocessError):
            raise ValueError('cloudflared_unavailable') from None
        if not importlib.util.find_spec('jwt') or not importlib.util.find_spec('cryptography'):
            raise ValueError('access_dependency_required')
        if not self.gateway:
            try:
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', r['port']))
            except OSError:
                raise ValueError('publication_port_in_use') from None
        target = urlsplit(private_origin(r['upstream']))
        try:
            conn = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
            conn.request('GET', '/api/me', headers={'Host': target.netloc})
            response = conn.getresponse()
            if response.status not in (200, 401):
                raise ValueError('console_origin_unavailable')
            response.read(65536)
        except OSError:
            raise ValueError('console_origin_unavailable') from None
        finally:
            conn.close()

    def start(self, r):
        with self.runtime_lock:
            if self.closed.is_set():
                raise ValueError('publication_stopping')
            if self.connector and self.connector.poll() is None:
                return
            self.prerequisites(r)
            self.stop()
            # Token content is never in argv, environment, logs or browser responses.
            private_read(self.folder / 'tunnel-token')
            private_write(self.folder / 'connector.yml', '# Dedicated remotely managed tunnel.\n')
            self.gateway = self.gateway_factory(r).start()
            try:
                env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if k in os.environ}
                self.connector = subprocess.Popen(connector_command(find_cloudflared(self.config), self.folder),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env, **process_options())
                self.close_job = child_job(self.connector)
            except Exception:
                self.stop()
                raise

    def stop(self):
        with self.runtime_lock:
            if self.connector:
                try:
                    stop_process(self.connector)
                    self.connector.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.connector.kill()
                    self.connector.wait()
                finally:
                    if self.close_job:
                        self.close_job()
                    self.connector = self.close_job = None
            if self.gateway:
                self.gateway.close()
                self.gateway = None

    def maintain(self):
        # cloudflared reconnects its transport; restart only our own exited child.
        # Persisted startup is opt-in and uses the existing panel login lifecycle.
        while not self.closed.wait(20):
            try:
                if self.connector and self.connector.poll() is not None:
                    self.work = {'phase': 'failed', 'error': 'connector_exited'}
                if self.enabled() and (not self.connector or self.connector.poll() is not None):
                    self.submit('start', {'resume_with_panel': True, 'deployment': self.receipt()['deployment']})
            except Exception:
                pass

    def close(self):
        self.closed.set()
        self.stop()
