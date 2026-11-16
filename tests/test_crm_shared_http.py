import asyncio
import json
import secrets
import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_crm_core import crm_world, call_body
from crm import shared_auth, shared_server, storage
from crm.contract import canonical


def test_two_manager_sessions_share_data_preserve_authors_and_obey_revocation(crm_world, monkeypatch):
    monkeypatch.setenv('HARD_CRM_PILOT', '1')
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?)", (shared_auth.NAMESPACE,))
        conn.execute("UPDATE crm_memberships SET role='manager',team_id=?,capabilities_json=?",
                     ('crm-pilot-managers', canonical(sorted(shared_auth.CAPABILITIES))))
    source = {secrets.token_urlsafe(32):dict(uid=uid,device_id=uid+1,name='Synthetic manager',role='manager') for uid in [101,202]}
    credentials = list(source)
    async def verify(token):
        if token not in source: raise shared_auth.fault('manager_required',403)
        return source[token]
    sessions = shared_auth.Sessions(secrets.token_bytes(32), verify)
    async def run():
        async with TestClient(TestServer(shared_server.create_app(sessions=sessions))) as client:
            async def request(method, route, token=None, body=None, headers=None):
                async with client.request(method, route, headers=headers or ({'Authorization':'Bearer '+token} if token else {}), json=body) as response:
                    assert response.headers.get('Cache-Control') == 'no-store'
                    return response.status, await response.json()
            tokens = []
            for credential in credentials:
                status, body = await request('POST','/auth/exchange',headers={'X-Pult-Token':credential})
                assert status == 200
                tokens.append(body['token'])
            assert tokens[0] != tokens[1]
            contexts = [await request('GET','/api/crm/context',token) for token in tokens]
            assert contexts[0][1]['employee_uuid'] != contexts[1][1]['employee_uuid']
            assert all(status==200 and not body['owner'] for status,body in contexts)
            first = call_body(crm_world,values={'soft_comment':'First manager'})
            status, ack = await request('POST','/api/crm/command',tokens[0],first)
            assert status==200 and ack['status']=='accepted'
            assert (await request('POST','/api/crm/command',tokens[0],first))[1] == ack
            status, card = await request('GET','/api/crm/card?person_uuid='+crm_world['person'],tokens[1])
            assert status==200 and all(row['fields']['soft_comment']=='First manager' for row in card['contracts'])
            second = call_body(crm_world,values={'soft_comment':'Second manager'})
            second['base_revisions']={cid:{'call':1} for cid in crm_world['contracts']}
            assert (await request('POST','/api/crm/command',tokens[1],second))[0]==200
            del source[credentials[0]]
            assert (await request('GET','/api/crm/context',tokens[0]))[0]==403
            assert (await request('GET','/api/crm/context',tokens[1]))[0]==200
            assert (await request('GET','/api/crm/context',headers={'X-Pult-Token':credentials[1]}))[0]==401
            assert (await request('POST','/auth/exchange',body={'actor_uid':101},headers={'X-Pult-Token':credentials[1]}))[0]==400
    asyncio.run(run())
    with storage.connect() as conn:
        assert [r[0] for r in conn.execute('SELECT actor_uid FROM crm_events ORDER BY sequence')] == [101,202]
        assert conn.execute('SELECT COUNT(*) FROM crm_commands').fetchone()[0] == 2


def test_second_bulk_request_is_busy_before_parsing_another_large_body(crm_world, monkeypatch):
    import threading
    from crm import exchange
    entered,release=threading.Event(),threading.Event()
    def export(principal,body):
        entered.set()
        assert release.wait(timeout=5)
        return {'ok':True}
    class Auth:
        async def authenticate(self,request):return crm_world['owner']
    monkeypatch.setattr(exchange,'export',export)
    async def scenario():
        async with TestClient(TestServer(shared_server.create_app(sessions=Auth()))) as client:
            first=asyncio.create_task(client.post('/api/crm/export',json={}))
            try:
                assert await asyncio.to_thread(entered.wait,3)
                second=await client.post('/api/crm/import-preview',data=b'not JSON')
                assert second.status==503 and (await second.json())['error']=='bulk_busy'
                assert second.headers['Retry-After']=='5'
            finally:release.set()
            response=await first
            assert response.status==200
    asyncio.run(scenario())
