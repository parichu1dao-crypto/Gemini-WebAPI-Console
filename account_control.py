"""Local admission switches. Independent of quota and failure cooldowns."""
from contextlib import closing
from quota_policy import connection


def enabled(name):
    with closing(connection()) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS account_control (account TEXT PRIMARY KEY, enabled INTEGER NOT NULL)')
        row=db.execute('SELECT enabled FROM account_control WHERE account=?',(name,)).fetchone()
        return row is None or bool(row[0])


def set_enabled(name, value):
    if type(value) is not bool:
        raise ValueError('Boolean state required')
    with closing(connection()) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS account_control (account TEXT PRIMARY KEY, enabled INTEGER NOT NULL)')
        db.execute('INSERT OR REPLACE INTO account_control VALUES (?,?)',(name,int(value)))
