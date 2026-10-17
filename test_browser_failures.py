import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from browser_failures import BrowserNotSubmitted, BrowserIdentityMismatch, check_identity, transient_transport_error
from browser_transport import BrowserTransport


class FailureClassificationTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_identity_is_not_a_mismatched_identity(self):
        self.assertFalse(check_identity([], 'demo@example.com'))
        self.assertTrue(check_identity(['Google Account: Demo (demo@example.com)'], 'demo@example.com'))
        with self.assertRaises(BrowserIdentityMismatch):
            check_identity(['Google Account: Other (other@example.com)'], 'demo@example.com')

    async def test_unready_page_never_claims_invalid_account(self):
        client=BrowserTransport(None,cdp_url='http://127.0.0.1:9999',email='demo@example.com')
        page=SimpleNamespace(locator=lambda _:SimpleNamespace(evaluate_all=AsyncMock(return_value=[])))
        with self.assertRaises(BrowserNotSubmitted):
            await client._verify_page_identity(page,timeout=0)

    def test_tls_is_transport_failure_not_authentication_failure(self):
        from curl_cffi.curl import CurlError
        self.assertTrue(transient_transport_error(CurlError('TLS',35)))
        self.assertFalse(transient_transport_error(BrowserIdentityMismatch('different identity')))
