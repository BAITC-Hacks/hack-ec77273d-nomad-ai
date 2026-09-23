"""FastAPI read API for the private starter kit and SQLite journal."""

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from dotenv import load_dotenv

from .engine.data_loader import load_dataset
from .repository import Repository
from .schemas import HealthResponse, ImportResponse

# Load project-local provider settings before the AI adapter reads os.environ.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

from .engine.ai_layer import rank_and_explain
from .engine.planner import generate_plan


def _paths():
    # app/main.py -> backend -> repository root
    backend_dir = Path(__file__).resolve().parents[1]
    repo_root = backend_dir.parent
    configured_data = os.getenv("DATA_DIR")
    data_dir = Path(configured_data) if configured_data else repo_root / "data"
    if not all((data_dir / name).is_file() for name in (
        "employees.json", "events.json", "skills.json", "activity_history.csv"
    )):
        fallback = backend_dir / "Dataset"
        if all((fallback / name).is_file() for name in (
            "employees.json", "events.json", "skills.json", "activity_history.csv"
        )):
            data_dir = fallback
    db_path = Path(os.getenv("DATABASE_PATH", str(repo_root / "var" / "nomad.sqlite3")))
    return data_dir, db_path


data_dir, database_path = _paths()
repository = Repository(database_path)
app = FastAPI(title="Nomad AI API", version="1.0.0")


def _portal():
    return FileResponse(Path(__file__).with_name("static") / "portal.html")


@app.get("/", include_in_schema=False)
@app.get("/employee", include_in_schema=False)
@app.get("/hr", include_in_schema=False)
@app.get("/boss", include_in_schema=False)
def portal():
    return _portal()


class AITestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    grade: str
    target: dict[str, Any]
    facts: list[dict[str, str]]
    routes: list[dict[str, str]]
    rerankable_route_ids: list[str]
    employee_id: str | None = None
    revision: int | None = None


@app.get("/admin/ai")
def ai_admin_console():
    return FileResponse(Path(__file__).with_name("static") / "ai_admin.html")


@app.post("/api/admin/ai-test")
def ai_admin_test(payload: AITestRequest, request: Request):
    # This development console runs on loopback. Do not expose the prototype API publicly.
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Admin console is local only")
    facts = payload.model_dump(exclude_none=True)
    result = rank_and_explain(facts)
    return result


@app.on_event("startup")
def startup():
    """Initialize SQLite and validate/register the source kit at API startup."""
    repository.initialize()
    global dataset, current_import
    try:
        dataset = load_dataset(data_dir)
        current_import = repository.register_import(
            as_of_date=dataset.as_of_date.isoformat(),
            manifest=dataset.manifest,
            counts=dataset.counts,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Dataset files are missing from '{data_dir}'. Set DATA_DIR or place the "
            "four starter-kit files in data/ or backend/Dataset/."
        ) from exc


@app.get("/api/health", response_model=HealthResponse)
def health():
    if "dataset" not in globals():
        raise HTTPException(status_code=503, detail="Dataset has not loaded")
    return HealthResponse(status="ok", as_of_date=dataset.as_of_date.isoformat(),
                          counts=dataset.counts)


@app.get("/api/import", response_model=ImportResponse)
def get_import():
    if "current_import" not in globals():
        raise HTTPException(status_code=503, detail="Dataset has not loaded")
    return ImportResponse(import_id=current_import["import_id"],
                          as_of_date=current_import["as_of_date"],
                          counts=dataset.counts)


@app.get("/api/employees")
def list_employees(q: str | None = None, limit: int = Query(50, ge=1, le=200),
                   offset: int = Query(0, ge=0)):
    rows = list(dataset.employees_by_id.values())
    if q:
        needle = q.casefold()
        rows = [row for row in rows if needle in str(row.get("employee_id", "")).casefold()
                or needle in str(row.get("name", "")).casefold()
                or needle in str(row.get("role", "")).casefold()]
    return {"total": len(rows), "items": rows[offset:offset + limit]}


@app.get("/api/hr/overview")
def hr_overview():
    employees = list(dataset.employees_by_id.values())
    departments = {}
    grades = {}
    roles = {}
    for row in employees:
        department = row.get("department", "Unknown")
        departments[department] = departments.get(department, 0) + 1
        grades[row["grade"]] = grades.get(row["grade"], 0) + 1
        roles[row["role"]] = roles.get(row["role"], 0) + 1
    by_event = {}
    for employee_id, history in dataset.history_by_employee.items():
        for record in history:
            item = by_event.setdefault(record["event_id"], {"participants": set(), "completed": 0,
                                                              "records": 0})
            item["participants"].add(employee_id)
            item["records"] += 1
            item["completed"] += record["status"] == "completed"
    activities = []
    for event_id, event in dataset.events_by_id.items():
        activity = by_event.get(event_id, {"participants": set(), "completed": 0, "records": 0})
        activities.append({"event_id": event_id, "title": event["title"],
                           "participants": len(activity["participants"]),
                           "completed": activity["completed"], "records": activity["records"],
                           "mandatory": event["mandatory"]})
    return {
        "as_of_date": dataset.as_of_date.isoformat(), "total_employees": len(employees),
        "total_events": len(dataset.events_by_id), "departments": departments,
        "grades": grades, "roles": roles,
        "activities": sorted(activities, key=lambda x: (-x["participants"], x["event_id"]))[:8],
    }


@app.get("/api/employees/{employee_id}")
def get_employee(employee_id: str):
    employee = dataset.employees_by_id.get(employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@app.get("/api/employees/{employee_id}/routes")
def employee_routes(employee_id: str):
    if employee_id not in dataset.employees_by_id:
        raise HTTPException(status_code=404, detail="Employee not found")
    plan = generate_plan(dataset, employee_id)
    employee = dataset.employees_by_id[employee_id]
    candidates = plan.pop("_candidate_routes", [])[:3]
    if not candidates:
        return {"employee": {"employee_id": employee_id,
                             "full_name": employee.get("full_name", employee_id),
                             "department": employee.get("department"), "role": employee["role"],
                             "grade": employee["grade"]},
                "target": plan["target"], "routes": [], "ai": {
                    "mode": "fallback", "fallback_reason": "no_available_route"},
                "no_route_reason": plan["status"]}

    route_rows = []
    for index, candidate in enumerate(candidates, 1):
        steps = candidate["steps"]
        route_id = f"route_{index}"
        route_rows.append({
            "route_id": route_id,
            "title": steps[0]["title"],
            "summary": " → ".join(step["title"] for step in steps)[:300],
            "courses": [{"event_id": step["event_id"], "title": step["title"],
                         "format": step["format"], "duration_hours": step["duration_hours"],
                         "start_date": step["start_date"],
                         "estimated_end_date": step["estimated_end_date"],
                         "action": step["action"], "skill_changes": step["skill_changes"]}
                        for step in steps],
            "utility": candidate["utility"],
        })

    gaps = plan["before"].get("gaps", {})
    gap_facts = []
    for skill_id, amount in sorted(gaps.items(), key=lambda pair: (-pair[1], pair[0])):
        if amount <= 0:
            continue
        skill_name = dataset.skills_by_id.get(skill_id, {}).get("name", skill_id)
        gap_facts.append({"fact_id": f"gap_{skill_id}", "category": "gap",
                          "text": f"Есть разрыв по навыку {skill_name}."})
        if len(gap_facts) == 2:
            break
    if not gap_facts:
        gap_facts.append({"fact_id": "gap_none", "category": "gap",
                          "text": "По рассчитанным требованиям целевого профиля разрывов нет."})
    history = [row for row in dataset.history_by_employee.get(employee_id, [])
               if row["date"] <= dataset.as_of_date.isoformat()]
    completed_count = sum(row["status"] == "completed" for row in history)
    history_text = (f"В истории {completed_count} завершённых записей о развитии."
                    if history else "История участия ограничена; причин пропусков нет в данных.")
    evidence = [
        {"fact_id": "grade", "category": "grade", "text": f"Текущий грейд — {employee['grade']}."},
        {"fact_id": "target", "category": "target",
         "text": f"Целевая роль — {plan['target']['target_role']}, грейд {plan['target']['target_grade']}."},
        *gap_facts,
        {"fact_id": "history_aggregate", "category": "history", "text": history_text},
    ]
    ai_result = rank_and_explain({
        "employee_id": employee_id, "revision": 1, "role": employee["role"],
        "grade": employee["grade"], "target": plan["target"], "facts": evidence,
        "routes": [{key: route[key] for key in ("route_id", "title", "summary")}
                   for route in route_rows],
        "rerankable_route_ids": [route["route_id"] for route in route_rows],
    })
    explanations = {row["route_id"]: row for row in ai_result["explanations"]}
    by_id = {route["route_id"]: route for route in route_rows}
    ordered = []
    for route_id in ai_result["ordered_route_ids"]:
        route = by_id[route_id]
        route["explanation"] = explanations.get(route_id, {}).get(
            "text", "Маршрут сформирован по данным навыков и доступных занятий.")
        ordered.append(route)
    return {
        "employee": {"employee_id": employee_id,
                     "full_name": employee.get("full_name", employee_id),
                     "department": employee.get("department"), "role": employee["role"],
                     "grade": employee["grade"]},
        "target": plan["target"], "coverage": plan["before"], "routes": ordered,
        "ai": {"mode": ai_result["mode"], "fallback_reason": ai_result["fallback_reason"]},
        "as_of_date": plan["as_of_date"],
    }


@app.get("/api/employees/{employee_id}/history")
def get_employee_history(employee_id: str, status: str | None = None,
                         limit: int = Query(100, ge=1, le=1000)):
    if employee_id not in dataset.employees_by_id:
        raise HTTPException(status_code=404, detail="Employee not found")
    records = dataset.history_by_employee.get(employee_id, [])
    if status:
        records = [row for row in records if row["status"] == status]
    return {"total": len(records), "items": records[:limit]}


@app.get("/api/events")
def list_events():
    return {"total": len(dataset.events_by_id), "items": list(dataset.events_by_id.values())}


@app.get("/api/events/{event_id}")
def get_event(event_id: str):
    event = dataset.events_by_id.get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.get("/api/skills")
def list_skills():
    return {"total": len(dataset.skills_by_id), "items": list(dataset.skills_by_id.values()),
            "proficiency_scale": dataset.proficiency_scale}


@app.get("/api/role-profiles")
def list_role_profiles(role: str | None = None, grade: str | None = None):
    rows = list(dataset.role_profiles_by_key.values())
    if role:
        rows = [row for row in rows if row["role"] == role]
    if grade:
        rows = [row for row in rows if str(row["grade"]) == grade]
    return {"total": len(rows), "items": rows}
