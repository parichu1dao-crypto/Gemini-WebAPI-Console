import unittest
import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch
from pathlib import Path
from browser_transport import BrowserTransport
from openai_compatible_server import normalize_messages, normalize_messages_with_files


class BrowserTransportTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        with patch('browser_transport.Path.exists', return_value=False):
            return BrowserTransport(None, cdp_url="http://127.0.0.1:9268", email="test@example.com")

    def test_remote_cdp_rejected(self):
        with self.assertRaises(ValueError):
            BrowserTransport(None, cdp_url="http://example.com:9222", email="test@example.com")

    def test_rotate_after_four_successes_or_model_change(self):
        client = self.client()
        url = "https://gemini.google.com/app/test"
        client.last_conversation_url = url
        client._conversation_model = "4 Argon"
        for n in range(1, 4):
            client._turns = client._expected_responses = n
            self.assertFalse(client._needs_new_conversation(n, "4 Argon", url))
        client._turns = client._expected_responses = 4
        self.assertTrue(client._needs_new_conversation(4, "4 Argon", url))
        client._turns = 1
        self.assertTrue(client._needs_new_conversation(4, "3.1 Pro", url))

    def test_external_navigation_or_response_change_rotates(self):
        client = self.client()
        client._conversation_model = "4 Argon"
        client._expected_responses = 1
        client.last_conversation_url = "https://gemini.google.com/app/a"
        self.assertTrue(client._needs_new_conversation(2, "4 Argon", client.last_conversation_url))
        self.assertTrue(client._needs_new_conversation(1, "4 Argon", "https://gemini.google.com/app/b"))

    async def test_closed_bound_tab_never_selects_other_account_tab(self):
        client = self.client()
        client._target_id = "expected"
        page = SimpleNamespace(url="https://gemini.google.com/app")
        with patch.object(client, "_page_id", AsyncMock(return_value="different")):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                await client._existing_page(SimpleNamespace(pages=[page]))

    def test_transport_has_no_tab_creation_or_focus_operations(self):
        source = inspect.getsource(BrowserTransport)
        for forbidden in (".new_page(", ".bring_to_front(", ".press(", ".fill(", "page.close(", "probe.close("):
            self.assertNotIn(forbidden, source)

    def test_single_user_content_not_wrapped(self):
        messages = [{"role": "user", "content": "Please polish this sentence."}]
        self.assertEqual(normalize_messages(messages), messages[0]["content"])
        self.assertEqual(normalize_messages_with_files(messages, Path(".")), (messages[0]["content"], []))

    def test_multi_message_roles_preserved(self):
        messages = [{"role": "system", "content": "Keep names."}, {"role": "user", "content": "Text"}]
        self.assertEqual(normalize_messages(messages), "system: Keep names.\n\nuser: Text")

    async def test_unavailable_model_rejected(self):
        client = self.client()
        client.available_browser_models = {"gemini-3.1-pro": "3.1 Pro"}
        with self.assertRaisesRegex(ValueError, "absent"):
            await client.generate_content("test", model="gemini-4-argon")

    async def test_unknown_model_rejected_before_connect(self):
        with self.assertRaises(ValueError):
            await self.client().generate_content("test", model="unknown-model")

    async def test_files_rejected_before_connect(self):
        with self.assertRaises(ValueError):
            await self.client().generate_content("test", model="gemini-3-pro", files=["test.png"])

    async def test_uncertain_submission_blocks_resend(self):
        client = self.client()
        client._blocked = True
        with self.assertRaisesRegex(RuntimeError, "quarantined"):
            await client.generate_content("test", model="gemini-3-pro")

    async def test_buffered_stream(self):
        client = self.client()
        async def generate(prompt, **kwargs):
            return "finished"
        client.generate_content = generate
        self.assertEqual([x async for x in client.generate_content_stream("test")], ["finished"])

    async def test_browser_accounts_do_not_compete_for_focus(self):
        active = 0
        peak = 0
        async def generate(client, prompt, label):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return label
        with patch.object(BrowserTransport, "_generate", generate):
            await asyncio.gather(*(self.client().generate_content("test", model="gemini-4-argon")
                                   for _ in range(4)))
        self.assertEqual(peak, 1)


if __name__ == "__main__":
    unittest.main()
