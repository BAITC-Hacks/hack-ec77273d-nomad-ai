# Nomad AI — local database

SQLite is a local journal of sessions, validated imports and demo completions.
The private JSON/CSV starter kit remains the source of employees, events, skills,
role requirements and participation history. No external database service or
third-party Python dependency is needed for this storage layer.

## Initialize

Run from either the repository root or the `backend` directory (Python 3.10+).
From the repository root:

```powershell
python scripts/init_db.py
python scripts/init_db.py --data-dir data
python -m unittest discover -s backend/tests -v
```

From `backend`:

```powershell
python scripts/init_db.py
python scripts/init_db.py --data-dir Dataset
python -m unittest discover -s tests -t .. -v
```

The first command creates `var/nomad.sqlite3`. The second validates the starter
kit and records its SHA-256 manifest, counts and snapshot date. Repeating either
command is safe. `--database` or the `DATABASE_PATH` environment variable changes
the SQLite path. `.env.example` documents configuration; this script does not
automatically load `.env`.

Obtain the starter kit from the hackathon organizers inside the private working
environment and put these four files in `data/`: `employees.json`, `events.json`,
`skills.json`, `activity_history.csv`. If `data/` is empty and all four files are
already in `backend/Dataset`, `--data-dir data` automatically uses `backend/Dataset`.
You can also pass `--data-dir backend/Dataset` explicitly.
Do not publish these source files or send full profiles to external services.
The source kit cannot be reproduced outside the event without organizer access.

## Storage

| Table | Purpose |
| --- | --- |
| `imports` | Dataset fingerprint, file hashes, counts, `meta.as_of_date` |
| `sessions` | Hashed opaque tokens, employee/HR role, dataset binding, expiry/revocation |
| `demo_completions` | Session-local completion overlay, scheduled session reference, continuation record, retry key |

Schema: `backend/app/schema.sql`; Python access: `backend/app/repository.py`.
Connections enable foreign keys, transactions and a busy timeout; initialization
enables WAL. Schema version is `PRAGMA user_version = 1`; unknown versions fail.
Tokens are returned once and only their SHA-256 hash is stored. Wall-clock UTC
is used for token expiry and audit timestamps, never for learning calculations.
Demo completions use the import snapshot date (`2026-10-01` for this kit).
Each login has its own demo overlay; logging in again starts a separate demo.

`load_dataset(path)` in `backend/app/engine/data_loader.py` unwraps source files,
validates identifiers and references and builds `employees_by_id`, `events_by_id`,
`role_profiles_by_key`, `skills_by_id`, `history_by_employee`. History is ordered
by `(date, record_id)`; missing optional feedback stays `None`. Counts are checked
by inspection, not hardcoded, so additional evaluation profiles can be loaded.
The base kit contains 200 employees, 40 events, 60 skills, 32 profiles and 2743
history records.

## Start the API

Install the backend dependencies and run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe scripts\run.py
```

The API starts at `http://127.0.0.1:8000`; interactive API docs are at
`http://127.0.0.1:8000/docs`. Startup initializes `var/nomad.sqlite3`, validates
the source kit, and registers its hashes/counts and `meta.as_of_date`. Set
`DATA_DIR` or `DATABASE_PATH` to use different local paths. The API loads data
from `data/`, falling back to `backend/Dataset/` when the four required files
are present there.

Read endpoints include `/api/health`, `/api/import`, `/api/employees`,
`/api/employees/{employee_id}`, `/api/employees/{employee_id}/history`,
`/api/events`, `/api/events/{event_id}`, `/api/skills` and `/api/role-profiles`.
This prototype API is for the closed local hackathon environment; do not expose
it publicly because these endpoints return employee profile and history data.

## Integration boundary

This is the database/data-loading layer, not the recommendation engine or REST API.
Before creating a session, the API must authenticate the actor and verify the
employee belongs to the selected dataset. Before writing a demo completion, the
engine must validate catalog eligibility, source completions, used sessions and
the latest in-progress record. Pass catalog repeatability to the repository;
`REPEATABLE_EVENT_IDS` represents the README's EV_036 catalog exception.
Never accept repeatability or another employee's identity from an untrusted UI.
SQLite prevents duplicate demo completions/retries; source-history duplication
must be checked by the engine. The repository does not award points or change grade.

For engine integration: missing skill means 0; source levels are at last review.
Replay only completed rows after review and on/before `as_of_date`, ordered by
`(date, record_id)`. Apply each catalog effect as
`max(before, min(before + gain, max_level))`. Self-paced history uses enrollment
date as an explicitly approximate completion timeline; do not invent
`completion_date`. Mandatory completions can affect skills but never enter
recommendations, engagement or points. Coverage, friction, scheduling and route
utility are derived by the engine, not persisted as authoritative employee data.

The private starter kit and SQLite files are ignored by Git. Ignore rules do not
remove files that were already staged or tracked; check `git diff --cached --name-only`
before publishing.
