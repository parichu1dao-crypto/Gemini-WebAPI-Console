import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import account_health
import openai_compatible_server as server


class HealthTests(unittest.TestCase):
    def test_restart_preserves_failure_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp, patch('quota_policy.DATABASE', Path(tmp)/'state.sqlite'):
            state = dict(failures=2, cooldown_until=time.time()+900, last_error_type='SubmissionUncertain')
            account_health.save('a', state)
            self.assertEqual(account_health.load('a'), state)
            self.assertEqual(account_health.load('b')['cooldown_until'], 0)

    def test_admission_exposes_reason_and_remaining_time(self):
        with tempfile.TemporaryDirectory() as tmp, patch('quota_policy.states', return_value={}):
            state = server.ServerState.__new__(server.ServerState)
            state.account_dir=Path(tmp)
            (Path(tmp)/'a.json').write_text('{}')
            state.account_health={'a':dict(cooldown_until=time.time()+100,last_error_type='SubmissionUncertain')}
            d=state.admission_status('a', object())
            self.assertEqual(d['reason'],'failure_cooldown')
            self.assertFalse(d['available'])
            self.assertTrue(99 <= d['retry_after'] <= 101)


if __name__ == '__main__':
    unittest.main()
