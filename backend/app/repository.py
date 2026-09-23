"""Local state journal. Engine validation must precede demo completion writes."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4


def _now():
    return datetime.now(timezone.utc)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Repository:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise ValueError(f'Unsupported database version: {version}')
            connection.execute('PRAGMA journal_mode = WAL')
            if version == 0:
                connection.executescript(Path(__file__).with_name('schema.sql').read_text())
            elif version == 1:
                # Version 2 adds saved plan snapshots; keep existing journal rows.
                connection.execute('BEGIN IMMEDIATE')
                try:
                    connection.execute('''CREATE TABLE IF NOT EXISTS development_plans (
                        plan_id TEXT PRIMARY KEY,
                        import_id TEXT NOT NULL REFERENCES imports(import_id),
                        employee_id TEXT NOT NULL,
                        result_json TEXT NOT NULL CHECK(json_valid(result_json)),
                        created_at TEXT NOT NULL
                    )''')
                    connection.execute('PRAGMA user_version = 2')
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            if version in (1, 2):
                # All v3 changes are additive. schema.sql wraps them in one transaction.
                connection.executescript(Path(__file__).with_name('schema.sql').read_text())

    def progress_state(self, import_id, employee_id):
        with self.connect() as connection:
            row = connection.execute(
                'SELECT target_json, revision FROM employee_state WHERE import_id=? AND employee_id=?',
                (import_id, employee_id)).fetchone()
            completions = [dict(r) for r in connection.execute(
                'SELECT * FROM employee_progress WHERE import_id=? AND employee_id=? '
                'ORDER BY created_at, completion_id', (import_id, employee_id))]
        return {'target': json.loads(row['target_json']) if row and row['target_json'] else None,
                'revision': row['revision'] if row else 0, 'completions': completions}

    def set_target(self, import_id, employee_id, target, expected_revision):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('INSERT OR IGNORE INTO employee_state(import_id, employee_id) VALUES (?, ?)',
                               (import_id, employee_id))
            revision = connection.execute('SELECT revision FROM employee_state WHERE import_id=? AND employee_id=?',
                                          (import_id, employee_id)).fetchone()[0]
            if revision != expected_revision:
                raise ValueError('revision_conflict')
            connection.execute('UPDATE employee_state SET target_json=?, revision=revision+1 '
                               'WHERE import_id=? AND employee_id=?', (_json(target), import_id, employee_id))

    def complete_employee(self, import_id, employee_id, *, event_id, idempotency_key,
                          expected_revision, as_of_date, repeatable=False,
                          event_session_key=None, continued_record_id=None):
        occurrence = f'session:{event_session_key}' if repeatable else 'once'
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute('SELECT * FROM employee_progress WHERE import_id=? '
                'AND employee_id=? AND idempotency_key=?', (import_id, employee_id, idempotency_key)).fetchone()
            if existing:
                if existing['event_id'] != event_id:
                    raise ValueError('idempotency_conflict')
                return dict(existing)
            connection.execute('INSERT OR IGNORE INTO employee_state(import_id, employee_id) VALUES (?, ?)',
                               (import_id, employee_id))
            revision = connection.execute('SELECT revision FROM employee_state WHERE import_id=? AND employee_id=?',
                                          (import_id, employee_id)).fetchone()[0]
            if revision != expected_revision:
                raise ValueError('revision_conflict')
            if repeatable and not event_session_key:
                raise ValueError('missing_event_session')
            completion_id = uuid4().hex
            try:
                connection.execute('INSERT INTO employee_progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (completion_id, import_id, employee_id, event_id, occurrence, event_session_key,
                     continued_record_id, as_of_date, idempotency_key, _now().isoformat()))
            except sqlite3.IntegrityError as exc:
                raise ValueError('already_completed') from exc
            connection.execute('UPDATE employee_state SET revision=revision+1 WHERE import_id=? AND employee_id=?',
                               (import_id, employee_id))
            return dict(connection.execute('SELECT * FROM employee_progress WHERE completion_id=?',
                                           (completion_id,)).fetchone())

    def get_setting(self, key):
        with self.connect() as connection:
            row = connection.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
            return row[0] if row else None

    def set_setting(self, key, value):
        with self.connect() as connection:
            connection.execute('INSERT INTO app_settings VALUES (?, ?) ON CONFLICT(key) '
                               'DO UPDATE SET value=excluded.value', (key, value))

    def rebind_session(self, token, import_id, employee_id=None):
        with self.connect() as connection:
            connection.execute('UPDATE sessions SET import_id=?, employee_id=? WHERE token_hash=?',
                               (import_id, employee_id, _hash(token)))

    def save_plan(self, import_id, plan):
        plan_id = uuid4().hex
        with self.connect() as connection:
            connection.execute('INSERT INTO development_plans VALUES (?, ?, ?, ?, ?)',
                               (plan_id, import_id, plan['employee_id'], _json(plan), _now().isoformat()))
        return self.get_plan(plan_id)

    def get_plan(self, plan_id):
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM development_plans WHERE plan_id=?', (plan_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['result'] = json.loads(result.pop('result_json'))
        return result

    def list_plans(self, employee_id, import_id):
        with self.connect() as connection:
            rows = connection.execute('SELECT plan_id FROM development_plans WHERE employee_id=? AND import_id=? '
                                      'ORDER BY created_at', (employee_id, import_id)).fetchall()
        return [self.get_plan(row[0]) for row in rows]

    def register_import(self, *, as_of_date, manifest, counts):
        """Register a successfully validated dataset, storing only hashes/counts.

        manifest maps source filenames to SHA-256 digests. No profiles or paths.
        as_of_date MUST come from skills.json/meta, never the wall clock.
        """
        date.fromisoformat(as_of_date)
        if not manifest or any(
            Path(name).name != name or '/' in name or '\\' in name
            or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)
            for name, digest in manifest.items()
        ):
            raise ValueError('Manifest requires filenames and lowercase SHA-256 hashes')
        if not counts or any(type(n) is not int or n < 0 for n in counts.values()):
            raise ValueError('Counts must be nonnegative integers')
        manifest_json = _json(manifest)
        counts_json = _json(counts)
        fingerprint = _hash(_json([as_of_date, manifest, counts]))
        with self.connect() as connection:
            connection.execute(
                'INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(fingerprint) DO NOTHING',
                (uuid4().hex, fingerprint, as_of_date, manifest_json, counts_json,
                 _now().isoformat()),
            )
            return dict(connection.execute(
                'SELECT * FROM imports WHERE fingerprint = ?', (fingerprint,)
            ).fetchone())

    def create_session(self, import_id, *, actor_role, employee_id=None, ttl_hours=24):
        """Call only after authentication and checking employee exists in import."""
        if actor_role not in ('employee', 'hr') or (
            actor_role == 'employee' and not employee_id
        ) or ttl_hours <= 0:
            raise ValueError('Invalid session role, employee or lifetime')
        token = secrets.token_urlsafe(32)
        now = _now()
        with self.connect() as connection:
            connection.execute('INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, NULL)',
                               (uuid4().hex, _hash(token), import_id, actor_role, employee_id,
                                now.isoformat(), (now + timedelta(hours=ttl_hours)).isoformat()))
        return token

    @staticmethod
    def _session(connection, token):
        row = connection.execute(
            'SELECT * FROM sessions WHERE token_hash = ? AND revoked_at IS NULL '
            'AND expires_at > ?', (_hash(token), _now().isoformat())
        ).fetchone()
        if row is None:
            raise PermissionError('Invalid or expired session')
        return row

    def get_session(self, token):
        with self.connect() as connection:
            session = dict(self._session(connection, token))
            session.pop('token_hash')
            return session

    def revoke_session(self, token):
        with self.connect() as connection:
            connection.execute('UPDATE sessions SET revoked_at = ? WHERE token_hash = ?',
                               (_now().isoformat(), _hash(token)))

    def record_demo_completion(self, token, *, event_id, idempotency_key,
                               repeatable=False, event_session_key=None,
                               continued_record_id=None):
        """Persist an engine-approved completion at the dataset's as_of_date.

        Caller checks eligibility, existing source history, mandatory policy, and
        supplies repeatable from catalog policy (not from employee/UI input).
        Historical self_paced dates are not changed or given completion_date.
        Effects are replayed by engine; this method never updates skill levels.
        """
        if not event_id or not idempotency_key or (repeatable and not event_session_key):
            raise ValueError('Event, idempotency key and repeatable session are required')
        occurrence = f'session:{event_session_key}' if repeatable else 'once'
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            session = self._session(connection, token)
            if session['actor_role'] != 'employee':
                raise PermissionError('Only employee sessions can record demo completions')
            existing = connection.execute(
                'SELECT * FROM demo_completions WHERE session_id = ? AND idempotency_key = ?',
                (session['session_id'], idempotency_key),
            ).fetchone()
            if existing:
                if (existing['event_id'], existing['occurrence_key'],
                    existing['event_session_key'], existing['continued_record_id']) != (
                    event_id, occurrence, event_session_key, continued_record_id
                ):
                    raise ValueError('Idempotency key already used for another request')
                return dict(existing)
            as_of_date = connection.execute('SELECT as_of_date FROM imports WHERE import_id = ?',
                                            (session['import_id'],)).fetchone()[0]
            completion_id = uuid4().hex
            try:
                connection.execute('INSERT INTO demo_completions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                                   (completion_id, session['session_id'], event_id, occurrence,
                                    event_session_key, continued_record_id, as_of_date,
                                    idempotency_key, _now().isoformat()))
            except sqlite3.IntegrityError as exc:
                raise ValueError('Event or catalog session already completed in this demo') from exc
            return dict(connection.execute('SELECT * FROM demo_completions WHERE completion_id = ?',
                                           (completion_id,)).fetchone())

    def list_demo_completions(self, token):
        with self.connect() as connection:
            session = self._session(connection, token)
            return [dict(row) for row in connection.execute(
                'SELECT * FROM demo_completions WHERE session_id = ? ORDER BY date, completion_id',
                (session['session_id'],),
            )]
