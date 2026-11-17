"""Actual primary account/device gates + shared CRM session/API + mobile BFF.

Only transport origins are redirected to ephemeral loopback test servers.
No production connection or weakening of the pinned-origin constructors.
"""
import asyncio,os,time,uuid
import aiohttp
from aiohttp.test_utils import TestClient,TestServer
import accounts,db
import web as primary
from crm.mobile_backend import SharedBackend,ServerExecutor
from crm.mobile_server import create_app
from crm.shared_auth import Authority,Sessions
from crm.shared_server import create_app as shared_app
from test_crm_core import crm_world

def test_pending_password_own_phone_approval_refresh_and_revocation(crm_world,tmp_path,monkeypatch):
    monkeypatch.setenv('HARD_CRM_MOBILE','1')
    monkeypatch.setattr(primary,'DEV',False)
    with db.connect() as conn:conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace','crm-shared-pilot-v1')");conn.commit()
    assert accounts.create_account('Employee','mobile-shared','temporary123',telegram_id=101,by='fixture')['ok']
    assert accounts.approve_account('mobile-shared','fixture')['ok']
    async def run():
        async with TestServer(primary.build_app(None)) as authority_server, aiohttp.ClientSession() as transport:
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
                    original=store.read(sid)['crm_session'];now[0]+=1801
                    assert (await boot())[0]==200
                    renewed=store.read(sid)['crm_session'];assert renewed!=original
                    assert (await boot())[0]==200 and store.read(sid)['crm_session']==renewed
                    with db.connect() as conn:conn.execute("UPDATE account_devices SET status='revoked' WHERE id=?",(device,));conn.commit()
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
    asyncio.run(run())
