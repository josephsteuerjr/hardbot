import asyncio
from dataclasses import replace
from types import SimpleNamespace
import os

import pytest
from crm import shared_auth, storage, schema
from crm.access import Principal
from crm.contract import CRMError, canonical
from test_crm_core import crm_world

TOKEN = 'synthetic-working-manager-token-01234567890123456789'
IDENTITY = {'uid': 90000100001, 'device_id': 51, 'name': 'Test manager', 'role': 'manager'}
PERSON = Principal(IDENTITY['uid'], 'employee-uuid', 'crm-pilot-managers', 'manager',
                   shared_auth.CAPABILITIES, frozenset(['employee-uuid']), all_contracts=True)


def payload():
    return {'ok': True, 'subject': {'type': 'manager', 'actor_uid': IDENTITY['uid']},
            'device': {'id': 51}, 'me': {'name': 'Test manager', 'role': 'manager'},
            'caps': {'binds_edit': True}}


def session(verifier=None, **kwargs):
    async def verify(token):
        assert token == TOKEN
        return dict(IDENTITY)
    return shared_auth.Sessions(b'x' * 32, verifier or verify, resolve=lambda _: PERSON,
                                audit=lambda *_: None, **kwargs)


def request(token):
    return SimpleNamespace(headers={'Authorization': 'Bearer ' + token})


def test_session_is_separate_sealed_and_reuses_short_authority_confirmation():
    calls = []
    now = [1000]
    async def verify(token):
        calls.append(token)
        return dict(IDENTITY)
    sessions = session(verify, clock=lambda: now[0])
    result = asyncio.run(sessions.exchange(TOKEN))
    assert TOKEN not in result['token'] and result['token'].startswith('crm1.')
    assert asyncio.run(sessions.authenticate(request(result['token']))) == PERSON
    assert asyncio.run(sessions.authenticate(request(result['token']))) == PERSON
    assert calls == [TOKEN]
    now[0] += shared_auth.AUTHORITY_CACHE_SECONDS
    assert asyncio.run(sessions.authenticate(request(result['token']))) == PERSON
    assert calls == [TOKEN] * 2
    assert sessions.authority_metrics()['hit'] == 2


def test_live_revocation_invalidates_an_already_issued_session_after_cache_ttl():
    active = True
    now = [1000]
    async def verify(token):
        if not active:
            raise shared_auth.fault('manager_required', 403)
        return dict(IDENTITY)
    sessions = session(verify, clock=lambda: now[0])
    token = asyncio.run(sessions.exchange(TOKEN))['token']
    active = False
    assert asyncio.run(sessions.authenticate(request(token))) == PERSON
    now[0] += shared_auth.AUTHORITY_CACHE_SECONDS
    with pytest.raises(CRMError) as exc:
        asyncio.run(sessions.authenticate(request(token)))
    assert exc.value.status == 403
    assert not sessions._authority_cache


def test_different_identity_or_device_cannot_reuse_a_session():
    identity = dict(IDENTITY)
    now = [1000]
    async def verify(token):
        return identity
    sessions = session(verify, clock=lambda: now[0])
    token = asyncio.run(sessions.exchange(TOKEN))['token']
    for key in ['uid', 'device_id']:
        identity[key] += 1
        now[0] += shared_auth.AUTHORITY_CACHE_SECONDS
        with pytest.raises(CRMError):
            asyncio.run(sessions.authenticate(request(token)))
        identity[key] -= 1
        now[0] += shared_auth.AUTHORITY_CACHE_SECONDS
        assert asyncio.run(sessions.authenticate(request(token))) == PERSON


def test_expiry_wrong_key_tampering_and_wrong_audience_are_rejected():
    now = [1000]
    sessions = session(clock=lambda: now[0])
    token = asyncio.run(sessions.exchange(TOKEN))['token']
    with pytest.raises(CRMError):
        other = shared_auth.Sessions(b'y' * 32, sessions.verify)
        other.open(token)
    with pytest.raises(CRMError):
        sessions.open(token[:30] + ('X' if token[30] != 'X' else 'Y') + token[31:])
    claims = sessions.open(token)
    claims['audience'] = 'production'
    with pytest.raises(CRMError):
        sessions.open(sessions.seal(claims))
    now[0] += shared_auth.TTL
    with pytest.raises(CRMError) as exc:
        asyncio.run(sessions.authenticate(request(token)))
    assert exc.value.code == 'pilot_session_expired'


@pytest.mark.parametrize('change', ['missing_subject', 'employee', 'no_write', 'false_uid', 'missing_device'])
def test_server_identity_requires_real_manager_write_authority(change):
    raw = payload()
    if change == 'missing_subject': raw.pop('subject')
    if change == 'employee': raw['me']['role'] = 'employee'
    if change == 'no_write': raw['caps']['binds_edit'] = False
    if change == 'false_uid': raw['subject']['actor_uid'] = True
    if change == 'missing_device': raw.pop('device')
    with pytest.raises(CRMError): shared_auth.validate_identity(raw)


def test_confirmed_identity_still_requires_independent_pilot_roster(crm_world):
    w = crm_world
    identity = {**IDENTITY, 'uid': w['member'].actor_uid}
    with pytest.raises(CRMError): shared_auth.principal(identity)
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?)", (shared_auth.NAMESPACE,))
    with pytest.raises(CRMError): shared_auth.principal(identity)
    with storage.connect() as conn:
        conn.execute("UPDATE crm_memberships SET role='manager',team_id=?,capabilities_json=? WHERE employee_uuid=?",
                     ('crm-pilot-managers', canonical(sorted(shared_auth.CAPABILITIES)), w['employee']))
    p = shared_auth.principal(identity)
    assert p.role == 'manager' and p.all_contracts and not p.owner
    assert p.capabilities == shared_auth.CAPABILITIES
    with storage.connect() as conn:
        conn.execute('UPDATE crm_actor_links SET active=0 WHERE actor_uid=?', (identity['uid'],))
    with pytest.raises(CRMError): shared_auth.principal(identity)


def test_employee_and_manager_identities_share_one_person_without_sharing_scope(crm_world):
    w = crm_world
    manager_uid = 303
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?)", (shared_auth.NAMESPACE,))
        conn.execute("UPDATE crm_memberships SET team_id='crm-cis',role='manager',capabilities_json=? WHERE employee_uuid=?",
                     (canonical(sorted(shared_auth.CAPABILITIES)), w['employee']))
        conn.execute('INSERT INTO crm_actor_links VALUES(?,?,?,?)', (manager_uid, w['employee'], 'fixture-manager', 1))
        conn.execute('INSERT INTO crm_authority_scopes VALUES(?,?,?,?)',
                     (manager_uid, canonical(sorted([w['employee'], w['stranger'].employee_uuid])), 1, 'fixture'))

    employee = shared_auth.principal({'uid': w['member'].actor_uid, 'role': 'employee'})
    manager = shared_auth.principal({'uid': manager_uid, 'role': 'manager'})
    assert employee.employee_uuid == manager.employee_uuid == w['employee']
    assert employee.role == 'employee' and employee.employee_scope == frozenset([w['employee']])
    assert not employee.all_contracts
    assert manager.role == 'manager' and manager.all_contracts
    assert manager.employee_scope == frozenset([w['employee'], w['stranger'].employee_uuid])

    from crm import manager_reports, work_portfolio
    manager_reports.require(manager)
    with pytest.raises(CRMError) as exc:
        manager_reports.require(employee)
    assert exc.value.status == 403
    for principal in (employee, manager):
        portfolio = work_portfolio.read(principal, work='all')
        assert portfolio['total_contracts'] == 2
        assert portfolio['counts']['call'] == 2 and portfolio['counts']['visit'] == 2


def test_original_credential_and_local_claims_are_not_api_authority():
    sessions = session()
    for headers in [{'X-Pult-Token': TOKEN}, {'X-Device-Token': TOKEN},
                    {'Authorization': 'Bearer ' + TOKEN}, {'X-Dev-Uid': str(IDENTITY['uid'])}]:
        with pytest.raises(CRMError):
            asyncio.run(sessions.authenticate(SimpleNamespace(headers=headers)))


def test_authority_origin_cannot_be_set_by_an_external_address():
    with pytest.raises(RuntimeError): shared_auth.Authority(None, 'https://elsewhere.invalid')


def test_unavailable_authority_has_only_bounded_grace_after_a_live_confirmation():
    now = [1000]
    available = True
    async def verify(token):
        if not available:
            raise shared_auth.fault('authority_unavailable', 503)
        return dict(IDENTITY)
    sessions = session(verify, clock=lambda: now[0])
    token = asyncio.run(sessions.exchange(TOKEN))['token']
    available = False
    with pytest.raises(CRMError) as exc:
        asyncio.run(sessions.exchange(TOKEN))
    assert exc.value.status == 503
    now[0] += shared_auth.AUTHORITY_CACHE_SECONDS
    assert asyncio.run(sessions.authenticate(request(token))) == PERSON
    assert sessions.authority_metrics()['grace'] == 1
    now[0] += shared_auth.AUTHORITY_GRACE_SECONDS
    with pytest.raises(CRMError) as exc:
        asyncio.run(sessions.authenticate(request(token)))
    assert exc.value.status == 503


def test_unavailable_authority_cannot_issue_or_validate_a_session():
    async def unavailable(token):
        raise shared_auth.fault('authority_unavailable', 503)
    with pytest.raises(CRMError) as exc:
        asyncio.run(session(unavailable).exchange(TOKEN))
    assert exc.value.status == 503
