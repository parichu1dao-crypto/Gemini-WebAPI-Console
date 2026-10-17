import json
from pathlib import Path
import tempfile
import unittest
from app import initialize


class BootstrapTests(unittest.TestCase):
    def test_credentials_are_fresh_and_existing_settings_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config.example.json').write_text('{"strict_pro":true,"account_dir":"accounts"}')
            self.assertTrue(initialize(root))
            path=root/'data/settings.json'
            original=path.read_bytes()
            config=json.loads(original)
            self.assertGreaterEqual(len(config['api_key']),32)
            self.assertNotEqual(config['api_key'], config['admin_key'])
            self.assertTrue(config['strict_pro'])
            self.assertFalse(initialize(root))
            self.assertEqual(path.read_bytes(),original)
