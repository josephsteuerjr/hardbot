import asyncio
from dataclasses import replace
import secrets
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from crm import shared_auth, shared_server, storage, store
from crm.contract import CRMError, canonical
from test_crm_core import crm_world, call_body
from test_crm_http import pilot_http
from test_manager_account_approval import call, world


def prepare(w, role, which='stranger'):
    member = w[which]
    caps = sorted(shared_auth.CAPABILITIES | {'delivery'})
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?) ON CONFLICT(key) DO NOTHING",
                     (shared_auth.NAMESPACE,))
        conn.execute('UPDATE crm_memberships SET role=?,capabilities_json=? WHERE employee_uuid=?',
                     (role, canonical(caps), member.employee_uuid))
    return dict(uid=member.actor_uid, device_id=member.actor_uid + 1, name='Synthetic', role=role)


@pytest.mark.parametrize('role', ['employee', 'courier', 'analyst', 'manager', 'observer'])
def test_server_identity_and_roster_produce_common_read_and_role_limited_writes(crm_world, role):
    identity = prepare(crm_world, role)
    principal = shared_auth.principal(identity)
    assert principal.role == role and principal.read_all_contracts and not principal.all_contracts
    assert store.portfolio(principal)['total_contracts'] == 2
    assert principal.capabilities <= shared_auth.ROLE_CAPABILITIES[role]
    with pytest.raises(CRMError):
        store.command(principal, call_body(crm_world))


def test_authority_role_does_not_promote_an_unrelated_roster_membership(crm_world):
    identity = prepare(crm_world, 'employee')
    with pytest.raises(CRMError):
        shared_auth.principal({**identity, 'role': 'analyst'})
    courier = shared_auth.principal({**identity, 'role': 'courier'})
    assert courier.role == 'courier' and not courier.capabilities & {'call', 'visit', 'accounting', 'exchange'}


def test_employee_entry_with_confirmed_courier_membership_is_restricted(crm_world):
    identity=prepare(crm_world,'courier')
    result=shared_auth.principal({**identity,'role':'employee'})
    assert result.role=='courier' and result.capabilities==shared_auth.ROLE_CAPABILITIES['courier']


def test_existing_admin_entry_restricts_pilot_manager_to_analyst_capabilities(crm_world):
    identity=prepare(crm_world,'manager')
    with storage.connect() as conn:
        conn.execute("UPDATE crm_memberships SET team_id='crm-pilot-managers' WHERE employee_uuid=?",(crm_world['stranger'].employee_uuid,))
    result=shared_auth.principal({**identity,'role':'analyst'})
    assert result.role=='analyst' and result.capabilities==shared_auth.ROLE_CAPABILITIES['analyst']
    assert not result.capabilities & {'call','visit','delivery','task'}


def test_security_manager_identity_is_read_only_even_with_edit_flag():
    raw=dict(ok=True,subject=dict(type='manager',actor_uid=101),device=dict(id=2),
             me=dict(role='security',name='Synthetic observer'),caps=dict(binds_edit=True))
    assert shared_auth.validate_identity(raw)['role']=='observer'


def test_manager_field_entry_is_a_reduction_to_own_employee_permissions(crm_world):
    identity=prepare(crm_world,'manager')
    result=shared_auth.principal({**identity,'role':'employee'})
    assert result.role=='employee' and result.employee_scope==frozenset({crm_world['stranger'].employee_uuid})
    assert not result.all_contracts and not result.capabilities & {'analytics','exchange','accounting','shift_correct'}


def test_explicit_authority_scope_overrides_the_old_copy_global_scope(crm_world):
    identity=prepare(crm_world,'manager')
    with storage.connect() as conn:
        conn.execute("UPDATE crm_memberships SET team_id='crm-pilot-managers' WHERE employee_uuid=?",(crm_world['stranger'].employee_uuid,))
        conn.execute('INSERT INTO crm_authority_scopes VALUES(?,?,?,?)',(identity['uid'],'[]',0,'a'*64))
    result=shared_auth.principal(identity)
    assert result.read_all_contracts and not result.all_contracts
    assert store.portfolio(result)['total_contracts']==2
    with pytest.raises(CRMError):store.command(result,call_body(crm_world))


def test_analyst_global_contract_scope_requires_an_explicit_authority_record(crm_world):
    from crm import access
    identity=prepare(crm_world,'analyst')
    before=shared_auth.principal(identity)
    with storage.connect() as conn:
        with pytest.raises(CRMError):access.contracts(conn,before,crm_world['contracts'],capability='accounting')
        conn.execute('INSERT INTO crm_authority_scopes VALUES(?,?,?,?)',(identity['uid'],'[]',1,'a'*64))
    after=shared_auth.principal(identity)
    with storage.connect() as conn:assert len(access.contracts(conn,after,crm_world['contracts'],capability='accounting'))==2


@pytest.mark.parametrize('kind,role,uid', [('employee','employee',101), ('employee','courier',101),
                                          ('analyst','analyst',202), ('analyst-device','analyst',-7)])
def test_identity_shape_uses_verified_subject_and_device(kind, role, uid):
    value = dict(ok=True, subject=dict(type=role, actor_uid=uid),
                 device=dict(id=7), me=dict(role=role, name='Synthetic'))
    result = shared_auth.validate_entry_identity(value, kind)
    assert result['uid'] == uid and result['role'] == role
    value['me']['role'] = 'manager'
    with pytest.raises(CRMError):
        shared_auth.validate_entry_identity(value, kind)


def test_staff_session_rechecks_same_entry_device_revocation_and_roster(crm_world):
    identity = prepare(crm_world, 'employee')
    token = secrets.token_urlsafe(32)
    active, calls = [True], []
    async def verify(credential, *, kind, device_fingerprint):
        assert credential == token and kind == 'employee' and device_fingerprint == ''
        calls.append(kind)
        if not active[0]:
            raise shared_auth.fault('pilot_session_invalid', 403)
        return identity
    sessions = shared_auth.Sessions(secrets.token_bytes(32), verify, audit=lambda *_: None)
    result = asyncio.run(sessions.exchange(token, kind='employee'))
    request = SimpleNamespace(headers={'Authorization':'Bearer '+result['token']})
    assert asyncio.run(sessions.authenticate(request)).role == 'employee'
    active[0] = False
    with pytest.raises(CRMError):
        asyncio.run(sessions.authenticate(request))
    active[0] = True
    with storage.connect() as conn:
        conn.execute('UPDATE crm_actor_links SET active=0 WHERE actor_uid=?', (identity['uid'],))
    with pytest.raises(CRMError):
        asyncio.run(sessions.authenticate(request))
    assert len(calls) == 4


def test_employee_and_analyst_http_sessions_share_read_but_not_writes(crm_world):
    employee = prepare(crm_world, 'employee', 'member')
    analyst = prepare(crm_world, 'analyst')
    sources = {'employee': (secrets.token_urlsafe(32), employee),
               'analyst': (secrets.token_urlsafe(32), analyst)}
    async def verify(token, *, kind, device_fingerprint):
        assert sources[kind][0] == token
        return sources[kind][1]
    sessions = shared_auth.Sessions(secrets.token_bytes(32), verify, audit=lambda *_: None)
    async def scenario():
        async with TestClient(TestServer(shared_server.create_app(sessions=sessions))) as client:
            tokens = {}
            for kind, header in [('employee','X-App-Token'), ('analyst','X-Pult-Token')]:
                headers = {header: sources[kind][0], 'X-CRM-Entry': kind}
                response = await client.post('/auth/exchange', headers=headers)
                assert response.status == 200
                tokens[kind] = {'Authorization':'Bearer '+(await response.json())['token']}
            mixed = await client.post('/auth/exchange', headers={
                'X-App-Token':sources['employee'][0], 'X-Pult-Token':sources['analyst'][0]})
            assert mixed.status == 401
            for headers in tokens.values():
                response = await client.get('/api/crm/portfolio', headers=headers)
                assert response.status == 200 and (await response.json())['total_contracts'] == 2
            body = call_body(crm_world)
            response = await client.post('/api/crm/command', headers=tokens['analyst'], json=body)
            assert response.status == 403
            response = await client.post('/api/crm/command', headers=tokens['employee'], json=body)
            assert response.status == 200
            response = await client.get('/api/crm/card?person_uuid='+crm_world['person'], headers=tokens['analyst'])
            assert (await response.json())['history'][0]['actor_uid'] == employee['uid']
    asyncio.run(scenario())


def test_identity_endpoint_uses_existing_employee_device_gate(pilot_http):
    status, result = call('GET', '/api/crm-identity', pilot_http['employee'])
    assert status == 200 and result['subject']['type'] == 'employee'
    assert result['device']['id'] > 0
    status, _ = call('GET', '/api/crm-identity', {'X-App-Token':'invalid'})
    assert status == 401
    mixed = {**pilot_http['employee'], 'X-Pult-Token':'invalid'}
    assert call('GET', '/api/crm-identity', mixed)[0] == 401
