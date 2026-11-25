"""Mobile boundary against actual isolated canonical engines, never live data."""
import asyncio
from datetime import datetime,timezone
import sqlite3
import uuid
import os,time,pytest
import aiohttp
from aiohttp.test_utils import TestClient,TestServer
from crm.mobile_backend import SyntheticBackend
from crm.mobile_server import create_app
from crm.mobile_server import Sessions
from crm.contract import CRMError

def test_session_transaction_releases_its_database_handle(tmp_path):
    store=Sessions(tmp_path,os.urandom(32))
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')

def test_finished_session_cannot_be_revived_by_a_late_request(tmp_path):
    store=Sessions(tmp_path,os.urandom(32));state=dict(expires_at=int((time.time()+60)*1000),csrf='synthetic')
    sid=store.write(state);late=store.read(sid);store.forget(sid)
    with pytest.raises(CRMError) as denied:store.write(late,sid)
    assert denied.value.status==401 and store.read(sid) is None
    assert store.read(store.write(state))==state

def test_cookie_csrf_origin_and_client_role_cannot_elevate(tmp_path):
    async def run():
        backend=SyntheticBackend(tmp_path/'synthetic')
        async with TestClient(TestServer(create_app(backend,tmp_path/'mobile')),cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
            response=await client.get('/mobile/boot');boot=await response.json();assert boot['session'] is None
            assert response.headers['Cache-Control']=='no-store'
            assert 'HttpOnly' in str(response.cookies)
            assert (await client.post('/mobile/login',json={'action':'synthetic','role':'manager'})).status==403
            headers={'X-CRM-CSRF':boot['csrf']}
            assert (await client.post('/mobile/login',headers={**headers,'Origin':'https://foreign.invalid'},json={'action':'synthetic','role':'manager'})).status==403
            assert (await client.post('/mobile/login',headers=headers,json={'action':'synthetic','role':'employee'})).status==200
            boot=await (await client.get('/mobile/boot')).json();headers={'X-CRM-CSRF':boot['csrf']}
            assert boot['session']['context']['role']=='employee'
            assert not any(k in str(boot) for k in ('credential','crm_session','X-Local-Secret'))
            response=await client.post('/mobile/api',headers=headers,json={'method':'GET','action':'context','body':{'role':'manager','actor_uid':91002}})
            assert (await response.json())['role']=='employee'
            assert (await client.post('/mobile/api',headers=headers,json={'method':'POST','action':'window-frame'})).status==400
            assert (await client.post('/mobile/admin',headers=headers,json={'action':'local','args':['/profile',{}]})).status==403
    asyncio.run(run())

def test_mobile_session_absolute_deadline_and_existing_session_upgrade(tmp_path):
    async def run():
        backend=SyntheticBackend(tmp_path/'synthetic')
        app=create_app(backend,tmp_path/'mobile')
        async with TestClient(TestServer(app)) as client:
            boot=await (await client.get('/mobile/boot')).json()
            before=int(time.time()*1000)
            response=await client.post('/mobile/login',headers={'X-CRM-CSRF':boot['csrf']},
                                       json={'action':'synthetic','role':'employee'})
            assert response.status==200 and int(response.cookies['crm_mobile_v1']['max-age']) in {172799,172800}
            sid=response.cookies['crm_mobile_v1'].value
            sessions=app['mobile_sessions']
            current=sessions.read(sid)
            assert before+48*3600*1000<=current['expires_at']<=int(time.time()*1000)+48*3600*1000
            current.pop('lifetime_seconds')
            current['expires_at']-=40*3600*1000
            sessions.write(current,sid)
            cutoff=current['expires_at']+40*3600*1000
            for _ in range(2):
                response=await client.get('/mobile/boot')
                state=(await response.json())['session']
                assert state['expires_at']==cutoff and state['owner']==current['owner']
            assert sessions.read(sid)['lifetime_seconds']==48*3600
            current=sessions.read(sid)
            current['expires_at']=int(time.time()*1000)-1
            sessions.write(current,sid)
            assert (await (await client.get('/mobile/boot')).json())['session'] is None
            assert sessions.read(sid) is None
    asyncio.run(run())


def test_unchanged_context_is_not_rewritten_on_every_api_call(tmp_path):
    async def run():
        backend=SyntheticBackend(tmp_path/'synthetic')
        app=create_app(backend,tmp_path/'mobile')
        async with TestClient(TestServer(app)) as client:
            boot=await (await client.get('/mobile/boot')).json()
            response=await client.post('/mobile/login',headers={'X-CRM-CSRF':boot['csrf']},json={'action':'synthetic','role':'employee'})
            sid=response.cookies['crm_mobile_v1'].value
            boot=await (await client.get('/mobile/boot')).json();headers={'X-CRM-CSRF':boot['csrf']}
            sessions=app['mobile_sessions']
            with sessions.connect() as conn:before=conn.execute('SELECT sealed,expires FROM sessions WHERE id=?',(sid,)).fetchone()
            for action in ('context','work-portfolio','work-portfolio'):
                response=await client.post('/mobile/api',headers=headers,json={'method':'GET','action':action,'body':{},'query':{}})
                assert response.status==200
                with sessions.connect() as conn:after=conn.execute('SELECT sealed,expires FROM sessions WHERE id=?',(sid,)).fetchone()
                assert tuple(after)==tuple(before)
    asyncio.run(run())


def test_storage_full_is_a_temporary_error_and_does_not_end_the_session(tmp_path):
    async def run():
        backend=SyntheticBackend(tmp_path/'synthetic')
        app=create_app(backend,tmp_path/'mobile')
        async with TestClient(TestServer(app)) as client:
            boot=await (await client.get('/mobile/boot')).json()
            response=await client.post('/mobile/login',headers={'X-CRM-CSRF':boot['csrf']},json={'action':'synthetic','role':'employee'})
            sid=response.cookies['crm_mobile_v1'].value
            sessions=app['mobile_sessions'];original=sessions.write
            def unavailable(*args,**kwargs):raise sqlite3.OperationalError('database or disk is full')
            sessions.write=unavailable
            response=await client.get('/mobile/boot')
            assert response.status==503 and (await response.json())['error']=='storage_unavailable'
            sessions.write=original
            assert sessions.read(sid) is not None
            assert (await client.get('/mobile/boot')).status==200
    asyncio.run(run())


def test_replayed_real_call_is_one_event_and_courier_cannot_write_call(tmp_path):
    async def run():
        backend=SyntheticBackend(tmp_path/'synthetic')
        async with TestClient(TestServer(create_app(backend,tmp_path/'mobile'))) as client:
            boot=await (await client.get('/mobile/boot')).json()
            await client.post('/mobile/login',headers={'X-CRM-CSRF':boot['csrf']},json={'action':'synthetic','role':'employee'})
            boot=await (await client.get('/mobile/boot')).json();headers={'X-CRM-CSRF':boot['csrf']}
            async def api(action,body=None,method='POST',query=None):
                response=await client.post('/mobile/api',headers=headers,json=dict(action=action,method=method,body=body or {},query=query or {}));return response.status,await response.json()
            status,portfolio=await api('work-portfolio',method='GET');assert status==200
            row=portfolio['contracts'][0]
            _,initial_card=await api('card',method='GET',query={'person_uuid':row['person_uuid']})
            contract=next(c for c in initial_card['contracts'] if c['contract_uuid']==row['contract_uuid'])
            body=dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='call',contract_ids=[row['contract_uuid']],payload={'values':{'soft_comment':'Mobile protocol test'}},base_revisions={row['contract_uuid']:{'call':contract['revisions'].get('call',0)}},occurred_at=datetime.now(timezone.utc).isoformat())
            first,second=await asyncio.gather(api('command',body),api('command',body))
            assert first[0]==200 and first==second
            _,card=await api('card',method='GET',query={'person_uuid':row['person_uuid']})
            assert sum(e.get('operation_id')==body['operation_id'] or e.get('event_id')==first[1]['event_id'] for e in card['history'])==1
            await client.post('/mobile/login',headers=headers,json={'action':'synthetic','role':'courier'})
            boot=await (await client.get('/mobile/boot')).json();headers={'X-CRM-CSRF':boot['csrf']}
            body['operation_id']=str(uuid.uuid4());assert (await api('command',body))[0]==403
    asyncio.run(run())

def test_uploaded_files_are_owned_and_paths_cannot_escape(tmp_path):
    async def run():
        backend=SyntheticBackend(tmp_path/'synthetic')
        async with TestClient(TestServer(create_app(backend,tmp_path/'mobile'))) as client:
            boot=await (await client.get('/mobile/boot')).json();headers={'X-CRM-CSRF':boot['csrf']}
            await client.post('/mobile/login',headers=headers,json={'action':'synthetic','role':'analyst'})
            boot=await (await client.get('/mobile/boot')).json();headers={'X-CRM-CSRF':boot['csrf']}
            response=await client.post('/mobile/upload',headers={**headers,'X-CRM-Filename':'sample.xlsx'},data=b'private synthetic file')
            assert response.status==200;file=(await response.json())['file']
            assert await (await client.get('/mobile/file/'+file)).read()==b'private synthetic file'
            assert (await client.post('/mobile/admin',headers=headers,json={'action':'local','args':['/profile',{}]})).status==403
            assert (await client.post('/mobile/admin',headers=headers,json={'action':'local','args':['/imp/load',{'path':'C:/Windows/win.ini'}]})).status==403
            for path in ['/etc/passwd','/admin/../../private.xlsx','\\\\host\\share\\secret.xlsx']:
                assert (await client.post('/mobile/admin',headers=headers,json={'action':'local','args':['/reg/pick',{'path':path}]})).status==403
            folder=await client.post('/mobile/admin',headers=headers,json={'action':'folder','args':[{'file':file,'relative_path':'Republic/Employee/sample.xlsx'}]})
            assert folder.status==200
            folder_id=(await folder.json())['file']
            owned=client.server.app['mobile_sessions'];session=next(v for v in [owned.read(c.value) for c in client.session.cookie_jar] if v and v.get('owner'))
            assert (owned.file(session['owner'],folder_id)[0]/'Republic/Employee/sample.xlsx').read_bytes()==b'private synthetic file'
            assert (await client.post('/mobile/admin',headers=headers,json={'action':'folder','args':[{'file':file,'relative_path':'../escape.xlsx'}]})).status==403
            await client.post('/mobile/login',headers=headers,json={'action':'synthetic','role':'manager'})
            assert (await client.get('/mobile/file/'+file)).status==403
            await client.get('/mobile/boot')
    asyncio.run(run())

def test_logout_invalidates_the_cookie_without_deleting_business_files(tmp_path):
    async def run():
        backend=SyntheticBackend(tmp_path/'synthetic')
        async with TestClient(TestServer(create_app(backend,tmp_path/'mobile'))) as client:
            boot=await (await client.get('/mobile/boot')).json()
            await client.post('/mobile/login',headers={'X-CRM-CSRF':boot['csrf']},json={'action':'synthetic','role':'manager'})
            boot=await (await client.get('/mobile/boot')).json();headers={'X-CRM-CSRF':boot['csrf']}
            assert (await client.post('/mobile/logout',headers=headers,json={})).status==200
            assert (await (await client.get('/mobile/boot')).json())['session'] is None
            assert (tmp_path/'synthetic/crm-pilot.sqlite3').is_file()
    asyncio.run(run())

def test_busy_native_executor_does_not_hold_canonical_reads(tmp_path):
    async def run():
        backend=SyntheticBackend(tmp_path/'synthetic')
        async with TestClient(TestServer(create_app(backend,tmp_path/'mobile'))) as client:
            boot=await (await client.get('/mobile/boot')).json()
            await client.post('/mobile/login',headers={'X-CRM-CSRF':boot['csrf']},json={'action':'synthetic','role':'manager'})
            boot=await (await client.get('/mobile/boot')).json();headers={'X-CRM-CSRF':boot['csrf']}
            await backend.native_lock.acquire()
            waiting=asyncio.create_task(backend.file_action({'role':'manager'},'report-write',{}))
            try:
                response=await asyncio.wait_for(client.post('/mobile/api',headers=headers,json=dict(method='GET',action='managed-portfolio')),3)
                assert response.status==200 and (await response.json())['people']
                assert not waiting.done()
            finally:
                waiting.cancel()
                try:await waiting
                except asyncio.CancelledError:pass
                backend.native_lock.release()
    asyncio.run(run())
