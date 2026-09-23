"""Validate immutable starter-kit snapshots and additional profiles before activation."""
from collections import defaultdict
import csv
from dataclasses import dataclass, field
from datetime import date
import hashlib
import io
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, ValidationError, field_validator

from ..contracts import CareerGoal, Contract, Grade, History, Level

FILES = ('employees.json', 'events.json', 'skills.json', 'activity_history.csv')
STATUSES = {'completed', 'in_progress', 'no_show', 'declined', 'dropped', 'overdue'}
REPEATABLE_EVENT_IDS = frozenset({'EV_036'})
HISTORY_FIELDS = tuple(History.model_fields)


class DataValidationError(ValueError):
    def __init__(self, details):
        self.details = details
        super().__init__('Некорректный импорт: ' + '; '.join(f"{d['path']}: {d['message']}" for d in details[:5]))


class RawEmployee(Contract):
    employee_id: Annotated[str, Field(min_length=1)]
    full_name: str
    department: str
    role: str
    grade: Grade
    manager_id: str | None
    hire_date: str
    tenure_months: Annotated[StrictInt, Field(ge=0)]
    work_format: Literal['office', 'hybrid', 'remote']
    preferred_language: Literal['ru', 'kk', 'en']
    career_goal: CareerGoal | None
    skills: dict[str, Level]
    last_review_date: str

    @field_validator('hire_date', 'last_review_date')
    @classmethod
    def iso_date(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('Expected ISO date YYYY-MM-DD')
        return value


class RawEffect(Contract):
    skill_id: str
    gain: Level
    max_level: Level


class RawEvent(Contract):
    event_id: Annotated[str, Field(min_length=1)]
    title: str
    description: str
    type: Literal['compliance', 'onboarding', 'course', 'workshop', 'mentoring', 'certification', 'meetup']
    format: Literal['online', 'offline', 'self_paced']
    duration_hours: Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]
    mandatory: StrictBool
    target_roles: list[str]
    target_grades: list[Grade]
    develops_skills: list[RawEffect]
    prerequisites: dict[str, Level]
    upcoming_sessions: list[str]

    @field_validator('upcoming_sessions')
    @classmethod
    def iso_dates(cls, values):
        for value in values:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError('Expected ISO date YYYY-MM-DD')
        return values


class RawSkill(Contract):
    skill_id: Annotated[str, Field(min_length=1)]
    name: str
    type: Literal['hard', 'soft']
    category: str
    description: str


class RawRoleProfile(Contract):
    role: str
    grade: Grade
    required_skills: dict[str, Level]
    critical_skills: list[str]


@dataclass
class Dataset:
    as_of_date: date
    employees_by_id: dict
    events_by_id: dict
    skills_by_id: dict
    role_profiles_by_key: dict
    history_by_employee: dict
    proficiency_scale: dict
    meta: dict
    manifest: dict
    raw_bytes: dict = field(default_factory=dict, repr=False)

    @property
    def counts(self):
        return dict(employees=len(self.employees_by_id), events=len(self.events_by_id),
                    skills=len(self.skills_by_id), role_profiles=len(self.role_profiles_by_key),
                    history=sum(map(len, self.history_by_employee.values())))


def _path(prefix, loc):
    return prefix + ''.join(f'[{part}]' if isinstance(part, int) else f'.{part}' for part in loc)


def _fail(path, message):
    raise DataValidationError([{'path': path, 'message': message}])


def _validate(model, value, path):
    try:
        return model.model_validate(value).model_dump()
    except ValidationError as exc:
        raise DataValidationError([{'path': _path(path, e['loc']), 'message': e['msg']}
                                   for e in exc.errors()]) from exc


def _decode(content, path):
    try:
        return json.loads(content.decode('utf-8-sig'))
    except (ValueError, UnicodeError) as exc:
        _fail(path, f'Expected UTF-8 JSON: {exc}')


def _index(rows, key, path):
    result = {}
    for index, row in enumerate(rows):
        identity = key(row)
        if identity in result:
            _fail(f'{path}[{index}]', f'Duplicate identifier: {identity}')
        result[identity] = row
    return result


def _history(content):
    try:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')), strict=True)
        if (set(reader.fieldnames or []) != set(HISTORY_FIELDS) or
                len(reader.fieldnames or []) != len(HISTORY_FIELDS)):
            _fail('activity_history.csv', 'Expected columns: ' + ','.join(HISTORY_FIELDS))
        output = []
        for index, raw in enumerate(reader):
            path = f'activity_history[{index}]'
            if None in raw or any(value is None for value in raw.values()):
                _fail(path, 'CSV row does not match the declared columns')
            for key in ('completion_pct', 'score', 'feedback_rating'):
                if raw[key] == '' and key != 'completion_pct':
                    raw[key] = None
                else:
                    try:
                        raw[key] = int(raw[key])
                    except (ValueError, TypeError):
                        _fail(f'{path}.{key}', 'Expected integer')
            raw['due_date'] = raw['due_date'] or None
            row = _validate(History, raw, path)
            pct, status = row['completion_pct'], row['status']
            if (status == 'completed' and pct != 100 or
                    status in ('no_show', 'declined') and pct != 0 or
                    status in ('in_progress', 'overdue') and pct > 95 or
                    status == 'dropped' and not 5 <= pct <= 95):
                _fail(path + '.completion_pct', 'Percentage inconsistent with status')
            output.append(row)
        _index(output, lambda r: r['record_id'], 'activity_history')
        return output
    except UnicodeError:
        _fail('activity_history.csv', 'Expected UTF-8 CSV')
    except csv.Error as exc:
        _fail('activity_history.csv', f'Invalid CSV: {exc}')


def load_dataset(directory, as_of_override=None):
    return load_dataset_bytes({name: (Path(directory) / name).read_bytes() for name in FILES}, as_of_override)


def load_dataset_bytes(raw, as_of_override=None):
    """Return a fully validated dataset; callers only persist it after this succeeds."""
    if any(name not in raw for name in FILES):
        _fail('files', 'Required: ' + ', '.join(FILES))
    wrappers = {name: _decode(raw[name], name) for name in FILES[:3]}
    snapshots = []
    for name, wrapper in wrappers.items():
        try:
            value = wrapper['meta']['as_of_date']
            snapshot = date.fromisoformat(value)
            if snapshot.isoformat() != value:
                raise ValueError()
            snapshots.append(snapshot)
        except (KeyError, TypeError, ValueError):
            _fail(name + '.meta.as_of_date', 'Expected ISO snapshot date YYYY-MM-DD')
    if len(set(snapshots)) != 1:
        _fail('meta.as_of_date', 'Dataset snapshot dates do not match')
    as_of = snapshots[0]
    if as_of_override is not None:
        try:
            as_of = date.fromisoformat(str(as_of_override))
            if as_of.isoformat() != str(as_of_override):
                raise ValueError()
        except ValueError:
            _fail('as_of_date', 'Expected ISO snapshot date YYYY-MM-DD')
        if as_of < snapshots[0]:
            _fail('as_of_date', 'Cannot reconstruct a date before the source snapshot')

    def rows(filename, field_name, model):
        values = wrappers[filename].get(field_name)
        if not isinstance(values, list):
            _fail(filename + '.' + field_name, 'Expected array')
        return [_validate(model, value, f'{field_name}[{i}]') for i, value in enumerate(values)]

    employees = rows('employees.json', 'employees', RawEmployee)
    events = rows('events.json', 'events', RawEvent)
    skills = rows('skills.json', 'skills', RawSkill)
    profiles = rows('skills.json', 'role_profiles', RawRoleProfile)
    ei = _index(employees, lambda r: r['employee_id'], 'employees')
    vi = _index(events, lambda r: r['event_id'], 'events')
    si = _index(skills, lambda r: r['skill_id'], 'skills')
    ri = _index(profiles, lambda r: (r['role'], r['grade']), 'role_profiles')
    proficiency = wrappers['skills.json'].get('proficiency_scale')
    if not isinstance(proficiency, dict) or set(proficiency) != set(map(str, range(6))):
        _fail('proficiency_scale', 'Expected levels 0..5')

    def known_skills(mapping, path):
        for sid in mapping:
            if sid not in si:
                _fail(f'{path}.{sid}', 'Unknown skill')

    for i, employee in enumerate(employees):
        path = f'employees[{i}]'
        if (employee['role'], employee['grade']) not in ri:
            _fail(path + '.role', 'Role/grade has no requirement profile')
        goal = employee['career_goal']
        if goal and (goal['target_role'], goal['target_grade']) not in ri:
            _fail(path + '.career_goal', 'Unknown target role/grade')
        manager = employee['manager_id']
        if manager is not None and manager not in ei:
            _fail(path + '.manager_id', 'Unknown manager')
        for field_name in ('hire_date', 'last_review_date'):
            if date.fromisoformat(employee[field_name]) > snapshots[0]:
                _fail(path + '.' + field_name, 'Date cannot be after the source snapshot')
        known_skills(employee['skills'], path + '.skills')
    for i, profile in enumerate(profiles):
        known_skills(profile['required_skills'], f'role_profiles[{i}].required_skills')
        if not set(profile['critical_skills']) <= profile['required_skills'].keys():
            _fail(f'role_profiles[{i}].critical_skills', 'Critical skills must have requirements')
    role_names = {role for role, _ in ri}
    for i, event in enumerate(events):
        path = f'events[{i}]'
        known_skills(event['prerequisites'], path + '.prerequisites')
        if not set(event['target_roles']) <= role_names:
            _fail(path + '.target_roles', 'Unknown role')
        effect_ids = [effect['skill_id'] for effect in event['develops_skills']]
        known_skills(effect_ids, path + '.develops_skills')
        if len(effect_ids) != len(set(effect_ids)):
            _fail(path + '.develops_skills', 'Duplicate skill effect')
    grouped = defaultdict(list)
    for i, row in enumerate(_history(raw['activity_history.csv'])):
        if row['employee_id'] not in ei or row['event_id'] not in vi:
            _fail(f'activity_history[{i}]', 'Unknown employee or event')
        grouped[row['employee_id']].append(row)
    for records in grouped.values():
        records.sort(key=lambda r: (r['date'], r['record_id']))
    return Dataset(as_of, ei, vi, si, ri, dict(grouped), proficiency,
                   dict(wrappers['skills.json']['meta'], as_of_date=as_of.isoformat()),
                   {name: hashlib.sha256(raw[name]).hexdigest() for name in FILES}, dict(raw))


def merge_additional_dataset(base, employees_bytes, history_bytes):
    """Append raw profiles and histories; reject collisions rather than overwrite progress."""
    extra = _decode(employees_bytes, 'employees.json')
    if isinstance(extra, list):
        extra = {'meta': {'as_of_date': base.meta['as_of_date']}, 'employees': extra}
    if not isinstance(extra, dict) or not isinstance(extra.get('employees'), list):
        _fail('employees.json', 'Expected starter-kit employees wrapper or profile array')
    if 'meta' in extra and (not isinstance(extra['meta'], dict) or
                           extra['meta'].get('as_of_date') != base.meta['as_of_date']):
        _fail('employees.json.meta.as_of_date', 'Additional profiles must use the active snapshot date')
    extra_rows = [_validate(RawEmployee, row, f'employees[{i}]') for i, row in enumerate(extra['employees'])]
    for index, row in enumerate(extra_rows):
        if row['employee_id'] in base.employees_by_id:
            _fail(f'employees[{index}].employee_id', 'Employee already exists')
    extra_history = _history(history_bytes)
    current = [r for records in base.history_by_employee.values() for r in records]
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=HISTORY_FIELDS)
    writer.writeheader()
    writer.writerows(current + extra_history)
    raw = dict(base.raw_bytes)
    for name, key, values in [('employees.json', 'employees', list(base.employees_by_id.values()) + extra_rows),
                              ('events.json', 'events', list(base.events_by_id.values()))]:
        raw[name] = json.dumps({'meta': base.meta, key: values}, ensure_ascii=False).encode()
    raw['skills.json'] = json.dumps({'meta': base.meta, 'skills': list(base.skills_by_id.values()),
        'role_profiles': list(base.role_profiles_by_key.values()), 'proficiency_scale': base.proficiency_scale},
        ensure_ascii=False).encode()
    raw['activity_history.csv'] = output.getvalue().encode()
    return load_dataset_bytes(raw, base.as_of_date.isoformat())
