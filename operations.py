"""Content-free operational metadata shared by the API and loopback console.

No prompts, responses, cookies or credential plaintext are written here.
"""
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'
SAFE_CONFIG = ('transport', 'model', 'strict_pro', 'port', 'browser_model',
               'browser_strict_pro', 'managed_model')


class AccessError(ValueError):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DATA / 'operations.sqlite3', timeout=20)
    c.row_factory = sqlite3.Row
    try:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS client_keys(id TEXT PRIMARY KEY,name TEXT NOT NULL,
          digest TEXT UNIQUE NOT NULL,prefix TEXT,created REAL,expires REAL,revoked INTEGER DEFAULT 0,
          models TEXT NOT NULL,rpm INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS rate_hits(key_id TEXT,stamp REAL);
        CREATE INDEX IF NOT EXISTS rate_time ON rate_hits(key_id,stamp);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,request_id TEXT,stamp REAL,
          stage TEXT,model TEXT,client_id TEXT,details TEXT);
        CREATE INDEX IF NOT EXISTS events_time ON events(stamp);
        CREATE INDEX IF NOT EXISTS events_request ON events(request_id,stamp);
        CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,stamp REAL,action TEXT,target TEXT,result TEXT);
        CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),days INTEGER NOT NULL);
        INSERT OR IGNORE INTO policy VALUES(1,90);
        ''')
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def identifier(value):
    """Never echo arbitrary caller strings in diagnostic exports."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def audit(action, target='', result='ok'):
    with db() as c:
        c.execute('INSERT INTO audit(stamp,action,target,result) VALUES(?,?,?,?)',
                  (time.time(), action[:100], identifier(target) if target else '', result[:80]))


def create_key(name, models, days, rpm):
    if not isinstance(name,str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('Client name must contain 1–80 characters')
    if not isinstance(models,list) or not models or len(models)>100 or any(
        not isinstance(m,str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}',m) for m in models):
        raise ValueError('Select at least one explicit model; wildcard is not allowed')
    if type(days) is not int or not 1 <= days <= 365 or type(rpm) is not int or not 1 <= rpm <= 600:
        raise ValueError('Expiry 1–365 days; rate limit 1–600 requests/minute')
    key='gwc_'+secrets.token_urlsafe(32)
    kid=uuid.uuid4().hex
    with db() as c:
        c.execute('INSERT INTO client_keys VALUES(?,?,?,?,?,?,0,?,?)',
                  (kid,name.strip(),hashlib.sha256(key.encode()).hexdigest(),key[:8],time.time(),
                   time.time()+days*86400,json.dumps(sorted(set(models))),rpm))
    audit('key.create',kid)
    return {'id':kid,'key':key,'notice':'Shown once only / 仅显示一次'}


def keys():
    with db() as c:
        return [dict(r)|{'models':json.loads(r['models'])} for r in c.execute(
            'SELECT id,name,prefix,created,expires,revoked,models,rpm FROM client_keys ORDER BY created DESC')]


def revoke(kid):
    with db() as c:
        if c.execute('UPDATE client_keys SET revoked=1 WHERE id=?',(kid,)).rowcount!=1:
            raise ValueError('Unknown client key')
    audit('key.revoke',kid)


def authorize(token, master, model=None, consume=False):
    if master and secrets.compare_digest(str(token),str(master)):
        return 'legacy'
    digest=hashlib.sha256(str(token).encode()).hexdigest()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM client_keys WHERE digest=?',(digest,)).fetchone()
        if not row or row['revoked'] or row['expires'] <= time.time():
            raise AccessError('Invalid, expired or revoked client key',401)
        if model is not None and model not in json.loads(row['models']):
            raise AccessError('Model is outside this client key scope')
        if consume:
            now=time.time()
            c.execute('DELETE FROM rate_hits WHERE stamp<?',(now-60,))
            n=c.execute('SELECT count(*) FROM rate_hits WHERE key_id=?',(row['id'],)).fetchone()[0]
            if n>=row['rpm']:
                raise AccessError('Client rate limit exceeded; not submitted',429)
            c.execute('INSERT INTO rate_hits VALUES(?,?)',(row['id'],now))
        return row['id']


def allowed_models(token,master,models):
    kid=authorize(token,master)
    if kid=='legacy': return models
    with db() as c:
        allowed=json.loads(c.execute('SELECT models FROM client_keys WHERE id=?',(kid,)).fetchone()[0])
    return [m for m in models if m in allowed]


def idempotency_key(client_id,key):
    if key is None or client_id=='legacy':return key
    if not isinstance(key,str) or not 1<=len(key)<=128 or any(ord(c)<33 or ord(c)>126 for c in key):
        raise ValueError('Invalid Idempotency-Key')
    return hashlib.sha256((client_id+':'+key).encode()).hexdigest()


def event(rid,stage,model=None,client_id=None,**details):
    # Strict allowlist: never write exception messages, account names, URLs or conversation IDs.
    safe={k:v for k,v in details.items() if k in {'http_status','wait_sec','elapsed_sec','code','submitted'}
          and type(v) in (int,float,bool)}
    if model and not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}',str(model)): model=None
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',str(stage)): stage='unknown'
    with db() as c:
        c.execute('INSERT INTO events(request_id,stamp,stage,model,client_id,details) VALUES(?,?,?,?,?,?)',
                  (identifier(rid),time.time(),stage,model,client_id,json.dumps(safe)))


def diagnostics(request_id=None,limit=500):
    with db() as c:
        rows=c.execute('SELECT request_id,stamp,stage,model,client_id,details FROM events '+
                       ('WHERE request_id=? ' if request_id else '')+'ORDER BY seq DESC LIMIT ?',
                       (request_id,limit) if request_id else (limit,)).fetchall()
    return {'schema':1,'generated_at':time.time(),'content_included':False,
            'note':'Model is requested/reported, not independent proof of upstream identity. Missing historical stages are unknown.',
            'events':[dict(r)|{'details':json.loads(r['details'])} for r in rows]}


def housekeeping(days=None,execute=False):
    with db() as c:
        if days is not None:
            if type(days) is not int or not 7<=days<=3650: raise ValueError('Retention must be 7–3650 days')
            c.execute('UPDATE policy SET days=? WHERE id=1',(days,))
        days=c.execute('SELECT days FROM policy').fetchone()[0]
        cutoff=time.time()-days*86400
        counts={t:c.execute(f'SELECT count(*) FROM {t} WHERE stamp<?',(cutoff,)).fetchone()[0]
                for t in ('events','audit')}
        if execute:
            for t in counts:c.execute(f'DELETE FROM {t} WHERE stamp<?',(cutoff,))
            c.execute('DELETE FROM rate_hits WHERE stamp<?',(time.time()-60,))
    # Only terminal accounting rows; running/unknown requests and idempotency ledger are retained.
    path=DATA/'activity.sqlite3'
    counts['calls']=0
    if path.exists():
        with sqlite3.connect(path,timeout=20) as c:
            sql="FROM calls WHERE ended<? AND status IN ('success','failed')"
            counts['calls']=c.execute('SELECT count(*) '+sql,(cutoff,)).fetchone()[0]
            if execute:c.execute('DELETE '+sql,(cutoff,))
    if days is not None and execute:audit('retention.cleanup',result='ok')
    return {'days':days,'eligible':counts,'executed':execute,
            'scope':'Operational events, audit and completed accounting only. Raw logs, profiles, cookies and idempotency results are never removed.'}


def audit_rows():
    with db() as c:return [dict(r) for r in c.execute('SELECT * FROM audit ORDER BY seq DESC LIMIT 200')]


def backup(config):
    """Portable recovery snapshot; intentionally excludes authentication and request results."""
    folder=DATA/'backups';folder.mkdir(exist_ok=True)
    name=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.json'
    policy=housekeeping()
    payload={'schema':1,'created':time.time(),'config':{k:config[k] for k in SAFE_CONFIG if k in config},
             'retention_days':policy['days']}
    content=json.dumps(payload,ensure_ascii=False,indent=2)
    (folder/name).write_text(content,encoding='utf-8')
    audit('backup.create',name)
    return {'name':name,'sha256':hashlib.sha256(content.encode()).hexdigest(),
            'scope':'Non-secret runtime preferences and retention policy only; excludes accounts, keys, cookies, logs and request results.'}


def backups():
    folder=DATA/'backups'
    return [{'name':p.name,'bytes':p.stat().st_size,'modified':p.stat().st_mtime}
            for p in sorted(folder.glob('*.json'),reverse=True)] if folder.exists() else []


def read_backup(name):
    if not isinstance(name,str) or not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{8}\.json',name):
        raise ValueError('Invalid backup name')
    path=DATA/'backups'/name
    if path.is_symlink() or path.stat().st_size>16384:raise ValueError('Invalid backup')
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema')!=1 or not isinstance(data.get('config'),dict) or set(data['config'])-set(SAFE_CONFIG):
        raise ValueError('Unsupported backup schema')
    conf=data['config']
    if conf.get('transport') not in {'cookie','browser','managed'}:raise ValueError('Invalid transport')
    if type(conf.get('port')) is not int or not 1024<=conf['port']<=65535:raise ValueError('Invalid port')
    for k,v in conf.items():
        if 'strict_pro' in k and type(v) is not bool:raise ValueError('Invalid strict flag')
        if 'model' in k and (not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}',v)):raise ValueError('Invalid model')
    days=data.get('retention_days')
    if type(days) is not int or not 7<=days<=3650:raise ValueError('Invalid retention')
    return data


class AdmissionGate:
    """Atomic admission/drain; queued requests count as in flight too."""
    def __init__(self):
        self.condition=threading.Condition();self.active=0;self.draining=False;self.restarting=False

    @contextmanager
    def enter(self):
        with self.condition:
            if self.draining:raise AccessError('Service draining; not submitted',503)
            self.active+=1
        try:yield
        finally:
            with self.condition:
                self.active-=1;self.condition.notify_all()

    def state(self):
        with self.condition:return {'active':self.active,'draining':self.draining,'restarting':self.restarting}

    def drain(self,value=True):
        with self.condition:
            if self.restarting:raise ValueError('Restart already scheduled')
            self.draining=value
        return self.state()

    def restart(self,callback,timeout=900,ready=lambda:True):
        with self.condition:
            if self.restarting:raise ValueError('Restart already scheduled')
            self.draining=True;self.restarting=True
        def wait():
            with self.condition:
                deadline=time.monotonic()+timeout
                while self.active or not ready():
                    remaining=deadline-time.monotonic()
                    if remaining<=0:
                        self.restarting=False
                        audit('service.restart',result='timeout-kept-drained')
                        return
                    self.condition.wait(min(.5,remaining))
            audit('service.restart',result='drained')
            callback()
        threading.Thread(target=wait,daemon=True).start()
        return self.state()
