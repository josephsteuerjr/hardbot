from dataclasses import replace
import json
import uuid
import pytest

from crm import access, storage, store
from crm.contract import CRMError
from test_crm_core import crm_world, call_body


def task(w, *, actor=None, work='call', assignee=None):
    return store.command(actor or w['owner'], dict(schema_version=1, operation_id=str(uuid.uuid4()),
        kind='task', contract_ids=[w['contracts'][0]], base_revisions={}, payload=dict(
            title='Work this client', due_at='2026-11-12T23:59:00+04:00', work_kind=work,
            assignee_uuid=assignee or w['stranger'].employee_uuid)))


def complete(w, actor):
    entity=next(row for row in store.card(w['owner'],w['person'])['entities'] if row['kind']=='task')
    return store.command(actor, dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='task_complete',
        contract_ids=[w['contracts'][0]],base_revisions={},
        payload=dict(entity_id=entity['entity_id'],entity_revision=entity['revision'])))


def test_manager_task_links_assignee_client_and_own_authorship(crm_world):
    w=crm_world
    task(w)
    card=store.card(replace(w['stranger'],read_all_contracts=True),w['person'])
    entity=next(row for row in card['entities'] if row['kind']=='task')
    assert entity['data']['assignee_uuid']==w['stranger'].employee_uuid
    assert entity['contract_uuid']==w['contracts'][0] and entity['assignee']=='Outsider'
    assert entity['can_complete']
    body=call_body(w);body['contract_ids']=[w['contracts'][0]]
    store.command(w['stranger'],body)
    with storage.connect() as conn:
        assert conn.execute("SELECT actor_uid FROM crm_events WHERE kind='call'").fetchone()[0]==202
        assert conn.execute('SELECT COUNT(*) FROM crm_assignments').fetchone()[0]==4


def test_task_delegation_is_limited_to_work_kind_and_client(crm_world):
    w=crm_world;task(w,work='visit')
    body=call_body(w);body['contract_ids']=[w['contracts'][0]]
    with pytest.raises(CRMError):store.command(w['stranger'],body)
    body.update(kind='visit',payload={'values':{'visit_comment':'Work done'}})
    store.command(w['stranger'],body)
    body['operation_id']=str(uuid.uuid4());body['contract_ids']=[w['contracts'][1]]
    with pytest.raises(CRMError):store.command(w['stranger'],body)
    principal=replace(w['stranger'],capabilities=w['stranger'].capabilities|{'accounting'})
    with storage.connect() as conn:
        with pytest.raises(CRMError):access.contracts(conn,principal,[w['contracts'][0]],capability='accounting')


def test_other_employee_cannot_complete_task_and_completion_ends_delegation(crm_world):
    w=crm_world;task(w)
    with pytest.raises(CRMError):complete(w,w['member'])
    complete(w,w['stranger'])
    body=call_body(w);body['contract_ids']=[w['contracts'][0]]
    with pytest.raises(CRMError):store.command(w['stranger'],body)
    store.rebuild(w['owner'])
    entity=next(row for row in store.card(w['owner'],w['person'])['entities'] if row['kind']=='task')
    assert entity['status']=='done' and not entity['can_complete']
    with storage.connect() as conn:assert not access.scoped_contracts(conn,w['stranger'])


def test_personal_reminder_cannot_assign_another_employee_or_forge_manager_grant(crm_world):
    w=crm_world
    with pytest.raises(CRMError):task(w,actor=w['member'])
    task(w,actor=w['member'],assignee=w['member'].employee_uuid)
    entity=next(row for row in store.card(w['member'],w['person'])['entities'] if row['kind']=='task')
    assert not entity['data']['assignment_grant']
    with storage.connect() as conn:
        conn.execute("UPDATE crm_assignments SET ends_at='2026-01-02T00:00:00Z'")
    with storage.connect() as conn:assert not access.scoped_contracts(conn,w['member'])
    body=dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='task',contract_ids=[w['contracts'][0]],
              base_revisions={},payload=dict(title='Fake',due_at='2026-11-12T23:59:00Z',assignment_grant=True))
    with pytest.raises(CRMError):store.command(w['member'],body)
