import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openai_compatible_server as server
from gemini_webapi.request_diagnostics import diagnostic_sink, emit


class SerializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_serialization_parallel_accounts_and_cancellation(self):
        state = server.ServerState.__new__(server.ServerState)
        state.account_locks = {}
        entered = asyncio.Event()
        release = asyncio.Event()
        events = []

        async def first():
            async with state.account_request("A", "first"):
                entered.set()
                await release.wait()

        async def queued():
            async with state.account_request("A", "queued"):
                events.append("unexpected")

        with tempfile.TemporaryDirectory() as tmp, patch.object(server, "ROOT", Path(tmp)):
            task = asyncio.create_task(first())
            await entered.wait()
            waiting = asyncio.create_task(queued())
            await asyncio.sleep(0)
            self.assertFalse(waiting.done())
            async with state.account_request("B", "parallel"):
                emit("conversation_identified", cid="test-cid")
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
            release.set()
            await task
            self.assertEqual(events, [])
            self.assertFalse(state.request_lock("A").locked())
            self.assertIsNone(diagnostic_sink.get())
            log = (Path(tmp) / "openai_request_diagnostics.jsonl").read_text()
            self.assertIn('"during": "queued"', log)
            self.assertIn('"cid": "test-cid"', log)


if __name__ == "__main__":
    unittest.main()
