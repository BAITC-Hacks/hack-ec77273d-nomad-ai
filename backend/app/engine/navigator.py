"""Deterministic, auditable career scenarios; no network calls or state mutation."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from math import ceil

from .data_loader import REPEATABLE_EVENT_IDS
from .planner import apply_effects, coverage, friction

GRADES = ('Junior', 'Middle', 'Senior', 'Lead')
EMPTY_PROFILE = {'required_skills': {}, 'critical_skills': []}


def resolve_target(dataset, employee, target=None):
    if target:
        result = {'role': target.get('role', target.get('target_role')),
                  'grade': target.get('grade', target.get('target_grade')),
                  'source': target.get('source', 'career_goal')}
    elif employee.get('career_goal'):
        result = {'role': employee['career_goal']['target_role'],
                  'grade': employee['career_goal']['target_grade'], 'source': 'career_goal'}
    else:
        index = GRADES.index(employee['grade'])
        result = {'role': employee['role'], 'grade': GRADES[min(index + 1, 3)],
                  'source': 'next_grade' if index < 3 else 'current_grade'}
    if (result['role'], result['grade']) not in dataset.role_profiles_by_key:
        raise ValueError('Неизвестная роль или грейд карьерной цели')
    return result


def _history_and_skills(dataset, employee_id, completions):
    raw = dataset.employees_by_id[employee_id]
    snapshot = dataset.as_of_date.isoformat()
    history = [dict(r) for r in dataset.history_by_employee.get(employee_id, []) if r['date'] <= snapshot]
    history.sort(key=lambda r: (r['date'], r['record_id']))
    skills = dict(raw['skills'])
    seen = set()
    for row in history:
        if row['status'] != 'completed':
            continue
        eid = row['event_id']
        identity = (eid, row['date'] if eid in REPEATABLE_EVENT_IDS else '')
        if identity not in seen and row['date'] > raw['last_review_date']:
            skills = apply_effects(skills, dataset.events_by_id[eid])
        seen.add(identity)
    seen_overlay = set()
    for index, item in enumerate(completions):
        eid = item['event_id']
        completed_at = item.get('completed_at', item.get('date', snapshot))[:10]
        if completed_at > snapshot:
            continue
        session = item.get('event_session_key', completed_at)
        identity = (eid, session if eid in REPEATABLE_EVENT_IDS else '')
        if identity in seen or identity in seen_overlay:
            continue
        seen_overlay.add(identity)
        skills = apply_effects(skills, dataset.events_by_id[eid])
        continued = item.get('continued_record_id')
        previous = next((r for r in history if r['record_id'] == continued), None)
        if previous:
            history.remove(previous)
        history.append({
            'record_id': continued or item.get('record_id', f'demo_{employee_id}_{index}'),
            'employee_id': employee_id, 'event_id': eid, 'date': completed_at,
            'due_date': previous.get('due_date') if previous else None, 'status': 'completed',
            'completion_pct': 100, 'score': item.get('score'), 'feedback_rating': item.get('feedback_rating'),
            'assigned_by': previous.get('assigned_by', 'self') if previous else 'self',
        })
    history.sort(key=lambda r: (r['date'], r['record_id']))
    return history, skills


def _gaps(dataset, skills, profile):
    return [{'skill_id': sid, 'name': dataset.skills_by_id[sid]['name'],
             'current': skills.get(sid, 0), 'required': required,
             'gap': max(0, required - skills.get(sid, 0)),
             'critical': sid in profile['critical_skills']}
            for sid, required in profile['required_skills'].items()]


def employee_state(dataset, employee_id, completions=(), target=None, revision=None):
    raw = dataset.employees_by_id[employee_id]
    target = resolve_target(dataset, raw, target)
    history, skills = _history_and_skills(dataset, employee_id, completions)
    profile = dataset.role_profiles_by_key[(target['role'], target['grade'])]
    summary = coverage(skills, profile)
    gaps = _gaps(dataset, skills, profile)
    if summary['coverage'] is None:
        status = 'no_requirements'
    elif all(g['gap'] == 0 for g in gaps):
        status = 'goal_needed' if target['source'] == 'current_grade' else 'requirements_met'
    else:
        status = 'active'
    rev = len(completions) if revision is None else revision
    employee = {key: raw.get(key) for key in ('employee_id', 'full_name', 'department', 'role', 'grade',
                'tenure_months', 'work_format', 'preferred_language', 'career_goal', 'last_review_date')}
    if raw.get('hire_date'):
        hired = date.fromisoformat(raw['hire_date'])
        today = dataset.as_of_date
        employee['tenure_months'] = max(0, (today.year - hired.year) * 12 + today.month - hired.month
                                       - (today.day < hired.day))
    employee.update(skills=skills, target=target, revision=rev, as_of_date=dataset.as_of_date.isoformat(),
                    completed_event_ids=sorted({r['event_id'] for r in history if r['status'] == 'completed'}))
    trajectory = {'employee_id': employee_id, 'revision': rev, 'target': target,
                  'coverage': round(summary['coverage'], 2) if summary['coverage'] is not None else None,
                  'critical_gap_units': summary['critical_gap_units'],
                  'critical_blockers': summary['critical_blockers'], 'gaps': gaps, 'status': status}
    current = dataset.role_profiles_by_key.get((raw['role'], raw['grade']), EMPTY_PROFILE)
    grade_index = GRADES.index(raw['grade'])
    next_profile = (dataset.role_profiles_by_key.get((raw['role'], GRADES[grade_index + 1]), EMPTY_PROFILE)
                    if grade_index < len(GRADES) - 1 else EMPTY_PROFILE)
    return {'employee': employee, 'trajectory': trajectory,
            'current_requirements': _gaps(dataset, skills, current),
            'next_requirements': _gaps(dataset, skills, next_profile), 'history': history,
            'target_options': [{'role': role, 'grade': grade, 'source': 'career_goal'}
                               for role, grade in sorted(dataset.role_profiles_by_key)]}


def _admission(dataset, employee, event, skills, history, earliest, used=(), completions=()):
    eid = event['event_id']
    def no(reason):
        return {'eligible': False, 'reason': reason, 'event_session_key': None,
                'continued_record_id': None, 'action': 'start'}
    if event['mandatory']:
        return no('Обязательная активность не является добровольной рекомендацией')
    if eid in used:
        return no('Активность уже включена в этот маршрут')
    if employee['role'] not in event['target_roles'] or employee['grade'] not in event['target_grades']:
        return no('Роль или грейд не входят в аудиторию активности')
    if any(skills.get(sid, 0) < level for sid, level in event['prerequisites'].items()):
        return no('Пока не выполнены предварительные требования')
    rows = [r for r in history if r['event_id'] == eid]
    if eid not in REPEATABLE_EVENT_IDS and any(r['status'] == 'completed' for r in rows):
        return no('Неповторяемая активность уже выполнена')
    latest = max(rows, key=lambda r: (r['date'], r['record_id'])) if rows else None
    continuing = latest is not None and latest['status'] == 'in_progress'
    if continuing or event['format'] == 'self_paced':
        start = earliest
        session_key = latest['date'] if continuing else 'self_paced'
    else:
        used_sessions = {r['date'] for r in rows if r['status'] == 'completed'}
        used_sessions.update(c.get('event_session_key') for c in completions if c['event_id'] == eid)
        available = [date.fromisoformat(s) for s in event['upcoming_sessions']
                     if date.fromisoformat(s) >= earliest and s not in used_sessions]
        if not available:
            return no('Нет доступной сессии на дату сценария или позднее')
        start = min(available)
        session_key = start.isoformat()
    return {'eligible': True, 'reason': None, 'event_session_key': session_key,
            'continued_record_id': latest['record_id'] if continuing else None,
            'action': 'continue' if continuing else 'start', 'planned_start': start.isoformat()}


def eligibility(dataset, employee_id, event_id, completions=(), target=None):
    employee = dataset.employees_by_id[employee_id]
    event = dataset.events_by_id[event_id]
    history, skills = _history_and_skills(dataset, employee_id, completions)
    return _admission(dataset, employee, event, skills, history, dataset.as_of_date, completions=completions)


def _impacts(skills, after, event, profile):
    return [{'skill_id': effect['skill_id'], 'before': skills.get(effect['skill_id'], 0),
             'after': after.get(effect['skill_id'], 0),
             'gain_applied': after.get(effect['skill_id'], 0) - skills.get(effect['skill_id'], 0),
             'max_level': effect['max_level'], 'required': profile['required_skills'].get(effect['skill_id'])}
            for effect in event['develops_skills'] if after.get(effect['skill_id'], 0) > skills.get(effect['skill_id'], 0)]


def recommendations(dataset, employee_id, completions=(), target=None, revision=None):
    state = employee_state(dataset, employee_id, completions, target, revision)
    employee, trajectory, history = state['employee'], state['trajectory'], state['history']
    profile = dataset.role_profiles_by_key[(employee['target']['role'], employee['target']['grade'])]
    initial_skills = employee['skills']
    before = coverage(initial_skills, profile)
    events = sorted(dataset.events_by_id.values(), key=lambda e: e['event_id'])
    signals = {event['event_id']: friction(event, history, dataset.events_by_id, dataset.as_of_date)
               for event in events}
    by_first = {}

    def options(skills, earliest, used):
        for event in events:
            allowed = _admission(dataset, employee, event, skills, history, earliest, used, completions)
            if not allowed['eligible']:
                continue
            after_skills = apply_effects(skills, event)
            impacts = _impacts(skills, after_skills, event, profile)
            if not impacts:
                continue
            initial, after = coverage(skills, profile), coverage(after_skills, profile)
            start = date.fromisoformat(allowed['planned_start'])
            end = start + timedelta(days=max(1, ceil(event['duration_hours'] / 2)))
            utility = (2 * (initial['critical_gap_units'] - after['critical_gap_units'])
                       + ((after['coverage'] or 0) - (initial['coverage'] or 0)) / 10
                       - 2 * signals[event['event_id']]['friction'] - event['duration_hours'] / 40
                       + (0.25 if allowed['action'] == 'continue' else 0))
            step = {'event_id': event['event_id'], 'title': event['title'], 'format': event['format'],
                    'duration_hours': event['duration_hours'], 'planned_start': start.isoformat(),
                    'planned_end': end.isoformat(), 'impacts': impacts,
                    'coverage_after': round(after['coverage'], 2) if after['coverage'] is not None else None,
                    'critical_gap_units_after': after['critical_gap_units']}
            yield event, after_skills, step, utility, allowed['action']

    def useful(steps):
        needed = {}
        for step in reversed(steps):
            closes = any(i['required'] is not None and i['before'] < i['required'] for i in step['impacts'])
            bridges = any(i['before'] < needed.get(i['skill_id'], 0) for i in step['impacts'])
            if not closes and not bridges:
                return False
            for sid, value in dataset.events_by_id[step['event_id']]['prerequisites'].items():
                needed[sid] = max(needed.get(sid, 0), value)
        return True

    def consider(steps, utility, action):
        if not useful(steps):
            return
        first = steps[0]['event_id']
        ids = tuple(s['event_id'] for s in steps)
        candidate = {'steps': steps, 'utility': utility, 'action': action, 'ids': ids}
        previous = by_first.get(first)
        if previous is None or (-utility, len(ids), ids) < (-previous['utility'], len(previous['ids']), previous['ids']):
            by_first[first] = candidate

    if trajectory['status'] == 'active':
        for event, skills1, step1, score1, action in options(initial_skills, dataset.as_of_date, set()):
            consider([step1], score1, action)
            for _, _, step2, score2, _ in options(skills1, date.fromisoformat(step1['planned_end']), {event['event_id']}):
                consider([step1, step2], score1 + 0.7 * score2, action)
    routes = sorted(by_first.values(), key=lambda r: (-r['utility'], r['ids']))[:5]
    candidates = []
    for route in routes:
        steps = route['steps']
        first = steps[0]
        eid = first['event_id']
        route_id = 'route_' + '_'.join(route['ids'])
        signal = signals[eid]
        counts = signal['format_counts']
        history_text = ('Истории участия в этом формате недостаточно для вывода о предпочтениях.'
                        if signal['history_evidence'] == 'insufficient' else
                        f"За последний год в этом формате: завершено {counts['completed']}, "
                        f"неявки {counts['no_show']}, отказы {counts['declined']}, прервано {counts['dropped']}. "
                        'Причины пропусков неизвестны; формат и длительность можно сравнить с альтернативами.')
        if route['action'] == 'continue':
            history_text += ' Эта активность уже начата.'
        gap_labels = []
        for impact in first['impacts']:
            if impact['required'] is not None and impact['before'] < impact['required']:
                name = dataset.skills_by_id[impact['skill_id']]['name']
                gap_labels.append(f"{name}: {impact['before']} → {impact['after']}, требование {impact['required']}")
        gap_text = ('Первый шаг уменьшает разрыв: ' + '; '.join(gap_labels) + '.' if gap_labels else
                    'Первый шаг развивает предварительный навык для следующей активности маршрута.')
        factors = [
            {'fact_id': 'grade', 'category': 'grade', 'text': f"Текущий грейд: {employee['grade']}."},
            {'fact_id': 'target', 'category': 'target',
             'text': f"Цель: {employee['target']['role']}, {employee['target']['grade']}."},
            {'fact_id': route_id + '_gap', 'category': 'gap', 'text': gap_text},
            {'fact_id': route_id + '_history', 'category': 'history', 'text': history_text},
            {'fact_id': route_id + '_eligibility', 'category': 'eligibility',
             'text': f"Роль, грейд и предварительные требования проверены. Формат: {first['format']}; "
                     f"длительность: {first['duration_hours']:g} ч."},
            {'fact_id': route_id + '_impact', 'category': 'impact',
             'text': f"Остаток критических разрывов после первого шага: {first['critical_gap_units_after']}."},
        ]
        initial_available = {e['event_id'] for e in events if _admission(
            dataset, employee, e, initial_skills, history, dataset.as_of_date, completions=completions)['eligible']}
        after1 = apply_effects(initial_skills, dataset.events_by_id[eid])
        unlocked = [e['event_id'] for e in events if e['event_id'] not in initial_available and _admission(
            dataset, employee, e, after1, history, date.fromisoformat(first['planned_end']), {eid}, completions)['eligible']]
        candidates.append({
            'route_id': route_id, 'event_id': eid, 'title': first['title'], 'action': route['action'],
            'score': round(route['utility'], 4), 'reason': ' '.join(f['text'] for f in factors[1:4]),
            'factors': factors, 'impacts': first['impacts'], 'progress_before': trajectory['coverage'],
            'progress_after': first['coverage_after'], 'critical_gap_before': before['critical_gap_units'],
            'critical_gap_after': first['critical_gap_units_after'], 'steps': steps, 'unlocked_event_ids': unlocked,
        })
    baseline = None
    if candidates:
        lowest = min(profile['required_skills'], key=lambda sid: (initial_skills.get(sid, 0), sid), default=None)
        naive = next((e for e in events if any(i['skill_id'] == lowest for i in e['develops_skills']) and
                      _admission(dataset, employee, e, initial_skills, history, dataset.as_of_date,
                                 completions=completions)['eligible']), None)
        if naive:
            baseline = {'event_id': naive['event_id'], 'skill_id': lowest,
                        'reason': 'Сравнение с простым выбором по самому низкому требуемому навыку; '
                                  'такой выбор не учитывает критичность, историю формата и весь маршрут.'}
    no_step = None
    if not candidates:
        no_step = {'requirements_met': 'Требования выбранной цели по навыкам уже выполнены. Это не присваивает новый грейд.',
                   'no_requirements': 'Для выбранной цели нет требований по навыкам.',
                   'goal_needed': 'Требования текущего грейда выполнены. Выберите карьерную цель.'}.get(
                       trajectory['status'], 'В каталоге нет доступного добровольного шага или пары шагов для этой цели. '
                       'Проверены аудитория, предварительные требования, завершения, даты и потолки навыков.')
    return {'employee_id': employee_id, 'revision': employee['revision'], 'mode': 'fallback',
            'fallback_reason': 'not_requested', 'recommendations': candidates[:3], 'baseline': baseline,
            'no_step_reason': no_step, '_candidates': candidates,
            '_rerankable_route_ids': [r['route_id'] for r in candidates]}


def _has_next_step(dataset, state, completions):
    """HR needs availability, not ranking; stop as soon as a useful scenario exists."""
    if state['trajectory']['status'] != 'active':
        return False
    employee, history = state['employee'], state['history']
    skills = employee['skills']
    required = dataset.role_profiles_by_key[(employee['target']['role'], employee['target']['grade'])]['required_skills']

    def closes(before, after):
        return any(before.get(sid, 0) < level and after.get(sid, 0) > before.get(sid, 0)
                   for sid, level in required.items())

    bridges = []
    for event in dataset.events_by_id.values():
        allowed = _admission(dataset, employee, event, skills, history, dataset.as_of_date, completions=completions)
        if not allowed['eligible']:
            continue
        after = apply_effects(skills, event)
        if closes(skills, after):
            return True
        if after != skills:
            end = date.fromisoformat(allowed['planned_start']) + timedelta(days=max(1, ceil(event['duration_hours'] / 2)))
            bridges.append((event, after, end))
    for first, after, earliest in bridges:
        for second in dataset.events_by_id.values():
            needed = second['prerequisites']
            useful_bridge = any(skills.get(sid, 0) < level and after.get(sid, 0) > skills.get(sid, 0)
                                for sid, level in needed.items())
            if not useful_bridge:
                continue
            allowed = _admission(dataset, employee, second, after, history, earliest, {first['event_id']}, completions)
            if allowed['eligible'] and closes(after, apply_effects(after, second)):
                return True
    return False


def hr_overview(dataset, overlays_by_employee=None, targets_by_employee=None):
    overlays_by_employee = overlays_by_employee or {}
    targets_by_employee = targets_by_employee or {}
    by_skill, no_step, records = defaultdict(list), [], defaultdict(list)
    for eid in dataset.employees_by_id:
        completions = overlays_by_employee.get(eid, [])
        target = targets_by_employee.get(eid)
        state = employee_state(dataset, eid, completions, target)
        for gap in state['trajectory']['gaps']:
            by_skill[gap['skill_id']].append(gap['gap'])
        if not _has_next_step(dataset, state, completions):
            recs = recommendations(dataset, eid, completions, target)
            no_step.append({'employee_id': eid, 'reason': recs['no_step_reason']})
        for row in state['history']:
            records[row['event_id']].append(row)
    skill_gaps = [{'skill_id': sid, 'name': dataset.skills_by_id[sid]['name'],
                   'affected_count': sum(gap > 0 for gap in gaps), 'eligible_population': len(gaps),
                   'mean_gap': round(sum(gaps) / len(gaps), 3)} for sid, gaps in by_skill.items()]
    skill_gaps.sort(key=lambda g: (-g['mean_gap'], -g['affected_count'], g['skill_id']))
    activities = []
    for eid, event in dataset.events_by_id.items():
        rows = records[eid]
        counts = Counter(r['status'] for r in rows)
        activities.append({'event_id': eid, 'title': event['title'], 'mandatory': event['mandatory'],
                           'unique_participants': len({r['employee_id'] for r in rows}),
                           'status_counts': dict(counts), 'completed_records': counts['completed'],
                           'total_records': len(rows),
                           'completion_rate': round(100 * counts['completed'] / len(rows), 2) if rows else None})
    return {'as_of_date': dataset.as_of_date.isoformat(), 'total_employees': len(dataset.employees_by_id),
            'skill_gaps': skill_gaps, 'no_next_step': no_step, 'activities': activities}
