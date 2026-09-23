import unittest
from copy import deepcopy
from datetime import date
from loaders import Dataset, load_dataset
from models import Activity
from scoring import rank_events, rejection_reasons

class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.data = load_dataset()
        self.employee = self.data.employees['E0005']

    def test_system_design_beats_lowest_public_speaking_after_three_declines(self):
        d = self.data
        e = self.employee
        profile = next(p for p in d.skills_file.role_profiles if p.role == e.role and p.grade == 'Senior')
        e.skills.update(profile.required_skills)
        e.skills['SK_SYSTEM_DESIGN'] = 2
        e.skills['SK_PUBLIC_SPEAKING'] = 0
        profile.critical_skills = ['SK_SYSTEM_DESIGN']
        profile.required_skills['SK_SYSTEM_DESIGN'] = 4
        profile.required_skills['SK_PUBLIC_SPEAKING'] = 3
        speaking = deepcopy(d.events['EV_006'])
        speaking.event_id = 'TEST_SPEAKING'
        speaking.title = 'Public Speaking'
        speaking.type = 'meetup'
        speaking.prerequisites = {}
        speaking.develops_skills = [speaking.develops_skills[0].model_copy(update={'skill_id': 'SK_PUBLIC_SPEAKING', 'gain': 3})]
        d.events = {'EV_006': d.events['EV_006'], speaking.event_id: speaking}
        d.history_by_employee[e.employee_id] = [Activity(record_id=f'TEST_{i}', employee_id=e.employee_id,
            event_id=speaking.event_id, date=date(2026, 9, i+1), due_date=None, status='declined',
            completion_pct=0, score=None, feedback_rating=None, assigned_by='manager') for i in range(3)]
        before = e.model_dump()
        result = rank_events(e, d)
        self.assertEqual(result[0]['event_id'], 'EV_006')
        self.assertEqual(result[0]['factors']['skills'][0]['name'], 'System Design')
        self.assertEqual(result[1]['factors']['history']['declined_or_no_show'], 3)
        self.assertLess(result[1]['factors']['values']['history_fit'], 0)
        self.assertEqual(before, e.model_dump())

    def test_all_real_recommendations_meet_filters(self):
        for employee in self.data.employees.values():
            for result in rank_events(employee, self.data):
                event = self.data.events[result['event_id']]
                self.assertFalse(rejection_reasons(employee, event, self.data))
                self.assertFalse(event.mandatory)
                self.assertEqual(result['score'], sum(result['factors']['weighted'].values()))

    def test_completion_exception_and_in_progress(self):
        e, d = self.employee, self.data
        for event_id in ['EV_006', 'EV_036']:
            record = Activity(record_id='T', employee_id=e.employee_id, event_id=event_id,
                date=date(2026,9,1), due_date=None, status='completed', completion_pct=100,
                score=None, feedback_rating=None, assigned_by='self')
            d.history_by_employee[e.employee_id] = [record]
            reasons = rejection_reasons(e, d.events[event_id], d)
            self.assertEqual('already_completed' in reasons, event_id != 'EV_036')
            record.status = 'in_progress'
            self.assertIn('in_progress', rejection_reasons(e, d.events[event_id], d))

    def test_max_level_headroom_and_missing_skill(self):
        e, d = self.employee, self.data
        d.history_by_employee[e.employee_id] = []
        e.skills['SK_SYSTEM_DESIGN'] = 5
        e.skills.pop('SK_API_DESIGN', None)
        d.events = {'EV_006': d.events['EV_006']}
        result = rank_events(e, d)[0]
        self.assertNotIn('SK_SYSTEM_DESIGN', [s['skill_id'] for s in result['factors']['skills']])
        api = next(s for s in result['factors']['skills'] if s['skill_id'] == 'SK_API_DESIGN')
        self.assertEqual(api['current'], 0)
        self.assertEqual(api['gap_closure'], 1)

if __name__ == '__main__':
    unittest.main()
