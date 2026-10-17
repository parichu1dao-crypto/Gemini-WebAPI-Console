import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import operations as ops


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        p=patch.object(ops,'DATA',self.root);p.start();self.addCleanup(p.stop)

    def key(self,**kw):
        return ops.create_key(**(dict(name='synthetic',models=['gemini-3-pro'],days=1,rpm=2)|kw))

    def test_hash_only_scope_rate_revocation(self):
        key=self.key();secret=key['key']
        self.assertNotIn(secret,json.dumps(ops.keys()))
        with ops.db() as c:self.assertNotIn(secret,str([tuple(r) for r in c.execute('SELECT * FROM client_keys')]))
        self.assertEqual(ops.authorize(secret,'master','gemini-3-pro',True),key['id'])
        ops.authorize(secret,'master','gemini-3-pro',True)
        with self.assertRaises(ops.AccessError) as caught:ops.authorize(secret,'master','gemini-3-pro',True)
        self.assertEqual(caught.exception.status,429)
        with self.assertRaises(ops.AccessError):ops.authorize(secret,'master','gemini-4-argon')
        ops.revoke(key['id'])
        with self.assertRaises(ops.AccessError):ops.authorize(secret,'master')

    def test_expiry_models_and_namespace(self):
        key=self.key()
        self.assertEqual(ops.allowed_models(key['key'],'master',['gemini-3-pro','flash']),['gemini-3-pro'])
        self.assertNotEqual(ops.idempotency_key('one','same'),ops.idempotency_key('two','same'))
        self.assertEqual(ops.idempotency_key('legacy','same'),'same')
        with ops.db() as c:c.execute('UPDATE client_keys SET expires=0')
        with self.assertRaises(ops.AccessError):ops.authorize(key['key'],'master')

    def test_concurrent_rate_admission(self):
        key=self.key(rpm=1);results=[]
        def call():
            try:ops.authorize(key['key'],'master','gemini-3-pro',True);results.append(True)
            except ops.AccessError:results.append(False)
        threads=[threading.Thread(target=call) for _ in range(6)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(results.count(True),1)

    def test_diagnostics_allowlist(self):
        ops.event('secret-request','generation_dispatched',model='gemini-3-pro',
                  cookie='secret-cookie',error='private chapter',email='private@example.com',http_status=200)
        data=ops.diagnostics();text=json.dumps(data)
        for secret in ['secret-request','secret-cookie','private chapter','private@example.com']:self.assertNotIn(secret,text)
        self.assertEqual(len(ops.diagnostics(data['events'][0]['request_id'])['events']),1)

    def test_gate_waits_without_interrupting(self):
        gate=ops.AdmissionGate();finished=threading.Event()
        with gate.enter():
            gate.restart(finished.set,2)
            self.assertFalse(finished.is_set())
            with self.assertRaises(ops.AccessError):
                with gate.enter():pass
        self.assertTrue(finished.wait(2))

    def test_restart_timeout_remains_drained(self):
        gate=ops.AdmissionGate();finished=threading.Event()
        with gate.enter():
            gate.restart(finished.set,.01);time.sleep(.1)
            self.assertFalse(finished.is_set());self.assertTrue(gate.state()['draining'])
            self.assertFalse(gate.state()['restarting'])
        gate.drain(False)
        with gate.enter():self.assertEqual(gate.state()['active'],1)

    def test_restart_refuses_incomplete_async_cleanup(self):
        gate=ops.AdmissionGate();finished=threading.Event()
        gate.restart(finished.set,.01,ready=lambda:False)
        time.sleep(.1)
        self.assertFalse(finished.is_set())
        self.assertTrue(gate.state()['draining'])

    def test_retention_preview_and_cleanup(self):
        ops.event('old','completed');ops.event('new','received')
        with ops.db() as c:c.execute('UPDATE events SET stamp=0 WHERE stage=?',('completed',))
        self.assertEqual(ops.housekeeping(7)['eligible']['events'],1)
        self.assertEqual(len(ops.diagnostics()['events']),2)
        ops.housekeeping(execute=True)
        self.assertEqual(len(ops.diagnostics()['events']),1)

    def test_backup_excludes_secrets_and_rejects_paths(self):
        result=ops.backup({'model':'gemini-3-pro','transport':'cookie','port':8787,'strict_pro':True,
                           'api_key':'secret','admin_key':'secret','proxy':'http://private:7890'})
        payload=ops.read_backup(result['name'])
        self.assertNotIn('secret',json.dumps(payload));self.assertNotIn('proxy',payload['config'])
        with self.assertRaises(ValueError):ops.read_backup('../settings.json')

    def test_invalid_key_parameters(self):
        for kw in [{'days':True},{'rpm':0},{'models':['*']},{'name':''}]:
            with self.assertRaises(ValueError):self.key(**kw)

    def test_console_restore_is_stopped_and_preserves_credentials(self):
        import console
        with patch.object(console,'DATA',self.root),patch.object(console,'CONFIG',self.root/'settings.json'), \
             patch.object(console,'cookie_upstream',side_effect=ValueError('offline')), \
             patch.object(console,'status',return_value={'running':False}), \
             patch.object(console,'BROWSER_GATE',ops.AdmissionGate()):
            initial=console.load_settings()
            backup=console.operations_action('backup',{})
            changed=initial|{'model':'gemini-3-flash','strict_pro':False,'api_key':'replacement-private-key'}
            console.save_settings(changed)
            with self.assertRaises(ValueError):console.operations_action('restore',{'name':backup['name']})
            with patch.object(console,'status',return_value={'running':True}):
                with self.assertRaisesRegex(ValueError,'Stop API'):
                    console.operations_action('restore',{'name':backup['name'],'confirmation':'RESTORE'})
            console.operations_action('restore',{'name':backup['name'],'confirmation':'RESTORE'})
            result=console.load_settings()
            self.assertEqual(result['model'],initial['model'])
            self.assertEqual(result['api_key'],changed['api_key'])
            self.assertGreaterEqual(len(ops.backups()),2)

    def test_cleanup_requires_confirmation(self):
        import console
        with self.assertRaisesRegex(ValueError,'CLEAN'):
            console.operations_action('retention',{'days':7,'execute':True})


if __name__=='__main__':unittest.main()
