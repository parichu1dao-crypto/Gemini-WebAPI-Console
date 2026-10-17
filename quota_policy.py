"""Persistent browser quota guard. Only UI chrome is evidence, never response text."""
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parent / 'data'
POLICY = ROOT / 'quota_policy.json'
DATABASE = ROOT / 'quota_state.sqlite3'


def policy():
    try:
        value = json.loads(POLICY.read_text(encoding='utf-8'))
        return {'pause_on_limit': value.get('pause_on_limit') is not False}
    except (OSError, ValueError):
        return {'pause_on_limit': True}  # fail closed


def save_policy(value):
    if set(value) != {'pause_on_limit'} or type(value['pause_on_limit']) is not bool:
        raise ValueError('pause_on_limit must be a boolean')
    POLICY.parent.mkdir(parents=True, exist_ok=True)
    temp = POLICY.with_suffix('.tmp')
    temp.write_text(json.dumps(value), encoding='utf-8')
    os.replace(temp, POLICY)


def connection():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS quota (account TEXT PRIMARY KEY, until REAL, observed REAL, source TEXT, evidence TEXT)')
    return db


def states():
    db = connection()
    try:
        return {r['account']: dict(r) for r in db.execute('SELECT * FROM quota')}
    finally:
        db.close()


def reset_time(text, now=None, offset=-480):
    """Browser UTC offset, not server timezone; nearest future clock occurrence."""
    now = time.time() if now is None else now
    local = datetime.fromtimestamp(now, timezone(timedelta(minutes=-offset)))
    relative = re.search(r'(?:in\s+)(\d+)\s*(hours?|minutes?)', text, re.I)
    if relative:
        return now + int(relative[1]) * (3600 if relative[2].lower().startswith('hour') else 60), 'web_relative'
    match = re.search(r'(?:resets?\s+(?:at|on)|重置(?:时间)?[：:]?|恢复(?:时间)?[：:]?)\s*(?:(tomorrow|明天)\s*)?(\d{1,2})[:：](\d{2})\s*(AM|PM)?', text, re.I)
    if match:
        hour, minute = int(match[2]), int(match[3])
        if minute < 60 and (1 <= hour <= 12 if match[4] else hour < 24):
            if match[4]:
                hour = hour % 12 + (12 if match[4].upper() == 'PM' else 0)
            target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if match[1] or target.timestamp() <= now:
                target += timedelta(days=1)
            return target.timestamp(), 'web_clock'
    return now + 12*3600, 'default_12h'


def record(account, evidence, offset=-480, now=None):
    now = time.time() if now is None else now
    until, source = reset_time(evidence, now, offset)
    db = connection()
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM quota WHERE account=?', (account,)).fetchone()
            # Repeated detections must not continually extend a fallback twelve hours.
            if previous and previous['until'] > now and (source == 'default_12h' or previous['evidence'] == evidence):
                return dict(previous)
            db.execute('INSERT OR REPLACE INTO quota VALUES(?,?,?,?,?)', (account, until, now, source, evidence[:240]))
        return dict(account=account, until=until, observed=now, source=source, evidence=evidence[:240])
    finally:
        db.close()


class QuotaExceeded(RuntimeError):
    def __init__(self, state):
        self.state = state
        self.until = state['until']
        super().__init__(f"Account quota limited until {datetime.fromtimestamp(self.until, timezone.utc).isoformat()} ({state['source']})")


async def inspect_banner(page):
    return await page.evaluate(r'''() => {
        const texts = [...document.querySelectorAll('span,div,p')]
          .filter(e => !e.closest('message-content,model-response,user-query') && e.getClientRects().length && !e.children.length)
          .map(e => (e.textContent || '').trim())
          .filter(t => t.length < 240 && /your limit resets|you(?:'ve| have) reached.*limit|usage limit reached|达到.*(?:上限|限额)|额度.*(?:耗尽|重置)/i.test(t));
        return {text:texts[0] || '', offset:new Date().getTimezoneOffset()};
    }''')
