"""Local, content-free accounting. Unknown and estimated usage are never billed usage."""
import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.importing = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, account TEXT NOT NULL,
                    model TEXT, started REAL NOT NULL, ended REAL, status TEXT NOT NULL,
                    input_tokens INTEGER, output_tokens INTEGER, usage_kind TEXT NOT NULL,
                    input_chars INTEGER, output_chars INTEGER, error TEXT, conversation TEXT);
                CREATE INDEX IF NOT EXISTS calls_time ON calls(started);
                CREATE INDEX IF NOT EXISTS calls_account ON calls(account,started);
                CREATE TABLE IF NOT EXISTS cursors (path TEXT PRIMARY KEY, identity TEXT, offset INTEGER);
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    external_key TEXT UNIQUE, email TEXT, port INTEGER UNIQUE,
                    profile TEXT, status TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                    models TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, verified REAL,
                    archived INTEGER NOT NULL DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS account_email ON accounts(lower(email)) WHERE email IS NOT NULL;
            ''')
            if 'deleted' not in {r['name'] for r in db.execute('PRAGMA table_info(accounts)')}:
                db.execute('ALTER TABLE accounts ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def begin(self, request_id, source, account, model, chars=None, started=None):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO calls(id,source,account,model,started,status,usage_kind,input_chars) VALUES(?,?,?,?,?,?,?,?)',
                       (request_id, source, account, model, started or time.time(), 'running', 'unknown', chars))

    def finish(self, request_id, *, output_chars=None, error=None, usage=None, ended=None, account=None, conversation=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM calls WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise ValueError('Missing request start')
            usage = usage or {}
            inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
            actual = (type(inp) is int and type(out) is int and inp >= 0 and out >= 0
                      and (inp + out > 0) and not usage.get('estimated'))
            if actual:
                kind = 'reported'
            elif not error and row['input_chars'] is not None and output_chars is not None:
                inp, out = math.ceil(row['input_chars']/4), math.ceil(output_chars/4)
                kind = 'estimated_chars_div4'
            else:
                inp = out = None
                kind = 'unknown'
            db.execute('UPDATE calls SET ended=?,status=?,input_tokens=?,output_tokens=?,usage_kind=?,output_chars=?,error=?,account=?,conversation=? WHERE id=?',
                       (ended or time.time(), 'failed' if error else 'success', inp, out, kind, output_chars,
                        str(error)[:300] if error else None, account or row['account'], conversation, request_id))

    def ingest(self, path, source='production'):
        """Incremental JSONL import; transactions prevent double counting and lost partial lines."""
        path = Path(path)
        if not path.exists():
            return 0
        import hashlib
        stat = path.stat()
        identity = str(stat.st_ino)
        with self.connect() as db, path.open('rb') as f:
            db.execute('BEGIN IMMEDIATE')
            cursor = db.execute('SELECT * FROM cursors WHERE path=?', (str(path),)).fetchone()
            offset = cursor['offset'] if cursor and cursor['identity'] == identity and cursor['offset'] <= stat.st_size else 0
            f.seek(offset)
            count = 0
            for _ in range(20000):
                line = f.readline()
                if not line or not line.endswith(b'\n'):
                    break
                offset = f.tell()
                try:
                    event = json.loads(line)
                    rid = source + ':' + event['request_id']
                    stamp = datetime.strptime(event['ts'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ).timestamp()
                    account = 'external-' + hashlib.sha256(event['account'].encode()).hexdigest()[:16]
                    db.execute('INSERT OR IGNORE INTO accounts(id,name,external_key,status,created) VALUES(?,?,?,?,?)',
                               (account, event['account'], event['account'], 'historical', stamp))
                    if event['event'] == 'begin':
                        db.execute('INSERT OR IGNORE INTO calls(id,source,account,model,started,status,usage_kind,input_chars) VALUES(?,?,?,?,?,?,?,?)',
                                   (rid, source, account, event.get('model'), stamp, 'running', 'unknown', event.get('prompt_chars')))
                    elif event['event'] in ('success', 'failure'):
                        row = db.execute('SELECT * FROM calls WHERE id=?', (rid,)).fetchone()
                        if row is None:
                            db.execute('INSERT INTO calls(id,source,account,model,started,status,usage_kind) VALUES(?,?,?,?,?,?,?)',
                                       (rid,source,account,event.get('model'),stamp-float(event.get('elapsed_sec',0)), 'running','unknown'))
                            row = db.execute('SELECT * FROM calls WHERE id=?', (rid,)).fetchone()
                        chars = event.get('response_chars')
                        known = event['event'] == 'success' and chars is not None and row['input_chars'] is not None
                        db.execute('UPDATE calls SET ended=?,status=?,input_tokens=?,output_tokens=?,usage_kind=?,output_chars=?,error=? WHERE id=?',
                                   (stamp, event['event'].replace('failure','failed'), math.ceil(row['input_chars']/4) if known else None,
                                    math.ceil(chars/4) if known else None, 'estimated_chars_div4' if known else 'unknown', chars,
                                    event.get('error_type') if not known else None, rid))
                    count += 1
                except (ValueError, KeyError, TypeError):
                    continue
            db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?)', (str(path), identity, offset))
            # Old unmatched starts are interrupted/unknown, never invented successes.
            db.execute("UPDATE calls SET status='interrupted',error='No terminal event recorded' WHERE status='running' AND source=? AND started<?", (source,time.time()-7200))
        return count

    def report(self, period='today', account='', source='all', offset=0, start=None, end=None, now=None):
        now = now or datetime.now(TZ)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        ranges = {'today': midnight, 'week': midnight-timedelta(days=midnight.weekday()),
                  'month': midnight.replace(day=1), '5h': now-timedelta(hours=5)}
        if period == 'custom':
            if not isinstance(start,str) or not isinstance(end,str):
                raise ValueError('Choose both start and end dates')
            left = datetime.fromisoformat(start).replace(tzinfo=TZ)
            right = datetime.fromisoformat(end).replace(tzinfo=TZ) + timedelta(days=1)
        else:
            if period not in ranges:
                raise ValueError('Invalid period')
            left, right = ranges[period], now
        if right <= left:
            raise ValueError('Invalid date range')
        where, params = ['started>=?', 'started<?'], [left.timestamp(), right.timestamp()]
        if source == 'all':
            where.append("source!='relay'")  # upstream observation + relay are the same request
        elif source in ('production','managed','relay','cookie'):
            where.append('source=?'); params.append(source)
        else:
            raise ValueError('Invalid source')
        if account:
            where.append('account=?'); params.append(account)
        clause = ' AND '.join(where)
        metrics = """count(*) requests, sum(status='success') success, sum(status='failed') failed,
            sum(status='running') running, sum(status='interrupted') interrupted,
            coalesce(sum(CASE WHEN usage_kind='reported' THEN input_tokens END),0) actual_input,
            coalesce(sum(CASE WHEN usage_kind='reported' THEN output_tokens END),0) actual_output,
            coalesce(sum(CASE WHEN usage_kind='estimated_chars_div4' THEN input_tokens END),0) estimated_input,
            coalesce(sum(CASE WHEN usage_kind='estimated_chars_div4' THEN output_tokens END),0) estimated_output,
            sum(usage_kind='reported') reported_requests,
            sum(usage_kind='unknown') unknown_usage"""
        with self.connect() as db:
            summary = dict(db.execute('SELECT '+metrics+' FROM calls WHERE '+clause,params).fetchone())
            daily = [dict(x) for x in db.execute("SELECT strftime('%Y-%m-%d',started+28800,'unixepoch') day,"+metrics+' FROM calls WHERE '+clause+' GROUP BY day ORDER BY day',params)]
            accounts = [dict(x) for x in db.execute('SELECT account,'+metrics+' FROM calls WHERE '+clause+' GROUP BY account',params)]
            rows = [dict(x) for x in db.execute('SELECT calls.*,a.name account_name FROM calls LEFT JOIN accounts a ON calls.account=a.id WHERE '+clause+' ORDER BY started DESC LIMIT 100 OFFSET ?',[*params,max(0,int(offset))])]
        return dict(summary=summary,daily=daily,accounts=accounts,records=rows,offset=max(0,int(offset)),
                    importing=self.importing,
                    timezone='Asia/Shanghai',start=left.isoformat(),end=right.isoformat(),
                    note='对话次数按模型请求轮次统计，不是聊天窗口数。历史网页 token 仅为字符数÷4 的粗估，不是 Google 计费用量；失败用量未知。默认排除转发观测，避免与生产日志重复计数。')
