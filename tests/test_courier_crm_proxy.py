import asyncio
import json
from types import SimpleNamespace
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import courier_crm


def test_existing_device_and_effective_courier_role_gate_exact_command():
    calls=[]
    role=['courier']
    def error(cls,code):return cls(text=json.dumps({'error':code}),content_type='application/json')
    def auth(request):
        if request.headers.get('X-App-Token')!='synthetic-device':raise error(web.HTTPUnauthorized,'invalid')
    async def transport(method,path,headers,body):
        calls.append((method,path,headers,body))
        if path=='/auth/exchange':return 200,{'token':'synthetic-session'}
        if path=='/api/crm/context':return 200,dict(role=role[0],employee_uuid='staff-one',fields=[])
        if path.startswith('/api/crm/contracts'):return 200,dict(contracts=[],truncated=False)
        return 200,dict(ok=True,operation_id=body['operation_id'])
    async def scenario():
        app=web.Application();courier_crm.install(app,SimpleNamespace(_auth=auth,_json_error=error),transport=transport)
        async with TestClient(TestServer(app)) as client:
            headers={'X-App-Token':'synthetic-device'}
            assert (await client.get('/api/courier/context')).status==401
            assert calls==[]
            response=await client.get('/api/courier/context',headers=headers)
            assert response.status==200 and (await response.json())['employee_uuid']=='staff-one'
            body=dict(kind='delivery',operation_id='exact-op',contract_ids=['contract-one'],payload={'delivery_comment':'Recipient absent'})
            response=await client.post('/api/courier/command',headers=headers,json=body)
            assert response.status==200 and calls[-1][3]==body
            assert calls[-1][2]=={'Authorization':'Bearer synthetic-session'}
            assert (await client.post('/api/courier/command',headers=headers,json={'kind':'call'})).status==400
            assert (await client.post('/api/courier/command',headers=headers,data='broken')).status==400
            assert (await client.get('/api/courier/contracts?search='+'a'*201,headers=headers)).status==400
            role[0]='employee';before=len(calls)
            assert (await client.post('/api/courier/command',headers=headers,json=body)).status==403
            assert len(calls)==before+2
    asyncio.run(scenario())


def test_upstream_unavailable_keeps_retryable_status():
    async def transport(*args):raise asyncio.TimeoutError()
    async def scenario():
        app=web.Application();courier_crm.install(app,SimpleNamespace(_auth=lambda req:None),transport=transport)
        async with TestClient(TestServer(app)) as client:
            result=await client.get('/api/courier/context',headers={'X-App-Token':'synthetic-device'})
            assert result.status==503 and (await result.json())['error']=='crm_unavailable'
    asyncio.run(scenario())


def test_courier_cannot_bypass_the_form_through_the_old_save_route():
    kind=['courier'];writes=[]
    def error(cls,code):return cls(text=json.dumps({'error':code}),content_type='application/json')
    async def save(request):writes.append('old-save');return web.json_response({'ok':True})
    async def scenario():
        app=web.Application();courier_crm.install(app,SimpleNamespace(_auth=lambda request:(101,'Synthetic'),_json_error=error,db=SimpleNamespace(get_kind=lambda uid:kind[0])))
        app.router.add_post('/api/save',save)
        async with TestClient(TestServer(app)) as client:
            blocked=await client.post('/api/save',headers={'X-App-Token':'synthetic'},json={})
            assert blocked.status==403 and (await blocked.json())['error']=='courier_form_only' and writes==[]
            mixed=await client.post('/api/save',headers={'X-App-Token':'synthetic','X-Pult-Token':'untrusted-header'},json={})
            assert mixed.status==403 and writes==[]
            kind[0]='agent';assert (await client.post('/api/save',headers={'X-App-Token':'synthetic'},json={})).status==200
            assert writes==['old-save']
    asyncio.run(scenario())
