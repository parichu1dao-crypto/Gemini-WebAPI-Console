import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from observability import Store
from account_manager import Accounts


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a=Accounts.__new__(Accounts)
        self.a.store=Store(Path(self.tmp.name)/'test.sqlite3')
        self.a.data=Path(self.tmp.name)
        self.a.lock=threading.RLock()
        self.a.clients={}
        self.row=self.a.create('test-account')

    def test_delete_hides_and_retains_history_and_profile(self):
        aid=self.row['id']
        profile=Path(self.row['profile']);profile.mkdir(parents=True)
        (profile/'sentinel').write_text('keep')
        self.a.store.begin('call','managed',aid,'pro',100)
        self.a.delete(aid,'test-account')
        with patch('quota_policy.states',return_value={}):
            self.assertEqual(self.a.list(),[])
        with self.assertRaises(ValueError):self.a.get(aid)
        self.assertTrue((profile/'sentinel').exists())
        with self.a.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM calls').fetchone()[0],1)

    def test_requires_confirmation_and_rejects_external_or_enabled(self):
        aid=self.row['id']
        with self.assertRaises(ValueError):self.a.delete(aid,'wrong')
        for column,value in [('enabled',1),('external_key','production')]:
            with self.a.store.connect() as db:
                db.execute('UPDATE accounts SET enabled=0,external_key=NULL')
                db.execute(f'UPDATE accounts SET {column}=? WHERE id=?',(value,aid))
            with self.assertRaises(ValueError):self.a.delete(aid,'test-account')


if __name__=='__main__':unittest.main()
