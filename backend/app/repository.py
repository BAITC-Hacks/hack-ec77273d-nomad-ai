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
            if version not in (0, 1):
                raise ValueError(f'Unsupported database version: {version}')
            connection.execute('PRAGMA journal_mode = WAL')
            if version == 0:
                connection.executescript(Path(__file__).with_name('schema.sql').read_text())

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
