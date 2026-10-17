import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import request_ledger as ledger


class LedgerTests(unittest.TestCase):
    def test_explicit_admission_rejection_can_retry_same_payload_only(self):
        class NotSubmitted(Exception):
            pass
        with self.assertRaises(NotSubmitted):
            ledger.execute('admission', {}, Mock(side_effect=NotSubmitted),
                           not_submitted_errors=(NotSubmitted,))
        with self.assertRaises(ledger.LedgerConflict):
            ledger.execute('admission', {'different': True}, Mock())
        call = Mock(return_value={'text': 'ok'})
        self.assertEqual(ledger.execute('admission', {}, call), {'text': 'ok'})
        self.assertEqual(ledger.execute('admission', {}, call), {'text': 'ok'})
        call.assert_called_once()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(ledger, 'DATABASE', Path(tmp.name)/'ledger.sqlite3')
        p.start()
        self.addCleanup(p.stop)

    def test_cached_response_without_replay_and_payload_conflict(self):
        call = Mock(return_value={'text':'ok'})
        for _ in range(2):
            self.assertEqual(ledger.execute('one', {'model':'pro'}, call), {'text':'ok'})
        call.assert_called_once()
        with self.assertRaises(ledger.LedgerConflict):
            ledger.execute('one', {'model':'flash'}, call)

    def test_failed_and_pending_keys_never_replay(self):
        call = Mock(side_effect=TimeoutError)
        with self.assertRaises(TimeoutError):
            ledger.execute('one', {}, call)
        with self.assertRaises(ledger.LedgerConflict):
            ledger.execute('one', {}, call)
        call.assert_called_once()

    def test_no_key_backward_compatibility(self):
        call = Mock(return_value=123)
        self.assertEqual(ledger.execute(None, {}, call), 123)

    def test_pending_request_is_not_dispatched_again(self):
        second = Mock()
        def first():
            with self.assertRaises(ledger.LedgerConflict):
                ledger.execute('pending', {}, second)
            return {'text': 'first only'}
        ledger.execute('pending', {}, first)
        second.assert_not_called()

    def test_invalid_key(self):
        with self.assertRaises(ValueError):
            ledger.execute('invalid key', {}, Mock())


if __name__ == '__main__':
    unittest.main()
