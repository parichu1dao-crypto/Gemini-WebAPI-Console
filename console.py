"""Loopback-only management service. No credential upload, remote admin or telemetry."""
import argparse
import atexit
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
CONFIG = DATA / 'settings.json'
LOCK = threading.RLock()
CHILD = None
LOG = None


def load_settings():
    DATA.mkdir(exist_ok=True)
    (DATA / 'accounts').mkdir(exist_ok=True)
    if not CONFIG.exists():
        value = dict(strict_pro=True, model='gemini-3-pro', port=8787,
                     proxy='', api_key=secrets.token_urlsafe(32),
                     admin_key=secrets.token_urlsafe(32))
        save_settings(value)
    return json.loads(CONFIG.read_text(encoding='utf-8'))


def save_settings(value):
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temp, CONFIG)


def validate_update(body, current):
    if not isinstance(body, dict):
        raise ValueError('Configuration must be a JSON object')
    if set(body) != {'strict_pro', 'model', 'port', 'proxy'}:
        raise ValueError('Only strict_pro, model, port and proxy may be changed')
    if type(body['strict_pro']) is not bool:
        raise ValueError('strict_pro must be a boolean')
    if type(body['port']) is not int or not 1024 <= body['port'] <= 65535:
        raise ValueError('API port must be between 1024 and 65535')
    if not isinstance(body['model'], str) or body['model'] not in {'gemini-3-pro', 'gemini-3-flash', 'gemini-3-flash-thinking'}:
        raise ValueError('Unsupported model identifier')
    if body['strict_pro'] and body['model'] != 'gemini-3-pro':
        raise ValueError('Strict Pro requires gemini-3-pro')
    proxy = body['proxy']
    if not isinstance(proxy, str) or len(proxy) > 300:
        raise ValueError('Invalid proxy')
    if proxy:
        url = urlsplit(proxy)
        if url.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not url.hostname or url.username or url.password:
            raise ValueError('Use a proxy URL without embedded credentials')
    return {**current, **body}


def worker_args(config):
    return [sys.executable, str(ROOT / 'openai_compatible_server.py'),
            '--host', '127.0.0.1', '--port', str(config['port']),
            '--account-dir', str(DATA / 'accounts'), '--accounts', 'all',
            '--default-model', config['model'],
            '--strict-pro' if config['strict_pro'] else '--allow-non-pro',
            *(['--proxy', config['proxy']] if config['proxy'] else ['--ignore-proxy'])]


def status():
    config = load_settings()
    running = CHILD is not None and CHILD.poll() is None
    return {k: config[k] for k in ('strict_pro', 'model', 'port', 'proxy')} | {
        'running': running, 'pid': CHILD.pid if running else None,
        'accounts': sorted(p.stem for p in (DATA / 'accounts').glob('*.json')),
        'exit_code': CHILD.poll() if CHILD else None,
    }


def stop():
    global CHILD, LOG
    with LOCK:
        if CHILD and CHILD.poll() is None:
            CHILD.terminate()
            try:
                CHILD.wait(timeout=10)
            except subprocess.TimeoutExpired:
                CHILD.kill()
                CHILD.wait(timeout=5)
        if LOG:
            LOG.close()
            LOG = None


def start():
    global CHILD, LOG
    with LOCK:
        if status()['running']:
            return
        stop()
        config = load_settings()
        if not list((DATA / 'accounts').glob('*.json')):
            raise ValueError('No accounts: add your own cookie JSON to data/accounts first')
        if config['port'] == MANAGEMENT_PORT:
            raise ValueError('API port conflicts with management port')
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', config['port']))
        env = os.environ.copy()
        env['GEMINI_CONSOLE_API_KEY'] = config['api_key']
        LOG = (DATA / 'server.log').open('a', encoding='utf-8')
        CHILD = subprocess.Popen(worker_args(config), cwd=DATA, env=env,
                                 stdout=LOG, stderr=LOG,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


MANAGEMENT_PORT = 8790


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, value):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        allowed = {f'127.0.0.1:{MANAGEMENT_PORT}', f'localhost:{MANAGEMENT_PORT}'}
        if self.headers.get('Host') not in allowed:
            self.reply(403, {'error': 'Invalid host'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{h}' for h in allowed}:
            self.reply(403, {'error': 'Invalid origin'})
            return False
        supplied = self.headers.get('X-Management-Key', '')
        if not secrets.compare_digest(supplied, load_settings()['admin_key']):
            self.reply(401, {'error': 'Management key required'})
            return False
        return True

    def do_GET(self):
        if self.path == '/':
            data = (ROOT / 'web' / 'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == '/api/status' and self.authorized():
            with LOCK:
                self.reply(200, status())
        elif self.path != '/api/status':
            self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.authorized():
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 0 or size > 4096:
                raise ValueError('Request too large')
            body = json.loads(self.rfile.read(size) or b'{}')
            with LOCK:
                if self.path == '/api/config':
                    if status()['running']:
                        self.reply(409, {'error': 'Stop API service before changing settings'})
                        return
                    save_settings(validate_update(body, load_settings()))
                elif self.path == '/api/start':
                    start()
                elif self.path == '/api/stop':
                    stop()
                else:
                    self.reply(404, {'error': 'Not found'})
                    return
                self.reply(200, status())
        except (ValueError, OSError, TypeError) as exc:
            self.reply(400, {'error': str(exc)})


def main():
    global MANAGEMENT_PORT
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8790)
    args = parser.parse_args()
    MANAGEMENT_PORT = args.port
    load_settings()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    atexit.register(stop)
    print(f'Console: http://127.0.0.1:{args.port} · Management key: data/settings.json')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stop()


if __name__ == '__main__':
    main()
