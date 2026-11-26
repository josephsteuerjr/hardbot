import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from crm import access, grants, schema, shared_auth, shared_server, storage, store
from crm.contract import CRMError, canonical
from crm.roles import GRANT_ROLE_CAPABILITIES, ROLE_CAPABILITIES
from test_crm_core import call_body, crm_world


def identity(world, role='employee', uid=None):
    uid = world['member'].actor_uid if uid is None else uid
    return dict(uid=uid, device_id=uid + 1000, name='Synthetic actor', role=role)


def namespace():
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?) ON CONFLICT(key) DO NOTHING",
                     (shared_auth.NAMESPACE,))


def same_authority(before, after):
    assert after.role == before.role
    assert after.capabilities == before.capabilities
    assert after.employee_scope == before.employee_scope
    assert after.all_contracts == before.all_contracts
    assert after.read_all_contracts == before.read_all_contracts


def test_legacy_is_default_and_backfill_is_idempotent_shadow_equivalent(crm_world):
    namespace()
    actor = identity(crm_world)
    before = shared_auth.principal(actor)
    assert before.access_model == 1 and before.policy_revision == 0
    assert before.grants == () and before.grant_id is None

    first = shared_auth.backfill(actor)
    assert first['equivalent'] and first['mismatches'] == []
    second = shared_auth.backfill(actor)
    assert second['equivalent']
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_grants').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM crm_grant_changes').fetchone()[0] == 1
        assert conn.execute('SELECT access_model FROM crm_actor_access').fetchone()[0] == 1
    after = shared_auth.principal(actor)
    same_authority(before, after)
    assert after.access_model == 1 and len(after.grants) == 1
    assert after.grants[0].territory == 'self'


def test_explicit_opt_in_preserves_legacy_authority_and_returns_grants(crm_world):
    namespace()
    actor = identity(crm_world)
    before = shared_auth.principal(actor)
    result = shared_auth.backfill(actor, activate=True, expected_revision=0)
    assert result['equivalent'] and result['policy_revision'] == 2
    after = shared_auth.principal(actor)
    same_authority(before, after)
    assert after.access_model == 2 and after.grant_id == after.grants[0].grant_id
    assert after.policy_revision == 2

    credential = 'synthetic-employee-token-01234567890123456789'
    async def verify(token, *, kind, device_fingerprint):
        assert token == credential and kind == 'employee'
        return actor
    sessions = shared_auth.Sessions(b'g' * 32, verify, audit=lambda *_: None)
    exchange = asyncio.run(sessions.exchange(credential, kind='employee'))
    assert exchange['access_model'] == 2 and exchange['grant_id'] == after.grant_id
    assert exchange['grants'][0]['territory'] == 'self'
    request = SimpleNamespace(method='GET', path='/api/crm/context', headers={'Authorization': 'Bearer ' + exchange['token']})
    assert asyncio.run(sessions.authenticate(request)).grant_id == after.grant_id


def test_legacy_and_grant_clients_share_http_service(crm_world):
    from aiohttp.test_utils import TestClient, TestServer

    namespace()
    legacy_token = 'synthetic-legacy-client-credential'
    grant_token = 'synthetic-grant-client-credential'
    legacy_actor = identity(crm_world)
    grant_actor = identity(crm_world, uid=crm_world['stranger'].actor_uid)

    async def verify(token, *, kind, device_fingerprint):
        assert kind == 'employee' and device_fingerprint == ''
        return {legacy_token: legacy_actor, grant_token: grant_actor}[token]

    async def run():
        sessions = shared_auth.Sessions(b'g' * 32, verify, audit=lambda *_: None)
        async with TestClient(TestServer(shared_server.create_app(sessions=sessions))) as client:
            response = await client.post('/auth/exchange', headers={'X-App-Token': legacy_token})
            assert response.status == 200
            legacy = await response.json()
            assert legacy['access_model'] == 1 and legacy['grant_id'] is None
            old_headers = {'Authorization': 'Bearer ' + legacy['token']}
            assert (await (await client.get('/api/crm/context', headers=old_headers)).json())['employee_uuid'] == legacy['employee_uuid']
            command = call_body(crm_world)
            response = await client.post('/api/crm/command', headers=old_headers, json=command)
            assert response.status == 200, await response.text()
            accepted = await response.json()

            grant_entry = {'X-App-Token': grant_token}
            response = await client.post('/auth/exchange', headers=grant_entry)
            assert response.status == 200
            pre_activation = {'Authorization': 'Bearer ' + (await response.json())['token']}
            with storage.connect() as conn:
                first = grants.upsert(conn, grant_actor['uid'], role='employee', territory='self', capabilities={'call'})
                second = grants.upsert(conn, grant_actor['uid'], role='employee', territory='team', capabilities={'visit'})
                grants.set_model(conn, grant_actor['uid'], 2)
            response = await client.post('/auth/exchange', headers={'X-App-Token': grant_token})
            assert response.status == 409 and (await response.json())['error'] == 'grant_required'

            with storage.connect() as conn:
                grants.upsert(conn, grant_actor['uid'], grant_id=first.grant_id, role='employee', territory='self',
                              capabilities={'call'}, is_default=True)
            response = await client.get('/api/crm/context', headers=pre_activation)
            assert response.status == 200
            old_context = await response.json()
            assert old_context['grant_id'] == first.grant_id and old_context['capabilities'] == ['call']
            response = await client.post('/auth/exchange', headers=grant_entry)
            assert response.status == 200 and (await response.json())['grant_id'] == first.grant_id

            response = await client.post('/auth/exchange', headers={**grant_entry, 'X-CRM-Grant': second.grant_id})
            assert response.status == 200
            chosen = await response.json()
            new_headers = {'Authorization': 'Bearer ' + chosen['token']}
            context = await (await client.get('/api/crm/context', headers=new_headers)).json()
            assert context['access_model'] == 2 and context['grant_id'] == second.grant_id and context['capabilities'] == ['visit']
            response = await client.get('/api/crm/card', headers=new_headers, params={'person_uuid': crm_world['person']})
            assert response.status in (403, 404)
            response = await client.post('/api/crm/command', headers=old_headers, json=command)
            assert response.status == 200 and (await response.json()) == accepted

    asyncio.run(run())


@pytest.mark.parametrize('territory', ['self', 'employees', 'team', 'all'])
def test_each_territory_is_resolved_independently(crm_world, territory):
    namespace()
    actor = identity(crm_world)
    with storage.connect() as conn:
        grant = grants.upsert(
            conn, actor['uid'], role='employee', territory=territory,
            capabilities={'call'}, employees={crm_world['stranger'].employee_uuid}
            if territory == 'employees' else set(), is_default=True,
        )
        grants.set_model(conn, actor['uid'], 2)
    result = shared_auth.principal(actor)
    expected = {
        'self': frozenset({crm_world['employee']}),
        'employees': frozenset({crm_world['stranger'].employee_uuid}),
        'team': frozenset({crm_world['employee']}),
        'all': frozenset({crm_world['employee'], crm_world['stranger'].employee_uuid}),
    }
    assert result.grant_id == grant.grant_id
    assert result.employee_scope == expected[territory]
    assert result.capabilities == frozenset({'call'})
    assert result.all_contracts is (territory == 'all')


def test_choose_requires_default_unique_or_explicit_grant():
    first = grants.Grant('grant-a', 1, 'manager', 'self', frozenset({'note'}), 1)
    second = grants.Grant('grant-b', 1, 'manager', 'all', frozenset({'analytics'}), 1,
                          all_contracts=True)
    with pytest.raises(CRMError) as exc:
        grants.choose([first, second], role='manager')
    assert exc.value.code == 'grant_required' and exc.value.status == 409
    assert grants.choose([first, second], role='manager', grant_id='grant-b') is second
    defaulted = grants.Grant(**{**first.__dict__, 'is_default': True})
    assert grants.choose([defaulted, second], role='manager') is defaulted
    unique = grants.Grant('grant-c', 1, 'observer', 'all', frozenset({'analytics'}), 1)
    assert grants.choose([first, unique], role='observer') is unique


def test_one_account_switches_between_separately_scoped_roles(crm_world):
    namespace()
    actor = identity(crm_world, 'manager')
    with storage.connect() as conn:
        manager = grants.upsert(conn, actor['uid'], role='manager', territory='self',
                                capabilities={'assign'}, is_default=True)
        logistic = grants.upsert(conn, actor['uid'], role='logistic', territory='employees',
                                 employees=[crm_world['stranger'].employee_uuid], capabilities={'logistics'})
        grants.set_model(conn, actor['uid'], 2)
    assert shared_auth.principal(actor).grant_id == manager.grant_id
    with pytest.raises(CRMError, match='полномочие'):
        shared_auth.principal(actor, grant_id=logistic.grant_id)
    verified_account = {**actor, 'account_bound':True, 'server_operator':True}
    assert shared_auth.principal(verified_account).grant_id == manager.grant_id
    active = shared_auth.principal(verified_account, grant_id=logistic.grant_id)
    assert active.role == 'logistic' and active.capabilities == {'logistics'}
    assert active.employee_scope == {crm_world['stranger'].employee_uuid}
    assert not active.server_operator and not active.read_all_contracts


def test_queue_rechecks_binding_and_recovers_exact_previous_receipt(crm_world):
    namespace()
    actor = identity(crm_world)
    async def verify(_token):
        return actor
    sessions = shared_auth.Sessions(b'q'*32,verify,audit=lambda *_:None)
    credential='confirmed-fixture-account-credential'
    first=asyncio.run(sessions.exchange(credential))
    body=call_body(crm_world)
    accepted=store.command(shared_auth.principal(actor),body)
    with storage.connect() as conn:
        grant=grants.upsert(conn,actor['uid'],role='employee',territory='self',capabilities={'call'},is_default=True)
        grants.set_model(conn,actor['uid'],2)
    def request(session,binding,*,recover=False):
        headers={'Authorization':'Bearer '+session['token'],'X-CRM-Work-Authority':binding}
        if recover:
            headers['X-CRM-Recover-Legacy']='1'
        return SimpleNamespace(method='POST',path='/api/crm/command',headers=headers)
    with pytest.raises(CRMError) as stale:
        asyncio.run(sessions.authenticate(request(first,f"{actor['uid']}/1/")))
    assert stale.value.code=='grant_changed'
    second=asyncio.run(sessions.exchange(credential,grant_id=grant.grant_id))
    binding=f"{actor['uid']}/2/{grant.grant_id}"
    principal=asyncio.run(sessions.authenticate(request(second,binding,recover=True)))
    recovered=store.command(principal,body)
    assert recovered==dict(ok=True,status='accepted',operation_id=body['operation_id'],event_id=accepted['event_id'])
    with storage.connect(readonly=True) as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_commands WHERE operation_id=?',(body['operation_id'],)).fetchone()[0]==1


def test_no_cross_product_between_grants_with_the_same_role(crm_world):
    namespace()
    actor = identity(crm_world, 'analyst')
    with storage.connect() as conn:
        conn.execute("UPDATE crm_memberships SET role='analyst',capabilities_json=? WHERE employee_uuid=?",
                     (canonical(sorted(ROLE_CAPABILITIES['analyst'])), crm_world['employee']))
    with storage.connect() as conn:
        own = grants.upsert(conn, actor['uid'], role='analyst', territory='self',
                            capabilities={'note'})
        wide = grants.upsert(conn, actor['uid'], role='analyst', territory='all',
                             capabilities={'analytics'}, all_contracts=True)
        grants.set_model(conn, actor['uid'], 2)
    with pytest.raises(CRMError) as exc:
        shared_auth.principal(actor)
    assert exc.value.code == 'grant_required' and exc.value.status == 409

    own_result = shared_auth.principal(actor, grant_id=own.grant_id)
    assert own_result.capabilities == frozenset({'note'})
    assert own_result.employee_scope == frozenset({crm_world['employee']})
    assert not own_result.all_contracts
    wide_result = shared_auth.principal(actor, grant_id=wide.grant_id)
    assert wide_result.capabilities == frozenset({'analytics'})
    assert 'note' not in wide_result.capabilities
    assert wide_result.all_contracts


def prepare_shared_person(world):
    namespace()
    manager_uid = 303
    with storage.connect() as conn:
        conn.execute("UPDATE crm_memberships SET team_id='crm-cis',role='manager',capabilities_json=? "
                     "WHERE employee_uuid=?",
                     (canonical(sorted(ROLE_CAPABILITIES['manager'])), world['employee']))
        conn.execute('INSERT INTO crm_actor_links VALUES(?,?,?,?)',
                     (manager_uid, world['employee'], 'fixture-manager', 1))
        conn.execute('INSERT INTO crm_authority_scopes VALUES(?,?,?,?)',
                     (manager_uid, canonical(sorted([world['employee'], world['stranger'].employee_uuid])),
                      1, 'fixture'))
    return identity(world, 'employee'), identity(world, 'manager', manager_uid)


def test_two_actors_of_one_person_never_inherit_each_others_grants(crm_world):
    employee_actor, manager_actor = prepare_shared_person(crm_world)
    employee_legacy = shared_auth.principal(employee_actor)
    manager_legacy = shared_auth.principal(manager_actor)
    assert shared_auth.backfill(employee_actor, activate=True)['equivalent']
    assert shared_auth.backfill(manager_actor, activate=True)['equivalent']
    employee = shared_auth.principal(employee_actor)
    manager = shared_auth.principal(manager_actor)
    same_authority(employee_legacy, employee)
    same_authority(manager_legacy, manager)
    assert employee.grants[0].actor_uid == employee_actor['uid']
    assert manager.grants[0].actor_uid == manager_actor['uid']
    assert employee.grants[0].grant_id != manager.grants[0].grant_id

    third_uid = 404
    with storage.connect() as conn:
        conn.execute('INSERT INTO crm_actor_links VALUES(?,?,?,?)',
                     (third_uid, crm_world['employee'], 'fixture-third', 1))
        grants.set_model(conn, third_uid, 2)
    with pytest.raises(CRMError) as exc:
        shared_auth.principal(identity(crm_world, 'employee', third_uid))
    assert exc.value.code == 'grant_required' and exc.value.status == 409
    same_authority(employee, shared_auth.principal(employee_actor))
    same_authority(manager, shared_auth.principal(manager_actor))


def test_revoke_role_only_keeps_other_role_and_other_actor(crm_world):
    employee_actor, manager_actor = prepare_shared_person(crm_world)
    shared_auth.backfill(employee_actor, activate=True)
    shared_auth.backfill(manager_actor, activate=True)
    with storage.connect() as conn:
        manager_grant = shared_auth.principal(manager_actor).grant_id
        grants.revoke(conn, manager_actor['uid'], manager_grant, role='manager')
    with pytest.raises(CRMError) as exc:
        shared_auth.principal(manager_actor)
    assert exc.value.code == 'grant_required'
    assert shared_auth.principal(employee_actor).role == 'employee'

    with storage.connect() as conn:
        employee_grant = grants.upsert(
            conn, manager_actor['uid'], role='employee', territory='self',
            capabilities={'call'}, is_default=True,
        )
    field = shared_auth.principal(identity(crm_world, 'employee', manager_actor['uid']))
    assert field.role == 'employee' and field.grant_id == employee_grant.grant_id
    with pytest.raises(CRMError):
        shared_auth.principal(manager_actor)


def test_owner_is_one_server_global_grant_not_an_employee_entry(crm_world):
    _, manager_actor = prepare_shared_person(crm_world)
    with storage.connect() as conn:
        grant = grants.upsert(
            conn, manager_actor['uid'], role='owner', territory='all',
            capabilities=GRANT_ROLE_CAPABILITIES['owner'], all_contracts=True,
            read_all_contracts=True, is_default=True,
        )
        grants.set_model(conn, manager_actor['uid'], 2)
    owner = shared_auth.principal(manager_actor)
    assert owner.owner and owner.role == 'owner' and owner.grant_id == grant.grant_id
    assert owner.all_contracts and owner.read_all_contracts
    assert owner.employee_scope == frozenset({crm_world['employee'], crm_world['stranger'].employee_uuid})
    access.require(owner, 'assign')
    with pytest.raises(CRMError) as exc:
        shared_auth.principal(identity(crm_world, 'employee', manager_actor['uid']))
    assert exc.value.code == 'grant_required'


def test_current_session_and_authority_grace_do_not_preserve_revoked_grant(crm_world):
    namespace()
    actor = identity(crm_world)
    shared_auth.backfill(actor, activate=True)
    grant_id = shared_auth.principal(actor).grant_id
    credential = 'synthetic-current-session-token-01234567890123456789'
    available = True
    clock = [1000]

    async def verify(token, *, kind, device_fingerprint):
        if not available:
            raise shared_auth.fault('authority_unavailable', 503)
        return actor

    sessions = shared_auth.Sessions(b's' * 32, verify, clock=lambda: clock[0], audit=lambda *_: None)
    token = asyncio.run(sessions.exchange(credential, kind='employee'))['token']
    request = SimpleNamespace(method='GET', path='/api/crm/card', headers={'Authorization': 'Bearer ' + token})
    assert asyncio.run(sessions.authenticate(request)).access_model == 2
    with storage.connect() as conn:
        grants.revoke(conn, actor['uid'], grant_id, role='employee')
    with pytest.raises(CRMError) as exc:
        asyncio.run(sessions.authenticate(request))
    assert exc.value.code == 'grant_revoked'

    available = False
    clock[0] += shared_auth.AUTHORITY_CACHE_SECONDS
    with pytest.raises(CRMError) as exc:
        asyncio.run(sessions.authenticate(request))
    assert exc.value.code == 'grant_revoked'
    assert sessions.authority_metrics()['grace'] == 1


def test_write_refresh_rechecks_current_grant(crm_world):
    namespace()
    actor = identity(crm_world)
    shared_auth.backfill(actor, activate=True)
    current = shared_auth.principal(actor)
    with storage.connect() as conn:
        assert access.refresh_write(conn, current).grant_id == current.grant_id
        grants.revoke(conn, current.actor_uid, current.grant_id)
    with storage.connect() as conn:
        with pytest.raises(CRMError) as exc:
            access.refresh_write(conn, current)
    assert exc.value.code == 'grant_revoked'


def test_grant_changes_are_audited_and_never_update_identity_tables(crm_world):
    namespace()
    actor = identity(crm_world)
    with storage.connect() as conn:
        before = {table: list(conn.execute('SELECT * FROM ' + table + ' ORDER BY 1'))
                  for table in ('crm_employees', 'crm_actor_links', 'crm_memberships', 'crm_authority_scopes')}
    shared_auth.backfill(actor, activate=True, expected_revision=0)
    with storage.connect() as conn:
        after = {table: list(conn.execute('SELECT * FROM ' + table + ' ORDER BY 1'))
                 for table in before}
        assert after == before
        actions = [row[0] for row in conn.execute('SELECT action FROM crm_grant_changes ORDER BY revision')]
        assert actions == ['grant_created', 'access_model_activated']
        with pytest.raises(CRMError) as exc:
            grants.set_model(conn, actor['uid'], 1, expected_revision=0)
        assert exc.value.code == 'policy_revision_conflict' and exc.value.status == 409
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE crm_grant_changes SET action='forged'")
    assert shared_auth.principal(actor).access_model == 2


def test_negative_analyst_device_actor_uid_uses_the_same_actor_grant(crm_world):
    namespace()
    actor = identity(crm_world, 'analyst', -7)
    with storage.connect() as conn:
        conn.execute('INSERT INTO crm_actor_links VALUES(?,?,?,?)',
                     (actor['uid'], crm_world['employee'], 'fixture-analyst-device', 1))
        grant = grants.upsert(conn, actor['uid'], role='analyst', territory='self',
                              capabilities={'analytics'}, is_default=True)
        grants.set_model(conn, actor['uid'], 2)
    result = shared_auth.principal(actor)
    assert result.actor_uid == -7 and result.grant_id == grant.grant_id
    assert result.employee_scope == frozenset({crm_world['employee']})


def test_schema_10_contains_only_additive_grant_tables(crm_world):
    assert schema.VERSION == 10
    with storage.connect() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'crm_grant%'")}
        assert {'crm_grants', 'crm_grant_employees', 'crm_grant_changes'} <= tables
        assert 'crm_actor_access' in {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='crm_actor_access'")}



def test_access_manage_comes_from_fresh_membership_not_stale_principal(crm_world):
    namespace()
    actor = identity(crm_world, 'analyst')
    capabilities = sorted(set(ROLE_CAPABILITIES['analyst']) | {'access_manage'})
    with storage.connect() as conn:
        conn.execute("UPDATE crm_memberships SET role='analyst',capabilities_json=? WHERE employee_uuid=?",
                     (canonical(capabilities), crm_world['employee']))
    legacy = shared_auth.principal(actor)
    assert 'access_manage' in legacy.capabilities
    with storage.connect() as conn:
        grants.upsert(conn, actor['uid'], role='analyst', territory='self',
                      capabilities={'analytics'}, is_default=True)
        grants.set_model(conn, actor['uid'], 2)
    selected = shared_auth.principal(actor)
    assert 'access_manage' in selected.capabilities
    with storage.connect() as conn:
        conn.execute("UPDATE crm_memberships SET capabilities_json=? WHERE employee_uuid=?",
                     (canonical(sorted(ROLE_CAPABILITIES['analyst'])), crm_world['employee']))
        refreshed = access.refresh_write(conn, selected)
    assert 'access_manage' not in refreshed.capabilities
