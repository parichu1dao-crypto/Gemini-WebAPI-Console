"""Isolated Chrome enrollment with stable IDs. Never reuses the default profile."""
import asyncio
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime
from observability import TZ


class Accounts:
    def __init__(self, store, data):
        self.store, self.data = store, Path(data)
        self.lock = threading.RLock()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.clients = {}
        self.index = 0

    def list(self):
        from quota_policy import states, policy
        limits = states()
        with self.store.connect() as db:
            return [dict(x) | {'models': json.loads(x['models']), 'quota': limits.get((x['email'] or '').casefold()),
                              'pause_on_limit': policy()['pause_on_limit']}
                    for x in db.execute('SELECT * FROM accounts WHERE deleted=0 ORDER BY created DESC')]

    def get(self, aid):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM accounts WHERE id=? AND deleted=0', (aid,)).fetchone()
        if row is None:
            raise ValueError('Account not found')
        return dict(row)

    @staticmethod
    def name(value):
        if not isinstance(value,str) or not value.strip() or len(value.strip()) > 60 or any(ord(c)<32 for c in value):
            raise ValueError('账号名称不能为空、不能包含控制字符，最长60字')
        return value.strip()

    def create(self, name=None):
        with self.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            base = self.name(name) if name else datetime.now(TZ).strftime('%y%m%d')
            chosen, n = base, 2
            while db.execute('SELECT 1 FROM accounts WHERE name=? COLLATE NOCASE', (chosen,)).fetchone():
                if name:
                    raise ValueError('Account name already exists')
                chosen, n = f'{base}-{n}', n+1
            used = {x[0] for x in db.execute('SELECT port FROM accounts')}
            port = None
            for candidate in range(9400, 9500):
                if candidate in used:
                    continue
                with socket.socket() as sock:
                    try:
                        sock.bind(('127.0.0.1',candidate))
                        port = candidate
                        break
                    except OSError:
                        continue
            if port is None:
                raise ValueError('No free enrollment port')
            aid = uuid.uuid4().hex
            profile = self.data / 'browser_profiles' / aid
            db.execute('INSERT INTO accounts(id,name,port,profile,status,created) VALUES(?,?,?,?,?,?)',
                       (aid,chosen,port,str(profile),'pending_login',time.time()))
        return self.get(aid)

    def open(self, aid):
        row = self.get(aid)
        if row['external_key'] or row['archived']:
            raise ValueError('外部生产账号仅观测；请使用原管理入口打开。归档账号不可登录。')
        paths = [Path(os.environ.get('PROGRAMFILES',r'C:\Program Files'))/'Google/Chrome/Application/chrome.exe',
                 Path(os.environ.get('LOCALAPPDATA',''))/'Google/Chrome/Application/chrome.exe']
        chrome = next((x for x in paths if x.is_file()),None)
        if chrome is None:
            raise ValueError('Chrome not installed')
        profile = Path(row['profile']).resolve()
        if not profile.is_relative_to((self.data/'browser_profiles').resolve()):
            raise ValueError('Invalid dedicated profile path')
        profile.mkdir(parents=True,exist_ok=True)
        subprocess.Popen([str(chrome),'--user-data-dir='+str(profile), '--remote-debugging-address=127.0.0.1',
                          '--remote-debugging-port='+str(row['port']), '--no-first-run','--new-window',
                          'https://gemini.google.com/app'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return row

    def verify(self, aid, expected_email):
        row = self.get(aid)
        if row['external_key'] or row['archived']:
            raise ValueError('External or archived account cannot be enrolled')
        email = expected_email.strip().casefold()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
            raise ValueError('请输入实际登录的邮箱以核对账号')
        with self.lock:
            if aid in self.clients:
                raise ValueError('账号已有运行会话；请先停用并重启控制台后重新验证')
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{row["port"]}', timeout=10000)
            if len(browser.contexts) != 1:
                raise ValueError('Ambiguous browser profile')
            context = browser.contexts[0]
            from urllib.parse import urlsplit
            pages = [p for p in context.pages if urlsplit(p.url).hostname == 'gemini.google.com']
            matched = None
            for page in pages:
                labels = page.locator('a[aria-label]').evaluate_all("els => els.map(e=>e.getAttribute('aria-label'))")
                if any(f'({email})' in label.casefold() for label in labels):
                    matched = page
                    break
            if matched is None:
                raise ValueError('未找到与填写邮箱一致的 Gemini 登录状态，请核对浏览器账号')
            matched.get_by_role('textbox',name='Enter a prompt for Gemini',exact=True).wait_for(timeout=10000)
            cookies = context.cookies('https://gemini.google.com')
            if not any(c['name']=='__Secure-1PSID' and c['value'] for c in cookies):
                raise ValueError('Missing authenticated Google session cookie')
            picker = matched.get_by_role('button',name=re.compile(r'^Open mode picker, currently '))
            picker.wait_for(timeout=10000)
            opened = False
            try:
                if picker.get_attribute('aria-expanded') != 'true':
                    picker.evaluate('el=>el.click()'); opened = True
                matched.get_by_role('menuitem').first.wait_for(timeout=10000)
                options = matched.get_by_role('menuitem').evaluate_all("els=>els.filter(e=>e.getAttribute('aria-disabled')!=='true').map(e=>e.textContent)")
                models = []
                for text in options:
                    match = re.search(r'(\d[\d.]*\s+(?:Flash-Lite|Flash|Pro|Argon))\b',text)
                    if match:
                        models.append('gemini-'+match.group(1).lower().replace(' ','-'))
            finally:
                if opened and picker.get_attribute('aria-expanded') == 'true':
                    picker.evaluate('el=>el.click()')
            if not models:
                raise ValueError('未发现可用模型；请确认 Gemini 使用英文界面')
        # Do not close Chrome, export cookies or store passwords.
        with self.store.connect() as db:
            duplicate = db.execute('SELECT name FROM accounts WHERE lower(email)=? AND id!=?',(email,aid)).fetchone()
            if duplicate:
                raise ValueError('此邮箱已录入：'+duplicate['name'])
            db.execute("UPDATE accounts SET email=?,status='verified',models=?,verified=? WHERE id=?",
                       (email,json.dumps(sorted(set(models))),time.time(),aid))
        return self.get(aid)

    def update(self, aid, body):
        row = self.get(aid)
        if set(body)-{'name','enabled','archived'}:
            raise ValueError('Unsupported account fields')
        name = self.name(body.get('name',row['name']))
        enabled, archived = body.get('enabled',bool(row['enabled'])), body.get('archived',bool(row['archived']))
        if type(enabled) is not bool or type(archived) is not bool:
            raise ValueError('Boolean state required')
        if row['external_key'] and ('enabled' in body or 'archived' in body):
            raise ValueError('外部生产账号为只读观测，不能从此处停用或归档')
        if enabled and row['status'] != 'verified':
            raise ValueError('先验证登录身份，再启用账号')
        with self.store.connect() as db:
            db.execute('UPDATE accounts SET name=?,enabled=?,archived=? WHERE id=?',(name,int(enabled and not archived),int(archived),aid))
        return self.get(aid)

    def delete(self, aid, confirmation):
        # Tombstone retains stable call/account joins; never remove a browser
        # profile or credentials while an administrator may still be using it.
        with self.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM accounts WHERE id=? AND deleted=0', (aid,)).fetchone()
            if row is None:
                raise ValueError('Account not found')
            if confirmation != row['name']:
                raise ValueError('Confirmation must match the account name')
            if row['external_key']:
                raise ValueError('External production accounts are read-only')
            if row['enabled'] or aid in self.clients:
                raise ValueError('Disable this account and restart the console before deleting an active session')
            db.execute('UPDATE accounts SET deleted=1,enabled=0,archived=1 WHERE id=?', (aid,))
        return {'id': aid, 'deleted': True, 'history_preserved': True, 'profile_preserved': True}

    def models(self):
        rows = [a for a in self.list() if a['enabled'] and not a['archived'] and not a['external_key']]
        return sorted(set.intersection(*(set(a['models']) for a in rows))) if rows else []

    def generate(self, body, store):
        from quota_policy import policy, QuotaExceeded
        with self.lock:
            rows = [a for a in self.list() if a['enabled'] and not a['archived'] and not a['external_key']]
            model = body.get('model')
            if not rows or model not in self.models():
                raise ValueError('没有启用的托管账号，或所选模型不是所有启用账号共有')
            if policy()['pause_on_limit']:
                ready = [a for a in rows if not a['quota'] or a['quota']['until'] <= time.time()]
                if not ready:
                    raise QuotaExceeded(min((a['quota'] for a in rows), key=lambda q:q['until']))
                rows = ready
            row = rows[self.index % len(rows)]; self.index += 1
        messages = body.get('messages')
        if not isinstance(messages,list) or not messages or any(not isinstance(m,dict) or not isinstance(m.get('content'),str) for m in messages):
            raise ValueError('Managed browser currently supports text messages only')
        prompt = messages[0]['content'] if len(messages)==1 else '\n\n'.join(str(m.get('role','user'))+': '+m['content'] for m in messages)
        rid = 'managed:'+uuid.uuid4().hex
        store.begin(rid,'managed',row['id'],model,len(prompt))
        async def run():
            from browser_transport import BrowserTransport
            if row['id'] not in self.clients:
                self.clients[row['id']] = BrowserTransport(None,cdp_url=f'http://127.0.0.1:{row["port"]}',email=row['email'],timeout=285)
            client = self.clients[row['id']]
            result = await client.generate_content(prompt,model=model)
            return result, client.last_conversation_url
        future = asyncio.run_coroutine_threadsafe(run(),self.loop)
        try:
            result, url = future.result(timeout=600)
            actual_model = getattr(result, 'actual_model', model)
            with store.connect() as db:
                db.execute('UPDATE calls SET model=? WHERE id=?',(actual_model,rid))
            store.finish(rid,output_chars=len(result.text),conversation=url)
            return dict(id=rid,object='chat.completion',created=int(time.time()),model=actual_model,requested_model=model,account=row['name'],
                        choices=[dict(index=0,message=dict(role='assistant',content=result.text),finish_reason='stop')],
                        usage={'estimated':True,'prompt_tokens':(len(prompt)+3)//4,'completion_tokens':(len(result.text)+3)//4,
                               'total_tokens':(len(prompt)+3)//4+(len(result.text)+3)//4})
        except Exception as exc:
            future.cancel()
            store.finish(rid,error=str(exc) if isinstance(exc,QuotaExceeded) else type(exc).__name__)
            from browser_failures import BrowserNotSubmitted, BrowserIdentityMismatch
            if isinstance(exc, (QuotaExceeded, BrowserNotSubmitted, BrowserIdentityMismatch)):
                raise
            raise ValueError('托管浏览器请求失败：'+type(exc).__name__+'；未自动重发，请检查账号页面') from None
