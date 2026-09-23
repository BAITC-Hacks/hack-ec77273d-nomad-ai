from copy import deepcopy
import csv
import io
import json
import unittest

from backend.app.engine.data_loader import DataValidationError, HISTORY_FIELDS, load_dataset_bytes, merge_additional_dataset
from backend.app.engine.navigator import employee_state
from backend.tests.test_navigator import example_dataset, history_row


def history_bytes(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=HISTORY_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def dataset_bytes():
    data = example_dataset()
    raw = {}
    for name, key, values in [('employees.json', 'employees', data.employees_by_id.values()),
                               ('events.json', 'events', data.events_by_id.values())]:
        raw[name] = json.dumps({'meta': data.meta, key: list(values)}).encode()
    raw['skills.json'] = json.dumps({'meta': data.meta, 'skills': list(data.skills_by_id.values()),
        'role_profiles': list(data.role_profiles_by_key.values()), 'proficiency_scale': data.proficiency_scale}).encode()
    raw['activity_history.csv'] = history_bytes([])
    return raw


class ImportTests(unittest.TestCase):
    def test_additional_unseen_employee_and_history_load(self):
        base = load_dataset_bytes(dataset_bytes())
        employee = deepcopy(base.employees_by_id['E1'])
        employee.update(employee_id='JURY_NEW_987', full_name='Synthetic Jury Profile')
        merged = merge_additional_dataset(base, json.dumps({'employees': [employee]}).encode(),
            history_bytes([history_row('JURY_RECORD', 'B_CORE', employee_id='JURY_NEW_987')]))
        self.assertEqual(merged.counts['employees'], 3)
        self.assertEqual(employee_state(merged, 'JURY_NEW_987')['employee']['skills']['CORE'], 3)
        self.assertEqual(base.counts['employees'], 2)

    def test_api_effective_employee_cannot_be_reimported_as_snapshot(self):
        base = load_dataset_bytes(dataset_bytes())
        public = employee_state(base, 'E1')['employee']
        public['employee_id'] = 'NEW'
        with self.assertRaises(DataValidationError) as caught:
            merge_additional_dataset(base, json.dumps([public]).encode(), history_bytes([]))
        self.assertTrue(any(d['path'].endswith('.hire_date') for d in caught.exception.details))

    def test_out_of_range_boolean_and_numeric_string_skill_have_precise_paths(self):
        for value in (6, True, '3'):
            raw = dataset_bytes()
            employees = json.loads(raw['employees.json'])
            employees['employees'][0]['skills']['CORE'] = value
            raw['employees.json'] = json.dumps(employees).encode()
            with self.assertRaises(DataValidationError) as caught:
                load_dataset_bytes(raw)
            self.assertEqual(caught.exception.details[0]['path'], 'employees[0].skills.CORE')

    def test_unknown_reference_duplicate_record_and_bad_status_rejected(self):
        for rows in ([history_row('a', 'UNKNOWN')], [history_row('a', 'B_CORE')] * 2,
                     [dict(history_row('a', 'B_CORE'), status='invented')]):
            raw = dataset_bytes()
            raw['activity_history.csv'] = history_bytes(rows)
            with self.assertRaises(DataValidationError):
                load_dataset_bytes(raw)

    def test_dates_are_snapshot_based_and_configurable(self):
        self.assertEqual(load_dataset_bytes(dataset_bytes()).as_of_date.isoformat(), '2026-10-01')
        self.assertEqual(load_dataset_bytes(dataset_bytes(), '2027-01-01').as_of_date.isoformat(), '2027-01-01')
        with self.assertRaises(DataValidationError):
            load_dataset_bytes(dataset_bytes(), 'today')
        with self.assertRaises(DataValidationError):
            load_dataset_bytes(dataset_bytes(), '2025-01-01')
        advanced = load_dataset_bytes(dataset_bytes(), '2027-01-01')
        self.assertEqual(employee_state(advanced, 'E1')['employee']['tenure_months'], 24)

    def test_append_mismatched_snapshot_is_rejected(self):
        base = load_dataset_bytes(dataset_bytes())
        payload = {'meta': {'as_of_date': '2027-01-01'}, 'employees': []}
        with self.assertRaises(DataValidationError) as caught:
            merge_additional_dataset(base, json.dumps(payload).encode(), history_bytes([]))
        self.assertEqual(caught.exception.details[0]['path'], 'employees.json.meta.as_of_date')

    def test_malformed_csv_and_future_assessment_return_validation_details(self):
        for content in (history_bytes([]) + b'"unterminated',
                        history_bytes([]) + b'short,row\n',
                        history_bytes([]).replace(b'record_id,', b'record_id,record_id,')):
            raw = dataset_bytes()
            raw['activity_history.csv'] = content
            with self.assertRaises(DataValidationError) as caught:
                load_dataset_bytes(raw)
            self.assertTrue(caught.exception.details[0]['path'].startswith('activity_history'))
        raw = dataset_bytes()
        profiles = json.loads(raw['employees.json'])
        profiles['employees'][0]['last_review_date'] = '2026-12-31'
        raw['employees.json'] = json.dumps(profiles).encode()
        with self.assertRaises(DataValidationError) as caught:
            load_dataset_bytes(raw)
        self.assertEqual(caught.exception.details[0]['path'], 'employees[0].last_review_date')


if __name__ == '__main__':
    unittest.main()
