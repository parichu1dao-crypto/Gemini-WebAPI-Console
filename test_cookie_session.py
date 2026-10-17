"""Offline isolation regression tests; only synthetic cookies and temporary files."""
import importlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import local_account_client as local
from curl_cffi.requests import Cookies
from gemini_webapi import GeminiClient
from gemini_webapi.exceptions import AuthError
from gemini_webapi.utils.decorators import running

token = importlib.import_module('gemini_webapi.utils.get_access_token')
client_module = importlib.import_module('gemini_webapi.client')


def synthetic_jar(value='account-a'):
    jar = Cookies()
    jar.set('__Secure-1PSID', value, domain='.google.com', secure=True)
    jar.set('__Secure-1PSIDTS', 'fresh-ts', domain='.google.com', secure=True)
    jar.set('SID', 'additional', domain='.google.com')
    jar.set('scoped', 'root', domain='gemini.google.com', path='/')
    jar.set('scoped', 'app', domain='gemini.google.com', path='/app')
    return jar


def snapshot(jar):
    return {(c.name, c.domain, c.path): c.value for c in jar.jar}


class AccountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.path = self.folder / 'a.json'
        self.data = {'proxy': 'http://synthetic.invalid:8080', 'cookies': [
            {'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path,
             'secure': c.secure} for c in synthetic_jar().jar] + [
            {'name': 'foreign', 'value': 'never', 'domain': 'evilgoogle.com', 'path': '/'},
            {'name': 'foreign2', 'value': 'never', 'domain': 'google.com.evil.test', 'path': '/'},
            {'name': 'expired', 'value': 'old', 'domain': '.google.com', 'expires': 1},
            {'name': 'session', 'value': 'valid', 'domain': '.google.com', 'expires': -1},
        ]}
        self.path.write_text(json.dumps(self.data), encoding='utf-8-sig')

    def test_full_scopes_and_legacy_tuple(self):
        before = self.path.read_bytes()
        jar, proxy = local.load_account_cookie_session('a', account_dir=self.folder)
        expected = snapshot(synthetic_jar())
        expected[('session', '.google.com', '/')] = 'valid'
        self.assertEqual(snapshot(jar), expected)
        self.assertEqual(local.load_account_credentials('a', False, self.folder),
                         ('account-a', 'fresh-ts', proxy))
        self.assertIsNone(local.load_account_credentials('a', True, self.folder)[2])
        self.assertEqual(before, self.path.read_bytes())

    def test_expiry_survives_load_save(self):
        self.data['cookies'][0]['expires'] = 4102444800
        self.path.write_text(json.dumps(self.data))
        jar, _ = local.load_account_cookie_session('a', account_dir=self.folder)
        cookie = next(c for c in jar.jar if c.name == '__Secure-1PSID')
        self.assertEqual(cookie.expires, 4102444800)
        self.assertFalse(cookie.discard)
        local.save_account_session('a', self.folder, jar, local.session_version('a', self.folder))
        saved = json.loads(self.path.read_text())
        self.assertEqual(next(c for c in saved['cookies'] if c['name'] == '__Secure-1PSID')['expires'], 4102444800)

    def test_missing_credentials_and_path_escape_fail_closed(self):
        self.path.write_text('{"cookies": []}')
        with self.assertRaises(ValueError):
            local.load_account_credentials('a', account_dir=self.folder)
        with self.assertRaises(ValueError):
            local.load_account_credentials('../outside', account_dir=self.folder)

    def test_two_account_files_never_merge(self):
        other = {'cookies': [{'name': c.name, 'value': c.value, 'domain': c.domain,
                              'path': c.path} for c in synthetic_jar('account-b').jar]}
        (self.folder / 'b.json').write_text(json.dumps(other))
        a, _ = local.load_account_cookie_session('a', account_dir=self.folder)
        b, _ = local.load_account_cookie_session('b', account_dir=self.folder)
        self.assertEqual(a.get('__Secure-1PSID'), 'account-a')
        self.assertEqual(b.get('__Secure-1PSID'), 'account-b')
        b.set('only-b', 'synthetic', domain='.google.com')
        self.assertNotIn(('only-b', '.google.com', '/'), snapshot(a))

    def test_account_refresh_is_atomic_and_preserves_new_import(self):
        version = local.session_version('a', self.folder)
        jar = synthetic_jar()
        jar.set('__Secure-1PSIDTS', 'rotated', domain='.google.com', secure=True)
        updated = local.save_account_session('a', self.folder, jar, version)
        self.assertNotEqual(version, updated)
        self.assertEqual(local.load_account_credentials('a', account_dir=self.folder)[1], 'rotated')
        replacement = self.path.read_bytes().replace(b'rotated', b'new-import')
        self.path.write_bytes(replacement)
        local.save_account_session('a', self.folder, jar, updated)
        self.assertEqual(self.path.read_bytes(), replacement)

    async def test_factory_passes_full_jar_proxy_and_isolation(self):
        before = self.path.read_bytes()
        fake = SimpleNamespace(init=AsyncMock(), close=AsyncMock())
        with patch.object(local, 'GeminiClient', return_value=fake) as ctor:
            result = await local.create_initialized_client('a', account_dir=self.folder,
                proxy_override='http://override.invalid', timeout=420, verify=True)
        self.assertIs(result, fake)
        ctor.assert_called_once_with(proxy='http://override.invalid', verify=True)
        self.assertIn(('SID', '.google.com', '/'), snapshot(fake.cookies))
        fake.init.assert_awaited_once_with(timeout=420, auto_refresh=False, supplied_cookies_only=True)
        self.assertEqual(before, self.path.read_bytes())

    async def test_factory_failure_closes_and_ignore_proxy_wins(self):
        fake = SimpleNamespace(init=AsyncMock(side_effect=AuthError('synthetic')), close=AsyncMock())
        with patch.object(local, 'GeminiClient', return_value=fake) as ctor:
            with self.assertRaises(AuthError):
                await local.create_initialized_client('a', account_dir=self.folder,
                    ignore_proxy=True, proxy_override='http://ignored.invalid')
        self.assertIsNone(ctor.call_args.kwargs['proxy'])
        fake.close.assert_awaited_once()


class TokenTests(unittest.IsolatedAsyncioTestCase):
    def guards(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.guarded = [stack.enter_context(patch.object(token, name,
            side_effect=AssertionError('Forbidden credential discovery'))) for name in
            ('_get_cookies_cache_path', '_get_cookie_cache_dir', 'load_browser_cookies')]
        return stack

    async def test_only_supplied_cookie_jar_and_no_preflight(self):
        stack = self.guards()
        supplied = synthetic_jar()
        before = snapshot(supplied)
        response = SimpleNamespace(text='{"SNlM0e":"synthetic-token"}', status_code=200,
                                   raise_for_status=lambda: None)
        session = SimpleNamespace(cookies=Cookies(), get=AsyncMock(return_value=response), close=AsyncMock())
        stack.enter_context(patch.object(token, 'AsyncSession', return_value=session))
        result = await token.get_access_token(supplied, supplied_cookies_only=True)
        self.assertEqual(result[0], 'synthetic-token')
        self.assertIs(result[-1], session)
        self.assertEqual(snapshot(session.cookies), before)
        self.assertEqual(snapshot(supplied), before)
        self.assertTrue(next(c for c in session.cookies.jar if c.name=='__Secure-1PSID').secure)
        session.get.assert_awaited_once_with(token.Endpoint.INIT, headers=token.Headers.GEMINI.value)
        for guard in self.guarded:
            guard.assert_not_called()

    async def test_missing_cookie_never_creates_session_or_discovers(self):
        stack = self.guards()
        ctor = stack.enter_context(patch.object(token, 'AsyncSession'))
        with self.assertRaises(AuthError):
            await token.get_access_token(Cookies(), supplied_cookies_only=True)
        ctor.assert_not_called()
        for guard in self.guarded:
            guard.assert_not_called()

    async def test_auth_failure_does_not_fall_back(self):
        stack = self.guards()
        session = SimpleNamespace(cookies=Cookies(), get=AsyncMock(side_effect=RuntimeError('synthetic')),
                                  close=AsyncMock())
        stack.enter_context(patch.object(token, 'AsyncSession', return_value=session))
        with self.assertRaises(AuthError):
            await token.get_access_token({'__Secure-1PSID': 'synthetic'}, supplied_cookies_only=True)
        session.get.assert_awaited_once()
        session.close.assert_awaited_once()
        for guard in self.guarded:
            guard.assert_not_called()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_and_both_running_wrappers_keep_isolation_and_timeout(self):
        client = GeminiClient()
        client.cookies = synthetic_jar()
        sessions = []
        async def access(**kwargs):
            self.assertTrue(kwargs['supplied_cookies_only'])
            session = SimpleNamespace(cookies=Cookies(kwargs['base_cookies']), close=AsyncMock())
            sessions.append(session)
            return 'token', None, None, None, None, session
        @running()
        async def call(c):
            return c.timeout
        @running()
        async def stream(c):
            yield c.timeout
        with patch.object(client_module, 'get_access_token', side_effect=access), \
             patch.object(GeminiClient, '_init_rpc', new_callable=AsyncMock), \
             patch.object(client_module, 'save_cookies') as save:
            await client.init(timeout=60, supplied_cookies_only=True)
            self.assertFalse(client.auto_refresh)
            self.assertIsNone(client.refresh_task)
            client.timeout = 420  # Runtime request timeout supplied by the owner.
            await client.close()
            self.assertEqual(await call(client), 420)
            self.assertEqual(sessions[-1].timeout, 420)
            await client.close()
            self.assertEqual([v async for v in stream(client)], [420])
            await client.close()
            save.assert_not_called()
        self.assertEqual(len(sessions), 3)

    async def test_failed_init_still_does_not_save_cache(self):
        client = GeminiClient('synthetic')
        with patch.object(client_module, 'get_access_token', side_effect=AuthError('synthetic')), \
             patch.object(client_module, 'save_cookies') as save:
            with self.assertRaises(AuthError):
                await client.init(supplied_cookies_only=True)
            await client.close()
            save.assert_not_called()

    async def test_legacy_client_keeps_default_mode(self):
        client = GeminiClient('synthetic')
        with patch.object(client_module, 'save_cookies') as save:
            await client.close()
            save.assert_called_once()


if __name__ == '__main__':
    unittest.main()
