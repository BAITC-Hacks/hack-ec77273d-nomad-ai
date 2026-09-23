"""Authenticated Career Navigator API. Numeric scenarios never depend on an LLM."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date
import os
from pathlib import Path
import threading
from time import perf_counter
from typing import Literal
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .access import AccessDirectory
from .engine.ai_layer import rank_and_explain, clear_rank_cache
from .engine.data_loader import (FILES, REPEATABLE_EVENT_IDS, load_dataset, load_dataset_bytes,
                                 merge_additional_dataset, DataValidationError)
from .engine.navigator import employee_state, recommendations, eligibility, hr_overview
from .importing import MAX_FILE_BYTES, read_zip
from .repository import Repository

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).with_name('static')
load_dotenv(ROOT / '.env', override=False)


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Login(Input):
    actor_role: Literal['employee', 'hr']
    employee_id: str | None = None
    access_code: str = Field(min_length=1, max_length=256)


class TargetInput(Input):
    role: str = Field(min_length=1, max_length=200)
    grade: Literal['Junior', 'Middle', 'Senior', 'Lead']
    revision: int = Field(ge=0)


class CompletionInput(Input):
    event_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    demo_confirmed: Literal[True]


def _error(code, message, details=None):
    return {'error': {'code': code, 'message': message, 'details': details or []}}


def create_app(data_dir=None, database_path=None, auth_secret=None, as_of_date=None):
    configured = Path(data_dir or os.getenv('DATA_DIR', str(ROOT / 'data')))
    if not configured.is_absolute():
        configured = ROOT / configured
    if data_dir is None and not all((configured / f).exists() for f in FILES):
        configured = ROOT / 'backend' / 'Dataset'
    db_path = Path(database_path or os.getenv('DATABASE_PATH', str(ROOT / 'var/nomad.sqlite3')))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    repo = Repository(db_path)
    access = AccessDirectory(auth_secret)
    lock = threading.RLock()
    state = {'data': None, 'import': None, 'generation': 0, 'hr_cache': None}
    snapshot_override = as_of_date or os.getenv('AS_OF_DATE') or None

    @asynccontextmanager
    async def lifespan(app):
        repo.initialize()
        saved = repo.get_setting('active_dataset') if data_dir is None else None
        directory = Path(saved) if saved else configured
        data = load_dataset(directory, as_of_override=snapshot_override)
        imported = repo.register_import(as_of_date=data.as_of_date.isoformat(),
                                        manifest=data.manifest, counts=data.counts)
        state.update(data=data, **{'import': imported})
        access.export_demo_accounts(data, db_path.parent / 'demo_accounts.json')
        yield

    app = FastAPI(title='Career Quest · Career Navigator', version='2.0.0', lifespan=lifespan)
    app.state.repository = repo
    app.state.access = access
    app.state.runtime = state
    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.middleware('http')
    async def timing_and_origin(request, call_next):
        started = perf_counter()
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                return JSONResponse(_error('FORBIDDEN', 'Источник запроса не разрешён'), status_code=403)
        response = await call_next(request)
        elapsed = (perf_counter() - started) * 1000
        response.headers['Server-Timing'] = f'app;dur={elapsed:.2f}'
        response.headers['X-Response-Time-Ms'] = f'{elapsed:.2f}'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        codes = {401:'UNAUTHORIZED',403:'FORBIDDEN',404:'NOT_FOUND',409:'CONFLICT',
                 422:'VALIDATION_ERROR',429:'TOO_MANY_REQUESTS',503:'UNAVAILABLE'}
        return JSONResponse(_error(codes.get(exc.status_code,'ERROR'), str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{'path': '.'.join(map(str, e['loc'][1:])), 'message': e['msg']} for e in exc.errors()]
        return JSONResponse(_error('VALIDATION_ERROR','Некорректные данные запроса',details),status_code=422)

    @app.exception_handler(DataValidationError)
    async def data_error(request, exc):
        duplicate = any('duplicate' in d['message'].lower() or 'already exists' in d['message'].lower()
                        for d in exc.details)
        return JSONResponse(_error('CONFLICT' if duplicate else 'VALIDATION_ERROR','Некорректный импорт',exc.details),
                            status_code=409 if duplicate else 422)

    def current_data():
        if state['data'] is None:
            raise HTTPException(503, 'Датасет ещё не загружен')
        return state['data']

    def token_from(request):
        header = request.headers.get('authorization', '')
        return header[7:] if header.startswith('Bearer ') else request.cookies.get('career_session','')

    def actor(request: Request):
        try:
            session = repo.get_session(token_from(request))
        except PermissionError:
            raise HTTPException(401, 'Войдите в систему') from None
        if session['import_id'] != state['import']['import_id']:
            raise HTTPException(409, 'Датасет обновлён. Войдите заново.')
        return session

    def hr(session=Depends(actor)):
        if session['actor_role'] != 'hr':
            raise HTTPException(403, 'Доступ только для HR')
        return session

    def own(session):
        if not session['employee_id']:
            raise HTTPException(422, 'Для личного профиля войдите с ID сотрудника')
        if session['employee_id'] not in current_data().employees_by_id:
            raise HTTPException(409, 'Профиль отсутствует в новом датасете. Войдите заново.')
        return session['employee_id']

    def same_import(session):
        if session['import_id'] != state['import']['import_id']:
            raise HTTPException(409, 'Датасет обновлён. Войдите заново.')

    def authorize_employee(employee_id, session):
        if session['actor_role'] != 'hr' and employee_id != session['employee_id']:
            raise HTTPException(403, 'Доступ к чужому профилю запрещён')
        if employee_id not in current_data().employees_by_id:
            raise HTTPException(404, 'Сотрудник не найден')

    def progress(employee_id):
        result = repo.progress_state(state['import']['import_id'], employee_id)
        for row in result['completions']:
            row['completed_at'] = row['date']
            row['record_id'] = 'demo_' + row['completion_id']
        return result

    def profile(employee_id):
        data = current_data()
        p = progress(employee_id)
        result = employee_state(data, employee_id, completions=p['completions'], target=p['target'], revision=p['revision'])
        result['events'] = list(data.events_by_id.values())
        return result

    def local_recommendations(employee_id):
        data = current_data()
        p = progress(employee_id)
        return recommendations(data, employee_id, completions=p['completions'], target=p['target'], revision=p['revision'])

    def ranked_recommendations(employee_id, use_ai):
        data = current_data()
        result = local_recommendations(employee_id)
        candidates = result.pop('_candidates', result['recommendations'])
        allowed = result.pop('_rerankable_route_ids', [r['route_id'] for r in candidates])
        for key in list(result):
            if key.startswith('_'):
                result.pop(key)
        if not use_ai or not candidates:
            return result
        person = profile(employee_id)
        factors = {}
        for route in candidates:
            for factor in route['factors']:
                factors[factor['fact_id']] = factor
        facts = {
            'employee_id': employee_id, 'revision': result['revision'],
            'role': person['employee']['role'], 'grade': person['employee']['grade'],
            'target': person['employee']['target'], 'gaps': person['trajectory']['gaps'],
            'facts': list(factors.values()),
            'routes': [{'route_id': r['route_id'], 'title': r['title'],
                        'summary': ' → '.join(s['title'] for s in r['steps']),
                        'fact_ids': [f['fact_id'] for f in r['factors']]} for r in candidates],
            'rerankable_route_ids': allowed,
        }
        answer = rank_and_explain(facts)
        by_id = {r['route_id']: r for r in candidates}
        explanations = {e['route_id']: e for e in answer['explanations']}
        ordered = []
        for rid in answer['ordered_route_ids']:
            if rid in by_id:
                row = deepcopy(by_id[rid])
                if answer['mode'] == 'llm' and rid in explanations:
                    row['reason'] = explanations[rid]['text']
                ordered.append(row)
        result['recommendations'] = (ordered or candidates)[:3]
        result['mode'] = answer['mode']
        result['fallback_reason'] = answer['fallback_reason']
        return result

    @app.get('/', include_in_schema=False)
    @app.get('/employee', include_in_schema=False)
    @app.get('/hr', include_in_schema=False)
    @app.get('/boss', include_in_schema=False)
    def portal():
        return FileResponse(STATIC / 'portal.html')

    @app.get('/admin/ai', include_in_schema=False)
    def lab_page(session=Depends(hr)):
        return FileResponse(STATIC / 'ai_admin.html')

    @app.get('/api/health')
    def health():
        data = current_data()
        return {'status':'ok','as_of_date':data.as_of_date.isoformat(),'counts':data.counts}

    @app.post('/api/login')
    def login(body: Login, response: Response):
        data = current_data()
        if not access.valid(body.actor_role, body.employee_id, body.access_code):
            raise HTTPException(401, 'Неверные данные для входа')
        if body.actor_role == 'employee' and not body.employee_id:
            raise HTTPException(401, 'Неверные данные для входа')
        if body.employee_id and body.employee_id not in data.employees_by_id:
            raise HTTPException(401, 'Неверные данные для входа')
        token = repo.create_session(state['import']['import_id'], actor_role=body.actor_role,
                                    employee_id=body.employee_id)
        response.set_cookie('career_session',token,httponly=True,samesite='strict',
                            secure=os.getenv('COOKIE_SECURE','false').lower()=='true',max_age=86400)
        return {'actor_role':body.actor_role,'employee_id':body.employee_id,'as_of_date':data.as_of_date.isoformat()}

    @app.get('/api/session')
    def session_info(session=Depends(actor)):
        return {'actor_role':session['actor_role'],'employee_id':session['employee_id'],
                'as_of_date':current_data().as_of_date.isoformat()}

    @app.post('/api/logout')
    def logout(request: Request, response: Response):
        repo.revoke_session(token_from(request))
        response.delete_cookie('career_session')
        return {'ok':True}

    @app.get('/api/me')
    def me(session=Depends(actor)):
        return profile(own(session))

    @app.get('/api/me/recommendations')
    def my_recommendations(ai: bool=False, session=Depends(actor)):
        return ranked_recommendations(own(session),ai)

    @app.patch('/api/me/target')
    def change_target(body: TargetInput, session=Depends(actor)):
        eid = own(session)
        with lock:
            same_import(session)
            if (body.role,body.grade) not in current_data().role_profiles_by_key:
                raise HTTPException(422,'Такого целевого профиля нет в датасете')
            try:
                repo.set_target(state['import']['import_id'],eid,
                                {'role':body.role,'grade':body.grade,'source':'career_goal'},body.revision)
            except ValueError:
                raise HTTPException(409,'Профиль обновился. Обновите страницу.') from None
            state['generation'] += 1
            clear_rank_cache(eid)
        return profile(eid)

    @app.post('/api/me/completions')
    def complete(body: CompletionInput, session=Depends(actor)):
        eid = own(session)
        with lock:
            same_import(session)
            p = progress(eid)
            previous = next((r for r in p['completions'] if r['idempotency_key']==body.idempotency_key),None)
            if previous:
                if previous['event_id'] != body.event_id:
                    raise HTTPException(409,'Этот ключ подтверждения уже использован')
                return profile(eid)
            if body.revision != p['revision']:
                raise HTTPException(409,'Профиль обновился. Сначала получите свежие рекомендации.')
            data = current_data()
            if body.event_id not in data.events_by_id:
                raise HTTPException(404,'Активность не найдена')
            if body.event_id not in REPEATABLE_EVENT_IDS and body.event_id in profile(eid)['employee']['completed_event_ids']:
                raise HTTPException(409,'Неповторяемая активность уже выполнена')
            eligible = eligibility(data,eid,body.event_id,completions=p['completions'],target=p['target'])
            if not eligible['eligible']:
                raise HTTPException(422,eligible.get('reason') or 'Активность недоступна')
            first_steps = {r['event_id'] for r in local_recommendations(eid).get('_candidates',[])}
            if body.event_id not in first_steps:
                raise HTTPException(422,'Подтвердить можно доступный первый шаг рассчитанного сценария')
            try:
                repo.complete_employee(state['import']['import_id'],eid,event_id=body.event_id,
                    idempotency_key=body.idempotency_key,expected_revision=body.revision,
                    as_of_date=data.as_of_date.isoformat(),repeatable=body.event_id in REPEATABLE_EVENT_IDS,
                    event_session_key=eligible.get('event_session_key'),
                    continued_record_id=eligible.get('continued_record_id'))
            except ValueError as exc:
                raise HTTPException(409,'Повторное подтверждение или устаревшая версия профиля') from exc
            state['generation'] += 1
            clear_rank_cache(eid)
        return profile(eid)

    @app.get('/api/employees')
    def employees(q: str='', limit: int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),session=Depends(hr)):
        rows = [r for r in current_data().employees_by_id.values() if q.casefold() in ' '.join(
            str(r.get(k,'')) for k in ('full_name','employee_id','role','department')).casefold()]
        return {'total':len(rows),'items':[profile(r['employee_id'])['employee'] for r in rows[offset:offset+limit]]}

    @app.get('/api/employees/{employee_id}')
    @app.get('/api/employees/{employee_id}/profile')
    def employee(employee_id: str,session=Depends(actor)):
        authorize_employee(employee_id,session)
        return profile(employee_id)

    @app.get('/api/employees/{employee_id}/history')
    def employee_history(employee_id:str,session=Depends(actor)):
        authorize_employee(employee_id,session)
        rows = profile(employee_id)['history']
        return {'total':len(rows),'items':rows}

    @app.get('/api/employees/{employee_id}/recommendations')
    @app.get('/api/employees/{employee_id}/routes')
    def employee_recommendations(employee_id:str,ai:bool=False,session=Depends(actor)):
        authorize_employee(employee_id,session)
        return ranked_recommendations(employee_id,ai)

    @app.get('/api/hr/overview')
    def overview(session=Depends(hr)):
        with lock:
            generation = state['generation']
            cached = state['hr_cache']
            if cached and cached[0]==generation:
                return cached[1]
            overlays, targets = {},{}
            for eid in current_data().employees_by_id:
                p = progress(eid)
                overlays[eid],targets[eid] = p['completions'],p['target']
            result = hr_overview(current_data(),overlays_by_employee=overlays,targets_by_employee=targets)
            state['hr_cache'] = (generation,result)
            return result

    @app.get('/api/events')
    def events(session=Depends(actor)):
        rows=list(current_data().events_by_id.values())
        return {'total':len(rows),'items':rows}

    @app.get('/api/events/{event_id}')
    def event(event_id:str,session=Depends(actor)):
        row=current_data().events_by_id.get(event_id)
        if row is None: raise HTTPException(404,'Активность не найдена')
        return row

    @app.get('/api/skills')
    def skills(session=Depends(actor)):
        data=current_data()
        return {'total':len(data.skills_by_id),'items':list(data.skills_by_id.values()),'proficiency_scale':data.proficiency_scale}

    @app.get('/api/role-profiles')
    def role_profiles(session=Depends(actor)):
        rows=list(current_data().role_profiles_by_key.values())
        return {'total':len(rows),'items':rows}

    @app.get('/api/import')
    def import_info(session=Depends(hr)):
        return {'import_id':state['import']['import_id'],'as_of_date':current_data().as_of_date.isoformat(),'counts':current_data().counts}

    @app.post('/api/hr/import')
    def import_data(request:Request, mode:str=Form('replace'),employees:UploadFile|None=File(None),
            events:UploadFile|None=File(None),skills:UploadFile|None=File(None),history:UploadFile|None=File(None),
            dataset_zip:UploadFile|None=File(None),session=Depends(hr)):
        def read_file(upload):
            content=upload.file.read(MAX_FILE_BYTES+1)
            if len(content)>MAX_FILE_BYTES: raise HTTPException(422,'Файл превышает 12 МБ')
            return content
        raw={}
        if dataset_zip:
            try: raw=read_zip(read_file(dataset_zip))
            except (ValueError,OSError) as exc: raise HTTPException(422,str(exc)) from None
        else:
            for name,upload in [('employees.json',employees),('events.json',events),('skills.json',skills),('activity_history.csv',history)]:
                if upload: raw[name]=read_file(upload)
        if mode not in ('append','replace'): raise HTTPException(422,'Выберите replace или append')
        required=FILES if mode=='replace' else ('employees.json','activity_history.csv')
        if not all(name in raw for name in required): raise HTTPException(422,'Загрузите все обязательные файлы')
        with lock:
            same_import(session)
            if mode=='append':
                data=merge_additional_dataset(current_data(),raw['employees.json'],raw['activity_history.csv'])
                raw=data.raw_bytes
            data=load_dataset_bytes(raw,as_of_override=snapshot_override)
            if data.manifest==current_data().manifest and data.as_of_date==current_data().as_of_date:
                raise HTTPException(409,'Этот набор уже активен')
            imported=repo.register_import(as_of_date=data.as_of_date.isoformat(),manifest=data.manifest,counts=data.counts)
            directory=db_path.parent/'datasets'/uuid4().hex
            directory.mkdir(parents=True,exist_ok=False)
            for name in FILES: (directory/name).write_bytes(raw[name])
            repo.set_setting('active_dataset',str(directory.resolve()))
            linked_id=session['employee_id'] if session['employee_id'] in data.employees_by_id else None
            repo.rebind_session(token_from(request),imported['import_id'],linked_id)
            state.update(data=data,**{'import':imported,'generation':state['generation']+1,'hr_cache':None})
            clear_rank_cache()
            access.export_demo_accounts(data,db_path.parent/'demo_accounts.json')
        return {'counts':data.counts,'as_of_date':data.as_of_date.isoformat(),'revision':state['generation']}

    @app.post('/api/admin/ai-test')
    def test_ai(session=Depends(hr)):
        # Only this server-authored synthetic fixture bypasses the derived-data cloud gate.
        facts={'role':'Аналитик','grade':'Junior','target':{'role':'Аналитик','grade':'Middle','source':'next_grade'},
            'facts':[{'fact_id':'grade','category':'grade','text':'Текущий грейд — Junior.'},
                     {'fact_id':'target','category':'target','text':'Цель — грейд Middle.'},
                     {'fact_id':'gap','category':'gap','text':'Есть разрыв по SQL.'},
                     {'fact_id':'history','category':'history','text':'Истории участия недостаточно.'}],
            'routes':[{'route_id':'route_sql','title':'Учебный SQL-практикум','summary':'Развитие SQL в своём темпе.',
                       'fact_ids':['grade','target','gap','history']},
                      {'route_id':'route_mentoring','title':'Учебное SQL-наставничество','summary':'Развитие SQL с наставником.',
                       'fact_ids':['grade','target','gap','history']}],
            'rerankable_route_ids':['route_sql','route_mentoring']}
        return rank_and_explain(facts,synthetic=True)

    return app


app=create_app()
