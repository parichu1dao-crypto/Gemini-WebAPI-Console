import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import console


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        data = Path(self.temp.name)
        for name, value in [('DATA', data), ('CONFIG', data / 'settings.json'), ('CHILD', None)]:
            p = patch.object(console, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.settings = console.load_settings()

    def body(self):
        return {k: self.settings[k] for k in ('strict_pro', 'model', 'port', 'proxy')}

    def test_defaults_and_secret_redaction(self):
        self.assertTrue(self.settings['strict_pro'])
        self.assertNotEqual(self.settings['api_key'], self.settings['admin_key'])
        self.assertNotIn('api_key', console.status())
        self.assertNotIn('admin_key', console.status())
        self.assertIn('--strict-pro', console.worker_args(self.settings))
        self.assertNotIn(self.settings['api_key'], console.worker_args(self.settings))

    def test_policy_toggle(self):
        body = self.body()
        body.update(strict_pro=False, model='gemini-3-flash')
        updated = console.validate_update(body, self.settings)
        self.assertIn('--allow-non-pro', console.worker_args(updated))
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
        with self.assertRaises(urllib.error.HTTPError) as error:
            call('status', key=self.settings['admin_key'], origin='https://example.com')
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        body = {**self.body(), 'strict_pro': False}
        with call('config', body, self.settings['admin_key']) as response:
            self.assertFalse(json.load(response)['strict_pro'])
        self.assertFalse(console.load_settings()['strict_pro'])


if __name__ == '__main__':
    unittest.main()
