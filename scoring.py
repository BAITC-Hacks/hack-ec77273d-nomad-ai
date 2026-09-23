"""Pure deterministic ranking. No I/O, clock reads, mutation, or LLM calls."""
from collections import Counter
from loaders import Dataset, TODAY
from models import Employee, Event

WEIGHTS = {'critical_gap_closure': 100, 'requirement_distance': 10,
           'history_fit': 5, 'grade_urgency': 3}
GRADES = ['Junior', 'Middle', 'Senior', 'Lead']

def target_source(employee):
    return 'career_goal' if employee.career_goal else ('current_role' if employee.grade == 'Lead' else 'next_grade')

def target_profile(employee: Employee, data: Dataset):
    goal = employee.career_goal
    role, grade = ((goal.target_role, goal.target_grade) if goal else
                   (employee.role, GRADES[min(GRADES.index(employee.grade) + 1, len(GRADES) - 1)]))
    return next(p for p in data.skills_file.role_profiles if (p.role, p.grade) == (role, grade))

def skill_rows(employee: Employee, data: Dataset):
    profile = target_profile(employee, data)
    names = {s.skill_id: s.name for s in data.skills_file.skills}
    return [{'skill_id': sid, 'name': names[sid], 'current': employee.skills.get(sid, 0),
             'required': required, 'gap': max(0, required - employee.skills.get(sid, 0)),
             'critical': sid in profile.critical_skills}
            for sid, required in profile.required_skills.items()]

def progress(employee: Employee, data: Dataset):
    rows = skill_rows(employee, data)
    total = sum(r['required'] for r in rows)
    return round(100 * sum(min(r['current'], r['required']) for r in rows) / total, 1) if total else 100.0

def rejection_reasons(employee: Employee, event: Event, data: Dataset, *, allow_in_progress=False):
    reasons = []
    if employee.role not in event.target_roles:
        reasons.append('role_mismatch')
    if employee.grade not in event.target_grades:
        reasons.append('grade_mismatch')
    if any(employee.skills.get(s, 0) < level for s, level in event.prerequisites.items()):
        reasons.append('prerequisites_not_met')
    history = data.history_by_employee[employee.employee_id]
    if event.event_id != 'EV_036' and any(r.event_id == event.event_id and r.status == 'completed' for r in history):
        reasons.append('already_completed')
    if not allow_in_progress and any(r.event_id == event.event_id and r.status == 'in_progress' for r in history):
        reasons.append('in_progress')
    return reasons

def recommendation_rejections(employee, event, data):
    reasons = rejection_reasons(employee, event, data)
    if event.mandatory:
        reasons.append('mandatory_activity')
    profile = target_profile(employee, data)
    if not any(min(d.gain, max(0, d.max_level - employee.skills.get(d.skill_id, 0)),
                   max(0, profile.required_skills.get(d.skill_id, 0) - employee.skills.get(d.skill_id, 0))) > 0
               for d in event.develops_skills):
        reasons.append('no_target_gap_closure')
    if event.event_id == 'EV_036' and any(r.event_id == event.event_id and r.status == 'completed' and r.date == TODAY
                                        for r in data.history_by_employee[employee.employee_id]):
        reasons.append('completed_today')
    return reasons

def rank_events(employee: Employee, data: Dataset, limit: int = 3):
    rows = {r['skill_id']: r for r in skill_rows(employee, data)}
    blockers = sum(r['critical'] and r['gap'] > 0 for r in rows.values())
    history = data.history_by_employee[employee.employee_id]
    negative = Counter(data.events[r.event_id].type for r in history if r.status in ('declined', 'no_show'))
    on_time = Counter(data.events[r.event_id].type for r in history
                      if r.status == 'completed' and data.events[r.event_id].format != 'self_paced'
                      and r.due_date is not None and r.date <= r.due_date)
    ranked = []
    for event in data.events.values():
        if recommendation_rejections(employee, event, data):
            continue
        gains = []
        for development in event.develops_skills:
            row = rows.get(development.skill_id)
            if not row:
                continue
            effective = min(development.gain, max(0, development.max_level - row['current']))
            closure = min(row['gap'], effective)
            if closure > 0:
                gains.append({**row, 'gain': development.gain, 'max_level': development.max_level,
                              'effective_gain': effective, 'gap_closure': closure})
        critical = sum(g['gap_closure'] for g in gains if g['critical'])
        distance = sum(g['gap'] * g['gap_closure'] for g in gains)
        refusals = negative[event.type]
        timely = on_time[event.type]
        fit = min(timely, 3) - (min(refusals, 5) if refusals >= 2 else 0)
        # Urgency helps events that actually address a promotion blocker.
        urgency = blockers if critical > 0 else 0
        values = dict(critical_gap_closure=critical, requirement_distance=distance,
                      history_fit=fit, grade_urgency=urgency)
        weighted = {key: value * WEIGHTS[key] for key, value in values.items()}
        ranked.append({'event_id': event.event_id, 'title': event.title, 'type': event.type,
                       'format': event.format, 'duration_hours': event.duration_hours,
                       'upcoming_sessions': [d.isoformat() for d in event.upcoming_sessions if d >= TODAY],
                       'score': sum(weighted.values()),
                       'factors': {'values': values, 'weights': WEIGHTS.copy(), 'weighted': weighted,
                                   'target': {'role': target_profile(employee, data).role,
                                              'grade': target_profile(employee, data).grade,
                                              'current_grade': employee.grade, 'source': target_source(employee)},
                                   'skills': gains, 'critical_skills_below_requirement': blockers,
                                   'history': {'event_type': event.type, 'declined_or_no_show': refusals,
                                               'on_time_completions': timely,
                                               'penalty_applied': refusals >= 2}}})
    return sorted(ranked, key=lambda r: (-r['score'], r['event_id']))[:max(0, min(limit, 3))]
