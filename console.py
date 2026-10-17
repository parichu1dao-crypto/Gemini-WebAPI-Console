"""Loopback-only management service. No credential upload, remote admin or telemetry."""
import argparse
import atexit
import json
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
import threading
import sqlite3
import time
import uuid
import urllib.request
import urllib.error
import operations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
CONFIG = DATA / 'settings.json'
LOCK = threading.RLock()
CHILD = None
LOG = None
BROWSER_ENABLED = False
LEGACY_MODELS = ['gemini-3-pro', 'gemini-3-flash', 'gemini-3-flash-thinking']
OBS = None
ACCOUNTS = None
COOKIE_HEALTH_CACHE = None
COOKIE_TIMEOUT = .4
BROWSER_GATE = operations.AdmissionGate()


def external_quota_paths():
    path = DATA / 'integration.json'
    if path.exists():
        mapping = json.loads(path.read_text(encoding='utf-8')).get('browser_mapping')
        if mapping:
            return Path(mapping).parent / 'data'
    return None


def quota_settings(update=None):
    from quota_policy import policy, save_policy
    if update is not None:
        save_policy(update)
        folder = external_quota_paths()
        if folder:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / 'quota_policy.json'
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(update), encoding='utf-8')
            os.replace(temp,target)
    return {**policy(), 'fallback_hours':12, 'production_configured':external_quota_paths() is not None}


def account_rows(accounts):
    rows = accounts.list()
    try:
        health=cookie_upstream(load_settings(), '/health')
    except ValueError:
        health={}
    from account_control import enabled
    for row in rows:
        name=row.get('external_key')
        row['production_control']=bool(name and health.get('account_control') and name in health.get('accounts', []))
        row['production_enabled']=enabled(name) if row['production_control'] else False
        row['admission']=health.get('account_details',{}).get(name,{}).get('admission',{})
        row['browser_bound']=False
        if name:
            try:
                from production_accounts import binding
                binding(DATA,name)
                row['browser_bound']=True
            except (ValueError,OSError,KeyError):pass
    folder = external_quota_paths()
    path = folder / 'quota_state.sqlite3' if folder else None
    if path and path.exists():
        db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            limits = {r['account']:dict(r) for r in db.execute('SELECT * FROM quota')}
        finally:
            db.close()
        for row in rows:
            if row['external_key']:
                legacy = limits.get((row['email'] or '').casefold())
                current = row.get('quota')
                row['quota'] = max((q for q in (current, legacy) if q), key=lambda q: q['until'], default=None)
    return rows


def management():
    global OBS, ACCOUNTS
    with LOCK:
        if OBS is None:
            from observability import Store
            from account_manager import Accounts
            OBS = Store(DATA / 'activity.sqlite3')
            ACCOUNTS = Accounts(OBS, DATA)
    return OBS, ACCOUNTS


def sync_observations():
    store, accounts = management()
    store.importing = False
    store.ingest(ROOT / 'openai_account_attempts.jsonl', source='cookie')
    integration_path = DATA / 'integration.json'
    if integration_path.exists():
        settings = json.loads(integration_path.read_text(encoding='utf-8'))
        if settings.get('production_log'):
            store.ingest(settings['production_log'])
            with store.connect() as db:
                cursor = db.execute('SELECT offset FROM cursors WHERE path=?',(settings['production_log'],)).fetchone()
            if cursor and Path(settings['production_log']).exists():
                store.importing = cursor['offset'] < Path(settings['production_log']).stat().st_size
        mapping = settings.get('browser_mapping')
        if mapping and Path(mapping).exists():
            import hashlib
            for name, cfg in json.loads(Path(mapping).read_text(encoding='utf-8-sig')).items():
                aid = 'external-' + hashlib.sha256(name.encode()).hexdigest()[:16]
                with store.connect() as db:
                    db.execute('INSERT OR IGNORE INTO accounts(id,name,external_key,status,created) VALUES(?,?,?,?,?)',
                               (aid,name,name,'external',time.time()))
                    db.execute("UPDATE accounts SET email=?,status='external' WHERE id=?", (cfg.get('email'),aid))
    return store, accounts


def load_settings():
    DATA.mkdir(exist_ok=True)
    (DATA / 'accounts').mkdir(exist_ok=True)
    if not CONFIG.exists():
        value = dict(strict_pro=True, model='gemini-3-pro', port=8787,
                     proxy='', api_key=secrets.token_urlsafe(32),
                     admin_key=secrets.token_urlsafe(32))
        save_settings(value)
    value = json.loads(CONFIG.read_text(encoding='utf-8'))
    defaults = dict(transport='cookie', strict_pro=True, browser_model='gemini-3-pro',
                browser_strict_pro=True, browser_url='http://127.0.0.1:8787',
                browser_key='')
    return {**defaults, **value}


def save_settings(value):
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temp, CONFIG)


def validate_update(body, current):
    if not isinstance(body, dict):
        raise ValueError('Configuration must be a JSON object')
    if body.get('transport') == 'managed':
        if set(body) != {'transport','model','strict_pro'} or type(body['strict_pro']) is not bool:
            raise ValueError('Managed settings require transport, model, strict_pro')
        if body['strict_pro']:
            raise ValueError('托管账号首版仅做网页模型校验，不提供旧 RPC 严格 Pro 校验；请关闭此开关')
        if body['model'] not in management()[1].models():
            raise ValueError('No enabled managed accounts support this model')
        return {**current,'transport':'managed','managed_model':body['model']}
    if body.get('transport') == 'browser':
        if set(body) - {'transport', 'model', 'strict_pro', 'browser_url', 'browser_key'}:
            raise ValueError('Unsupported browser configuration field')
        if type(body.get('strict_pro')) is not bool or not isinstance(body.get('model'), str):
            raise ValueError('Model and boolean strict_pro required')
        updated = {**current, 'transport': 'browser', 'browser_model': body['model'],
                   'browser_strict_pro': body['strict_pro']}
        for key in ('browser_url', 'browser_key'):
            if key in body:
                if not isinstance(body[key], str) or len(body[key]) > 512:
                    raise ValueError('Invalid browser setting')
                # Empty key means keep the saved credential, never send it back.
                if key != 'browser_key' or body[key]:
                    updated[key] = body[key].strip()
        validate_browser_url(updated['browser_url'])
        models = browser_models(updated)
        validate_browser_model(updated, body['model'], models)
        return updated
    if body.get('transport', 'cookie') != 'cookie':
        raise ValueError('Unknown transport')
    body = {k: v for k, v in body.items() if k != 'transport'}
    if set(body) != {'strict_pro', 'model', 'port', 'proxy'}:
        raise ValueError('Only strict_pro, model, port and proxy may be changed')
    if type(body['strict_pro']) is not bool:
        raise ValueError('strict_pro must be a boolean')
    if type(body['port']) is not int or not 1024 <= body['port'] <= 65535:
        raise ValueError('API port must be between 1024 and 65535')
    if not isinstance(body['model'], str) or not body['model']:
        raise ValueError('Unsupported model identifier')
    # Discover on the currently configured port, not an uncommitted new port.
    models, online = cookie_models(current)
    if body['model'] not in models:
        raise ValueError('Unsupported Cookie model; refresh the live model catalog')
    if not online and body['model'] not in LEGACY_MODELS and body['model'] != current['model']:
        raise ValueError('New Cookie models require live validation')
    if body['strict_pro'] and not re.fullmatch(r'gemini-\d+(?:\.\d+)*-pro(?:-(?:plus|advanced))?', body['model']):
        raise ValueError('Strict Pro requires a Pro model; disable it for Argon/Flash')
    proxy = body['proxy']
    if not isinstance(proxy, str) or len(proxy) > 300:
        raise ValueError('Invalid proxy')
    if proxy:
        url = urlsplit(proxy)
        if url.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not url.hostname or url.username or url.password:
            raise ValueError('Use a proxy URL without embedded credentials')
    return {**current, **body, 'transport': 'cookie'}


def validate_browser_url(url):
    p = urlsplit(url)
    if (p.scheme != 'http' or p.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or p.username or p.password or p.path not in {'', '/'} or p.query or p.fragment
            or p.port == MANAGEMENT_PORT):
        raise ValueError('Browser upstream must be a separate loopback HTTP service origin')


def upstream(config, path, payload=None, timeout=10):
    validate_browser_url(config['browser_url'])
    if not config['browser_key']:
        raise ValueError('Set the existing browser service API key first')
    req = urllib.request.Request(config['browser_url'].rstrip('/') + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + config['browser_key'], 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError(f'Browser upstream HTTP {code}; no model fallback') from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f'Browser upstream unavailable ({type(exc).__name__}); no model fallback') from None


def browser_models(config):
    health = upstream(config, '/health')
    details = health.get('account_details', {})
    if not details or any(x.get('transport') != 'browser' for x in details.values()):
        raise ValueError('Upstream is not an all-browser account pool')
    advertised = {x['id'] for x in upstream(config, '/v1/models').get('data', [])}
    # Only advertise models supported by EVERY rotating account, not a union.
    shared = set.intersection(*(set(x.get('browser_models', [])) for x in details.values()))
    return sorted(advertised & shared)


def validate_browser_model(config, model, models):
    if model not in models:
        raise ValueError('Model unavailable across browser accounts; refresh models (no substitution)')
    if config['browser_strict_pro'] and not model.endswith('-pro'):
        raise ValueError('Strict Pro requires a Pro model; turn it off to select Argon/Flash')


def browser_generate(config, body):
    if config['transport'] == 'managed':
        return management()[1].generate({**body,'model':body.get('model',config.get('managed_model','gemini-4-argon'))},management()[0])
    if not isinstance(body, dict) or not isinstance(body.get('messages'), list) or not body['messages']:
        raise ValueError('Nonempty messages array required')
    model = body.get('model', config['browser_model'])
    validate_browser_model(config, model, browser_models(config))
    store, _ = management()
    rid = 'relay:'+uuid.uuid4().hex
    prompt_chars = sum(len(m.get('content','')) for m in body['messages'] if isinstance(m,dict) and isinstance(m.get('content'),str))
    store.begin(rid,'relay','upstream-pending',model,prompt_chars)
    try:
        result = upstream(config, '/v1/chat/completions', {**body, 'model': model, 'stream': False}, timeout=360)
    except Exception as exc:
        store.finish(rid,error=type(exc).__name__)
        raise
    # The existing service reports its legacy Pro alias in responses.
    actual = result.get('model')
    from quota_policy import policy
    allowed_basic = (not policy()['pause_on_limit'] and not config['browser_strict_pro']
                     and actual == 'gemini-3.5-flash-lite')
    if actual != model and not (model == 'gemini-3.1-pro' and actual == 'gemini-3-pro') and not allowed_basic:
        store.finish(rid,error='Model mismatch')
        raise ValueError('Upstream returned a different model identifier; result rejected')
    import hashlib
    aid = 'external-'+hashlib.sha256(str(result.get('account','unknown')).encode()).hexdigest()[:16]
    with store.connect() as db:
        db.execute('UPDATE calls SET model=? WHERE id=?',(actual,rid))
    content = result.get('choices',[{}])[0].get('message',{}).get('content','')
    store.finish(rid,output_chars=len(content),usage=result.get('usage'),account=aid)
    return result


def worker_args(config):
    return [sys.executable, str(ROOT / 'service.py'), '--config', str(CONFIG)]


def cookie_upstream(config, path, body=None):
    if config['port'] == MANAGEMENT_PORT:
        raise ValueError('Cookie service port conflicts with manager')
    req = urllib.request.Request(f'http://127.0.0.1:{int(config["port"])}{path}',
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + config['api_key'],'Content-Type':'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=COOKIE_TIMEOUT) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise ValueError('Invalid Cookie response')
        return value
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError(f'Cookie service HTTP {code}') from None
    except (OSError, ValueError):
        raise ValueError('Cookie service unavailable or invalid response') from None


def cookie_models(config):
    try:
        data = cookie_upstream(config, '/v1/models')['data']
        if not isinstance(data, list) or any(not isinstance(x, dict) or
                not isinstance(x.get('id'), str) or not x['id'].strip() for x in data):
            raise ValueError('Invalid model catalog')
        return sorted({x['id'] for x in data}), True
    except (ValueError, KeyError):
        return list(dict.fromkeys([*LEGACY_MODELS, config['model']])), False


def cookie_external(config, fresh=False):
    global COOKIE_HEALTH_CACHE
    fingerprint = (config['port'], config['api_key'])
    now = time.monotonic()
    with LOCK:
        if not fresh and COOKIE_HEALTH_CACHE and COOKIE_HEALTH_CACHE[0] == fingerprint and now - COOKIE_HEALTH_CACHE[1] < 2:
            return COOKIE_HEALTH_CACHE[2]
        try:
            live = cookie_upstream(config, '/health').get('transport') == 'cookie'
        except ValueError:
            live = False
        COOKIE_HEALTH_CACHE = (fingerprint, time.monotonic(), live)
        return live


def status():
    config = load_settings()
    running = CHILD is not None and CHILD.poll() is None
    browser = config['transport'] in {'browser','managed'}
    external = not browser and not running and cookie_external(config)
    return {k: config[k] for k in ('strict_pro', 'model', 'port', 'proxy')} | {
        'transport': config['transport'], 'browser_model': config['browser_model'],
        'managed_model': config.get('managed_model','gemini-4-argon'),
        'browser_strict_pro': config['browser_strict_pro'], 'browser_url': config['browser_url'],
        'browser_key_set': bool(config['browser_key']),
        'endpoint': f'http://127.0.0.1:{MANAGEMENT_PORT if browser else config["port"]}/v1',
        'running': BROWSER_ENABLED if browser else running or external, 'pid': CHILD.pid if running else None,
        'ownership': 'external' if external else 'console' if running or (browser and BROWSER_ENABLED) else 'none',
        'external': external,
        'accounts': sorted(p.stem for p in (DATA / 'accounts').glob('*.json')),
        'exit_code': CHILD.poll() if CHILD else None,
    }


def operations_state():
    config=load_settings()
    try:health=cookie_upstream(config,'/health')
    except ValueError:health={}
    runtime=health.get('runtime_config')
    wanted={k:config[k] for k in ('model','strict_pro','port')}
    return {'keys':operations.keys(),'audit':operations.audit_rows(),'retention':operations.housekeeping(),
            'backups':operations.backups(),'cookie_supported':bool(health.get('operations_v1')),
            'cookie':health.get('admission_gate'), 'browser':BROWSER_GATE.state(),
            'saved':wanted,'applied':runtime,'pending':None if runtime is None else runtime!=wanted,
            'legacy_key':'Compatibility master key remains valid. Client keys do not grant management access.'}


def operations_action(action,body):
    if action=='keys/create':return operations.create_key(body.get('name'),body.get('models'),body.get('days'),body.get('rpm'))
    if action=='keys/revoke':operations.revoke(body.get('id'));return {'ok':True}
    if action=='retention':
        if body.get('execute') and body.get('confirmation')!='CLEAN':raise ValueError('Type CLEAN to confirm cleanup')
        return operations.housekeeping(body.get('days'),body.get('execute',False))
    if action=='backup':return operations.backup(load_settings())
    if action=='restore':
        if body.get('confirmation')!='RESTORE':raise ValueError('Type RESTORE to confirm')
        if status()['running'] or BROWSER_GATE.state()['active']:raise ValueError('Stop API through its owner before restoring preferences')
        saved=operations.read_backup(body.get('name'))
        operations.backup(load_settings())
        candidate={**load_settings(),**saved['config']}
        if candidate['transport']=='cookie':
            candidate=validate_update({k:candidate[k] for k in ('transport','model','strict_pro','port','proxy')},candidate)
        save_settings(candidate)
        operations.housekeeping(saved['retention_days'])
        operations.audit('backup.restore',body['name'])
        return {'ok':True,'notice':'Preferences restored; credentials and history unchanged. API remains stopped.'}
    if action=='stage-config':
        current=load_settings()
        if current['transport']!='cookie':raise ValueError('Select Cookie transport before staging Cookie settings')
        candidate=validate_update({**{k:current[k] for k in ('port','proxy')},'transport':'cookie',
                                  'model':body.get('model'),'strict_pro':body.get('strict_pro')},current)
        save_settings(candidate)
        operations.audit('config.stage')
        return operations_state()
    if action in ('drain','resume','restart'):
        target=body.get('target','cookie')
        if target=='cookie':
            if not cookie_upstream(load_settings(),'/health').get('operations_v1'):
                raise ValueError('Cookie service needs operations upgrade; no forced stop performed')
            result=cookie_upstream(load_settings(),'/internal/operations',{'action':action})
        elif target=='browser':
            if action=='restart':raise ValueError('Browser entrypoint uses live configuration; drain/resume instead of restarting browsers')
            result=BROWSER_GATE.drain(action=='drain')
        else:raise ValueError('Invalid target')
        operations.audit('service.'+action,target)
        return result
    raise ValueError('Unknown operation')


def stop(check_external=True):
    global CHILD, LOG, BROWSER_ENABLED
    with LOCK:
        if check_external and not (CHILD and CHILD.poll() is None):
            config = load_settings()
            if config['transport'] == 'cookie' and cookie_external(config, fresh=True):
                raise ValueError('Cookie service has external ownership (for example Windows Task Scheduler); stop it using its owner, not this console')
        BROWSER_ENABLED = False
        if CHILD and CHILD.poll() is None:
            try:
                health=cookie_upstream(load_settings(),'/health')
                if not health.get('operations_v1'):raise ValueError('Upgrade API before safe stop')
                gate=cookie_upstream(load_settings(),'/internal/operations',{'action':'drain'})
                health=cookie_upstream(load_settings(),'/health')
                if gate.get('active',1) or health.get('admission_gate',{}).get('async_tasks') or health.get('admission_gate',{}).get('cleanup_pending'):
                    raise ValueError('Draining: requests still active; retry stop after completion')
            except ValueError:
                if check_external:raise
                # Exiting the console must never kill a generation whose state is unknown.
                if LOG:
                    LOG.close()
                    LOG=None
                return
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
    global CHILD, LOG, BROWSER_ENABLED
    with LOCK:
        config = load_settings()
        if config['transport'] == 'cookie' and not (CHILD and CHILD.poll() is None) and cookie_external(config, fresh=True):
            raise ValueError('Cookie service already running with external ownership; duplicate start refused')
        if status()['running']:
            return
        stop(check_external=False)
        config = load_settings()
        if config['transport'] == 'managed':
            if config.get('managed_model') not in management()[1].models():
                raise ValueError('No enabled managed account supports the model')
            BROWSER_ENABLED = True
            return
        if config['transport'] == 'browser':
            validate_browser_model(config, config['browser_model'], browser_models(config))
            BROWSER_ENABLED = True
            return
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


MANAGEMENT_PORT = 8791


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, value):
        if getattr(self,'_admin_authorized',False) and getattr(self,'command','')=='POST':
            route=self.path.removeprefix('/api/')
            allowed={'quota','test','api-settings','config','browser-discover','start','stop',
                     'accounts/create','accounts/open','accounts/production-toggle','accounts/verify',
                     'accounts/update','accounts/delete','ops/keys/create','ops/keys/revoke','ops/retention',
                     'ops/backup','ops/restore','ops/stage-config','ops/drain','ops/resume','ops/restart'}
            operations.audit('admin.'+(route if route in allowed else 'unknown'),result=str(code))
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
        self._admin_authorized=True
        return True

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path in ('/api/ops/state','/api/ops/diagnostics'):
            if not self.authorized():return
            try:
                result=operations_state() if parsed.path.endswith('state') else operations.diagnostics(
                    parse_qs(parsed.query).get('request_id',[None])[-1])
                self.reply(200,result)
            except (ValueError,OSError,sqlite3.Error):self.reply(400,{'error':'Operations data unavailable'})
            return
        if parsed.path == '/api/quota':
            if self.authorized():
                self.reply(200,quota_settings())
            return
        if parsed.path in {'/api/activity', '/api/accounts', '/api/managed-models'}:
            if not self.authorized():
                return
            try:
                store, accounts = sync_observations()
                if parsed.path == '/api/activity':
                    query = {k:v[-1] for k,v in parse_qs(parsed.query).items()}
                    if set(query)-{'period','account','source','offset','start','end'}:
                        raise ValueError('Unknown filter')
                    self.reply(200,store.report(**query))
                elif parsed.path == '/api/managed-models':
                    self.reply(200,{'models':accounts.models()})
                else:
                    self.reply(200,{'accounts':account_rows(accounts)})
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.reply(400,{'error':str(exc)})
            return
        static = {'/': ('index.html','text/html'), '/settings': ('settings.html','text/html'), '/help': ('help.html','text/html'),
                  '/operations':('operations.html','text/html'),
                  '/app.css': ('app.css','text/css'), '/app.js': ('app.js','application/javascript'),
                  '/i18n.js': ('i18n.js','application/javascript')}
        if self.path in static:
            filename, mime = static[self.path]
            data = (ROOT / 'web' / filename).read_bytes()
            if filename == 'settings.html':
                # Shared dynamic Cookie controls live in app.js; keep the existing settings page intact.
                data = data.replace(b'</html>', b'<script src="/app.js"></script></html>')
            self.send_response(200)
            self.send_header('Content-Type', mime+'; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'SAMEORIGIN' if self.path in ('/settings', '/help','/operations') else 'DENY')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == '/v1/models':
            if not self.browser_authorized():
                return
            try:
                self.reply(200, {'object': 'list', 'data': [dict(id=m, object='model', owned_by='gemini-browser')
                            for m in operations.allowed_models(self.headers.get('Authorization','').removeprefix('Bearer '),load_settings()['api_key'],
                              management()[1].models() if load_settings()['transport']=='managed' else browser_models(load_settings()))]})
            except ValueError as exc:
                self.reply(502, {'error': str(exc)})
        elif parsed.path == '/api/models' and self.authorized():
            try:
                config = load_settings()
                transport = parse_qs(parsed.query).get('transport', [config['transport']])[-1]
                if transport == 'cookie':
                    models, online = cookie_models(config)
                    self.reply(200, {'models': models, 'transport': 'cookie', 'online': online})
                elif transport == 'managed':
                    self.reply(200, {'models': management()[1].models(), 'transport': 'managed'})
                elif transport == 'browser':
                    self.reply(200, {'models': browser_models(config), 'transport': 'browser'})
                else:
                    raise ValueError('Unknown transport')
            except ValueError as exc:
                self.reply(400, {'error': str(exc)})
        elif self.path == '/api/status' and self.authorized():
            with LOCK:
                self.reply(200, status())
        elif self.path not in {'/api/status', '/api/models'}:
            self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        from quota_policy import QuotaExceeded
        from browser_failures import BrowserNotSubmitted, BrowserIdentityMismatch
        if self.path == '/v1/chat/completions':
            if not self.browser_authorized():
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2_000_000:
                    raise ValueError('Request too large or empty')
                body = json.loads(self.rfile.read(size))
                config = load_settings()  # snapshot: later saves affect only new requests
                model=body.get('model',config.get('managed_model','gemini-4-argon') if config['transport']=='managed' else config['browser_model'])
                client=operations.authorize(self.headers.get('Authorization','').removeprefix('Bearer '),config['api_key'],model,consume=True)
                rid=uuid.uuid4().hex
                with BROWSER_GATE.enter():
                    operations.event(rid,'received',model=model,client_id=client)
                    try:
                        operations.event(rid,'browser_handoff',model=model)
                        result = browser_generate(config, body)
                        operations.event(rid,'completed',model=result.get('model'))
                    except Exception:
                        operations.event(rid,'failed_or_uncertain')
                        raise
                if body.get('stream'):
                    chunk = {**result, 'object': 'chat.completion.chunk', 'choices': [
                        {'index': 0, 'delta': {'role': 'assistant', 'content': result['choices'][0]['message']['content']},
                         'finish_reason': 'stop'}]}
                    data = ('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.reply(200, result)
            except operations.AccessError as exc:
                self.reply(exc.status,{'error':{'message':str(exc),'submitted':False}})
            except QuotaExceeded as exc:
                self.reply(429, {'error': {'message':str(exc), 'retry_at':exc.until}})
            except BrowserNotSubmitted as exc:
                self.reply(503, {'error': {'message':str(exc), 'type':'not_submitted',
                    'submitted':False, 'retry_after':exc.retry_after}})
            except BrowserIdentityMismatch as exc:
                self.reply(409, {'error': {'message':str(exc), 'type':'identity_mismatch', 'submitted':False}})
            except (ValueError, TypeError, KeyError) as exc:
                self.reply(400, {'error': {'message': str(exc)}})
            return
        if not self.authorized():
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 0 or size > 4096:
                raise ValueError('Request too large')
            body = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Request must be a JSON object')
            if self.path.startswith('/api/ops/'):
                with LOCK:self.reply(200,operations_action(self.path.removeprefix('/api/ops/'),body))
                return
            if self.path == '/api/quota':
                with LOCK:
                    self.reply(200,quota_settings(body))
                return
            if self.path.startswith('/api/accounts/'):
                store, accounts = management()
                action = self.path.removeprefix('/api/accounts/')
                if action == 'create':
                    row = accounts.create(body.get('name'))
                    try:
                        accounts.open(row['id'])
                        self.reply(200,{'account':row})
                    except Exception:
                        self.reply(200,{'account':row,'warning':'账号已建立，但浏览器启动失败。请点击打开登录页重试；不要重复创建。'})
                elif action == 'open':
                    row=accounts.get(body['id'])
                    if row['external_key']:
                        from production_accounts import open_browser
                        self.reply(200,open_browser(DATA,row['external_key']))
                    else:
                        self.reply(200,{'account':accounts.open(body['id'])})
                elif action == 'production-toggle':
                    row=accounts.get(body['id'])
                    name=row['external_key']
                    health=cookie_upstream(load_settings(),'/health')
                    if not name or not health.get('account_control') or name not in health.get('accounts',[]):
                        raise ValueError('Production service does not support this account control')
                    from account_control import set_enabled
                    set_enabled(name,body.get('enabled'))
                    self.reply(200,{'enabled':body['enabled'],'inflight':'Existing requests may finish; new admissions use the saved switch immediately.'})
                elif action == 'verify':
                    self.reply(200,{'account':accounts.verify(body['id'],body.get('email',''))})
                elif action == 'update':
                    self.reply(200,{'account':accounts.update(body['id'],body['changes'])})
                elif action == 'delete':
                    self.reply(200,accounts.delete(body['id'],body.get('confirmation')))
                else:
                    raise ValueError('Unknown account action')
                return
            if self.path == '/api/test':
                config = load_settings()
                if config['transport'] not in {'browser','managed'}:
                    raise ValueError('Select and save browser transport first')
                result = browser_generate(config, {'model': body.get('model', config['browser_model']),
                    'messages': [{'role': 'user', 'content': 'Return only this exact text: MODEL-SELECTION-OK'}]})
                self.reply(200, {'model': result['model'], 'account': result.get('account'),
                                'text': result['choices'][0]['message']['content']})
                return
            with LOCK:
                if self.path == '/api/api-settings':
                    if set(body) != {'api_key'} or not isinstance(body['api_key'],str) or not 16 <= len(body['api_key']) <= 256 or any(ord(c)<33 or ord(c)>126 for c in body['api_key']):
                        raise ValueError('API key must contain 16–256 printable ASCII characters without spaces')
                    if (CHILD is not None and CHILD.poll() is None) or cookie_external(load_settings(), fresh=True):
                        raise ValueError('Stop the Cookie API through its owner before changing its key')
                    config = load_settings()
                    config['api_key'] = body['api_key']
                    save_settings(config)
                elif self.path == '/api/config':
                    if (CHILD is not None and CHILD.poll() is None) or cookie_external(load_settings(), fresh=True):
                        self.reply(409, {'error': 'Stop Cookie API through its owner before changing settings'})
                        return
                    save_settings(validate_update(body, load_settings()))
                elif self.path == '/api/browser-discover':
                    config = load_settings()
                    candidate = {**config, 'browser_url': body.get('browser_url', config['browser_url']),
                                 'browser_key': body.get('browser_key') or config['browser_key']}
                    models = browser_models(candidate)
                    self.reply(200, {'models': models})
                    return
                elif self.path == '/api/start':
                    start()
                elif self.path == '/api/stop':
                    stop()
                else:
                    self.reply(404, {'error': 'Not found'})
                    return
                self.reply(200, status())
        except QuotaExceeded as exc:
            self.reply(429, {'error':str(exc),'retry_at':exc.until})
        except (ValueError, OSError, TypeError, KeyError, sqlite3.Error) as exc:
            self.reply(400, {'error': str(exc)})
        except Exception as exc:
            self.reply(400, {'error': '操作失败：'+type(exc).__name__+'；请检查登录页面和本机服务。'})

    def browser_authorized(self):
        config = load_settings()
        if self.headers.get('Host') not in {f'127.0.0.1:{MANAGEMENT_PORT}', f'localhost:{MANAGEMENT_PORT}'}:
            self.reply(403, {'error': 'Invalid host'})
            return False
        try:
            operations.authorize(self.headers.get('Authorization','').removeprefix('Bearer '),config['api_key'])
        except operations.AccessError:
            self.reply(401, {'error': 'API key required (not management key)'})
            return False
        if config['transport'] not in {'browser','managed'} or not BROWSER_ENABLED:
            self.reply(503, {'error': 'Browser alternative endpoint is stopped'})
            return False
        return True


def main():
    global MANAGEMENT_PORT
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8791)
    args = parser.parse_args()
    MANAGEMENT_PORT = args.port
    load_settings()
    def maintenance():
        while True:
            try:operations.housekeeping(execute=True)
            except (OSError,ValueError,sqlite3.Error):pass
            time.sleep(86400)
    threading.Thread(target=maintenance,daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    atexit.register(stop, check_external=False)
    print(f'Console: http://127.0.0.1:{args.port} · Management key: data/settings.json')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stop(check_external=False)


if __name__ == '__main__':
    main()
