"""Optional caller-supplied idempotency. No prompts or credentials persisted.

Results are retained in private data/ so a client can retrieve a completed
request after losing its connection. Pending/crashed/uncertain keys never replay.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import time

DATABASE = Path(__file__).resolve().parent / 'data' / 'requests.sqlite3'


class LedgerConflict(RuntimeError):
    pass


def connect():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE, timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL, result TEXT, created REAL NOT NULL)')
    return db


def execute(key, payload, call, *, not_submitted_errors=()):
    if key is None:
        return call()
    if not isinstance(key, str) or not 1 <= len(key) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError('Idempotency-Key must contain 1-128 printable ASCII characters')
    # Hash the key too: callers occasionally (incorrectly) reuse secrets as IDs.
    key = hashlib.sha256(key.encode()).hexdigest()
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    db = connect()
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT digest,state,result FROM requests WHERE key=?', (key,)).fetchone()
            if row:
                if row[0] != digest:
                    raise LedgerConflict('Idempotency key already belongs to another payload; not submitted')
                if row[1] == 'completed':
                    return json.loads(row[2])
                if row[1] != 'not_submitted':
                    raise LedgerConflict('submission outcome unknown or request pending; reconcile existing request, do not replay')
                db.execute("UPDATE requests SET state='pending' WHERE key=?", (key,))
            else:
                db.execute('INSERT INTO requests VALUES(?,?,?,NULL,?)', (key, digest, 'pending', time.time()))
        try:
            result = call()
        except BaseException as exc:
            state = 'not_submitted' if isinstance(exc, not_submitted_errors) else 'uncertain'
            with db:
                db.execute('UPDATE requests SET state=? WHERE key=?', (state, key))
            raise
        with db:
            db.execute("UPDATE requests SET state='completed',result=? WHERE key=?", (json.dumps(result, ensure_ascii=False), key))
        return result
    finally:
        db.close()
