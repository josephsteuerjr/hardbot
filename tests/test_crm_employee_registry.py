"""Same employee registry invariants run on SQLite and real isolated PostgreSQL."""
from dataclasses import replace
import json
import uuid
import pytest
import db
from crm import registry, store, cabinet, analytics
from crm.contract import CRMError, now
from crm.contract import identifier, canonical
from test_crm_core import crm_world, call_body


def edit(w, values=None, cid=None, revisions=None):
    values = values or {'soft_comment': 'Исправленный текст'}
    return dict(schema_version=1, operation_id=str(uuid.uuid4()), rows=[dict(
        contract_uuid=cid or w['contracts'][0], values=values,
        clear=[key for key, value in values.items() if value is None],
        base_revisions=revisions or {registry.group(key): 0 for key in values})])


def test_registry_edit_is_audited_replayable_and_not_work(crm_world):
    w=crm_world
    body=edit(w)
    result=registry.edit(w['member'], body)
    assert registry.edit(w['member'], body)==result
    card=store.card(w['member'], w['person'])
    assert card['contracts'][0]['fields']['soft_comment']=='Исправленный текст'
    assert card['history'][0]['kind']=='registry_edit'
    assert card['history'][0]['payload']['before']['soft_comment']==dict(present=True,value='Base')
    analytics.aggregate()
    data=cabinet.dashboard(w['member'], now()[:7])
    assert data['stats'].get('call',0)==0 and data['stats']['processed_contracts']==0
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM crm_metric_facts').fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM crm_audit WHERE kind='registry_edit'").fetchone()[0]==1
    store.rebuild(w['owner'])
    assert store.card(w['member'],w['person'])['contracts'][0]['fields']['soft_comment']=='Исправленный текст'


def test_registry_common_read_never_grants_edit(crm_world):
    w=crm_world
    foreign=replace(w['stranger'],read_all_contracts=True)
    rows=registry.read(foreign,view='all')['rows']
    assert len(rows)==2 and all(not r['writable_fields'] for r in rows)
    assert registry.read(foreign)['rows']==[]
    with pytest.raises(CRMError) as exc:registry.edit(foreign, edit(w))
    assert exc.value.status==403


def test_registry_assignment_and_each_work_kind_are_rechecked(crm_world):
    w=crm_world;cid=w['contracts'][0]
    with db.connect() as conn:
        conn.execute("UPDATE crm_assignments SET ends_at=? WHERE contract_uuid=? AND capability='visit'",('2026-01-02T00:00:00+00:00',cid))
        conn.commit()
    rows=registry.read(w['member'])['rows']
    own=next(r for r in rows if r['contract_uuid']==cid)
    assert 'soft_comment' in own['writable_fields'] and 'visit_comment' not in own['writable_fields']
    body=edit(w,{'soft_comment':'Allowed','visit_comment':'Denied'})
    with pytest.raises(CRMError):registry.edit(w['member'],body)
    assert store.card(w['member'],w['person'])['history']==[]
    registry.edit(w['member'],edit(w))
    with db.connect() as conn:
        conn.execute('UPDATE crm_assignments SET ends_at=? WHERE contract_uuid=?',('2026-01-02T00:00:00+00:00',cid));conn.commit()
    with pytest.raises(CRMError):registry.edit(w['member'],edit(w,revisions={'call':1}))


def test_registry_atomic_paste_conflict_clear_and_forgery(crm_world):
    w=crm_world
    body=edit(w)
    registry.edit(w['member'],body)
    atomic=edit(w,{'soft_comment':'Uncommitted'},cid=w['contracts'][1])
    atomic['rows'].append(edit(w,{'soft_comment':'Stale'})['rows'][0])
    with pytest.raises(CRMError) as exc:registry.edit(w['member'],atomic)
    assert exc.value.code=='revision_conflict'
    untouched=next(r for r in store.card(w['member'],w['person'])['contracts'] if r['contract_uuid']==w['contracts'][1])
    assert untouched['fields']['soft_comment']=='Base'
    pasted=edit(w,{'visit_comment':'Should roll back'})
    pasted['rows'].append(edit(w,{'soft_comment':'Stale'})['rows'][0])
    pasted['rows'][1]['contract_uuid']=w['contracts'][1]
    pasted['rows'][1]['values']={'soft_comm_date':'not a date'}
    with pytest.raises(CRMError):registry.edit(w['member'],pasted)
    stale=edit(w)
    with pytest.raises(CRMError) as exc:registry.edit(w['member'],stale)
    assert exc.value.code=='revision_conflict'
    # A separate visit revision remains independently writable.
    registry.edit(w['member'],edit(w,{'visit_comment':'Visit correction'}))
    registry.edit(w['member'],edit(w,{'soft_comment':None},revisions={'call':1}))
    card=store.card(w['member'],w['person'])
    assert card['contracts'][0]['fields']['soft_comment'] is None
    assert card['contracts'][0]['fields']['visit_comment']=='Visit correction'
    for key in ('debt_total','contract_id','unknown','soft_history'):
        with pytest.raises(CRMError):registry.edit(w['member'],edit(w,{key:'999'}))
    forged=edit(w);forged['role']='owner'
    with pytest.raises(CRMError):registry.edit(w['member'],forged)


def test_registry_source_details_preserve_business_without_raw_blobs(crm_world):
    w=crm_world
    record={'values':{'contract_id':'DUPLICATE','soft_comment':None},
            'extra_columns':[{'title':'Место работы','value':'Тестовый офис'},
                             {'title':'secret_token','value':'must not leave server'}],
            'token':'must not leave server'}
    with db.connect() as conn:
        conn.execute('INSERT INTO crm_registry_sources VALUES(?,?,?,?,?,?,?)',('fixture','0'*64,'1'*64,'Портфели',now(),1,'{}'))
        conn.execute('INSERT INTO crm_registry_rows VALUES(?,?,?,?)',(w['contracts'][0],'fixture',2,json.dumps(record)))
        conn.commit()
    row=store.card(w['member'],w['person'])['contracts'][0]
    assert row['business_source']['present']==['contract_id','soft_comment']
    assert len(row['business_source']['extras'])==1
    assert row['business_source']['extras'][0]['title']=='Место работы'
    assert row['business_source']['extras'][0]['value']=='Тестовый офис'
    listed=registry.read(w['member'])
    extra=next(c for c in listed['columns'] if c['title']=='Место работы')
    assert extra['readonly'] and all(extra['key'] not in r['writable_fields'] for r in listed['rows'])
    assert listed['rows'][0]['fields'][extra['key']]=='Тестовый офис'
    assert 'must not leave server' not in json.dumps(row)


def test_registry_pagination_identity_sort_and_input_validation(crm_world):
    w=crm_world
    page=registry.read(w['member'],limit=1)
    assert page['total']==2 and page['has_more'] and len(page['rows'])==1
    second=registry.read(w['member'],offset=1,limit=1)
    assert page['rows'][0]['contract_uuid']!=second['rows'][0]['contract_uuid']
    assert registry.read(w['member'],search='not present')['rows']==[]
    assert len(registry.read(w['member'],ids=w['contracts'][1])['rows'])==1
    with pytest.raises(CRMError):registry.read(w['member'],sort="client'; DROP TABLE crm_people;--")


def test_registry_rechecks_membership_after_authentication(crm_world):
    w=crm_world
    with db.connect() as conn:
        conn.execute('UPDATE crm_memberships SET capabilities_json=? WHERE employee_uuid=?',('[]',w['employee']));conn.commit()
    with pytest.raises(CRMError):registry.edit(w['member'],edit(w))
    with db.connect() as conn:
        conn.execute('UPDATE crm_actor_links SET active=0 WHERE actor_uid=?',(w['member'].actor_uid,));conn.commit()
    with pytest.raises(CRMError):registry.edit(w['member'],edit(w))


def test_registry_large_portfolio_is_paged_before_business_rows(crm_world):
    w=crm_world;at='2026-01-01T00:00:00.000+00:00';contracts=[];assignments=[]
    for index in range(20000):
        cid=identifier('registry-scale',index)
        values=canonical(dict(contract_id=str(index).zfill(8),client='Synthetic scale',phone='+00123',soft_comment='Base'))
        contracts.append((cid,w['person'],'scale-creditor',str(index).zfill(8),None,None,values,values,'{}','{}',at))
        assignments.append((identifier('registry-scale-assignment',index),w['employee'],cid,'call',at,None,'fixture'))
    with db.connect() as conn:
        conn.executemany('INSERT INTO crm_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?)',contracts)
        conn.executemany('INSERT INTO crm_assignments VALUES(?,?,?,?,?,?,?)',assignments);conn.commit()
    page=registry.read(w['member'],offset=19950,limit=50)
    assert page['total']==20002 and len(page['rows'])==50 and page['has_more']
    assert all('soft_comment' in r['writable_fields'] for r in page['rows'])
    filtered=registry.read(w['member'],search='00001234',limit=50)
    assert filtered['total']==1 and filtered['rows'][0]['fields']['phone']=='+00123'
