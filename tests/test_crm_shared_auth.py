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


def test_session_is_separate_sealed_and_checks_authority_on_every_use():
    calls = []
    async def verify(token):
        calls.append(token)
        return dict(IDENTITY)
    sessions = session(verify)
    result = asyncio.run(sessions.exchange(TOKEN))
    assert TOKEN not in result['token'] and result['token'].startswith('crm1.')
    assert asyncio.run(sessions.authenticate(request(result['token']))) == PERSON
    assert asyncio.run(sessions.authenticate(request(result['token']))) == PERSON
    assert calls == [TOKEN] * 3


def test_live_revocation_invalidates_an_already_issued_session():
    active = True
    async def verify(token):
        if not active:
            raise shared_auth.fault('manager_required', 403)
        return dict(IDENTITY)
    sessions = session(verify)
    token = asyncio.run(sessions.exchange(TOKEN))['token']
    active = False
    with pytest.raises(CRMError) as exc:
        asyncio.run(sessions.authenticate(request(token)))
    assert exc.value.status == 403


def test_different_identity_or_device_cannot_reuse_a_session():
    identity = dict(IDENTITY)
    async def verify(token):
        return identity
    sessions = session(verify)
    token = asyncio.run(sessions.exchange(TOKEN))['token']
    for key in ['uid', 'device_id']:
        identity[key] += 1
        with pytest.raises(CRMError):
            asyncio.run(sessions.authenticate(request(token)))
        identity[key] -= 1


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


@pytest.mark.parametrize('change', ['missing_subject', 'observer', 'employee', 'no_write', 'false_uid', 'missing_device'])
def test_server_identity_requires_real_manager_write_authority(change):
    raw = payload()
    if change == 'missing_subject': raw.pop('subject')
    if change == 'observer': raw['me']['role'] = 'security'
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
        conn.execute('UPDATE crm_memberships SET team_id=?,capabilities_json=? WHERE employee_uuid=?',
                     ('crm-pilot-managers', canonical(sorted(shared_auth.CAPABILITIES)), w['employee']))
    p = shared_auth.principal(identity)
    assert p.role == 'manager' and p.all_contracts and not p.owner
    assert p.capabilities == shared_auth.CAPABILITIES
    with storage.connect() as conn:
        conn.execute('UPDATE crm_actor_links SET active=0 WHERE actor_uid=?', (identity['uid'],))
    with pytest.raises(CRMError): shared_auth.principal(identity)


def test_original_credential_and_local_claims_are_not_api_authority():
    sessions = session()
    for headers in [{'X-Pult-Token': TOKEN}, {'X-Device-Token': TOKEN},
                    {'Authorization': 'Bearer ' + TOKEN}, {'X-Dev-Uid': str(IDENTITY['uid'])}]:
        with pytest.raises(CRMError):
            asyncio.run(sessions.authenticate(SimpleNamespace(headers=headers)))


def test_authority_origin_cannot_be_set_by_an_external_address():
    with pytest.raises(RuntimeError): shared_auth.Authority(None, 'https://elsewhere.invalid')


def test_unavailable_authority_cannot_issue_or_validate_a_session():
    async def unavailable(token):
        raise shared_auth.fault('authority_unavailable', 503)
    with pytest.raises(CRMError) as exc:
        asyncio.run(session(unavailable).exchange(TOKEN))
    assert exc.value.status == 503
