-- Source employees, events, skills and history remain in read-only JSON/CSV.
-- Source IDs are external references; they cannot have SQLite foreign keys.
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS imports (
    import_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE CHECK(length(fingerprint) = 64),
    as_of_date TEXT NOT NULL CHECK(length(as_of_date) = 10),
    manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
    counts_json TEXT NOT NULL CHECK(json_valid(counts_json)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash) = 64),
    import_id TEXT NOT NULL REFERENCES imports(import_id),
    actor_role TEXT NOT NULL CHECK(actor_role IN ('employee', 'hr')),
    employee_id TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    CHECK(actor_role != 'employee' OR employee_id IS NOT NULL),
    CHECK(expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);

-- Each login has its own demo overlay. Never change source skills/grade/history.
CREATE TABLE IF NOT EXISTS demo_completions (
    completion_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    event_id TEXT NOT NULL,
    occurrence_key TEXT NOT NULL,
    event_session_key TEXT,
    continued_record_id TEXT,
    date TEXT NOT NULL CHECK(length(date) = 10),
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, idempotency_key),
    UNIQUE(session_id, event_id, occurrence_key)
);
CREATE INDEX IF NOT EXISTS demo_history_order
    ON demo_completions(session_id, date, completion_id);

-- Saved plan snapshots are additive; source employee data remains external.
CREATE TABLE IF NOT EXISTS development_plans (
    plan_id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL REFERENCES imports(import_id),
    employee_id TEXT NOT NULL,
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employee_state (
    import_id TEXT NOT NULL REFERENCES imports(import_id),
    employee_id TEXT NOT NULL,
    target_json TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(import_id, employee_id)
);
CREATE TABLE IF NOT EXISTS employee_progress (
    completion_id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL REFERENCES imports(import_id),
    employee_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    occurrence_key TEXT NOT NULL,
    event_session_key TEXT,
    continued_record_id TEXT,
    date TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(import_id, employee_id, idempotency_key),
    UNIQUE(import_id, employee_id, event_id, occurrence_key)
);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

PRAGMA user_version = 3;
COMMIT;
