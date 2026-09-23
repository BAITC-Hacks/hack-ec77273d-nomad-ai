import json
import os
import time
import unittest
from datetime import date
from unittest.mock import patch
from explanations import explain_many, template
from loaders import DEFAULT_DATA, Dataset, load_dataset
from models import Activity, EmployeesFile, EventsFile, SkillsFile
from scoring import rank_events, target_profile, recommendation_rejections

class RequirementTests(unittest.TestCase):
    def setUp(self):
        self.data = load_dataset()

    def test_no_goal_uses_next_grade_and_lead_does_not_overflow(self):
        e = self.data.employees['E0005'].model_copy(deep=True)
        e.career_goal = None
        self.assertEqual(target_profile(e, self.data).grade, 'Senior')
        e.grade = 'Lead'
        self.assertEqual(target_profile(e, self.data).grade, 'Lead')

    def test_no_benefit_event_is_not_recommended(self):
        e = self.data.employees['E0005']
        e.skills.update(target_profile(e, self.data).required_skills)
        self.assertEqual(rank_events(e, self.data), [])
        self.assertIn('no_target_gap_closure', recommendation_rejections(e, self.data.events['EV_006'], self.data))

    def test_post_review_history_applied_once_with_caps(self):
        skills = SkillsFile.model_validate_json((DEFAULT_DATA/'skills.json').read_text())
        employees = EmployeesFile.model_validate_json((DEFAULT_DATA/'employees.json').read_text())
        events = EventsFile.model_validate_json((DEFAULT_DATA/'events.json').read_text())
        e = employees.employees[0]
        e.last_review_date = date(2026, 9, 1)
        e.skills['SK_SYSTEM_DESIGN'] = 2
        e.skills['SK_API_DESIGN'] = 4  # EV_005 cap is 3; never decrease.
        def record(record_id, day, status):
            return Activity(record_id=record_id, employee_id=e.employee_id, event_id='EV_005',
                date=date(2026,9,day), due_date=None, status=status, completion_pct=100 if status=='completed' else 0,
                score=None, feedback_rating=None, assigned_by='self')
        d = Dataset(skills, employees, events, [record('before',1,'completed'),record('after',2,'completed'),record('decline',3,'declined')])
        self.assertEqual(d.employees[e.employee_id].skills['SK_SYSTEM_DESIGN'], 3)
        self.assertEqual(d.employees[e.employee_id].skills['SK_API_DESIGN'], 4)
        self.assertEqual(d.assessment_skills[e.employee_id]['SK_SYSTEM_DESIGN'], 2)
        rank_events(e, d)
        rank_events(e, d)
        self.assertEqual(e.skills['SK_SYSTEM_DESIGN'], 3)

    def test_every_template_has_grade_gap_and_history(self):
        for employee in self.data.employees.values():
            for rec in rank_events(employee, self.data):
                text = template(rec)
                self.assertIn(rec['factors']['target']['grade'], text)
                self.assertIn('needs', text)
                self.assertTrue(any(word in text for word in ['declines', 'on-time']))

    def test_slow_llm_respects_shared_deadline(self):
        recs = rank_events(self.data.employees['E0005'], self.data)
        def slow(_):
            time.sleep(.2)
            return {'explanation':'late', 'explanation_source':'llm'}
        with patch.dict(os.environ, {'OPENAI_API_KEY':'test-only'}), patch('explanations.explain', side_effect=slow):
            start = time.monotonic()
            output = explain_many(recs, timeout=.03)
            self.assertLess(time.monotonic()-start, .15)
            self.assertTrue(all(r['explanation_source']=='template' for r in output))
            self.assertEqual([r['event_id'] for r in output], [r['event_id'] for r in recs])

if __name__ == '__main__':
    unittest.main()
