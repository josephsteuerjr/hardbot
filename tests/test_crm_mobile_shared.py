"""Actual primary account/device gates + shared CRM session/API + mobile BFF.

Only transport origins are redirected to ephemeral loopback test servers.
No production connection or weakening of the pinned-origin constructors.
"""
import asyncio,os,time,uuid
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
import accounts,db
import web as primary
from crm.mobile_backend import SharedBackend,ServerExecutor
from crm.contract import CRMError
from crm.mobile_server import create_app
from crm.shared_auth import Authority,Sessions,AUTHORITY_CACHE_SECONDS
from crm.shared_server import create_app as shared_app
from test_crm_core import crm_world


def test_shared_password_requests_report_the_mobile_protocol_version():
    async def run():
        seen = []

        async def account_request(request):
            seen.append(request.path)
            assert request.headers['X-Client-Version'] == '3.3.0-mobile.1'
            assert request.headers['X-Work-Product'] == 'crm'
            assert request.headers['X-Client-Runtime'] == 'crm-mobile'
            if request.path.endswith('/login'):
                return web.json_response(dict(action='ok_pending', token='synthetic', employee='Employee',
                                              role='agent', username='synthetic', must_change_password=True))
            return web.json_response(dict(ok=True))

        app = web.Application()
        app.router.add_post('/api/account/{action}', account_request)
        async with TestServer(app) as authority:
            class Loopback(SharedBackend):
                async def json(self, url, *args, **kwargs):
                    return await super().json(str(authority.make_url(url[len(self.primary):])), *args, **kwargs)

            backend = Loopback('https://hard.rimborso.ru', 'https://hard.rimborso.ru/crm-pilot')
            await backend.start()
            try:
                login = await backend.login(dict(username='synthetic', password='temporary123', device_id='test-phone'))
                assert login['pending']['status'] == 'password'
                assert (await backend.password(login, dict(current='temporary123', new='personal456')))['ok']
            finally:
                await backend.close()
        assert seen == ['/api/account/login', '/api/account/password']

    asyncio.run(run())

def test_automatic_confirmation_is_completed_before_pending_screen():
    async def run():
        activations = []
        class AutoBackend(SharedBackend):
            async def json(self, url, *args, **kwargs):
                assert url.endswith('/api/account/login')
                return dict(action='ok_pending', token='synthetic-credential', employee='Employee',
                            role='agent', username='synthetic', must_change_password=False)
            async def activate(self, session):
                activations.append(True)
                session.pop('pending', None)
                session['context'] = {'role': 'employee'}
                return session
        backend = AutoBackend('https://hard.rimborso.ru', 'https://hard.rimborso.ru/crm-pilot')
        session = await backend.login(dict(username='synthetic', password='personal456', device_id='test-phone'))
        assert activations == [True]
        assert 'pending' not in session and session['context']['role'] == 'employee'
    asyncio.run(run())

def test_pending_password_own_phone_approval_refresh_and_revocation(crm_world,tmp_path,monkeypatch):
    monkeypatch.setenv('HARD_CRM_MOBILE','1')
    monkeypatch.setattr(primary,'DEV',False)
    with db.connect() as conn:conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace','crm-shared-pilot-v1')");conn.commit()
    assert accounts.create_account('Employee','mobile-shared','temporary123',telegram_id=101,by='fixture')['ok']
    assert accounts.approve_account('mobile-shared','fixture')['ok']
    async def run():
        identity_calls = []
        authority_app = primary.build_app(None)
        @web.middleware
        async def count_identity(request, handler):
            if request.path == '/api/crm-identity':
                identity_calls.append(request.path)
            return await handler(request)
        authority_app.middlewares.append(count_identity)
        async with TestServer(authority_app) as authority_server, aiohttp.ClientSession() as transport:
            authority=Authority(transport)
            authority.identity_url=str(authority_server.make_url('/api/crm-identity'))
            authority.url=str(authority_server.make_url('/api/manager/device/me'))
            now=[time.time()];shared_sessions=Sessions(os.urandom(32),authority,clock=lambda:now[0])
            async with TestServer(shared_app(sessions=shared_sessions)) as crm_server:
                class Loopback(SharedBackend):
                    async def json(self,url,*args,**kwargs):
                        if url.startswith(self.crm+'/'):url=str(crm_server.make_url(url[len(self.crm):]))
                        elif url.startswith(self.primary+'/'):url=str(authority_server.make_url(url[len(self.primary):]))
                        else:raise AssertionError('Unexpected outbound origin')
                        return await super().json(url,*args,**kwargs)
                key=tmp_path/'runtime.key';key.write_bytes(os.urandom(32))
                executor=ServerExecutor(tmp_path/'executor','https://hard.rimborso.ru',key)
                backend=Loopback('https://hard.rimborso.ru','https://hard.rimborso.ru/crm-shared-pilot',executor)
                app=create_app(backend,tmp_path/'mobile',key=os.urandom(32))
                async with TestClient(TestServer(app)) as client:
                    async def boot():
                        r=await client.get('/mobile/boot');return r.status,await r.json()
                    _,b=await boot();headers={'X-CRM-CSRF':b['csrf']}
                    r=await client.post('/mobile/login',headers=headers,json=dict(username='mobile-shared',password='temporary123',device_id='mobile-shared-test-phone',device_label='Synthetic phone'))
                    assert r.status==200
                    _,b=await boot();assert b['session'] is None and b['pending']['status']=='password'
                    assert not any(k in str(b) for k in ['credential','token','crm_session'])
                    headers={'X-CRM-CSRF':b['csrf']}
                    r=await client.post('/mobile/api',headers=headers,json=dict(method='GET',action='context'));assert r.status==401
                    r=await client.post('/mobile/password',headers=headers,json=dict(current='temporary123',new='personal456'));assert r.status==200
                    _,b=await boot();assert b['pending']['status']=='device' and b['session'] is None
                    with db.connect() as conn:device=conn.execute("SELECT id FROM account_devices WHERE fingerprint='mobile-shared-test-phone'").fetchone()[0]
                    assert accounts.approve_device(device,'fixture')['ok']
                    status,b=await boot();assert status==200 and b['pending'] is None
                    assert b['session']['context']['role']=='employee'
                    assert b['session']['context']['employee_uuid']==crm_world['employee']
                    assert (await (await client.get('/mobile/health')).json())['executor']=='python-xlsxwriter'
                    headers={'X-CRM-CSRF':b['csrf']}
                    download=dict(action='shift-report',operation_id=str(uuid.uuid4()),input=dict(report=dict(since='2026-11-01',until='2026-11-17')))
                    response=await client.post('/mobile/workbook',headers=headers,json=download)
                    assert response.status==200,await response.text() if response.status!=200 else ''
                    saved=await response.read();assert saved.startswith(b'PK')
                    response=await client.post('/mobile/workbook',headers=headers,json=download)
                    assert response.status==200 and await response.read()==saved
                    store=app['mobile_sessions'];sid=client.session.cookie_jar.filter_cookies(client.make_url('/mobile/boot'))['crm_mobile_v1'].value
                    credential=store.read(sid)['credential']
                    async with TestClient(TestServer(create_app(Loopback(backend.primary,backend.crm),tmp_path/'resumed',key=os.urandom(32)))) as old_shortcut:
                        boot_response=await old_shortcut.get('/mobile/boot')
                        initial=await boot_response.json()
                        resume=dict(credential=credential,entry_role='agent')
                        resume_headers={'X-CRM-CSRF':initial['csrf']}
                        denied=await old_shortcut.post('/mobile/resume',headers=resume_headers,json=resume)
                        assert denied.status==403
                        resume_headers['Origin']=str(old_shortcut.make_url('/').origin())
                        resumed=await old_shortcut.post('/mobile/resume',headers=resume_headers,json=resume)
                        assert resumed.status==200,await resumed.text()
                        rebound=await (await old_shortcut.get('/mobile/boot')).json()
                        assert rebound['session']['owner']==b['session']['owner']
                        assert rebound['csrf']!=initial['csrf']
                        resume_headers['X-CRM-CSRF']=rebound['csrf']
                        duplicate=await old_shortcut.post('/mobile/resume',headers=resume_headers,json=resume)
                        assert duplicate.status==409
                    original=store.read(sid)['crm_session'];now[0]+=1801
                    assert (await boot())[0]==200
                    renewed=store.read(sid)['crm_session'];assert renewed!=original
                    assert (await boot())[0]==200 and store.read(sid)['crm_session']==renewed
                    before = len(identity_calls)
                    response = await client.post('/mobile/api', headers=headers,
                        json=dict(method='GET', action='work-portfolio', query={}))
                    assert response.status == 200, await response.text()
                    assert len(identity_calls) <= before + 1
                    stored = store.read(sid)
                    stored['crm_session'] = 'crm1.' + 'x' * 120
                    store.write(stored, sid)
                    response = await client.post('/mobile/api', headers=headers,
                        json=dict(method='GET', action='work-portfolio', query={}))
                    assert response.status == 200, await response.text()
                    assert store.read(sid) is not None
                    assert store.read(sid)['crm_session'] != stored['crm_session']
                    with db.connect() as conn:conn.execute("UPDATE account_devices SET status='revoked' WHERE id=?",(device,));conn.commit()
                    now[0] += AUTHORITY_CACHE_SECONDS
                    assert (await boot())[0]==401
                    assert store.read(sid) is None
                    _,anonymous=await boot();assert anonymous['session'] is None
                    response=await client.post('/mobile/login',headers={'X-CRM-CSRF':anonymous['csrf']},json=dict(username='mobile-shared',password='personal456',device_id='mobile-shared-test-phone',device_label='Synthetic phone'))
                    assert response.status==200
                    _,pending=await boot();pending_sid=client.session.cookie_jar.filter_cookies(client.make_url('/mobile/boot'))['crm_mobile_v1'].value
                    credential=store.read(pending_sid)['credential']
                    assert (await client.post('/mobile/logout',headers={'X-CRM-CSRF':pending['csrf']},json={})).status==200
                    assert db.agent_device_check(credential)[0]!='ok'
    asyncio.run(run())

def test_team_adapter_uses_registered_primary_routes():
    registered={route.resource.canonical for route in primary.build_app(None).router.routes()}
    async def run():
        class Probe(SharedBackend):
            async def json(self,url,*args,**kwargs):
                assert url[len(self.primary):] in registered
                return dict(ok=True)
        backend=Probe('https://hard.rimborso.ru','https://hard.rimborso.ru/crm-shared-pilot')
        session=dict(role='manager',credential_header='X-Pult-Token',credential='synthetic-test-credential')
        for resource in ['team','approve','reset','revoke','board']:
            assert (await backend.request(session,'GET' if resource=='team' else 'POST','manager-access',dict(resource=resource,payload={}),dict(resource=resource)))['ok']
        scoped=dict(session,context=dict(access_model=2,actor_uid=303,grant_id='grant-narrow',role='manager',
            grants=[dict(grant_id='grant-narrow',actor_uid=303,role='manager',capabilities=[])],
            workspace=dict(tree=[dict(id='team',children=[])])))
        try:
            await backend.request(scoped,'GET','manager-access',{},dict(resource='team'))
            raise AssertionError('Scoped grant reached broad legacy team route')
        except CRMError as exc:
            assert exc.status==403
    asyncio.run(run())


def test_shared_exchange_forwards_only_the_explicit_grant_header():
    async def run():
        calls=[]
        class ExchangeSessions:
            async def exchange(self,credential,**kwargs):
                calls.append((credential,kwargs))
                return dict(token='synthetic-crm-token')
            def authority_metrics(self):return {}
        async with TestClient(TestServer(shared_app(sessions=ExchangeSessions()))) as client:
            response=await client.post('/auth/exchange',headers={'X-Pult-Token':'synthetic-credential','X-CRM-Grant':'grant-manager'})
            assert response.status==200
            response=await client.post('/auth/exchange',headers={'X-Pult-Token':'synthetic-credential'})
            assert response.status==200
            assert (await client.post('/auth/exchange?grant_id=grant-manager',headers={'X-Pult-Token':'synthetic-credential'})).status==400
        assert [call[1]['grant_id'] for call in calls]==['grant-manager',None]
    asyncio.run(run())


def test_mobile_grant_switch_keeps_person_owner_and_rejects_stale_envelope(tmp_path):
    contexts={
        'grant-manager':dict(actor_uid=303,employee_uuid='person-one',role='manager',capabilities=['assign','exchange'],workspace=dict(defaultModule='portfolio',tree=[dict(id='portfolio',children=[]),dict(id='team',children=[])])),
        'grant-analyst':dict(actor_uid=303,employee_uuid='person-one',role='analyst',capabilities=['exchange'],workspace=dict(defaultModule='registry',tree=[dict(id='registry',children=[]),dict(id='exchange',children=[])])),
    }
    grants=[dict(grant_id=key,actor_uid=303,role=value['role'],territory='self',capabilities=value['capabilities']) for key,value in contexts.items()]
    exchanges=[];receipts=[]
    class GrantsBackend(SharedBackend):
        async def json(self,url,method='GET',headers=None,body=None):
            if url.endswith('/api/account/login'):
                return dict(action='ok',token='synthetic-account-credential',role='full',username='synthetic')
            if url.endswith('/api/crm-identity'):
                return dict(me=dict(role='manager'))
            if url.endswith('/auth/exchange'):
                grant_id=(headers or {}).get('X-CRM-Grant','grant-manager');exchanges.append(grant_id)
                return dict(token='token-'+grant_id,grant_id=grant_id,grants=grants,access_model=2,policy_revision=7)
            if url.endswith('/api/crm/context'):
                grant_id=session_grant[0]
                return dict(contexts[grant_id],grant_id=grant_id)
            if url.endswith('/api/crm/receipt'):
                receipts.append((headers['Authorization'],body));return dict(ok=True)
            raise AssertionError(url)
    session_grant=['grant-manager']
    backend=GrantsBackend('https://hard.rimborso.ru','https://hard.rimborso.ru/crm-shared-pilot')
    original_exchange=backend.exchange
    async def exchange(session):
        result=await original_exchange(session);session_grant[0]=session['grant_id'];return result
    backend.exchange=exchange
    async def run():
        app=create_app(backend,tmp_path/'mobile',key=os.urandom(32))
        async with TestClient(TestServer(app)) as client:
            initial=await (await client.get('/mobile/boot')).json()
            response=await client.post('/mobile/login',headers={'X-CRM-CSRF':initial['csrf']},json=dict(username='synthetic',password='synthetic',grant_id='grant-manager'))
            assert response.status==200
            manager=await (await client.get('/mobile/boot')).json();headers={'X-CRM-CSRF':manager['csrf']}
            owner=manager['session']['owner']
            denied=await client.post('/mobile/admin',headers=headers,json=dict(action='reserve',args=['synthetic.xlsx']))
            assert denied.status==403
            response=await client.post('/mobile/api',headers=headers,json=dict(method='POST',action='grant',body=dict(grant_id='grant-analyst')))
            assert response.status==200,await response.text()
            analyst=await (await client.get('/mobile/boot')).json()
            assert analyst['session']['owner']==owner
            assert analyst['session']['context']['role']=='analyst'
            assert analyst['session']['context']['grant_id']=='grant-analyst'
            assert analyst['session']['context']['workspace']['defaultModule']=='registry'
            headers={'X-CRM-CSRF':analyst['csrf']}
            reserved=await client.post('/mobile/admin',headers=headers,json=dict(action='reserve',args=['synthetic.xlsx']))
            assert reserved.status==200
            stale=await client.post('/mobile/api',headers=headers,json=dict(method='GET',action='context',grant_id='grant-manager'))
            assert stale.status==409 and (await stale.json())['error']=='grant_changed'
            identifier=str(uuid.uuid4());store=app['mobile_sessions']
            store.job(owner,identifier,dict(access_model=2,actor_uid=303,grant_id='grant-manager',receipt=dict(receipt_id=identifier),receipt_state='pending'))
            response=await client.post('/mobile/receipts',headers=headers,json=dict(action='flush'))
            entries=(await response.json())['entries']
            assert entries[0]['code']=='grant_changed' and receipts==[]
            await client.post('/mobile/api',headers=headers,json=dict(method='POST',action='grant',body=dict(grant_id='grant-manager')))
            manager=await (await client.get('/mobile/boot')).json();headers={'X-CRM-CSRF':manager['csrf']}
            response=await client.post('/mobile/receipts',headers=headers,json=dict(action='flush'))
            assert (await response.json())['entries'][0]['state']=='accepted'
            assert len(receipts)==1 and receipts[0][0]=='Bearer token-grant-manager'
        assert exchanges==['grant-manager','grant-analyst','grant-manager']
    asyncio.run(run())


def test_background_executor_rechecks_revoked_grant_before_starting(tmp_path):
    async def run():
        async def denied(request):
            return web.json_response(dict(error='grant_revoked',text='Grant revoked'),status=403)
        authority=web.Application();authority.router.add_get('/api/crm/context',denied)
        key=tmp_path/'runtime.key';key.write_bytes(os.urandom(32))
        executor=ServerExecutor(tmp_path/'executor','https://hard.rimborso.ru',key)
        async with TestServer(authority) as server:
            executor.crm=str(server.make_url('')).rstrip('/')
            async with aiohttp.ClientSession() as client:
                executor.client=client
                session=dict(context=dict(actor_uid=303,employee_uuid='person-one',role='analyst',grant_id='grant-revoked',access_model=2),crm_session='stale-token',grant_id='grant-revoked',access_model=2)
                try:
                    await executor.crm_context(session)
                    raise AssertionError('revoked background grant was accepted')
                except CRMError as exc:
                    assert exc.code=='grant_revoked'
    asyncio.run(run())



def test_exchange_preserves_grant_candidates_for_native_and_mobile():
    async def run():
        class ExchangeSessions:
            async def exchange(self,credential,**kwargs):
                raise CRMError('Выберите полномочие.','grant_required',409,
                    {'grants':[dict(grant_id='grant-a',role='manager'),dict(grant_id='grant-b',role='manager')]})
            def authority_metrics(self):return {}
        async with TestClient(TestServer(shared_app(sessions=ExchangeSessions()))) as client:
            response=await client.post('/auth/exchange',headers={'X-Pult-Token':'synthetic-credential'})
            assert response.status==409
            payload=await response.json()
            assert [grant['grant_id'] for grant in payload['details']['grants']]==['grant-a','grant-b']
    asyncio.run(run())


def test_mobile_vault_separates_actors_of_one_employee(tmp_path):
    class ActorBackend(SharedBackend):
        async def login(self,body):
            actor=303 if body['username']=='first' else 404
            context=dict(actor_uid=actor,employee_uuid='person-one',role='employee',access_model=2,
                         grant_id='grant-'+str(actor),capabilities=['call'])
            return dict(role='employee',context=context,synthetic=False)
        async def request(self,session,method,action,body=None,query=None):
            assert action=='context'
            return session['context']
    async def run():
        backend=ActorBackend('https://hard.rimborso.ru','https://hard.rimborso.ru/crm-shared-pilot')
        app=create_app(backend,tmp_path/'mobile',key=os.urandom(32))
        async with TestClient(TestServer(app)) as client:
            owners=[]
            for username in ('first','second'):
                initial=await (await client.get('/mobile/boot')).json()
                response=await client.post('/mobile/login',headers={'X-CRM-CSRF':initial['csrf']},json=dict(username=username,password='synthetic'))
                assert response.status==200
                boot=await (await client.get('/mobile/boot')).json()
                owners.append(boot['session']['owner'])
            assert owners[0]!=owners[1]
    asyncio.run(run())
