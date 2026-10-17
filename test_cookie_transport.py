import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import openai_compatible_server as server
from cookie_transport import generate, SubmissionUncertain, UpstreamErrorCard


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_error_card_is_not_unknown_or_replayed(self):
        client, calls, original = self.client()
        async def run(*args, **kwargs):
            client.client.stream('POST', 'https://example/StreamGenerate')
            yield SimpleNamespace(text='localized error notice', chosen=0,
                                  candidates=[SimpleNamespace(is_error_card=True)])
        client.generate_content_stream = run
        with self.assertRaises(UpstreamErrorCard):
            await generate(client, 'test', model=object())
        self.assertEqual(len(calls), 1)
        self.assertIs(client.client.stream, original)

    async def test_generation_uses_budget_and_restores_sdk_limits(self):
        client, calls, _ = self.client()
        client.timeout = 30
        client.watchdog_timeout = 120
        client.client.timeout = 45
        original = client.generate_content_stream
        async def checked(*args, **kwargs):
            self.assertEqual(client.timeout, 480)
            self.assertEqual(client.watchdog_timeout, 480)
            self.assertEqual(client.client.timeout, 480)
            async for output in original(*args, **kwargs):
                yield output
        client.generate_content_stream = checked
        await generate(client, 'test', model=object(), timeout=480)
        self.assertEqual((client.timeout, client.watchdog_timeout), (30, 120))
        self.assertEqual(client.client.timeout, 45)
        self.assertEqual(len(calls), 1)

    async def test_unauthenticated_never_submits(self):
        from gemini_webapi.constants import AccountStatus
        from gemini_webapi.exceptions import AuthError
        client, calls, _ = self.client()
        client.account_status = AccountStatus.UNAUTHENTICATED
        with self.assertRaises(AuthError):
            await generate(client, 'test', model=object())
        self.assertEqual(calls, [])

    def client(self, mode='ok'):
        calls = []
        def stream(*args, **kwargs):
            calls.append(args)
        session = SimpleNamespace(stream=stream)
        async def run(*args, **kwargs):
            self.assertTrue(kwargs['temporary'])
            self.assertEqual(kwargs['current_retry'], 0)
            session.stream('POST', 'https://example/StreamGenerate')
            if mode == 'duplicate':
                session.stream('POST', 'https://example/StreamGenerate')
            if mode == 'timeout':
                await asyncio.sleep(1)
            if mode == 'parse':
                raise ValueError('synthetic parse failure')
            yield SimpleNamespace(text='OK')
        return SimpleNamespace(client=session, generate_content_stream=run), calls, stream

    async def test_single_dispatch_and_restore(self):
        client, calls, original = self.client()
        result = await generate(client, 'test', model=object())
        self.assertEqual(result.text, 'OK')
        self.assertEqual(len(calls), 1)
        self.assertIs(client.client.stream, original)

    async def test_duplicate_and_timeout_are_uncertain(self):
        for mode in ('duplicate', 'timeout', 'parse'):
            client, calls, original = self.client(mode)
            with self.assertRaises(SubmissionUncertain):
                await generate(client, 'test', model=object(), timeout=.01)
            self.assertEqual(len(calls), 1)
            self.assertIs(client.client.stream, original)

    async def test_queue_timeout_sends_nothing(self):
        state = server.ServerState.__new__(server.ServerState)
        state.args = SimpleNamespace(queue_timeout=.01)
        state.account_locks = {'a': asyncio.Lock()}
        await state.account_locks['a'].acquire()
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'ROOT', Path(tmp)):
            with self.assertRaises(server.PoolUnavailable):
                async with state.account_request('a', 'req'):
                    self.fail('must not enter generation')
        self.assertTrue(state.account_locks['a'].locked())
        state.account_locks['a'].release()


class PoolTests(unittest.TestCase):
    def test_http_deadline_contract(self):
        self.assertEqual(server.http_generation_timeout({}, 480), 480)
        self.assertEqual(server.http_generation_timeout({'X-Client-Name': 'webnovel-translation'}, 480), 270)
        self.assertEqual(server.http_generation_timeout({'X-Request-Timeout-Seconds': '540'}, 480), 480)
        for invalid in ('nan', 'inf', '0', '-1', 'bad'):
            with self.assertRaises(ValueError):
                server.http_generation_timeout({'X-Request-Timeout-Seconds': invalid}, 480)

    def test_all_cooling_accounts_never_forced(self):
        state = server.ServerState.__new__(server.ServerState)
        state.clients = [('a', object())]
        state.next_index = 0
        state.client_lock = threading.Lock()
        state.account_health = {'a': {'cooldown_until': float('inf')}}
        with tempfile.TemporaryDirectory() as tmp, patch('quota_policy.states', return_value={}):
            state.account_dir = Path(tmp)
            (Path(tmp)/'a.json').write_text('{}')
            with self.assertRaises(server.PoolUnavailable):
                state.next_client()

    def test_service_errors_rejected(self):
        for text in ('', 'I encountered an error doing what you asked. Could you try again?',
                     "I'm having a hard time fulfilling your request. Can I help you with something else instead?"):
            with self.assertRaises(RuntimeError):
                server.validate_generated_text(text)


if __name__ == '__main__':
    unittest.main()
