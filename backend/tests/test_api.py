"""Risk-focused integration tests with isolated SQLite and synthetic starter-kit data."""
from copy import deepcopy
import json
import io
import zipfile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.tests.test_imports import dataset_bytes, history_bytes


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.raw = dataset_bytes()
        for name, blob in self.raw.items(): (self.data / name).write_bytes(blob)
        self.env = patch.dict('os.environ', {'AI_PROVIDER':'none','AI_ALLOW_EXTERNAL_DERIVED':'false',
                                            'HR_ACCESS_CODE':'', 'EMPLOYEE_ACCESS_CODES':'{}'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = create_app(self.data, self.root/'test.sqlite3', auth_secret='synthetic-test-secret')
        self.client = self.enterContext(TestClient(self.app))

    def login(self, employee_id='E1', role='employee', client=None):
        code = self.app.state.access.hr_code if role=='hr' else self.app.state.access.employee_code(employee_id)
        response = (client or self.client).post('/api/login', json={
            'actor_role':role,'employee_id':employee_id,'access_code':code})
        self.assertEqual(response.status_code,200,response.text)

    def test_employee_cannot_read_foreign_profile_history_or_hr_and_role_cannot_be_spoofed(self):
        self.assertEqual(self.client.get('/api/me').status_code,401)
        self.assertEqual(self.client.get('/api/employees').status_code,401)
        self.login()
        for path in ('/api/hr/overview','/api/employees','/api/employees/E2/history',
                     '/api/employees/E2/profile','/api/employees/E2/routes','/api/admin/ai-test'):
            response=self.client.post(path) if path.endswith('ai-test') else self.client.get(path)
            self.assertEqual(response.status_code,403,path)
            self.assertEqual(response.json()['error']['code'],'FORBIDDEN')
        me=self.client.get('/api/me').json()
        self.assertEqual(me['employee']['employee_id'],'E1')
        self.assertNotIn('manager_id',me['employee'])
        self.assertNotIn('hire_date',me['employee'])
        response=self.client.post('/api/login',json={'actor_role':'hr','employee_id':'E1',
                                    'access_code':self.app.state.access.employee_code('E1')})
        self.assertEqual(response.status_code,401)

    def test_completed_effect_is_idempotent_persistent_and_does_not_promote(self):
        self.login()
        before=self.client.get('/api/me').json()['employee']
        rec=self.client.get('/api/me/recommendations').json()['recommendations'][0]
        self.assertEqual(rec['event_id'],'B_CORE')
        body={'event_id':rec['event_id'],'idempotency_key':'once','revision':before['revision'],'demo_confirmed':True}
        response=self.client.post('/api/me/completions',json=body)
        self.assertEqual(response.status_code,200,response.text)
        after=response.json()['employee']
        self.assertEqual(after['skills']['CORE'],3)
        self.assertEqual(after['grade'],before['grade'])
        self.assertEqual(after['revision'],before['revision']+1)
        self.assertEqual(self.client.post('/api/me/completions',json=body).json()['employee'],after)
        self.assertEqual(self.client.post('/api/me/completions',json={**body,'revision':after['revision'],
                                                                    'idempotency_key':'another'}).status_code,409)
        self.client.post('/api/logout')
        self.login()
        self.assertEqual(self.client.get('/api/me').json()['employee'],after)
        self.assertNotIn('B_CORE',[r['event_id'] for r in self.client.get('/api/me/recommendations').json()['recommendations']])

    def test_profile_and_hr_do_not_call_model_and_history_is_separate(self):
        self.login(role='hr')
        with patch('backend.app.application.rank_and_explain',side_effect=AssertionError('Model must not run')):
            self.assertEqual(self.client.get('/api/me').status_code,200)
            overview=self.client.get('/api/hr/overview')
            self.assertEqual(overview.status_code,200)
            self.assertIn('skill_gaps',overview.json())
            self.assertIn('no_next_step',overview.json())
            rec=self.client.get('/api/me/recommendations').json()
            self.assertEqual(rec['mode'],'fallback')
            self.assertNotIn('_candidates',rec)

    def test_completion_requires_confirmation_and_current_revision(self):
        self.login()
        body={'event_id':'B_CORE','idempotency_key':'x','revision':0}
        self.assertEqual(self.client.post('/api/me/completions',json=body).status_code,422)
        changed=self.client.patch('/api/me/target',json={'role':'Engineer','grade':'Lead','revision':0})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(self.client.post('/api/me/completions',json={**body,'demo_confirmed':True}).status_code,409)
        self.assertEqual(self.client.patch('/api/me/target',json={'role':'Engineer','grade':'Middle','revision':0}).status_code,409)

    def test_additional_import_uses_new_ids_and_failure_leaves_active_data_untouched(self):
        self.login(role='hr')
        extra=deepcopy(json.loads(self.raw['employees.json'])['employees'][0])
        extra['employee_id']='JURY_987'
        files={'employees':('employees.json',json.dumps({'employees':[extra]}),'application/json'),
               'history':('activity_history.csv',history_bytes([]),'text/csv')}
        response=self.client.post('/api/hr/import',data={'mode':'append'},files=files)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['counts']['employees'],3)
        self.assertEqual(self.client.get('/api/employees/JURY_987/profile').status_code,200)
        duplicate=self.client.post('/api/hr/import',data={'mode':'append'},files=files)
        self.assertEqual(duplicate.status_code,409,duplicate.text)
        self.assertEqual(self.client.get('/api/employees').json()['total'],3)
        extra['employee_id']='BAD'
        extra['skills']['CORE']=9
        files['employees']=('employees.json',json.dumps({'employees':[extra]}),'application/json')
        invalid=self.client.post('/api/hr/import',data={'mode':'append'},files=files)
        self.assertEqual(invalid.status_code,422,invalid.text)
        self.assertIn('skills.CORE',invalid.json()['error']['details'][0]['path'])
        self.assertEqual(self.client.get('/api/employees').json()['total'],3)

    def test_disabled_model_returns_honest_fallback_and_valid_numbers(self):
        self.login()
        local=self.client.get('/api/me/recommendations').json()
        ai=self.client.get('/api/me/recommendations?ai=true').json()
        self.assertEqual(ai['mode'],'fallback')
        self.assertEqual(ai['fallback_reason'],'disabled')
        self.assertEqual(ai['recommendations'],local['recommendations'])

    def test_zip_replacement_invalidates_other_sessions_and_persists_snapshot(self):
        other=TestClient(self.app)
        self.addCleanup(other.close)
        self.login(client=other)
        self.login(role='hr')
        bad=self.client.post('/api/hr/import',data={'mode':'replace'},
                             files={'dataset_zip':('bad.zip',b'not zip','application/zip')})
        self.assertEqual(bad.status_code,422)
        changed=deepcopy(self.raw)
        employees=json.loads(changed['employees.json'])
        for index,row in enumerate(employees['employees']): row['employee_id']=f'NEW_{index}'
        changed['employees.json']=json.dumps(employees).encode()
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            for name,blob in changed.items(): archive.writestr('starter/'+name,blob)
        response=self.client.post('/api/hr/import',data={'mode':'replace'},
                   files={'dataset_zip':('starter.zip',buffer.getvalue(),'application/zip')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(self.client.get('/api/session').json()['employee_id'])
        self.assertEqual(other.get('/api/me').status_code,409)
        # A restart restores the validated imported files rather than the original kit.
        reopened=create_app(database_path=self.root/'test.sqlite3',auth_secret='synthetic-test-secret')
        with TestClient(reopened) as client:
            reply=client.post('/api/login',json={'actor_role':'employee','employee_id':'NEW_0',
                  'access_code':reopened.state.access.employee_code('NEW_0')})
            self.assertEqual(reply.status_code,200)
            self.assertEqual(client.get('/api/me').json()['employee']['employee_id'],'NEW_0')

    def test_hr_observes_confirmed_employee_progress(self):
        self.login()
        before=self.client.get('/api/me').json()['employee']
        response=self.client.post('/api/me/completions',json={'event_id':'B_CORE','idempotency_key':'hr-visible',
            'revision':before['revision'],'demo_confirmed':True})
        self.assertEqual(response.status_code,200)
        self.login(role='hr')
        activity=next(a for a in self.client.get('/api/hr/overview').json()['activities'] if a['event_id']=='B_CORE')
        self.assertEqual(activity['completed_records'],1)
        self.assertEqual(activity['completion_rate'],100)


if __name__=='__main__':
    unittest.main()
