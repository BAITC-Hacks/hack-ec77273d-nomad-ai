"""Read-only catalog endpoints backed by the live application dataset."""

import json

from fastapi import APIRouter, HTTPException, Query, Request


router = APIRouter(prefix="/api")


def _data(request: Request):
    return request.app.state.data


@router.get("/health")
def health(request: Request):
    data = _data(request)
    return {
        "status": "ok",
        "as_of_date": data.as_of_date.isoformat(),
        "dataset_version": request.app.state.version,
        "counts": {
            "employees": len(data.employees),
            "events": len(data.events),
            "skills": len(data.skills_file.skills),
            "role_profiles": len(data.skills_file.role_profiles),
            "history": len(data.history),
        },
    }


@router.get("/import")
def current_import(request: Request):
    record = request.app.state.import_record
    return {
        "import_id": record["import_id"],
        "as_of_date": record["as_of_date"],
        "counts": json.loads(record["counts_json"]),
        "dataset_version": request.app.state.version,
        "source": "startup_files",
    }


@router.get("/employees")
def employees(request: Request, q: str | None = None,
              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    rows = list(_data(request).employees.values())
    if q:
        needle = q.casefold()
        rows = [row for row in rows if needle in (
            row.employee_id + " " + row.full_name + " " + row.role
        ).casefold()]
    return {"total": len(rows), "items": [
        row.model_dump(mode="json") for row in rows[offset:offset + limit]
    ]}


@router.get("/employees/{employee_id}")
def employee(request: Request, employee_id: str):
    row = _data(request).employees.get(employee_id)
    if row is None:
        raise HTTPException(404, "Employee not found")
    return row.model_dump(mode="json")


@router.get("/employees/{employee_id}/history")
def employee_history(request: Request, employee_id: str, status: str | None = None,
                     limit: int = Query(100, ge=1, le=1000)):
    data = _data(request)
    if employee_id not in data.employees:
        raise HTTPException(404, "Employee not found")
    rows = data.history_by_employee[employee_id]
    if status:
        rows = [row for row in rows if row.status == status]
    return {"total": len(rows), "items": [
        row.model_dump(mode="json") for row in rows[:limit]
    ]}


@router.get("/events")
def events(request: Request):
    rows = list(_data(request).events.values())
    return {"total": len(rows), "items": [
        row.model_dump(mode="json") for row in rows
    ]}


@router.get("/events/{event_id}")
def event(request: Request, event_id: str):
    row = _data(request).events.get(event_id)
    if row is None:
        raise HTTPException(404, "Event not found")
    return row.model_dump(mode="json")


@router.get("/skills")
def skills(request: Request):
    source = _data(request).skills_file
    return {
        "total": len(source.skills),
        "items": [row.model_dump(mode="json") for row in source.skills],
        "proficiency_scale": source.proficiency_scale,
    }


@router.get("/role-profiles")
def role_profiles(request: Request, role: str | None = None, grade: str | None = None):
    rows = _data(request).skills_file.role_profiles
    if role:
        rows = [row for row in rows if row.role == role]
    if grade:
        rows = [row for row in rows if row.grade == grade]
    return {"total": len(rows), "items": [
        row.model_dump(mode="json") for row in rows
    ]}
