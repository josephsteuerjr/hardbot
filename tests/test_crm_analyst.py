from dataclasses import replace
import json
import uuid
import pytest
from crm import assignments,manager_reports,registry,storage
from crm.contract import CRMError,now
from crm.roles import ROLE_CAPABILITIES
from test_crm_core import crm_world
from test_crm_assignments import allocation_world
from test_crm_employee_registry import edit

def analyst(w):
    manager=w['managers'][0]
    caps=ROLE_CAPABILITIES['analyst']
    with storage.connect() as conn:
        conn.execute("UPDATE crm_memberships SET role='analyst',capabilities_json=? WHERE employee_uuid=?",(json.dumps(sorted(caps)),manager.employee_uuid))
        conn.execute("UPDATE crm_meta SET value='mixed' WHERE key='distribution_mode'")
    return replace(manager,role='analyst',capabilities=caps,employee_scope=frozenset({w['employee']}))

def test_analyst_reports_are_readable_without_distribution_and_do_not_grant_assignment(crm_world):
    w=allocation_world(crm_world);p=analyst(w)
    result=assignments.statistics(p,month=now()[:7])
    assert result['contracts']==2 and result['debt']=={'RUB':'246.90'}
    assert {row['employee_uuid'] for row in result['staff']}=={w['employee']}
    assert manager_reports.read(p)['report']['sheets']
    other=assignments.statistics(w['managers'][1])
    assert other['contracts']==2 and w['employee'] in {row['employee_uuid'] for row in other['staff']}
    with pytest.raises(CRMError):assignments.preview(p,{})
    with pytest.raises(CRMError):assignments.statistics(p,employee_uuid=w['managers'][1].employee_uuid,kind='call')

def test_existing_pilot_membership_keeps_analyst_capability_reduction(crm_world):
    w=allocation_world(crm_world)
    p=replace(w['managers'][0],role='analyst',capabilities=ROLE_CAPABILITIES['analyst'])
    assert assignments.statistics(p)['contracts']==2
    assert 'call' not in p.capabilities
    assert any('debt_today' in row['writable_fields'] for row in registry.read(p,view='all')['rows'])
    with pytest.raises(CRMError):assignments.preview(p,{})

def test_analyst_business_edit_is_scoped_audited_and_never_contact_work(crm_world):
    w=allocation_world(crm_world);p=analyst(w)
    before=registry.read(p,view='all')
    row=next(r for r in before['rows'] if r['contract_uuid']==w['contracts'][0])
    assert 'debt_today' in row['writable_fields'] and 'responsible' not in row['writable_fields']
    patch=dict(schema_version=1,operation_id=str(uuid.uuid4()),rows=[dict(contract_uuid=row['contract_uuid'],base_revisions={'accounting':row['revisions'].get('accounting',0)},clear=[],values={'debt_today':'901.25'})])
    result=registry.edit(p,patch);assert registry.edit(p,patch)==result
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_events WHERE kind='call'").fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM crm_events WHERE kind='registry_edit'").fetchone()[0]==1
        conn.execute("UPDATE crm_memberships SET role='employee' WHERE employee_uuid=?",(p.employee_uuid,))
    assert all('debt_today' not in r['writable_fields'] for r in registry.read(p,view='all')['rows'])
    with pytest.raises(CRMError):assignments.statistics(p)
