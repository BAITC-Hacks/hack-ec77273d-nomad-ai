"""Standard-library ASGI checks: no pytest/httpx dependency needed."""
import asyncio
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from main import create_app
from explanations import explain, template
from loaders import DEFAULT_DATA, load_dataset
from scoring import rank_events

async def request(app, method, path, body=None):
    path, _, query = path.partition('?')
    sent = []
    payload = json.dumps(body).encode() if body is not None else b''
    async def receive():
        return {'type': 'http.request', 'body': payload, 'more_body': False}
    async def send(message):
        sent.append(message)
    await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
               'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
               'query_string': query.encode(), 'root_path': '', 'headers': [(b'content-type', b'application/json')],
               'server': ('test', 80), 'client': ('test', 1)}, receive, send)
    status = next(m['status'] for m in sent if m['type'] == 'http.response.start')
    raw = b''.join(m.get('body', b'') for m in sent if m['type'] == 'http.response.body')
    return status, json.loads(raw)

class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {'OPENAI_API_KEY': ''})
        self.env.start()
        self.app = create_app()
        self.context = self.app.router.lifespan_context(self.app)
        await self.context.__aenter__()

    async def asyncTearDown(self):
        await self.context.__aexit__(None, None, None)
        self.env.stop()

    async def test_01_profile_real_data(self):
        status, body = await request(self.app, 'GET', '/employees/E0005')
        self.assertEqual(status, 200)
        self.assertEqual(body['profile']['employee_id'], 'E0005')
        self.assertTrue(body['skills'])
        self.assertTrue(0 <= body['overall_progress_pct'] <= 100)
        self.assertEqual((await request(self.app, 'GET', '/employees/missing'))[0], 404)

    async def test_02_recommendations_real_data(self):
        status, body = await request(self.app, 'GET', '/employees/E0005/recommendations')
        self.assertEqual(status, 200)
        self.assertTrue(1 <= len(body['recommendations']) <= 3)
        self.assertTrue(all(r['explanation_source'] == 'template' for r in body['recommendations']))

    async def test_03_completion_idempotent_and_capped(self):
        _, before = await request(self.app, 'GET', '/employees/E0005')
        _, recs = await request(self.app, 'GET', '/employees/E0005/recommendations')
        eid = recs['recommendations'][0]['event_id']
        url = f'/employees/E0005/activities/{eid}/complete'
        status, after = await request(self.app, 'POST', url)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(after['overall_progress_pct'], before['overall_progress_pct'])
        event = self.app.state.data.events[eid]
        for development in event.develops_skills:
            old = before['profile']['skills'].get(development.skill_id, 0)
            expected = old + min(development.gain, max(0, development.max_level - old))
            self.assertEqual(after['profile']['skills'][development.skill_id], expected)
        _, repeat = await request(self.app, 'POST', url)
        self.assertTrue(repeat['already_completed'])
        self.assertEqual(after['profile']['skills'], repeat['profile']['skills'])
        self.assertEqual((await request(self.app, 'POST', url + '?expected_version=1'))[0], 409)
        _, new_recs = await request(self.app, 'GET', '/employees/E0005/recommendations')
        if eid != 'EV_036':
            self.assertNotIn(eid, [r['event_id'] for r in new_recs['recommendations']])

    async def test_04_hr_gate_and_rates(self):
        self.assertEqual((await request(self.app, 'GET', '/hr/overview'))[0], 403)
        status, body = await request(self.app, 'GET', '/hr/overview?role=hr')
        self.assertEqual(status, 200)
        self.assertEqual(body['employee_count'], 200)
        self.assertEqual(len(body['event_participation']), 40)
        self.assertTrue(body['critical_gaps'])

    async def test_05_reload_atomic_and_replaces(self):
        employees = json.loads((DEFAULT_DATA / 'employees.json').read_text())
        history = (DEFAULT_DATA / 'activity_history.csv').read_text()
        payload = {'employees': employees, 'activity_history_csv': history}
        self.assertEqual((await request(self.app, 'POST', '/admin/reload-dataset', payload))[0], 403)
        employees['employees'][0]['full_name'] = 'Judge replacement'
        status, _ = await request(self.app, 'POST', '/admin/reload-dataset?role=hr', payload)
        self.assertEqual(status, 200)
        _, profile = await request(self.app, 'GET', '/employees/E0001')
        self.assertEqual(profile['profile']['full_name'], 'Judge replacement')
        payload['activity_history_csv'] = history.replace('EV_035', 'MISSING', 1)
        version = self.app.state.version
        self.assertEqual((await request(self.app, 'POST', '/admin/reload-dataset?role=hr', payload))[0], 422)
        self.assertEqual(self.app.state.version, version)
        self.assertEqual(self.app.state.data.employees['E0001'].full_name, 'Judge replacement')

    async def test_06_merge_judge_profile_retains_other_employees(self):
        employees = json.loads((DEFAULT_DATA / 'employees.json').read_text())
        judge = employees['employees'][0]
        judge['employee_id'] = 'JUDGE_001'
        employees['employees'] = [judge]
        history_header = (DEFAULT_DATA / 'activity_history.csv').read_text().splitlines()[0] + '\n'
        before = dict(self.app.state.data.employees['E0005'].skills)
        payload = {'employees': employees, 'activity_history_csv': history_header}
        status, result = await request(self.app, 'POST', '/admin/reload-dataset?role=hr&mode=merge', payload)
        self.assertEqual(status, 200)
        self.assertEqual(result['employees'], 201)
        self.assertEqual(self.app.state.data.employees['E0005'].skills, before)
        self.assertEqual((await request(self.app, 'GET', '/employees/JUDGE_001/recommendations'))[0], 200)

class ExplanationTests(unittest.TestCase):
    def setUp(self):
        data = load_dataset()
        self.rec = rank_events(data.employees['E0005'], data)[0]

    def test_missing_key(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertEqual(explain(self.rec)['explanation'], template(self.rec))

    def test_failure_and_empty_response(self):
        client = Mock()
        client.chat.completions.create.side_effect = TimeoutError('offline')
        self.assertEqual(explain(self.rec, client)['explanation'], template(self.rec))
        client.chat.completions.create.assert_called_once()
        client.chat.completions.create.side_effect = None
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=''))])
        self.assertEqual(explain(self.rec, client)['explanation_source'], 'template')

    def test_success_exactly_one_call(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='A grounded explanation.'))])
        self.assertEqual(explain(self.rec, client)['explanation_source'], 'llm')
        client.chat.completions.create.assert_called_once()

if __name__ == '__main__':
    unittest.main()
