"""Measure actual API latency against the starter kit in an isolated temporary database."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from fastapi.testclient import TestClient
from backend.app.application import create_app


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',default=str(ROOT/'backend/Dataset'))
    parser.add_argument('--output',default=str(ROOT/'var/verification.json'))
    parser.add_argument('--synthetic-ai',action='store_true',help='One real request using only the fixed synthetic AI lab case')
    args=parser.parse_args()
    report={'measured_at':datetime.now(timezone.utc).isoformat(),'transport':'FastAPI TestClient in this workspace',
            'python':sys.version.split()[0], 'timings_ms':{}}
    with TemporaryDirectory() as folder:
        app=create_app(args.data_dir,Path(folder)/'check.sqlite3',auth_secret='isolated-verification-secret')
        with TestClient(app) as client:
            report['dataset']=client.get('/api/health').json()
            def login(eid=None,role='employee'):
                code=app.state.access.hr_code if role=='hr' else app.state.access.employee_code(eid)
                response=client.post('/api/login',json={'actor_role':role,'employee_id':eid,'access_code':code})
                response.raise_for_status()
            def measure(name,path,post=False):
                started=perf_counter()
                result=client.post(path) if post else client.get(path)
                report['timings_ms'][name]=round((perf_counter()-started)*1000,2)
                result.raise_for_status()
                return result.json()
            for eid in ('E0015','E0137'):
                if eid not in app.state.runtime['data'].employees_by_id:
                    continue
                login(eid)
                measure('profile_'+eid,'/api/me')
                recs=measure('recommendations_'+eid,'/api/me/recommendations')
                report[eid]={'mode':recs['mode'],'route_count':len(recs['recommendations'])}
            login(role='hr')
            measure('hr_cold','/api/hr/overview')
            measure('hr_cached','/api/hr/overview')
            measure('directory_first_page','/api/employees?limit=50')
            if args.synthetic_ai:
                ai=measure('synthetic_ai','/api/admin/ai-test',True)
                report['synthetic_ai']={'mode':ai['mode'],'fallback_reason':ai['fallback_reason'],
                                        'diagnostics':ai.get('diagnostics',{})}
    report['within_profile_hr_budget']=all(v<2000 for k,v in report['timings_ms'].items()
                                           if k.startswith(('profile_','hr_','directory_')))
    report['within_recommendation_budget']=all(v<10000 for k,v in report['timings_ms'].items()
                                               if k.startswith(('recommendations_','synthetic_ai')))
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0 if report['within_profile_hr_budget'] and report['within_recommendation_budget'] else 1


if __name__=='__main__':
    raise SystemExit(main())
