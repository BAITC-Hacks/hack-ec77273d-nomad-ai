from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend.app.repository import Repository
from backend.app.engine.data_loader import load_dataset


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository(Path(self.temp.name) / 'state.sqlite3')
        self.repo.initialize()
        self.import_args = dict(as_of_date='2026-10-01', manifest={'skills.json': 'a' * 64},
                                counts={'employees': 1})
        self.import_id = self.repo.register_import(**self.import_args)['import_id']
        self.token = self.repo.create_session(self.import_id, actor_role='employee', employee_id='E1')

    def complete(self, **kwargs):
        return self.repo.record_demo_completion(self.token, event_id='EV_TEST', **kwargs)

    def test_initialization_and_import_are_idempotent(self):
        self.repo.initialize()
        self.assertEqual(self.import_id, self.repo.register_import(**self.import_args)['import_id'])

    def test_version_two_upgrade_preserves_journal_and_enables_employee_state(self):
        with self.repo.connect() as connection:
            connection.execute('PRAGMA user_version = 2')
        self.repo.initialize()
        self.assertEqual(self.repo.get_session(self.token)['employee_id'], 'E1')
        self.repo.complete_employee(self.import_id, 'E1', event_id='EV_TEST', idempotency_key='new',
                                   expected_revision=0, as_of_date='2026-10-01')
        state = self.repo.progress_state(self.import_id, 'E1')
        self.assertEqual(state['revision'], 1)
        self.assertEqual(len(state['completions']), 1)
        self.assertEqual(self.import_id, self.repo.register_import(**self.import_args)['import_id'])

    def test_duplicate_completion_and_idempotent_retry(self):
        first = self.complete(idempotency_key='request-1')
        self.assertEqual(first, self.complete(idempotency_key='request-1'))
        self.assertEqual(first['date'], '2026-10-01')
        with self.assertRaises(ValueError):
            self.complete(idempotency_key='request-2')
        with self.assertRaises(ValueError):
            self.complete(idempotency_key='request-1', continued_record_id='different')
        self.assertEqual(len(self.repo.list_demo_completions(self.token)), 1)

    def test_repeatable_session_uniqueness(self):
        self.complete(idempotency_key='1', repeatable=True, event_session_key='2026-10-01')
        self.complete(idempotency_key='2', repeatable=True, event_session_key='2026-10-02')
        with self.assertRaises(ValueError):
            self.complete(idempotency_key='3', repeatable=True, event_session_key='2026-10-02')

    def test_session_isolation_and_revocation(self):
        self.complete(idempotency_key='1')
        second = self.repo.create_session(self.import_id, actor_role='employee', employee_id='E2')
        self.assertEqual(self.repo.list_demo_completions(second), [])
        self.repo.revoke_session(self.token)
        with self.assertRaises(PermissionError):
            self.repo.list_demo_completions(self.token)

    def test_auth_and_foreign_keys(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.create_session('missing', actor_role='hr')
        token = self.repo.create_session(self.import_id, actor_role='hr')
        with self.assertRaises(PermissionError):
            self.repo.record_demo_completion(token, event_id='EV_TEST', idempotency_key='1')
        with self.repo.connect() as connection:
            hashes = [row[0] for row in connection.execute('SELECT token_hash FROM sessions')]
            self.assertNotIn(self.token, hashes)
            connection.execute("UPDATE sessions SET expires_at = '2000-01-02', created_at = '2000-01-01'")
        with self.assertRaises(PermissionError):
            self.repo.get_session(self.token)

    def test_private_dataset_if_available(self):
        root = Path(__file__).resolve().parents[2]
        directory = next((path for path in (root / 'data', root / 'backend/Dataset')
                          if (path / 'employees.json').exists()), None)
        if directory is None:
            self.skipTest('Private starter kit is not installed')
        dataset = load_dataset(directory)
        self.assertTrue(dataset.employees_by_id)
        self.assertEqual(dataset.as_of_date.isoformat(), dataset.meta['as_of_date'])
        for records in dataset.history_by_employee.values():
            self.assertEqual(records, sorted(records, key=lambda row: (row['date'], row['record_id'])))


if __name__ == '__main__':
    unittest.main()
