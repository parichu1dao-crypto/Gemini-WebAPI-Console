import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from observability import Store, TZ
from account_manager import Accounts


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'activity.db')
        self.now = datetime(2026,10,16,2,0,tzinfo=TZ)

    def test_period_boundaries_and_unknown(self):
        for rid, hours in [('today',1),('yesterday',3),('old',6)]:
            self.store.begin(rid,'managed','a','argon',40,(self.now-timedelta(hours=hours)).timestamp())
            self.store.finish(rid,output_chars=20,ended=self.now.timestamp()-1)
        self.assertEqual(self.store.report(now=self.now)['summary']['requests'],1)
        self.assertEqual(self.store.report('5h',now=self.now)['summary']['requests'],2)
        self.assertEqual(self.store.report('week',now=self.now)['summary']['requests'],3)
        self.assertEqual(self.store.report('month',now=self.now)['summary']['requests'],3)
        self.store.begin('failed','managed','a','argon',20,self.now.timestamp()-30)
        self.store.finish('failed',error='network',ended=self.now.timestamp()-1)
        result=self.store.report(now=self.now)
        self.assertEqual(result['summary']['unknown_usage'],1)
        self.assertIsNone(result['records'][0]['input_tokens'])

    def test_reported_zero_placeholder_and_duplicate_relay(self):
        for rid,usage in [('reported',{'prompt_tokens':11,'completion_tokens':4}),('zero',{'prompt_tokens':0,'completion_tokens':0})]:
            self.store.begin(rid,'managed','a','x',40,self.now.timestamp()-10)
            self.store.finish(rid,usage=usage,output_chars=20)
        self.store.begin('relay','relay','a','x',40,self.now.timestamp()-10)
        r=self.store.report(now=self.now)['summary']
        self.assertEqual(r['requests'],2)
        self.assertEqual(r['actual_input'],11)
        self.assertEqual(r['estimated_input'],10)

    def test_log_reimport_partial_lines_and_failed_usage(self):
        path=self.root/'log.jsonl'
        begin=dict(ts='2026-10-16 01:00:00',request_id='abc',account='demo-account',event='begin',model='argon',prompt_chars=100)
        end={**begin,'event':'success','response_chars':40}
        path.write_bytes((json.dumps(begin)+'\n'+json.dumps(end)[:20]).encode())
        self.store.ingest(path)
        path.write_text(json.dumps(begin)+'\n'+json.dumps(end)+'\n')
        self.store.ingest(path); self.store.ingest(path)
        r=self.store.report(now=self.now)
        self.assertEqual(r['summary']['requests'],1)
        self.assertEqual(r['summary']['estimated_input'],25)
        self.assertEqual(r['summary']['success'],1)

    def test_rename_preserves_history_and_identity(self):
        accounts=Accounts(self.store,self.root)
        first=accounts.create(); second=accounts.create()
        self.assertNotEqual(first['name'],second['name'])
        self.assertNotEqual(first['profile'],second['profile'])
        self.store.begin('x','managed',first['id'],'argon',10,self.now.timestamp()-10)
        accounts.update(first['id'],{'name':'renamed'})
        row=self.store.report(now=self.now)['records'][0]
        self.assertEqual(row['account_name'],'renamed')
        with self.assertRaises(ValueError):
            accounts.update(first['id'],{'enabled':True})
        with self.assertRaises(ValueError):
            accounts.update(first['id'],{'name':'\n'})

    def test_launch_uses_unique_profile_and_no_shell(self):
        accounts=Accounts(self.store,self.root)
        row=accounts.create()
        with patch('account_manager.Path.is_file',return_value=True),patch('account_manager.subprocess.Popen') as spawn:
            accounts.open(row['id'])
            args=spawn.call_args.args[0]
            self.assertIn('--user-data-dir='+row['profile'],args)
            self.assertIn('--remote-debugging-address=127.0.0.1',args)
            self.assertNotIn('shell',spawn.call_args.kwargs)

    def fake_browser(self, email):
        factory=MagicMock()
        context=MagicMock()
        factory.return_value.__enter__.return_value.chromium.connect_over_cdp.return_value.contexts=[context]
        page=MagicMock(); page.url='https://gemini.google.com/app'
        context.pages=[page]
        context.cookies.return_value=[{'name':'__Secure-1PSID','value':'test-only'}]
        page.locator.return_value.evaluate_all.return_value=['Google Account: Test ('+email+')']
        picker=MagicMock(); picker.get_attribute.return_value='true'
        items=MagicMock(); items.evaluate_all.return_value=['4 Argon','3.8 Flash']
        page.get_by_role.side_effect=lambda role,**kw: picker if role=='button' else items if role=='menuitem' else MagicMock()
        return factory

    def test_identity_mismatch_rejected_and_duplicate_email_blocked(self):
        manager=Accounts(self.store,self.root)
        a=manager.create(); b=manager.create()
        with patch('playwright.sync_api.sync_playwright',self.fake_browser('right@example.com')):
            with self.assertRaisesRegex(ValueError,'一致'):
                manager.verify(a['id'],'wrong@example.com')
            saved=manager.verify(a['id'],'right@example.com')
            self.assertEqual(saved['status'],'verified')
            self.assertFalse(saved['enabled'])
            with self.assertRaisesRegex(ValueError,'已录入'):
                manager.verify(b['id'],'right@example.com')
        manager.update(a['id'],{'enabled':True})
        self.assertIn('gemini-4-argon',manager.models())
        manager.update(a['id'],{'archived':True})
        self.assertEqual(manager.models(),[])

    def test_managed_generation_is_attributed_and_disabling_blocks_new_work(self):
        from types import SimpleNamespace
        manager=Accounts(self.store,self.root)
        a=manager.create()
        with self.store.connect() as db:
            db.execute("UPDATE accounts SET status='verified',email='test@example.com',models=?,enabled=1 WHERE id=?",
                       (json.dumps(['gemini-4-argon']),a['id']))
        class FakeClient:
            def __init__(self,*args,**kw): self.last_conversation_url='https://gemini.google.com/app/test'
            async def generate_content(self,prompt,model): return SimpleNamespace(text='answer')
        with patch('browser_transport.BrowserTransport',FakeClient):
            result=manager.generate({'model':'gemini-4-argon','messages':[{'role':'user','content':'hello'}]},self.store)
        self.assertEqual(result['account'],a['name'])
        self.assertTrue(result['usage']['estimated'])
        records=self.store.report()['records']
        self.assertEqual(records[0]['account'],a['id'])
        manager.update(a['id'],{'enabled':False})
        with self.assertRaises(ValueError):
            manager.generate({'model':'gemini-4-argon','messages':[{'content':'hello'}]},self.store)


if __name__=='__main__': unittest.main()
