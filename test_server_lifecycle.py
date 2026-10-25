"""Offline lifecycle/dispatch regressions; no listening socket or live account."""
import asyncio
import inspect
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import openai_compatible_server as server


def args(root, **overrides):
    values = vars(server.build_parser().parse_args([]))
    values.update(account_dir=str(root), account='a', strict_pro=False,
                  request_timeout=.15, init_timeout=.02, queue_timeout=.1, quiet=True)
    values.update(overrides)
    return SimpleNamespace(**values)


class Offline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'a.json').write_text('{"email":"a@example.test"}')
        original_connect = socket.socket.connect
        def guarded_connect(sock, address):
            # Windows asyncio's self-pipe uses socketpair's private loopback connect.
            # Permit only that stdlib call, never arbitrary localhost/8787 traffic.
            if any(frame.filename == socket.__file__ and frame.function in ('socketpair', '_socketpair', '_fallback_socketpair')
                   for frame in inspect.stack()):
                return original_connect(sock, address)
            raise AssertionError('network forbidden')
        for target, kw in [
            ('socket.create_connection', {'side_effect': AssertionError('network forbidden')}),
            ('quota_policy.states', {'return_value': {}}),
            ('quota_policy.record', {}),
            ('account_health.load', {'return_value': {'failures': 0, 'cooldown_until': 0.0}}),
            ('account_health.save', {}),
            ('openai_compatible_server.emit', {}),
            ('openai_compatible_server.write_account_attempt_log', {}),
        ]:
            p = patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(socket.socket, 'connect', guarded_connect)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(server, 'ROOT', self.root)
        p.start()
        self.addCleanup(p.stop)


class AsyncTests(Offline, unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_unauthenticated_stops_before_model_resolution(self):
        client = SimpleNamespace(_running=False, init=AsyncMock(), account_status=server.AccountStatus.UNAUTHENTICATED)
        token = server.REQUEST_BUDGET.set(server.RequestBudget(10))
        try:
            from gemini_webapi.exceptions import AuthError
            with self.assertRaises(AuthError):
                await self.state.prepare_request_model('a', client, 'gemini-3-pro')
        finally:
            server.REQUEST_BUDGET.reset(token)

    async def test_each_turn_preserves_authenticated_session_before_resolution(self):
        client = SimpleNamespace(_running=True, client=object(), cookies=object(),
                                 _fetch_user_status=AsyncMock(), account_status=server.AccountStatus.AVAILABLE)
        self.state._session_checked_at['a'] = time.monotonic() - 601
        self.state._credential_versions['a'] = server.session_version('a', self.root)
        async def close():
            client._running = False
        async def init(**kwargs):
            self.assertTrue(kwargs['supplied_cookies_only'])
            client._running = True
        client.close = AsyncMock(side_effect=close)
        client.init = AsyncMock(side_effect=init)
        token = server.REQUEST_BUDGET.set(server.RequestBudget(10))
        try:
            with patch('gemini_webapi.utils.rotate_1psidts.rotate_1psidts', new_callable=AsyncMock) as rotate, patch.object(server, 'save_account_session', return_value='new') as save, patch.object(server.CookieModelCatalog, 'from_client'):
                await self.state.prepare_request_model('a', client, 'gemini-3-pro')
                rotate.assert_not_awaited()
                client.close.assert_not_awaited()
                client.init.assert_not_awaited()
                client._fetch_user_status.assert_awaited_once()
        finally:
            server.REQUEST_BUDGET.reset(token)

    async def test_new_import_is_loaded_before_init_without_browser_generation(self):
        self.state._credential_versions['a'] = 'previous-version'
        jar = object()
        client = SimpleNamespace(_running=True, cookies=object(), account_status=server.AccountStatus.AVAILABLE)
        async def close():
            client._running = False
        async def init(**kwargs):
            self.assertIs(client.cookies, jar)
            self.assertTrue(kwargs['supplied_cookies_only'])
            client._running = True
        client.close = AsyncMock(side_effect=close)
        client.init = AsyncMock(side_effect=init)
        token = server.REQUEST_BUDGET.set(server.RequestBudget(10))
        try:
            with patch.object(server, 'load_account_cookie_session', return_value=(jar, None)), patch.object(server.CookieModelCatalog, 'from_client'):
                await self.state.prepare_request_model('a', client, 'gemini-3-pro')
            client.close.assert_awaited_once()
            client.init.assert_awaited_once()
            self.assertEqual(self.state._credential_versions['a'], server.session_version('a', self.root))
        finally:
            server.REQUEST_BUDGET.reset(token)

    async def asyncSetUp(self):
        self.state = server.ServerState(args(self.root))
        self.addCleanup(self.state.loop.close)
        self.state.account_health['a'] = {'failures': 0, 'cooldown_until': 0}
        self.state.account_locks['a'] = asyncio.Lock()
        self.state.clients = [('a', object())]

    async def test_failed_refresh_restores_snapshot_and_does_not_generate(self):
        model = SimpleNamespace(model_id=server.STRICT_PRO_MODEL_ID,
                                model_name=server.STRICT_PRO_MODEL, display_name='Pro',
                                is_available=True, capacity=2)
        registry = {'old': model}
        async def fail():
            registry['partial'] = object()
            raise RuntimeError('RPC failed')
        client = SimpleNamespace(_model_registry=registry,
                                 account_status=server.AccountStatus.AVAILABLE,
                                 _fetch_user_status=fail,
                                 list_models=lambda: list(registry.values()))
        with self.assertRaises(RuntimeError):
            await self.state.prepare_strict_pro_model('a', client)
        self.assertEqual(registry, {'old': model})
        self.state.clients = [('a', client)]
        self.assertIs(self.state.next_client(server.STRICT_PRO_MODEL)[1], client)
        async def success():
            registry['new'] = model
        client._fetch_user_status = success
        self.assertIs(await self.state.prepare_strict_pro_model('a', client), model)
        self.assertEqual(registry, {'new': model})

    async def test_queued_request_rechecks_quota_after_lock(self):
        lock = self.state.request_lock('a')
        await lock.acquire()
        entered = []
        async def request():
            async with self.state.account_request('a', 'queued'):
                entered.append(True)
        with patch('quota_policy.states', return_value={}) as quotas:
            task = asyncio.create_task(request())
            await asyncio.sleep(.005)
            quotas.return_value = {'a@example.test': {'until': time.time() + 60}}
            lock.release()
            with self.assertRaises(server.PoolUnavailable):
                await task
        self.assertFalse(entered)
        self.assertFalse(lock.locked())

    async def test_failure_recorded_before_release_and_next_waiter_rejected(self):
        from gemini_webapi.exceptions import UsageLimitExceeded
        entered = asyncio.Event()
        release = asyncio.Event()
        original = self.state.mark_account_failure
        def failure(name, exc):
            self.assertTrue(self.state.request_lock(name).locked())
            original(name, exc)
        async def first():
            async with self.state.account_request('a', 'first'):
                entered.set()
                await release.wait()
                raise UsageLimitExceeded('quota')
        async def second():
            async with self.state.account_request('a', 'second'):
                self.fail('cooled account must not dispatch')
        with patch.object(self.state, 'mark_account_failure', side_effect=failure):
            a = asyncio.create_task(first())
            await entered.wait()
            b = asyncio.create_task(second())
            await asyncio.sleep(.005)
            release.set()
            with self.assertRaises(UsageLimitExceeded):
                await a
            with self.assertRaises(server.PoolUnavailable):
                await b
        self.assertEqual(self.state.account_health['a']['failures'], 1)

    async def test_queue_and_preparation_share_deadline_no_dispatch(self):
        self.state.args.request_timeout = .03
        client = SimpleNamespace(_running=True)
        self.state.clients = [('a', client)]
        async def slow(*_):
            await asyncio.sleep(.06)
            return object()
        with patch.object(server, 'STATE', self.state), \
             patch.object(self.state, 'next_client', return_value=('a', client)), \
             patch.object(self.state, 'prepare_request_model', side_effect=slow), \
             patch.object(server, 'cookie_generate', new_callable=AsyncMock) as send:
            with self.assertRaises(server.PoolUnavailable):
                await server.create_chat_completion({'messages': [{'content': 'test'}]})
            send.assert_not_called()

    async def test_subfive_second_budget_is_positive_and_no_model_substitution(self):
        self.state.args.request_timeout = .2
        model = object()
        client = SimpleNamespace(_running=True)
        async def prepare(*_):
            await asyncio.sleep(.01)
            return model
        async def send(c, prompt, **kw):
            self.assertIs(c, client)
            self.assertIs(kw['model'], model)
            self.assertGreater(kw['timeout'], 0)
            self.assertLess(kw['timeout'], .2)
            return SimpleNamespace(text='answer')
        with patch.object(server, 'STATE', self.state), \
             patch.object(self.state, 'next_client', return_value=('a', client)), \
             patch.object(self.state, 'prepare_request_model', side_effect=prepare), \
             patch.object(server, 'cookie_generate', side_effect=send):
            result = await server.create_chat_completion({'messages': [{'content': 'test'}]})
        self.assertEqual(result['choices'][0]['message']['content'], 'answer')

    async def test_image_disabled_without_account_selection(self):
        with patch.object(server, 'STATE', self.state), \
             patch.object(self.state, 'next_client') as select:
            with self.assertRaises(server.EndpointDisabled):
                await server.create_image_generation({'prompt': 'test'})
            select.assert_not_called()

    async def test_cancelled_refresh_restores_original_registry(self):
        registry = {'original': object()}
        started = asyncio.Event()
        async def refresh():
            registry['partial'] = object()
            started.set()
            await asyncio.sleep(60)
        client = SimpleNamespace(_model_registry=registry, account_status='before',
                                 _fetch_user_status=refresh)
        snapshot = registry.copy()
        task = asyncio.create_task(self.state.prepare_strict_pro_model('a', client))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(registry, snapshot)
        self.assertEqual(client.account_status, 'before')

    async def test_queue_time_is_deducted_from_preparation_budget(self):
        self.state.args.request_timeout = .08
        lock = self.state.request_lock('a')
        await lock.acquire()
        async def prepare(*_):
            await asyncio.sleep(.06)
            return object()
        with patch.object(server, 'STATE', self.state), \
             patch.object(self.state, 'next_client', return_value=('a', object())), \
             patch.object(self.state, 'prepare_request_model', side_effect=prepare), \
             patch.object(server, 'cookie_generate', new_callable=AsyncMock) as send:
            request = asyncio.create_task(server.create_chat_completion({'messages': [{'content': 'test'}]}))
            await asyncio.sleep(.04)
            lock.release()
            with self.assertRaises(server.PoolUnavailable):
                await request
            send.assert_not_called()

    async def test_dispatched_deadline_is_uncertain_and_accounted_under_lock(self):
        from gemini_webapi.request_diagnostics import emit
        self.state.args.request_timeout = 1.0
        async def send(*_, **kw):
            emit('generation_dispatched', transport='cookie')
            await asyncio.sleep(60)
        original = self.state.mark_account_failure
        def failure(name, exc):
            self.assertTrue(self.state.request_lock(name).locked())
            original(name, exc)
        with patch.object(server, 'STATE', self.state), \
             patch.object(self.state, 'next_client', return_value=('a', object())), \
             patch.object(self.state, 'prepare_request_model', new_callable=AsyncMock), \
             patch.object(self.state, 'mark_account_failure', side_effect=failure) as record, \
             patch.object(server, 'cookie_generate', side_effect=send):
            with self.assertRaises(server.SubmissionUncertain):
                await server.create_chat_completion({'messages': [{'content': 'test'}]})
            record.assert_called_once()

    async def test_stream_keeps_selected_model_and_remaining_budget(self):
        self.state.args.request_timeout = .2
        model = object()
        sent = []
        async def stream(*_, **kw):
            sent.append(kw)
            yield SimpleNamespace(text='answer', text_delta='answer')
        with patch.object(server, 'STATE', self.state), \
             patch.object(self.state, 'next_client', return_value=('a', object())), \
             patch.object(self.state, 'prepare_request_model', new_callable=AsyncMock, return_value=model), \
             patch.object(server, 'cookie_generate_stream', stream):
            result = await server.create_chat_completion_chunks({'messages': [{'content': 'test'}]})
        self.assertEqual(len(sent), 1)
        self.assertIs(sent[0]['model'], model)
        self.assertTrue(0 < sent[0]['timeout'] <= .2 + 1e-6)
        self.assertEqual(result[-1]['choices'][0]['finish_reason'], 'stop')


class LifecycleTests(Offline):
    def test_startup_error_is_fail_closed_by_default(self):
        self.assertFalse(server.StartupError('failure').cleanup_complete)
        self.assertFalse(server.StartupError('failure').retryable)
        self.assertFalse(server.StartupError('failure', retryable=True).retryable)

    def test_startup_retryability_classifies_causes_after_cleanup(self):
        from gemini_webapi.exceptions import AuthError, TimeoutError as SDKTimeout
        from curl_cffi.requests.exceptions import ConnectionError as CurlConnection, Timeout as CurlTimeout
        cases = [([AuthError('auth')], False), ([FileNotFoundError('file')], False),
                 ([ValueError('config')], False), ([RuntimeError('unknown')], False),
                 ([server.StrictProUnavailable('eligibility')], False),
                 ([ConnectionError('connection')], True), ([SDKTimeout('timeout')], True),
                 ([CurlConnection('connection')], True), ([CurlTimeout('timeout')], True),
                 ([AuthError('auth'), TimeoutError('temporary')], True)]
        for errors, expected in cases:
            with self.subTest(errors=errors):
                state = server.ServerState(args(self.root, account=None,
                                               accounts=','.join(str(i) for i in range(len(errors)))))
                self.addCleanup(state.stop)
                with patch.object(state, 'initialize_client', AsyncMock(side_effect=errors)):
                    with self.assertRaises(server.StartupError) as caught:
                        state.start()
                self.assertTrue(caught.exception.cleanup_complete)
                self.assertEqual(caught.exception.retryable, expected)

    def test_strict_ineligibility_is_not_retryable(self):
        state = server.ServerState(args(self.root, strict_pro=True))
        self.addCleanup(state.stop)
        client = SimpleNamespace(close=AsyncMock())
        with patch.object(state, 'initialize_client', AsyncMock(return_value=client)), \
             patch.object(state, 'strict_pro_eligibility', return_value=(False, 'no entitlement', None)):
            with self.assertRaises(server.StartupError) as caught:
                state.start()
        self.assertTrue(caught.exception.cleanup_complete)
        self.assertFalse(caught.exception.retryable)

    def test_cancelled_initialization_closed_before_retry_flag(self):
        closed = []
        async def init(**kw):
            await asyncio.sleep(60)
        async def close():
            await asyncio.sleep(.005)
            closed.append(True)
        client = SimpleNamespace(cookies=None, init=init, close=close)
        state = server.ServerState(args(self.root))
        self.addCleanup(state.stop)
        with patch.object(server, 'load_account_cookie_session', return_value=({}, None)), \
             patch.object(server, 'GeminiClient', return_value=client):
            with self.assertRaises(server.StartupError) as caught:
                state.start()
        self.assertTrue(closed)
        self.assertTrue(caught.exception.cleanup_complete)
        self.assertTrue(caught.exception.retryable)
        self.assertFalse(state.thread.is_alive())
        self.assertTrue(state.loop.is_closed())

    def test_run_async_waits_for_cancellation_cleanup(self):
        state = server.ServerState(args(self.root))
        state.thread.start()
        self.addCleanup(state.stop)
        cleaned = []
        async def work():
            try:
                await asyncio.sleep(60)
            finally:
                await asyncio.sleep(.02)
                cleaned.append(True)
        with self.assertRaises(server.PoolUnavailable):
            state.run_async(work(), timeout=.01)
        self.assertEqual(cleaned, [True])

    def test_failed_init_cleanup_never_claims_retry_safe(self):
        async def init(**_):
            raise ConnectionError('temporary init failure')
        client = SimpleNamespace(cookies=None, init=init,
                                 close=AsyncMock(side_effect=RuntimeError('close failure')))
        state = server.ServerState(args(self.root))
        def finish():
            client.close = AsyncMock()
            state._cleanup_failed = False
            state.stop()
        self.addCleanup(finish)
        with patch.object(server, 'load_account_cookie_session', return_value=({}, None)), \
             patch.object(server, 'GeminiClient', return_value=client):
            with self.assertRaises(server.StartupError) as caught:
                state.start()
        self.assertFalse(caught.exception.cleanup_complete)
        self.assertFalse(caught.exception.retryable)


class HandlerTests(Offline):
    def setUp(self):
        super().setUp()
        self.ops_patch=patch.object(server.operations,'DATA',self.root/'ops')
        self.ops_patch.start();self.addCleanup(self.ops_patch.stop)
        self.resolve_patch=patch.object(server,'resolve_requested_model',return_value=('gemini-3-pro','gemini-3-pro'))
        self.resolve_patch.start();self.addCleanup(self.resolve_patch.stop)
        self.auth_patch=patch.object(server.operations,'authorize',return_value='legacy')
        self.auth_patch.start();self.addCleanup(self.auth_patch.stop)

    def handler(self, payload, stream=False):
        handler = server.OpenAICompatibleHandler.__new__(server.OpenAICompatibleHandler)
        handler.path = '/v1/chat/completions'
        handler.headers = {'Idempotency-Key': 'test-key'}
        handler._authorized = lambda: True
        handler._client_id='legacy'
        handler._client_token='test'
        handler._read_json = lambda: payload
        handler._send_json = Mock()
        handler._send_sse = Mock()
        return handler

    def test_ledger_wraps_both_chat_paths_and_cached_result_skips_call(self):
        for stream in (False, True):
            payload = {'messages': [{'content': 'test'}], 'stream': stream}
            result = [] if stream else {'cached': True}
            ledger = SimpleNamespace(execute=Mock(return_value=result), LedgerConflict=type('LedgerConflict', (Exception,), {}))
            state = SimpleNamespace(run_async=Mock(), clients=[('a', object())],args=SimpleNamespace(api_key='test',request_timeout=480))
            handler = self.handler(payload)
            with patch.dict('sys.modules', request_ledger=ledger), patch.object(server, 'STATE', state):
                handler.do_POST()
            self.assertEqual(ledger.execute.call_args.args[:2], ('test-key', payload))
            state.run_async.assert_not_called()
            (handler._send_sse if stream else handler._send_json).assert_called_once()

    def test_uncertain_and_conflict_are_409(self):
        conflict = type('LedgerConflict', (Exception,), {})
        for exc in (server.SubmissionUncertain('submission outcome unknown'), conflict('conflict')):
            handler = self.handler({})
            ledger = SimpleNamespace(execute=Mock(side_effect=exc), LedgerConflict=conflict)
            with patch.dict('sys.modules', request_ledger=ledger), patch.object(server, 'STATE', SimpleNamespace(clients=[('a', object())],args=SimpleNamespace(api_key='test',request_timeout=480))):
                handler.do_POST()
            status, body = handler._send_json.call_args.args
            self.assertEqual(status, 409)
            if isinstance(exc, server.SubmissionUncertain):
                self.assertEqual(body['error']['type'], 'submission_uncertain')
                self.assertTrue(body['error']['submitted'])
                self.assertFalse(body['error']['retryable'])

    def test_pool_unavailable_and_disabled_image_status(self):
        conflict = type('LedgerConflict', (Exception,), {})
        ledger = SimpleNamespace(execute=Mock(side_effect=server.PoolUnavailable('not submitted')),
                                 LedgerConflict=conflict)
        state = SimpleNamespace(clients=[('a', object())],args=SimpleNamespace(api_key='test',request_timeout=480))
        handler = self.handler({})
        with patch.dict('sys.modules', request_ledger=ledger), patch.object(server, 'STATE', state):
            handler.do_POST()
            self.assertEqual(handler._send_json.call_args.args[0], 503)
            self.assertEqual(handler._send_json.call_args.args[1]['error']['type'], 'not_submitted')
            ledger.execute.reset_mock()
            handler.path = '/v1/images/generations'
            handler.do_POST()
            self.assertEqual(handler._send_json.call_args.args[0], 501)
            ledger.execute.assert_not_called()

    def test_real_ledger_cache_for_dict_and_stream_list_uses_no_second_dispatch(self):
        import request_ledger
        for stream in (False, True):
            payload = {'messages': [{'content': 'synthetic'}], 'stream': stream}
            result = [{'chunk': 'synthetic'}] if stream else {'answer': 'synthetic'}
            calls = []
            def run(coro, timeout=None):
                coro.close()  # Never execute generation; exercise only ledger/handler wiring.
                self.assertEqual(timeout, 60)
                calls.append(True)
                return result
            handler = self.handler(payload)
            handler.headers['X-Request-Timeout-Seconds'] = '90'
            state = SimpleNamespace(clients=[('a', object())], run_async=run,args=SimpleNamespace(api_key='test',request_timeout=480))
            with patch.object(request_ledger, 'DATABASE', self.root / f'ledger_{stream}.sqlite3'), \
                 patch.object(server, 'STATE', state):
                handler.do_POST()
                handler.do_POST()
            self.assertEqual(len(calls), 1)
            response = handler._send_sse if stream else handler._send_json
            self.assertEqual(response.call_count, 2)
            self.assertEqual(response.call_args.args[-1], result)


if __name__ == '__main__':
    unittest.main()
