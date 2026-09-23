# Career Quest

NomadAi's HackAlem AI / Halyk Bank track demo: deterministic, explainable career development recommendations. FastAPI, Pydantic, a single Python process, in-memory data, and two plain HTML/vanilla-JavaScript pages. No database, frontend build, or npm installation.

## Run

Use Python 3.10 or newer. One-command setup and launch from this directory:

```sh
python run.py
```

The launcher creates `.venv`, installs requirements if missing (network needed on first run), and starts one local server. For manual setup:

```sh
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python check_data.py
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/employee.html> and <http://127.0.0.1:8000/hr.html>. Serve the pages through FastAPI rather than opening them as `file://` files. API docs: <http://127.0.0.1:8000/docs>. Run **one worker**; multiple workers would have separate datasets. No key is needed for the demo.

The four supplied files in `data/` were copied unchanged from `career_quest_dataset.zip`. They contain synthetic people: 200 employees, 60 skills, 32 role profiles, 40 events, and 2,743 activity records. The fixed snapshot is **2026-10-01**, regardless of the machine date. Set `CAREER_DATA_DIR` to load another directory at startup.

Tailwind loads through a CDN script tag. Small embedded CSS rules keep both pages usable without network access. There are no downloaded fonts or chart libraries.

## Demo sequence

1. Open the employee page; it starts on **E0005**, a Middle Backend Engineer targeting Senior.
2. Expand **Why this ranks here** to inspect actual gaps, gains, history counts, weights, and points.
3. Select **Mark complete**. Skill gains are applied, bars update, and the next recommendations are computed again.
4. Open HR overview to inspect critical gaps, event participation, and employees with no eligible activities.
5. Expand the judge-data section to load a replacement employee JSON and history CSV together.

Completions and reloads are in memory only. Restart to restore the files on disk. Repeat completion requests do not grant skills again. For recurring `EV_036`, the fixed snapshot allows one new completion per employee on that date; another request that day is idempotent.

## Scoring in plain language

`rank_events(employee, dataset)` in `scoring.py` is a pure function with no I/O, LLM, random values, or system-clock reads. It returns up to three eligible events, ordered by score and then event ID to break ties.

First filter to the employee's **current role and grade**, satisfied prerequisites, events never completed (except recurring EV_036), and no in-progress record. Mandatory events are excluded from recommendations, as specified by the dataset notes; they remain visible in history and HR participation. Empty role/grade lists match nobody. Session availability is displayed but does not change the specified eligibility rules.

The target is the employee's career goal. If there is no goal, use the **next grade in the same role**. Lead remains at Lead. The UI labels this inferred target. Missing skill levels are zero. The baseline is the supplied assessment plus completed activities dated after `last_review_date`, applied once in chronological order at load/reload using event gains and caps, as required by the starter-kit notes. Assessment values remain available in the profile API as `assessment_skills`. Same-day records are treated as already assessed. CSV dates are the only chronology available; self-paced assignment dates cannot establish an exact completion time.

For each developed target skill:

```text
gap = max(0, required - current)
effective_gain = min(event_gain, max(0, event_max_level - current))
gap_closure = min(gap, effective_gain)
```

| Factor | Raw value | Weight |
| --- | --- | --- |
| Critical-gap closure | Sum of gap closure for critical target skills | 100 |
| Requirement distance | Sum of gap × gap closure for all target skills | 10 |
| History fit | Up to 3 dated on-time completions of this event type, minus up to 5 declines/no-shows when their combined count is at least 2 | 5 |
| Grade urgency | Total critical skills below target, only when this event closes a critical gap | 3 |

The score is the sum of raw value × weight. This is a weighted heuristic, not a prediction or a promotion decision. Urgency is tied to critical closure so it can distinguish useful events for the same employee. Events that close no target gap are excluded. If none remain, the employee appears in the HR no-recommendation list. EV_036 completed on the snapshot date is also excluded until a future snapshot.

History fit uses the schema's **event type**, not delivery format. The on-time proxy requires a completed scheduled activity, a nonempty `due_date`, and `date <= due_date`. Records without due dates and self-paced records receive no on-time boost: an assignment date is not a completion timestamp. For this dataset that may leave history boosts at zero; declines/no-shows still affect ranking. Explanations explicitly describe neutral history rather than inventing positive evidence.

Progress is `100 × sum(min(current, required)) / sum(required)` over target requirements; no requirements means 100%. It is not a completed-course percentage. A completion never lowers a skill already above the event cap.

## Explanations

Without `OPENAI_API_KEY`, explanations are deterministic sentences generated from the factor breakdown. With a key, an OpenAI-compatible client makes **one call per returned recommendation**, after ranking. It receives only the selected title and factors, not employee names. Set `OPENAI_MODEL` (default `gpt-4o-mini`) and optionally `OPENAI_BASE_URL` for another compatible endpoint. Calls use a five-second client timeout and no retries. Up to three calls run concurrently under a shared seven-second response deadline; unfinished results fall back without delaying the response. Profile rendering is independent of explanation completion. Every result carries deterministic three-factor evidence (target grade, skill gap/gain, participation history), displayed alongside LLM wording. Missing key, failure, or empty output falls back to the template. The API labels the source as `template` or `llm`.

The LLM cannot alter event selection or scores. Live model output was not tested with a paid key; mocked success/failure and the actual no-key path are tested.

## API and judge reload

| Method and path | Behavior |
| --- | --- |
| `GET /employees` | Employee picker list |
| `GET /employees/{id}` | Profile, target requirements, progress, completed records |
| `GET /employees/{id}/recommendations` | Top 0–3 events, scores, factors, explanations |
| `POST /employees/{id}/activities/{event_id}/complete` | Apply capped gains and record completion |
| `GET /hr/overview?role=hr` | Critical gaps, participation rates, zero-eligible list |
| `POST /admin/reload-dataset?role=hr` | Validate and atomically replace employees/history |

Completion optionally accepts `?expected_version=N`; the UI uses this to reject stale recommendations after another mutation with HTTP 409. Unknown employee/event returns 404; an ineligible completion returns 409. In-progress activities may be completed directly, though they are not recommended. HR/admin calls without `role=hr` return 403.

The reload endpoint takes JSON (avoids a multipart dependency):

```json
{
  "employees": {"meta": {}, "employees": []},
  "activity_history_csv": "record_id,employee_id,event_id,date,due_date,status,completion_pct,score,feedback_rating,assigned_by\n"
}
```

Use the two file inputs on the HR page, or this standard-library script from the project directory:

```python
import json
from pathlib import Path
from urllib.request import Request, urlopen

body = {
    "employees": json.loads(Path("judge/employees.json").read_text(encoding="utf-8-sig")),
    "activity_history_csv": Path("judge/activity_history.csv").read_text(encoding="utf-8-sig"),
}
request = Request(
    "http://127.0.0.1:8000/admin/reload-dataset?role=hr",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
print(urlopen(request).read().decode())
```

Both replacements must use the original schema. IDs must be unique, skill/event/profile references valid, and manager IDs must exist in the replacement employee set. CSV nullable cells may be empty; history dates cannot exceed the snapshot. Validation failures return 422 and keep the previous dataset intact. Default `mode=replace` replaces the whole employee/history set, including demo completions. For a small additional judge set use `?role=hr&mode=merge` or choose Merge judge profiles in the HR form. Merge upserts supplied profiles, replaces history for those employee IDs, and retains all other employees/history. Managers may reference retained employees. Neither mode changes catalogs, writes files, or restarts the server.

HR critical-gap counts use target profiles. Decline/no-show rate uses all history rows for the event as its denominator, not distinct employees. The zero-eligible list includes overlapping exclusion counts to explain why an employee has no choices.

## Validation

```sh
python -m unittest discover -v
```

Tests use Python's standard library; no pytest or additional test client dependency. They cover the exact Public Speaking / System Design scenario, all real-data recommendation filters, recurring events, missing skills, headroom, API profiles/recommendations, completion/idempotency, stale versions, HR gating, atomic reload, and explanation success/failure/no-key behavior.

The demo gate `?role=hr` is deliberately not authentication. Real RBAC, durable storage, completion timestamps, and audited assessment updates are next steps. Run locally for this demo; no production deployment is included.

## Requirements audit

See [REQUIREMENTS_REVIEW.md](REQUIREMENTS_REVIEW.md) for the PDF comparison, corrected mismatches, and remaining demo limitations.
