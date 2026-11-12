from dataclasses import replace
from decimal import Decimal
import json,uuid
import pytest
from crm import storage,store,work_portfolio
from crm.contract import CRMError,canonical,identifier,now
from test_crm_core import crm_world


def test_two_work_kinds_are_one_contract_and_one_debt(crm_world):
    w=crm_world;result=work_portfolio.read(w['member'])
    assert result['counts']==dict(all=2,call=2,visit=2,both=2,delivery=0,only_call=0,only_visit=0)
    assert len(result['contracts'])==2 and result['currencies']=={'RUB':'246.90'}
    assert all(r['work_kinds']==['call','visit'] for r in result['contracts'])
    # Equal external IDs from two creditors remain two separate contracts.
    assert len({r['external_id'] for r in result['contracts']})==1
    assert len({r['creditor_id'] for r in result['contracts']})==2


def test_current_assignments_and_viewer_scope_are_separate(crm_world):
    w=crm_world
    with storage.connect() as conn:
        conn.execute("UPDATE crm_assignments SET ends_at=? WHERE capability='visit' AND contract_uuid=?",('2026-02-01T00:00:00Z',w['contracts'][0]))
    principal=replace(w['member'],read_all_contracts=True)
    assert work_portfolio.read(principal,work='visit')['total_contracts']==1
    assert work_portfolio.read(principal)['counts']['only_call']==1
    assert store.portfolio(replace(w['stranger'],read_all_contracts=True))['total_contracts']==2
    assert work_portfolio.read(w['stranger'])['total_contracts']==0
    with pytest.raises(CRMError):work_portfolio.read(w['stranger'],employee=w['employee'])
    assert work_portfolio.read(w['owner'],employee=w['employee'])['total_contracts']==2


def test_entry_capabilities_can_reduce_the_personal_queue(crm_world):
    p=replace(crm_world['member'],capabilities=frozenset({'visit'}))
    result=work_portfolio.read(p)
    assert result['counts']['visit']==2 and result['counts']['call']==0 and result['counts']['both']==0
    assert result['counts']['only_visit']==2


def test_a_manager_task_adds_and_completion_removes_work_without_reassignment(crm_world):
    w=crm_world;cid=w['contracts'][0]
    owner=w['owner'];body=dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='task',contract_ids=[cid],base_revisions={},
        payload=dict(title='Explicit visit',assignee_uuid=w['stranger'].employee_uuid,work_kind='visit',due_at='2026-12-01T12:00:00Z'))
    result=store.command(owner,body)
    queue=work_portfolio.read(w['stranger'])
    assert queue['counts']['visit']==1 and queue['counts']['call']==0
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_assignments WHERE employee_uuid=?',(w['stranger'].employee_uuid,)).fetchone()[0]==0
        entity=conn.execute("SELECT entity_id FROM crm_entities WHERE kind='task'").fetchone()[0]
    store.command(w['stranger'],dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='task_complete',contract_ids=[cid],base_revisions={},payload=dict(entity_id=entity,entity_revision=1)))
    assert work_portfolio.read(w['stranger'])['counts']['all']==0


def test_469_contracts_are_filtered_before_paging_and_counted_once(crm_world):
    w=crm_world;at=now()
    with storage.connect() as conn:
        conn.execute('DELETE FROM crm_assignments')
        # 429 call-only, 6 visit-only, 34 with both work kinds.
        for index in range(469):
            cid=identifier('contract','dual-queue-'+str(index))
            values=canonical(dict(debt_total='10.00',phone='555-12345',address='Synthetic address'))
            conn.execute('INSERT INTO crm_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (cid,w['person'],'synthetic-creditor',f'Q{index:04}',None,'RUB',values,values,'{}','{}',at))
            kinds=['call'] if index<429 else ['visit'] if index<435 else ['call','visit']
            for kind in kinds:conn.execute('INSERT INTO crm_assignments VALUES(?,?,?,?,?,NULL,?)',
                (str(uuid.uuid4()),w['employee'],cid,kind,at,'synthetic'))
    result=work_portfolio.read(w['member'],limit=100)
    assert result['counts']==dict(all=469,call=463,visit=40,both=34,delivery=0,only_call=429,only_visit=6)
    assert result['currencies']=={'RUB':'4690.00'} and result['has_more']
    both=work_portfolio.read(w['member'],work='both',limit=10)
    assert both['total_contracts']==34 and len(both['contracts'])==10 and both['contracts'][0]['external_id']=='Q0435'
    last=work_portfolio.read(w['member'],work='both',offset=30,limit=10)
    assert len(last['contracts'])==4 and not last['has_more']
    assert work_portfolio.read(w['member'],search='Q0468')['total_contracts']==1


def test_inactive_staff_and_invalid_query_do_not_return_a_queue(crm_world):
    w=crm_world
    for kwargs in (dict(work='unknown'),dict(offset=-1),dict(limit=201),dict(search='x'*201)):
        with pytest.raises(CRMError):work_portfolio.read(w['member'],**kwargs)
    with storage.connect() as conn:conn.execute('UPDATE crm_employees SET active=0 WHERE employee_uuid=?',(w['employee'],))
    with pytest.raises(CRMError):work_portfolio.read(w['member'])
