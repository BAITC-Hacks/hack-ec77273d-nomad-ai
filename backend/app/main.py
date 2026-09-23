"""FastAPI read API for the private starter kit and SQLite journal."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from .engine.data_loader import load_dataset
from .repository import Repository
from .schemas import HealthResponse, ImportResponse


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


@app.get("/api/employees/{employee_id}")
def get_employee(employee_id: str):
    employee = dataset.employees_by_id.get(employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


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
