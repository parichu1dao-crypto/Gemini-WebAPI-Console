import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock,patch
import openai_compatible_server as server
import service


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def state(self,protocol='process-session'):
        return SimpleNamespace(args=SimpleNamespace(cookie_protocol=protocol),account_dir=Path('accounts'))

    async def test_new_protocol_returns_only_validated_result_and_never_legacy(self):
        mock=AsyncMock(return_value=SimpleNamespace(text='complete'))
        with patch('cookie_protocol.generate',mock),patch.object(server,'cookie_generate',side_effect=AssertionError('No fallback')):
            out=await server.selected_cookie_generate(self.state(),'sample',SimpleNamespace(proxy=None),'prompt',files=None,model=SimpleNamespace(model_id='pro-id'),timeout=60)
        self.assertEqual(out.text,'complete')
        self.assertEqual(mock.call_args.kwargs['model_id'],'pro-id')

    async def test_uncertain_is_not_retried(self):
        from cookie_protocol import SubmissionUncertain
        mock=AsyncMock(side_effect=SubmissionUncertain('unknown'))
        with patch('cookie_protocol.generate',mock),patch.object(server,'cookie_generate',side_effect=AssertionError('No fallback')):
            with self.assertRaises(server.SubmissionUncertain):
                await server.selected_cookie_generate(self.state(),'sample',SimpleNamespace(proxy=None),'prompt',files=None,model=SimpleNamespace(model_id='pro-id'),timeout=60)
        self.assertEqual(mock.await_count,1)

    async def test_preflight_is_not_submitted(self):
        from cookie_protocol import NotSubmitted
        with patch('cookie_protocol.generate',AsyncMock(side_effect=NotSubmitted('browser unavailable'))):
            with self.assertRaises(server.PoolUnavailable):
                await server.selected_cookie_generate(self.state(),'sample',SimpleNamespace(proxy=None),'prompt',files=None,model=SimpleNamespace(model_id='pro-id'),timeout=60)

    async def test_attachments_fail_before_dispatch(self):
        with patch('cookie_protocol.generate',AsyncMock()) as generate:
            with self.assertRaises(server.EndpointDisabled):
                await server.selected_cookie_generate(self.state(),'sample',None,'prompt',files=['file'],model=None,timeout=60)
            generate.assert_not_called()

    def test_parser_has_explicit_protocol_no_default_change(self):
        self.assertEqual(server.build_parser().parse_args([]).cookie_protocol,'legacy')
        self.assertEqual(server.build_parser().parse_args(['--cookie-protocol','process-session']).cookie_protocol,'process-session')
        self.assertEqual(service.DEFAULTS['cookie_protocol'],'legacy')

if __name__=='__main__':unittest.main()
