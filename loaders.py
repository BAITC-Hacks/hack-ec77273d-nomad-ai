import csv
import io
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from models import Activity, Employee, EmployeesFile, Event, EventsFile, SkillsFile

TODAY = date(2026, 10, 1)
DEFAULT_DATA = Path(__file__).parent / 'data'

def unique(items, key, label):
    result = {}
    for item in items:
        value = key(item)
        if value in result:
            raise ValueError(f'Duplicate {label}: {value}')
        result[value] = item
    return result

@dataclass
class Dataset:
    skills_file: SkillsFile
    employees_file: EmployeesFile
    events_file: EventsFile
    history: list[Activity]
    employees: dict[str, Employee] = field(init=False)
    events: dict[str, Event] = field(init=False)
    history_by_employee: dict[str, list[Activity]] = field(init=False)
    assessment_skills: dict[str, dict[str, int]] = field(init=False)

    def __post_init__(self):
        self.employees = unique(self.employees_file.employees, lambda e: e.employee_id, 'employee')
        self.events = unique(self.events_file.events, lambda e: e.event_id, 'event')
        skills = unique(self.skills_file.skills, lambda s: s.skill_id, 'skill')
        profiles = unique(self.skills_file.role_profiles, lambda p: (p.role, p.grade), 'role profile')
        unique(self.history, lambda r: r.record_id, 'history record')
        def known(ids):
            missing = set(ids) - skills.keys()
            if missing:
                raise ValueError(f'Unknown skills: {sorted(missing)}')
        for profile in profiles.values():
            known(profile.required_skills)
            if not set(profile.critical_skills) <= profile.required_skills.keys():
                raise ValueError('Critical skills must have required levels')
        for employee in self.employees.values():
            known(employee.skills)
            if (employee.role, employee.grade) not in profiles:
                raise ValueError(f'Unknown role profile: {employee.employee_id}')
            goal = employee.career_goal
            if goal and (goal.target_role, goal.target_grade) not in profiles:
                raise ValueError(f'Unknown career goal: {employee.employee_id}')
            if employee.manager_id and employee.manager_id not in self.employees:
                raise ValueError(f'Unknown manager: {employee.manager_id}')
        for event in self.events.values():
            known(event.prerequisites)
            known(d.skill_id for d in event.develops_skills)
            unique(event.develops_skills, lambda d: d.skill_id, 'developed skill')
        self.history_by_employee = {eid: [] for eid in self.employees}
        for record in self.history:
            if record.employee_id not in self.employees or record.event_id not in self.events:
                raise ValueError(f'Unknown employee/event in {record.record_id}')
            if record.date > TODAY:
                raise ValueError(f'History after snapshot: {record.record_id}')
            self.history_by_employee[record.employee_id].append(record)
        self.assessment_skills = {eid: dict(e.skills) for eid, e in self.employees.items()}
        # The kit states that post-review completions are absent from assessment
        # skills. Apply them once, chronologically, when loading a fresh dataset.
        for employee in self.employees.values():
            for record in sorted(self.history_by_employee[employee.employee_id], key=lambda r: (r.date, r.record_id)):
                if record.status == 'completed' and record.date > employee.last_review_date:
                    for development in self.events[record.event_id].develops_skills:
                        current = employee.skills.get(development.skill_id, 0)
                        employee.skills[development.skill_id] = current + min(development.gain, max(0, development.max_level - current))

def parse_history(text: str) -> list[Activity]:
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if set(reader.fieldnames or []) != set(Activity.model_fields):
        raise ValueError('CSV columns must match activity_history.csv schema exactly')
    return [Activity.model_validate(row) for row in reader]

def load_dataset(folder: Path = DEFAULT_DATA) -> Dataset:
    def read(name):
        return (Path(folder) / name).read_text(encoding='utf-8-sig')
    return Dataset(SkillsFile.model_validate_json(read('skills.json')),
                   EmployeesFile.model_validate_json(read('employees.json')),
                   EventsFile.model_validate_json(read('events.json')),
                   parse_history(read('activity_history.csv')))

def replacement_dataset(current: Dataset, employees: EmployeesFile, history_csv: str, *, merge=False) -> Dataset:
    # Fully validate before the caller atomically replaces the live reference.
    history = parse_history(history_csv)
    if merge:
        incoming = {e.employee_id for e in employees.employees}
        # Reconstruct assessment baselines, not materialized skills, before replay.
        retained = [e.model_copy(deep=True, update={'skills': dict(current.assessment_skills[e.employee_id])})
                    for e in current.employees.values() if e.employee_id not in incoming]
        employees = EmployeesFile(meta=employees.meta, employees=retained + employees.employees)
        history = [r.model_copy(deep=True) for r in current.history if r.employee_id not in incoming] + history
    return Dataset(current.skills_file, employees, current.events_file, history)
