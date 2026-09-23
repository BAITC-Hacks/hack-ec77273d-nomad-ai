from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from uuid import uuid4
from typing import Literal
import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from models import Activity, EmployeesFile, Model
from loaders import DEFAULT_DATA, TODAY, load_dataset, replacement_dataset
from scoring import progress, rank_events, rejection_reasons, recommendation_rejections, skill_rows, target_profile, target_source
from explanations import explain_many

class ReloadRequest(Model):
    employees: EmployeesFile
    activity_history_csv: str

def create_app(data_dir=DEFAULT_DATA):
    lock = RLock()
    @asynccontextmanager
    async def lifespan(app):
        app.state.data = load_dataset(Path(data_dir))
        app.state.version = 1
        yield

    app = FastAPI(title='Career Quest · NomadAi', lifespan=lifespan)

    def employee_or_404(employee_id):
        employee = app.state.data.employees.get(employee_id)
        if employee is None:
            raise HTTPException(404, 'Employee not found')
        return employee

    def require_hr(role):
        if role != 'hr':
            raise HTTPException(403, 'Demo HR role required: ?role=hr')

    def profile_payload(employee):
        data = app.state.data
        target = target_profile(employee, data)
        return {'profile': employee.model_dump(mode='json'), 'snapshot_date': TODAY.isoformat(),
                'dataset_version': app.state.version,
                'target': {'role': target.role, 'grade': target.grade,
                           'source': target_source(employee)},
                'assessment_skills': data.assessment_skills[employee.employee_id],
                'skills': skill_rows(employee, data), 'overall_progress_pct': progress(employee, data),
                'completed_activities': [{**r.model_dump(mode='json'), 'title': data.events[r.event_id].title}
                                         for r in data.history_by_employee[employee.employee_id] if r.status == 'completed']}

    @app.get('/employees')
    def employees():
        with lock:
            return [{'employee_id': e.employee_id, 'full_name': e.full_name} for e in app.state.data.employees.values()]

    @app.get('/employees/{employee_id}')
    def employee_profile(employee_id: str):
        with lock:
            return profile_payload(employee_or_404(employee_id))

    @app.get('/employees/{employee_id}/recommendations')
    def recommendations(employee_id: str):
        with lock:
            results = rank_events(employee_or_404(employee_id), app.state.data)
            version = app.state.version
        return {'employee_id': employee_id, 'dataset_version': version,
                'recommendations': explain_many(results)}

    @app.post('/employees/{employee_id}/activities/{event_id}/complete')
    def complete(employee_id: str, event_id: str, expected_version: int | None = Query(default=None)):
        with lock:
            if expected_version is not None and expected_version != app.state.version:
                raise HTTPException(409, 'Dataset changed; refresh before completing an activity')
            data = app.state.data
            employee = employee_or_404(employee_id)
            event = data.events.get(event_id)
            if event is None:
                raise HTTPException(404, 'Event not found')
            history = data.history_by_employee[employee_id]
            completed = [r for r in history if r.event_id == event_id and r.status == 'completed']
            if completed and (event_id != 'EV_036' or any(r.date == TODAY for r in completed)):
                return {'already_completed': True, **profile_payload(employee)}
            reasons = rejection_reasons(employee, event, data, allow_in_progress=True)
            if reasons:
                raise HTTPException(409, {'message': 'Activity is not eligible', 'reasons': reasons})
            for development in event.develops_skills:
                current = employee.skills.get(development.skill_id, 0)
                employee.skills[development.skill_id] = current + min(development.gain, max(0, development.max_level - current))
            active = [r for r in history if r.event_id == event_id and r.status == 'in_progress']
            if active:
                for record in active:
                    record.status = 'completed'
                    record.completion_pct = 100
                    record.date = TODAY
            else:
                record = Activity(record_id='DEMO_' + uuid4().hex, employee_id=employee_id, event_id=event_id,
                    date=TODAY, due_date=None, status='completed', completion_pct=100,
                    score=None, feedback_rating=None, assigned_by='self')
                data.history.append(record)
                history.append(record)
            app.state.version += 1
            return {'already_completed': False, **profile_payload(employee)}

    @app.get('/hr/overview')
    def overview(role: str = 'employee'):
        require_hr(role)
        with lock:
            data = app.state.data
            gaps, levels = Counter(), Counter()
            at_risk = []
            for employee in data.employees.values():
                rows = skill_rows(employee, data)
                for row in rows:
                    if row['critical'] and row['gap']:
                        gaps[row['skill_id']] += 1
                        levels[row['skill_id']] += row['gap']
                if not rank_events(employee, data):
                    exclusions = Counter()
                    for event in data.events.values():
                        if not event.mandatory:
                            exclusions.update(recommendation_rejections(employee, event, data))
                    at_risk.append({'employee_id': employee.employee_id, 'full_name': employee.full_name,
                                    'critical_gap_count': sum(r['critical'] and r['gap'] > 0 for r in rows),
                                    'exclusion_counts': dict(exclusions)})
            names = {s.skill_id: s.name for s in data.skills_file.skills}
            by_event = {eid: Counter() for eid in data.events}
            for row in data.history:
                by_event[row.event_id][row.status] += 1
            participation = []
            for eid, counts in by_event.items():
                total = sum(counts.values())
                participation.append({'event_id': eid, 'title': data.events[eid].title, 'total_records': total,
                    'completed': counts['completed'], 'declined': counts['declined'], 'no_show': counts['no_show'],
                    'decline_no_show_rate_pct': round(100 * (counts['declined'] + counts['no_show']) / total, 1) if total else 0})
            return {'employee_count': len(data.employees), 'snapshot_date': TODAY.isoformat(),
                    'critical_gaps': [{'skill_id': sid, 'name': names[sid], 'employee_count': count,
                                       'total_missing_levels': levels[sid]} for sid, count in gaps.most_common()],
                    'event_participation': participation, 'zero_eligible_recommendations': at_risk}

    @app.post('/admin/reload-dataset')
    def reload_dataset(body: ReloadRequest, role: str = 'employee', mode: Literal['replace', 'merge'] = 'replace'):
        require_hr(role)
        with lock:
            try:
                new_data = replacement_dataset(app.state.data, body.employees, body.activity_history_csv, merge=mode == 'merge')
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            app.state.data = new_data
            app.state.version += 1
            return {'employees': len(new_data.employees), 'history_records': len(new_data.history),
                    'dataset_version': app.state.version}

    @app.get('/', include_in_schema=False)
    @app.get('/employee.html', include_in_schema=False)
    def employee_page():
        return FileResponse(Path(__file__).parent / 'static' / 'employee.html')

    @app.get('/hr.html', include_in_schema=False)
    def hr_page():
        return FileResponse(Path(__file__).parent / 'static' / 'hr.html')

    return app

app = create_app(os.getenv('CAREER_DATA_DIR', str(DEFAULT_DATA)))
