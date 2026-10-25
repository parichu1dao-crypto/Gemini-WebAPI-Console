import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import openai_compatible_server as s


class Errors(unittest.IsolatedAsyncioTestCase):
    def test_distinct_output_errors(self):
        with self.assertRaises(s.EmptyGeneration):
            s.validate_generated_text('   ')
        with self.assertRaises(s.UpstreamServiceMessage):
            s.validate_generated_text('Sorry, something went wrong. Please try your request again.')
        s.validate_generated_text('Chapter 1\n\nReal story content.')

    async def test_second_status_expiry_recovers_before_pro_check(self):
        state = s.ServerState.__new__(s.ServerState)
        state.args = SimpleNamespace(allow_free_pro=False)
        model = SimpleNamespace(model_id=s.STRICT_PRO_MODEL_ID, model_name=s.STRICT_PRO_MODEL,
                                is_available=True, capacity=2)
        client = SimpleNamespace(account_status=s.AccountStatus.AVAILABLE, _model_registry={},
                                 list_models=lambda: [model])
        async def expire():
            client.account_status = s.AccountStatus.UNAUTHENTICATED
        async def recover(*args):
            client.account_status = s.AccountStatus.AVAILABLE
        client._fetch_user_status = expire
        state.ensure_authenticated = AsyncMock(side_effect=recover)
        self.assertIs(await state.prepare_strict_pro_model('a', client), model)
        state.ensure_authenticated.assert_awaited_once()

    async def test_preflight_auth_is_not_submitted_and_not_penalized(self):
        import asyncio
        state = s.ServerState.__new__(s.ServerState)
        state.args = SimpleNamespace(queue_timeout=1)
        state.account_locks = {'a': asyncio.Lock()}
        state.account_health = {}
        state.account_available = lambda name: True
        state.mark_account_failure = lambda *a: self.fail('not-submitted must not penalize account')
        budget = s.RequestBudget(30)
        token = s.REQUEST_BUDGET.set(budget)
        try:
            with self.assertRaises(s.PoolUnavailable):
                async with state.account_request('a', 'test'):
                    raise s.AuthError('invalid session')
        finally:
            s.REQUEST_BUDGET.reset(token)
