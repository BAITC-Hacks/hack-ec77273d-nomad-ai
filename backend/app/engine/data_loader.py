"""Load the private starter kit without modifying its files."""
from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import date
import hashlib
import io
import json
from pathlib import Path

FILES = ('employees.json', 'events.json', 'skills.json', 'activity_history.csv')
STATUSES = {'completed', 'in_progress', 'no_show', 'declined', 'dropped', 'overdue'}
# Catalog-level rule from the starter kit README, never an employee exception.
REPEATABLE_EVENT_IDS = frozenset({'EV_036'})


def _index(rows, key):
    result = {}
    for row in rows:
        identity = key(row)
        if identity in result:
            raise ValueError(f'Duplicate identifier: {identity}')
        result[identity] = row
    return result


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

    @property
    def counts(self):
        return dict(employees=len(self.employees_by_id), events=len(self.events_by_id),
                    skills=len(self.skills_by_id), role_profiles=len(self.role_profiles_by_key),
                    history=sum(map(len, self.history_by_employee.values())))


def load_dataset(directory):
    raw = {name: (Path(directory) / name).read_bytes() for name in FILES}
    employees, events, skills = [json.loads(raw[name].decode('utf-8-sig')) for name in FILES[:3]]
    as_of = date.fromisoformat(skills['meta']['as_of_date'])
    for wrapper in (employees, events):
        if date.fromisoformat(wrapper['meta']['as_of_date']) != as_of:
            raise ValueError('Dataset snapshot dates do not match')
    employee_index = _index(employees['employees'], lambda r: r['employee_id'])
    event_index = _index(events['events'], lambda r: r['event_id'])
    skill_index = _index(skills['skills'], lambda r: r['skill_id'])
    roles = _index(skills['role_profiles'], lambda r: (r['role'], r['grade']))
    levels = {int(level) for level in skills['proficiency_scale']}

    def check_levels(mapping):
        if any(skill not in skill_index or type(level) is not int or level not in levels
               for skill, level in mapping.items()):
            raise ValueError('Unknown skill or invalid proficiency level')

    for employee in employee_index.values():
        if (employee['role'], employee['grade']) not in roles:
            raise ValueError('Employee role/grade has no profile')
        if employee['manager_id'] is not None and employee['manager_id'] not in employee_index:
            raise ValueError('Unknown manager')
        date.fromisoformat(employee['last_review_date'])
        check_levels(employee['skills'])
    for profile in roles.values():
        check_levels(profile['required_skills'])
        if not set(profile['critical_skills']) <= profile['required_skills'].keys():
            raise ValueError('Critical skills must have requirements')
    for event in event_index.values():
        check_levels(event['prerequisites'])
        if type(event['mandatory']) is not bool or event['duration_hours'] <= 0:
            raise ValueError('Invalid event mandatory flag or duration')
        for effect in event['develops_skills']:
            check_levels({effect['skill_id']: effect['max_level']})
            if type(effect['gain']) is not int or effect['gain'] < 0:
                raise ValueError('Invalid skill gain')
        for session in event['upcoming_sessions']:
            date.fromisoformat(session)

    history = list(csv.DictReader(io.StringIO(raw[FILES[3]].decode('utf-8-sig'))))
    _index(history, lambda r: r['record_id'])
    grouped = defaultdict(list)
    for row in history:
        if row['employee_id'] not in employee_index or row['event_id'] not in event_index:
            raise ValueError('History references an unknown employee or event')
        if row['status'] not in STATUSES:
            raise ValueError('Unknown history status')
        date.fromisoformat(row['date'])
        row['due_date'] = row['due_date'] or None
        if row['due_date']:
            date.fromisoformat(row['due_date'])
        for field, minimum, maximum in [('completion_pct', 0, 100), ('score', 0, 100),
                                        ('feedback_rating', 1, 5)]:
            row[field] = int(row[field]) if row[field] != '' else None
            if row[field] is not None and not minimum <= row[field] <= maximum:
                raise ValueError(f'Invalid history {field}')
        grouped[row['employee_id']].append(row)
    for records in grouped.values():
        records.sort(key=lambda row: (row['date'], row['record_id']))
    return Dataset(as_of, employee_index, event_index, skill_index, roles, dict(grouped),
                   skills['proficiency_scale'], skills['meta'],
                   {name: hashlib.sha256(content).hexdigest() for name, content in raw.items()})
