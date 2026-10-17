import json
import io
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch, Mock

import console


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        data = Path(self.temp.name)
        for name, value in [('ROOT', data), ('DATA', data), ('CONFIG', data / 'settings.json'), ('CHILD', None), ('LOG', None), ('COOKIE_HEALTH_CACHE', None), ('BROWSER_ENABLED', False), ('OBS',None), ('ACCOUNTS',None)]:
            p = patch.object(console, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.settings = console.load_settings()
        # No test may discover the machine's production API or read its private policy/logs.
        self.cookie_request = console.cookie_upstream
        p = patch.object(console, 'cookie_upstream', side_effect=ValueError('offline mock'))
        self.cookie = p.start()
        self.addCleanup(p.stop)
        p = patch('quota_policy.policy', return_value={'pause_on_limit': True})
        p.start()
        self.addCleanup(p.stop)

    def body(self):
        return {k: self.settings[k] for k in ('strict_pro', 'model', 'port', 'proxy')}

    def test_defaults_and_secret_redaction(self):
        self.assertTrue(self.settings['strict_pro'])
        self.assertNotEqual(self.settings['api_key'], self.settings['admin_key'])
        self.assertNotIn('api_key', console.status())
        self.assertNotIn('admin_key', console.status())
        self.assertEqual(console.worker_args(self.settings),
                         [console.sys.executable, str(console.ROOT / 'service.py'), '--config', str(console.CONFIG)])
        self.assertNotIn(self.settings['api_key'], console.worker_args(self.settings))

    def test_policy_toggle(self):
        body = self.body()
        body.update(strict_pro=False, model='gemini-3-flash')
        updated = console.validate_update(body, self.settings)
        self.assertFalse(updated['strict_pro'])
        self.assertNotIn('--api-key', console.worker_args(updated))
        self.assertEqual(updated['api_key'], self.settings['api_key'])
        body['strict_pro'] = True
        with self.assertRaises(ValueError):
            console.validate_update(body, self.settings)

    def test_invalid_updates(self):
        invalid = [None, [], {'api_key': 'replace'}]
        for key, val in [('model', []), ('port', True), ('port', 1), ('strict_pro', 'false'),
                         ('proxy', 'http://user:secret@localhost:7890')]:
            invalid.append({**self.body(), key: val})
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(ValueError):
                console.validate_update(body, self.settings)

    def test_no_accounts_cannot_start(self):
        with self.assertRaisesRegex(ValueError, 'No accounts'):
            console.start()

    def test_cookie_dynamic_models_and_unknown_validation(self):
        self.cookie.side_effect = None
        self.cookie.return_value = {'data': [{'id': 'gemini-4-argon'}, {'id': 'gemini-3-pro'}]}
        self.assertEqual(console.cookie_models(self.settings), (['gemini-3-pro', 'gemini-4-argon'], True))
        body = {**self.body(), 'strict_pro': False, 'model': 'gemini-4-argon', 'port': 9001}
        updated = console.validate_update(body, self.settings)
        self.assertEqual(updated['model'], 'gemini-4-argon')
        self.assertEqual(self.cookie.call_args.args[0]['port'], self.settings['port'])
        for model, strict in [('invented', False), ('gemini-4-argon', True), ('gemini-3-flash', False)]:
            with self.subTest(model=model, strict=strict), self.assertRaises(ValueError):
                console.validate_update({**body, 'model': model, 'strict_pro': strict}, self.settings)

    def test_cookie_offline_keeps_saved_selection_but_not_arbitrary_names(self):
        current = {**self.settings, 'model': 'gemini-4-argon', 'strict_pro': False}
        models, online = console.cookie_models(current)
        self.assertFalse(online)
        self.assertIn('gemini-4-argon', models)
        self.assertTrue(set(console.LEGACY_MODELS) <= set(models))
        body = {**self.body(), 'model': 'gemini-4-argon', 'strict_pro': False}
        self.assertEqual(console.validate_update(body, current)['model'], 'gemini-4-argon')
        with self.assertRaises(ValueError):
            console.validate_update(body, self.settings)
        with self.assertRaises(ValueError):
            console.validate_update({**body, 'model': 'arbitrary'}, current)

    def test_cookie_request_authenticated_short_and_no_proxy(self):
        opener = Mock()
        opener.open.return_value = io.StringIO('{"transport":"cookie"}')
        with patch.object(console.urllib.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(self.cookie_request(self.settings, '/health')['transport'], 'cookie')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, f'http://127.0.0.1:{self.settings["port"]}/health')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.settings['api_key'])
        self.assertLessEqual(opener.open.call_args.kwargs['timeout'], .5)
        self.assertEqual(build.call_args.args[0].proxies, {})

    def test_cookie_request_http_failure_is_redacted(self):
        opener = Mock()
        failure = urllib.error.HTTPError('http://127.0.0.1:8787/health', 401,
                                         self.settings['api_key'], {}, io.BytesIO(b'private'))
        opener.open.side_effect = failure
        with patch.object(console.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, 'HTTP 401') as error:
                self.cookie_request(self.settings, '/health')
        self.assertNotIn(self.settings['api_key'], str(error.exception))
        self.assertTrue(failure.fp.closed)

    def test_launcher_reads_dynamic_cookie_models(self):
        import launcher
        with patch.object(launcher, 'request', return_value={'models': ['gemini-4-argon']}) as request:
            self.assertEqual(launcher.available_models('cookie'), ['gemini-4-argon'])
            request.assert_called_once_with('models?transport=cookie')

    @unittest.skipUnless(shutil.which('node'), 'Node required for isolated JS smoke test')
    def test_settings_dynamic_cookie_controls(self):
        # Execute the actual app.js settings branch in an isolated DOM stub, never a browser/service.
        script = r'''
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
const elements={};
for(const id of ['transport','model','status','stop','start','notice'])elements[id]={value:'',textContent:''};
elements.transport.value='cookie'; elements.model.after=()=>{};
const context={document:{getElementById:id=>elements[id],createElement:()=>({append(){}}),querySelectorAll:()=>[]},
 saved:{model:'gemini-4-argon'},mode(){},render(){},loadModels(){},
 options(models,chosen){elements.model.models=models;elements.model.value=models.includes(chosen)?chosen:''},
 api:async path=>{assert.equal(path,'models?transport=cookie');return {models:['gemini-4-argon','new-model']}},
 action:fn=>fn()};
context.window=context;vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
(async()=>{
 context.mode();await context.loadModels();
 assert.deepEqual(elements.model.models,['gemini-4-argon','new-model']);
 assert.equal(elements.model.value,'gemini-4-argon');
 context.render({ownership:'external'});
 assert.equal(elements.stop.disabled,true);assert.equal(elements.start.disabled,true);
 assert.match(elements.status.textContent,/External ownership/);
 context.render({ownership:'none'});assert.equal(elements.stop.disabled,false);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script, str(Path(__file__).parent / 'web/app.js')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_external_ownership_and_cached_status(self):
        self.cookie.side_effect = None
        self.cookie.return_value = {'transport': 'cookie'}
        with patch.object(console.time, 'monotonic', return_value=10):
            for _ in range(5):
                state = console.status()
                self.assertTrue(state['running'])
                self.assertEqual(state['ownership'], 'external')
                self.assertIsNone(state['pid'])
            self.assertEqual(self.cookie.call_count, 1)
            with patch.object(console.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(ValueError, 'external ownership'):
                    console.start()
                with self.assertRaisesRegex(ValueError, 'external ownership'):
                    console.stop()
                spawn.assert_not_called()
            # Shutdown cleanup does not stop or even query an external owner.
            calls = self.cookie.call_count
            console.stop(check_external=False)
            self.assertEqual(self.cookie.call_count, calls)
        with patch.object(console.time, 'monotonic', return_value=13):
            self.cookie.return_value = {'transport': 'browser'}
            self.assertFalse(console.status()['running'])

    def test_owned_child_stop_drains_before_termination(self):
        child = Mock(pid=123)
        child.poll.return_value = None
        self.cookie.side_effect=None
        self.cookie.return_value={'operations_v1':True,'active':0}
        with patch.object(console, 'CHILD', child):
            self.assertEqual(console.status()['ownership'], 'console')
            console.stop()
        child.terminate.assert_called_once()
        self.assertEqual(self.cookie.call_count,3)

    def test_owned_child_stop_refuses_active_generation(self):
        child=Mock(pid=123);child.poll.return_value=None
        self.cookie.side_effect=None;self.cookie.return_value={'operations_v1':True,'active':1}
        with patch.object(console,'CHILD',child):
            with self.assertRaisesRegex(ValueError,'still active'):console.stop()
            console.stop(check_external=False)
        child.terminate.assert_not_called()

    def test_cookie_start_uses_service_and_saved_config(self):
        (console.DATA / 'accounts' / 'mock.json').write_text('{}', encoding='utf-8')
        with patch('socket.socket') as socket, patch.object(console.subprocess, 'Popen') as spawn:
            spawn.return_value.poll.return_value = None
            console.start()
            args = spawn.call_args.args[0]
            self.assertEqual(args, console.worker_args(self.settings))
            self.assertNotIn(self.settings['api_key'], ' '.join(args))
            self.assertEqual(spawn.call_args.kwargs['env']['GEMINI_CONSOLE_API_KEY'], self.settings['api_key'])
            socket.return_value.__enter__.return_value.bind.assert_called_once()
            console.stop(check_external=False)

    def test_browser_preserves_cookie_settings(self):
        body = dict(transport='browser', model='gemini-4-argon', strict_pro=False, browser_key='secret')
        with patch.object(console, 'browser_models', return_value=['gemini-4-argon']):
            updated = console.validate_update(body, self.settings)
        for key in ('model', 'strict_pro', 'port', 'proxy', 'api_key', 'admin_key'):
            self.assertEqual(updated[key], self.settings[key])
        console.save_settings(updated)
        self.assertNotIn('browser_key', console.status())

    def test_browser_selection_is_forwarded_and_mismatch_rejected(self):
        settings = {**self.settings, 'browser_model': 'gemini-4-argon', 'browser_strict_pro': False}
        body = {'messages': [{'role': 'user', 'content': 'test'}], 'model': 'gemini-3.8-flash'}
        with patch.object(console, 'browser_models', return_value=['gemini-4-argon', 'gemini-3.8-flash']), patch.object(console, 'upstream') as call:
            call.return_value = {'model': 'gemini-3.8-flash'}
            console.browser_generate(settings, body)
            self.assertEqual(call.call_args.args[2]['model'], 'gemini-3.8-flash')
            call.return_value = {'model': 'gemini-4-argon'}
            with self.assertRaisesRegex(ValueError, 'different model'):
                console.browser_generate(settings, body)

    def test_unavailable_or_non_pro_never_generates(self):
        with patch.object(console, 'browser_models', return_value=['gemini-4-argon']), patch.object(console, 'upstream') as call:
            with self.assertRaises(ValueError):
                console.browser_generate(self.settings, {'messages': [{}], 'model': 'unknown'})
            with self.assertRaises(ValueError):
                console.browser_generate({**self.settings, 'browser_strict_pro': True}, {'messages': [{}]})
            call.assert_not_called()

    def test_models_are_intersection_not_union(self):
        health = {'account_details': {'a': {'transport': 'browser', 'browser_models': ['pro', 'flash']},
                                      'b': {'transport': 'browser', 'browser_models': ['flash']}}}
        with patch.object(console, 'upstream', side_effect=[health, {'data': [{'id': 'pro'}, {'id': 'flash'}]}]):
            self.assertEqual(console.browser_models(self.settings), ['flash'])

    def test_browser_start_never_spawns_process(self):
        console.save_settings({**self.settings, 'transport': 'browser', 'browser_model': 'gemini-4-argon', 'browser_strict_pro': False})
        with patch.object(console, 'browser_models', return_value=['gemini-4-argon']), patch.object(console.subprocess, 'Popen') as spawn:
            console.start()
            self.assertTrue(console.status()['running'])
            spawn.assert_not_called()
            console.stop()
            self.assertFalse(console.status()['running'])

    def test_remote_or_self_upstream_rejected(self):
        for url in ['http://example.com', f'http://127.0.0.1:{console.MANAGEMENT_PORT}', 'http://a:b@localhost:8787']:
            with self.assertRaises(ValueError):
                console.validate_browser_url(url)

    def test_http_auth_and_config(self):
        server = console.ThreadingHTTPServer(('127.0.0.1', 0), console.Handler)
        port = server.server_port
        p = patch.object(console, 'MANAGEMENT_PORT', port)
        p.start()
        self.addCleanup(p.stop)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def call(path, body=None, key=None, origin=None):
            headers = {'Content-Type': 'application/json'}
            if key:
                headers['X-Management-Key'] = key
            if origin:
                headers['Origin'] = origin
            request = urllib.request.Request(f'http://127.0.0.1:{port}/api/{path}',
                data=json.dumps(body).encode() if body is not None else None, headers=headers)
            return opener.open(request, timeout=5)

        with self.assertRaises(urllib.error.HTTPError) as error:
            call('status')
        self.assertEqual(error.exception.code, 401)
        error.exception.close()
        for endpoint in ['activity', 'accounts', 'managed-models', 'quota']:
            with self.assertRaises(urllib.error.HTTPError) as error:
                call(endpoint)
            self.assertEqual(error.exception.code,401)
            error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            call('status', key=self.settings['admin_key'], origin='https://example.com')
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        body = {**self.body(), 'strict_pro': False}
        with call('config', body, self.settings['admin_key']) as response:
            self.assertFalse(json.load(response)['strict_pro'])
        self.assertFalse(console.load_settings()['strict_pro'])
        with call('models', key=self.settings['admin_key']) as response:
            models = json.load(response)
            self.assertEqual(models['transport'], 'cookie')
            self.assertFalse(models['online'])
        self.cookie.side_effect = None
        self.cookie.return_value = {'data': [{'id': 'gemini-4-argon'}]}
        with call('models?transport=cookie', key=self.settings['admin_key']) as response:
            self.assertEqual(json.load(response)['models'], ['gemini-4-argon'])
        self.cookie.return_value = {'transport': 'cookie'}
        with self.assertRaises(urllib.error.HTTPError) as error:
            call('config', body, self.settings['admin_key'])
        self.assertEqual(error.exception.code, 409)
        error.exception.close()
        self.cookie.side_effect = ValueError('offline mock')
        with call('accounts', key=self.settings['admin_key']) as response:
            self.assertEqual(json.load(response)['accounts'],[])
        with call('activity?period=5h', key=self.settings['admin_key']) as response:
            self.assertEqual(json.load(response)['summary']['requests'],0)
        with self.assertRaises(urllib.error.HTTPError) as error:
            call('api-settings', {'api_key':'test-key-1234567890'})
        self.assertEqual(error.exception.code,401)
        error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            call('api-settings', {'api_key':'short'}, self.settings['admin_key'])
        self.assertEqual(error.exception.code,400)
        error.exception.close()
        with call('api-settings', {'api_key':'test-key-1234567890'}, self.settings['admin_key']) as response:
            self.assertNotIn('test-key-1234567890', response.read().decode())
        self.assertEqual(console.load_settings()['api_key'],'test-key-1234567890')


if __name__ == '__main__':
    unittest.main()
