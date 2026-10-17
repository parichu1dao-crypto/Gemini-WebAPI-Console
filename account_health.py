"""Persist failure cooldowns across restarts, separate from real quota limits."""
import json
from contextlib import closing
from quota_policy import connection


def load(account):
    with closing(connection()) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS account_health (account TEXT PRIMARY KEY, state TEXT)')
        row = db.execute('SELECT state FROM account_health WHERE account=?', (account,)).fetchone()
        return json.loads(row[0]) if row else {'failures': 0, 'cooldown_until': 0.0}


def save(account, state):
    with closing(connection()) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS account_health (account TEXT PRIMARY KEY, state TEXT)')
        db.execute('INSERT OR REPLACE INTO account_health VALUES (?, ?)',
                   (account, json.dumps(state)))
