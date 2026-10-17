import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import session_recovery as recovery


class RecoveryTests(unittest.TestCase):
    def test_only_verified_enrolled_identity_is_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'data').mkdir()
            accounts = root/'data'/'accounts'
            accounts.mkdir()
            (accounts/'a.json').write_text(json.dumps({'email':'a@example.test'}))
            mapping = root/'mapping.json'
            mapping.write_text(json.dumps({'a':{'email':'a@example.test','cdp_url':'http://127.0.0.1:9999'}}))
            (root/'data'/'integration.json').write_text(json.dumps({'browser_mapping':str(mapping)}))
            with patch.object(recovery,'ROOT',root), patch('import_browser_session.import_session') as importer:
                self.assertTrue(recovery.recover_session('a',accounts))
                importer.assert_called_once_with('a','http://127.0.0.1:9999','a@example.test',accounts)
                importer.reset_mock()
                mapping.write_text(json.dumps({'a':{'email':'other@example.test','cdp_url':'http://127.0.0.1:9999'}}))
                with self.assertRaises(ValueError): recovery.recover_session('a',accounts)
                importer.assert_not_called()

    def test_no_integration_means_no_browser_dependency(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(recovery,'ROOT',Path(tmp)), patch('import_browser_session.import_session') as importer:
            self.assertFalse(recovery.recover_session('a',tmp))
            importer.assert_not_called()
