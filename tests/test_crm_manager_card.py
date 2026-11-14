"""Manager card corrections share registry versions and preserve employee grants."""
from dataclasses import replace
import json
import uuid
import pytest
from crm import access, assignments, registry, storage, store
from crm.contract import CRMError, canonical, group
from test_crm_core import crm_world
from test_crm_assignments import allocation_world, preview, apply


def world(fixture):
    w=allocation_world(fixture)
    m=replace(w['managers'][0],capabilities=w['managers'][0].capabilities|{'accounting'})
    with storage.connect() as c:
        c.execute('UPDATE crm_memberships SET capabilities_json=? WHERE employee_uuid=?',
                  (canonical(sorted(m.capabilities)),m.employee_uuid))
        c.commit()
    w['manager']=m
    return w


def body(w,values,ids=None,revisions=None):
    return dict(schema_version=1,operation_id=str(uuid.uuid4()),rows=[dict(
        contract_uuid=cid,values=values,clear=[k for k,v in values.items() if v is None],
        base_revisions=revisions or {group(k):0 for k in values}) for cid in ids or [w['contracts'][0]]])


def test_manager_edits_business_card_with_audit_replay_and_versions(crm_world):
    w=world(crm_world);m=w['manager'];cid=w['contracts'][0]
    patch=body(w,{'debt_today':'140.50','forecast_sum':'10.00','phone':'12345'})
    result=registry.edit(m,patch)
    assert registry.edit(m,patch)==result
    card=store.card(m,w['person']);row=next(r for r in card['contracts'] if r['contract_uuid']==cid)
    assert row['fields']['debt_today']=='140.50'
    assert row['fields']['phone']=='12345'
    assert 'debt_today' in row['writable_fields']
    assert [e['kind'] for e in card['history']]==['registry_edit']
    assert card['history'][0]['employee_uuid']==m.employee_uuid
    with pytest.raises(CRMError) as e:registry.edit(m,body(w,{'debt_today':'200.00'}))
    assert e.value.code=='revision_conflict'
    with storage.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0]==1


def test_manager_edit_survives_unassignment_without_common_read_escalation(crm_world):
    w=world(crm_world);m=w['manager']
    apply(w,preview(w,'remove',['call','visit'],None))
    registry.edit(m,body(w,{'debt_today':'200.00'}))
    foreign=replace(w['member'],read_all_contracts=True)
    for role in ['employee','observer','analyst']:
        with pytest.raises(CRMError):registry.edit(replace(foreign,role=role,capabilities=m.capabilities),body(w,{'debt_today':'300.00'},revisions={'accounting':1}))
    with storage.connect() as c:
        c.execute('DELETE FROM crm_distribution_managers WHERE employee_uuid=?',(m.employee_uuid,));c.commit()
    with pytest.raises(CRMError):registry.edit(replace(m,read_all_contracts=True),body(w,{'debt_today':'300.00'},revisions={'accounting':1}))


def test_person_name_changes_all_contracts_atomically(crm_world):
    w=world(crm_world);m=w['manager']
    with pytest.raises(CRMError):registry.edit(m,body(w,{'client':'Новое имя'}))
    result=registry.edit(m,body(w,{'client':'Новое имя'},w['contracts']))
    card=store.card(m,w['person'])
    assert card['person']['name']=='Новое имя'
    assert all(r['fields']['client']=='Новое имя' for r in card['contracts'])
    assert len(result['event_ids'])==len(w['contracts'])


def test_assignment_identity_and_evidence_are_not_text_edits(crm_world):
    w=world(crm_world)
    for key in ['contract_id','responsible','responsible_soft','delivery_author','delivery_received_at']:
        with pytest.raises(CRMError):registry.edit(w['manager'],body(w,{key:'changed'}))
    with storage.connect() as c:
        c.execute('UPDATE crm_memberships SET capabilities_json=? WHERE employee_uuid=?',
                  (canonical(sorted(w['manager'].capabilities-{'accounting'})),w['manager'].employee_uuid));c.commit()
    with pytest.raises(CRMError):registry.edit(w['manager'],body(w,{'debt_today':'1.00'}))
