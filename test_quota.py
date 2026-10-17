import asyncio
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock
import quota_policy as q
from browser_transport import BrowserTransport


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, value in [('DATABASE', root/'quota.sqlite'),('POLICY',root/'policy.json')]:
            p=patch.object(q,name,value); p.start(); self.addCleanup(p.stop)
        self.now=datetime(2026,10,16,0,0,tzinfo=timezone.utc).timestamp()

    def test_browser_clock_no_server_timezone_dependency(self):
        until,source=q.reset_time('Your limit resets at 12:01 PM', self.now, -480)
        self.assertEqual(datetime.fromtimestamp(until,timezone.utc).hour,4)
        self.assertEqual(source,'web_clock')
        self.assertEqual(q.reset_time('Your limit resets at 12:01 AM',self.now,-480)[0]-until,12*3600)

    def test_relative_unknown_invalid_and_midnight(self):
        self.assertEqual(q.reset_time('Limit resets in 2 hours',self.now)[0],self.now+7200)
        for text in ['limit reached','Your limit resets at 99:99 PM','Tomorrow some time']:
            self.assertEqual(q.reset_time(text,self.now),(self.now+43200,'default_12h'))
        self.assertEqual(q.reset_time('Your limit resets at tomorrow 08:00',self.now,-480)[0],self.now+86400)

    def test_persistent_fallback_does_not_slide(self):
        first=q.record('account','disabled',now=self.now)
        later=q.record('account','disabled',now=self.now+100)
        self.assertEqual(first['until'],later['until'])
        self.assertEqual(q.states()['account']['until'],first['until'])
        upgraded=q.record('account','Your limit resets at 12:01 PM',now=self.now+100)
        self.assertEqual(upgraded['source'],'web_clock')

    def test_default_fail_closed_and_optin(self):
        self.assertTrue(q.policy()['pause_on_limit'])
        q.save_policy({'pause_on_limit':False})
        self.assertFalse(q.policy()['pause_on_limit'])
        with self.assertRaises(ValueError):q.save_policy({'pause_on_limit':'false'})
        q.POLICY.write_text('broken')
        self.assertTrue(q.policy()['pause_on_limit'])

    def test_no_submission_during_cooldown_and_strict_wins(self):
        client=BrowserTransport(None,cdp_url='http://127.0.0.1:9999',email='a@example.com')
        q.record(client.email,'disabled')
        client._generate=AsyncMock()
        with self.assertRaises(q.QuotaExceeded):
            asyncio.run(client.generate_content('hello',model='gemini-4-argon'))
        client._generate.assert_not_called()
        q.save_policy({'pause_on_limit':False})
        self.assertFalse(client.quota_paused())
        client.allow_basic_override=False
        self.assertTrue(client.quota_paused())

    def test_disabled_without_banner_uses_12h(self):
        client=BrowserTransport(None,cdp_url='http://127.0.0.1:9999',email='a@example.com')
        with patch('browser_transport.inspect_banner',AsyncMock(return_value={'text':'','offset':-480})):
            with self.assertRaises(q.QuotaExceeded) as ctx:
                asyncio.run(client.check_quota(None,disabled=True))
        self.assertEqual(ctx.exception.state['source'],'default_12h')
        q.save_policy({'pause_on_limit':False})
        with patch('browser_transport.inspect_banner',AsyncMock(return_value={'text':'','offset':-480})):
            self.assertIsNotNone(asyncio.run(client.check_quota(None)))
            with self.assertRaises(q.QuotaExceeded):asyncio.run(client.check_quota(None,after_submit=True))

    def test_expired_state_reenters_pool(self):
        client=BrowserTransport(None,cdp_url='http://127.0.0.1:9999',email='a@example.com')
        q.record(client.email,'disabled',now=1)
        self.assertIsNone(client.quota_state())

    def test_managed_pool_skips_limited_account_and_records_actual_model(self):
        from account_manager import Accounts
        from observability import Store
        root=Path(self.tmp.name); store=Store(root/'activity.sqlite')
        accounts=Accounts(store,root)
        a,b=accounts.create('limited'),accounts.create('ready')
        import json
        with store.connect() as db:
            for row in [a,b]:
                db.execute("UPDATE accounts SET status='verified',email=?,models=?,enabled=1 WHERE id=?",
                           (row['name']+'@example.com',json.dumps(['gemini-4-argon']),row['id']))
        q.record('limited@example.com','disabled')
        class Fake:
            def __init__(self,*args,**kw):self.last_conversation_url=None
            async def generate_content(self,*args,**kw):
                return SimpleNamespace(text='ok',actual_model='gemini-3.5-flash-lite')
        with patch('browser_transport.BrowserTransport',Fake):
            result=accounts.generate({'model':'gemini-4-argon','messages':[{'content':'hello'}]},store)
        self.assertEqual(result['account'],'ready')
        self.assertEqual(result['model'],'gemini-3.5-flash-lite')
        self.assertEqual(store.report()['records'][0]['model'],'gemini-3.5-flash-lite')
        q.record('ready@example.com','disabled')
        with self.assertRaises(q.QuotaExceeded):
            accounts.generate({'model':'gemini-4-argon','messages':[{'content':'hello'}]},store)
        accounts.loop.call_soon_threadsafe(accounts.loop.stop)

    def test_production_does_not_force_cooled_account(self):
        path=Path(__file__).resolve().parent/'openai_compatible_server.py'
        spec=importlib.util.spec_from_file_location('production_quota_test',path)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        state=module.ServerState.__new__(module.ServerState)
        state.clients=[('test',SimpleNamespace(_blocked=False))]
        state.client_lock=threading.Lock();state.next_index=0;state.browser_accounts={}
        state.account_health={'test':{'cooldown_until':float('inf')}}
        state.account_dir=Path(self.tmp.name)
        (state.account_dir/'test.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'not submitted'):state.next_client()

if __name__=='__main__':unittest.main()
