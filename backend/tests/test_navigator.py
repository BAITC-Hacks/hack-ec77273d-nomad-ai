from copy import deepcopy
from datetime import date
import unittest

from backend.app.contracts import Employee, HROverview, RecommendationResponse, Trajectory
from backend.app.engine.data_loader import Dataset
from backend.app.engine.navigator import eligibility, employee_state, hr_overview, recommendations


def example_dataset():
    def event(eid, skill, gain=1, maximum=5, **kwargs):
        item = dict(event_id=eid, title=eid, description='Synthetic course', type='course', mandatory=False,
                    target_roles=['Engineer'], target_grades=['Junior'], prerequisites={},
                    develops_skills=[dict(skill_id=skill, gain=gain, max_level=maximum)],
                    duration_hours=2, format='self_paced', upcoming_sessions=[])
        item.update(kwargs)
        return item
    employee = dict(employee_id='E1', full_name='Synthetic One', department='Engineering', role='Engineer',
                    grade='Junior', manager_id=None, hire_date='2025-01-01', tenure_months=21,
                    work_format='remote', preferred_language='ru',
                    career_goal=dict(target_role='Engineer', target_grade='Middle'),
                    skills={'LOW': 0, 'CORE': 2}, last_review_date='2026-09-01')
    other = dict(employee, employee_id='E2', full_name='Synthetic Two')
    events = [event('A_LOW', 'LOW', duration_hours=30), event('B_CORE', 'CORE', maximum=4),
              event('C_ADVANCED', 'CORE', maximum=4, prerequisites={'CORE': 3}),
              event('M_MANDATORY', 'CORE', gain=3, mandatory=True)]
    events[1]['develops_skills'].append({'skill_id': 'EXTRA', 'gain': 1, 'max_level': 3})
    return Dataset(date(2026, 10, 1), {'E1': employee, 'E2': other},
                   {e['event_id']: e for e in events},
                   {s: {'skill_id': s, 'name': s, 'description': s, 'type': 'hard', 'category': 'engineering'}
                    for s in ('LOW', 'CORE', 'EXTRA')},
                   {('Engineer', 'Junior'): {'role': 'Engineer', 'grade': 'Junior',
                     'required_skills': {'CORE': 2}, 'critical_skills': ['CORE']},
                    ('Engineer', 'Middle'): {'role': 'Engineer', 'grade': 'Middle',
                     'required_skills': {'LOW': 1, 'CORE': 4}, 'critical_skills': ['CORE']},
                    ('Engineer', 'Lead'): {'role': 'Engineer', 'grade': 'Lead',
                     'required_skills': {'CORE': 5}, 'critical_skills': ['CORE']}},
                   {}, {str(i): str(i) for i in range(6)}, {'as_of_date': '2026-10-01'}, {})


def history_row(record_id, event_id, status='completed', day='2026-09-02', employee_id='E1'):
    return dict(record_id=record_id, employee_id=employee_id, event_id=event_id, date=day,
                due_date=None, status=status, completion_pct=100 if status == 'completed' else 40,
                score=None, feedback_rating=None, assigned_by='self')


class NavigatorTests(unittest.TestCase):
    def test_critical_requirement_beats_lowest_skill_and_includes_real_effects(self):
        data = example_dataset()
        result = recommendations(data, 'E1')
        best = result['recommendations'][0]
        self.assertEqual(best['event_id'], 'B_CORE')
        self.assertEqual(result['baseline']['event_id'], 'A_LOW')
        self.assertEqual([s['event_id'] for s in best['steps']], ['B_CORE', 'C_ADVANCED'])
        self.assertLess(best['progress_after'], best['steps'][-1]['coverage_after'])
        self.assertEqual(next(i for i in best['impacts'] if i['skill_id'] == 'EXTRA')['required'], None)
        self.assertGreaterEqual(len({f['category'] for f in best['factors']}), 3)
        self.assertEqual(best['unlocked_event_ids'], ['C_ADVANCED'])
        RecommendationResponse.model_validate({k: v for k, v in result.items() if not k.startswith('_')})

    def test_mandatory_audience_prerequisites_dates_and_completed_are_checked(self):
        data = example_dataset()
        self.assertFalse(eligibility(data, 'E1', 'M_MANDATORY')['eligible'])
        self.assertFalse(eligibility(data, 'E1', 'C_ADVANCED')['eligible'])
        for change in ({'target_roles': ['Other']}, {'target_grades': ['Lead']},
                       {'format': 'offline', 'upcoming_sessions': ['2026-09-30']}):
            modified = deepcopy(data)
            modified.events_by_id['B_CORE'].update(change)
            self.assertFalse(eligibility(modified, 'E1', 'B_CORE')['eligible'])
        self.assertTrue(eligibility(data, 'E1', 'B_CORE')['eligible'])
        self.assertFalse(eligibility(data, 'E1', 'B_CORE', [{'event_id': 'B_CORE'}])['eligible'])

    def test_effects_respect_caps_and_completed_after_review_applies_once(self):
        data = example_dataset()
        data.history_by_employee['E1'] = [history_row('old', 'A_LOW', day='2026-08-20'),
                                          history_row('new', 'B_CORE'), history_row('duplicate', 'B_CORE'),
                                          history_row('future', 'C_ADVANCED', day='2026-10-02')]
        state = employee_state(data, 'E1', [{'event_id': 'B_CORE'}, {'event_id': 'B_CORE'}])
        self.assertEqual(state['employee']['skills']['CORE'], 3)
        self.assertEqual(state['employee']['skills']['LOW'], 0)
        self.assertEqual(data.employees_by_id['E1']['skills']['CORE'], 2)
        data.employees_by_id['E1']['skills']['CORE'] = 5
        self.assertEqual(employee_state(data, 'E1')['employee']['skills']['CORE'], 5)
        self.assertNotIn('B_CORE', [r['event_id'] for r in recommendations(data, 'E1')['_candidates']])

    def test_confirmation_replaces_in_progress_without_double_count(self):
        data = example_dataset()
        data.history_by_employee['E1'] = [history_row('started', 'B_CORE', 'in_progress')]
        admission = eligibility(data, 'E1', 'B_CORE')
        self.assertEqual(admission['action'], 'continue')
        self.assertEqual(admission['continued_record_id'], 'started')
        completion = {'event_id': 'B_CORE', 'continued_record_id': 'started', 'date': '2026-10-01'}
        state = employee_state(data, 'E1', [completion, completion])
        self.assertEqual(state['employee']['skills']['CORE'], 3)
        self.assertEqual(len(state['history']), 1)
        self.assertEqual(state['history'][0]['status'], 'completed')

    def test_history_signals_change_format_choice_without_inventing_causes(self):
        data = example_dataset()
        data.events_by_id = {name: dict(data.events_by_id['B_CORE'], event_id=name, title=name)
                             for name in ['A_OFFLINE', 'B_SELF_PACED']}
        data.events_by_id['A_OFFLINE'].update(format='offline', upcoming_sessions=['2026-10-01', '2026-10-10'])
        # Equal skill gains: recent abandoned sessions make the shorter/other format preferable.
        data.history_by_employee['E1'] = [history_row(str(i), 'A_OFFLINE', 'dropped', '2026-08-01') for i in range(4)]
        result = recommendations(data, 'E1')
        self.assertEqual(result['recommendations'][0]['event_id'], 'B_SELF_PACED')
        offline = next(r for r in result['_candidates'] if r['event_id'] == 'A_OFFLINE')
        self.assertIn('Причины пропусков неизвестны', offline['reason'])

    def test_employee_contract_and_no_mutation_or_raw_identity_leaks(self):
        data = example_dataset()
        original = deepcopy(data.employees_by_id)
        state = employee_state(data, 'E1', revision=7)
        Employee.model_validate(state['employee'])
        Trajectory.model_validate(state['trajectory'])
        self.assertEqual(state['employee']['revision'], 7)
        self.assertNotIn('manager_id', state['employee'])
        self.assertNotIn('hire_date', state['employee'])
        self.assertEqual(data.employees_by_id, original)

    def test_lead_without_goal_and_empty_requirements_are_explicit(self):
        data = example_dataset()
        data.employees_by_id['E1'].update(grade='Lead', career_goal=None, skills={'CORE': 5})
        lead_state = employee_state(data, 'E1')
        self.assertEqual(lead_state['trajectory']['status'], 'goal_needed')
        self.assertEqual(lead_state['next_requirements'], [])
        self.assertTrue(lead_state['current_requirements'])
        self.assertFalse(recommendations(data, 'E1')['recommendations'])
        data.role_profiles_by_key[('Engineer', 'Lead')].update(required_skills={}, critical_skills=[])
        state = employee_state(data, 'E1')
        self.assertEqual(state['trajectory']['status'], 'no_requirements')
        self.assertIsNone(state['trajectory']['coverage'])

    def test_hr_denominators_and_unique_participants(self):
        data = example_dataset()
        data.history_by_employee['E1'] = [history_row('a', 'A_LOW', 'dropped'), history_row('b', 'A_LOW')]
        overview = hr_overview(data)
        HROverview.model_validate(overview)
        core = next(g for g in overview['skill_gaps'] if g['skill_id'] == 'CORE')
        self.assertEqual(core['eligible_population'], 2)
        self.assertEqual(core['mean_gap'], 2)
        activity = next(a for a in overview['activities'] if a['event_id'] == 'A_LOW')
        self.assertEqual(activity['unique_participants'], 1)
        self.assertEqual(activity['completion_rate'], 50)

    def test_repeatable_club_uses_distinct_sessions_and_replay_is_idempotent(self):
        data = example_dataset()
        data.events_by_id['EV_036'] = dict(data.events_by_id['B_CORE'], event_id='EV_036',
            format='online', upcoming_sessions=['2026-10-02', '2026-10-10'])
        data.history_by_employee['E1'] = [history_row('club1', 'EV_036', day='2026-09-02')]
        self.assertEqual(eligibility(data, 'E1', 'EV_036')['event_session_key'], '2026-10-02')
        done = {'event_id': 'EV_036', 'date': '2026-10-01', 'event_session_key': '2026-10-02'}
        self.assertEqual(employee_state(data, 'E1', [done, done])['employee']['skills']['CORE'], 4)
        self.assertEqual(eligibility(data, 'E1', 'EV_036', [done])['event_session_key'], '2026-10-10')


if __name__ == '__main__':
    unittest.main()
