"""Local demo credentials are scoped per identity; never trust a browser role switch."""
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path


class AccessDirectory:
    def __init__(self, secret=None):
        self.secret = (secret or os.getenv('AUTH_SECRET') or secrets.token_urlsafe(48)).encode()
        self.overrides = json.loads(os.getenv('EMPLOYEE_ACCESS_CODES', '{}'))
        self.hr_override = os.getenv('HR_ACCESS_CODE')

    def employee_code(self, employee_id):
        return self.overrides.get(employee_id) or self._code('employee:' + employee_id)

    def _code(self, identity):
        return hmac.new(self.secret, identity.encode(), hashlib.sha256).hexdigest()[:16]

    @property
    def hr_code(self):
        return self.hr_override or self._code('role:hr')

    def valid(self, role, employee_id, code):
        expected = self.hr_code if role == 'hr' else self.employee_code(employee_id or '')
        return hmac.compare_digest(expected.encode(), code.encode())

    def export_demo_accounts(self, dataset, path):
        """A local, ignored handoff file for the person operating the synthetic demo."""
        accounts = {'hr': {'actor_role': 'hr', 'access_code': self.hr_code}, 'employees': [
            {'employee_id': eid, 'full_name': row['full_name'],
             'role': row['role'], 'grade': row['grade'], 'access_code': self.employee_code(eid)}
            for eid, row in dataset.employees_by_id.items()]}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding='utf-8')
