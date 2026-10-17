import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from account_control import enabled,set_enabled
from openai_compatible_server import ServerState


class ControlTests(unittest.TestCase):
    def test_switch_blocks_actual_admission_without_clearing_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp, patch('quota_policy.DATABASE',Path(tmp)/'quota.sqlite'),patch('quota_policy.states',return_value={}):
            s=ServerState.__new__(ServerState)
            s.account_dir=Path(tmp)
            (Path(tmp)/'a.json').write_text('{}')
            s.account_health={'a':{'cooldown_until':0}}
            self.assertTrue(s.account_available('a'))
            set_enabled('a',False)
            self.assertFalse(s.account_available('a'))
            self.assertEqual(s.admission_status('a',object())['reason'],'administrator_stopped')
            s.account_health['a']['cooldown_until']=1e12
            set_enabled('a',True)
            self.assertTrue(enabled('a'))
            self.assertFalse(s.account_available('a'))
            self.assertEqual(s.account_health['a']['cooldown_until'],1e12)

    def test_invalid_boolean_rejected(self):
        with self.assertRaises(ValueError):set_enabled('a','false')


if __name__=='__main__':unittest.main()
