import copy,json
from dataclasses import replace
import pytest
from crm import authority_import as imports,storage,store,cabinet,access
from crm.contract import CRMError,now,canonical,digest
from crm.shared_auth import NAMESPACE
from test_crm_core import crm_world,call_body


def plan(w):
    at=now()
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?) ON CONFLICT(key) DO NOTHING",(NAMESPACE,))
        employees=[]
        for row in conn.execute('SELECT e.*,m.team_id,m.role,m.capabilities_json,m.timezone FROM crm_employees e JOIN crm_memberships m USING(employee_uuid) WHERE ends_at IS NULL'):
            employees.append(dict(employee_uuid=row['employee_uuid'],staff_id=row['staff_id'],name=row['name'],active=row['active'],evidence='Synthetic exact roster',
                team_id=row['team_id'],role=row['role'],capabilities=json.loads(row['capabilities_json']),timezone=row['timezone']))
        actors=[dict(row) for row in conn.execute('SELECT * FROM crm_actor_links')]
        contracts=[dict(contract_uuid=row['contract_uuid'],person_uuid=row['person_uuid'],creditor_id=row['creditor_id'],external_id=row['external_id'],
                        legacy_id=row['legacy_id'],currency=row['currency'],fields=json.loads(row['fields_json']),client='Source client') for row in conn.execute('SELECT * FROM crm_contracts')]
        conn.commit()
    event=dict(legacy_source='calls:1',operation_id='original-op',source_hash='a'*64,actor_uid=101,employee_uuid=w['member'].employee_uuid,
        contract_ids=w['contracts'],payload=dict(values={'soft_comment':'Recorded history'},legacy_created_at=at),occurred_at=at,received_at=at,time_quality='legacy_recorded',kind='call')
    return dict(namespace=NAMESPACE,source_sha256='b'*64,captured_at=at,employees=employees,actors=actors,contracts=contracts,scopes=[],
        assignments=[dict(employee_uuid=w['stranger'].employee_uuid,contract_uuid=w['contracts'][0],capability='visit',starts_at=at)],events=[event],
        monthly=[dict(contract_uuid=w['contracts'][0],month=at[:7],amount='12.34',currency='RUB',source_column='Source month',observed_at=at)])


def test_exact_initial_import_preserves_authors_monthly_sources_and_closes_old_scope(crm_world):
    w=crm_world;body=plan(w);review=imports.preview(body)
    result=imports.apply(body,review)
    assert imports.apply(body,review)==result and result['logins_created']==0
    with storage.connect() as conn:
        assert access.scoped_contracts(conn,w['member'])==set()
        assert access.scoped_contracts(conn,w['stranger'])=={w['contracts'][0]}
        history=conn.execute('SELECT * FROM crm_events').fetchone()
        assert history['actor_uid']==101 and history['employee_uuid']==w['member'].employee_uuid
        assert history['time_quality']=='legacy_recorded' and history['projection_status']=='historical_only'
        assert conn.execute('SELECT COUNT(*) FROM crm_outbox').fetchone()[0]==0
    data=cabinet.dashboard(w['stranger'],body['captured_at'][:7])
    assert data['payments']['totals']==[dict(unit='RUB',currency='RUB',amount='12.34')]
    assert cabinet.dashboard(w['member'],body['captured_at'][:7])['stats']['call']==1
    with pytest.raises(CRMError):store.command(w['member'],call_body(w))


def test_initial_import_preview_detects_data_changes_before_writing(crm_world):
    body=plan(crm_world);review=imports.preview(body)
    with storage.connect() as conn:
        conn.execute('UPDATE crm_employees SET name=? WHERE employee_uuid=?',('Changed after review',crm_world['member'].employee_uuid));conn.commit()
    with pytest.raises(CRMError):imports.apply(body,review)
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM crm_authority_imports').fetchone()[0]==0


def test_existing_canonical_work_is_never_replaced_by_a_source_baseline(crm_world):
    w=crm_world;body=plan(w);review=imports.preview(body)
    store.command(w['member'],call_body(w))
    with pytest.raises(CRMError):imports.preview(body)
    with pytest.raises(CRMError):imports.apply(body,review)
    assert len(store.card(w['member'],w['person'])['history'])==1


def test_historical_workday_is_counted_without_inventing_a_time_of_day(crm_world):
    w=crm_world;body=plan(w)
    body['events'][0]['time_quality']='legacy_day'
    body['events'][0]['payload']['legacy_workday']='2026-01-15'
    imports.apply(body,imports.preview(body))
    report=cabinet.dashboard(w['member'],'2026-01')
    assert report['stats']['call']==1 and report['operations'][0]['workday']=='2026-01-15'
    assert report['operations'][0]['occurred_at'] is None
    with storage.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM crm_metric_facts').fetchone()[0]==0
