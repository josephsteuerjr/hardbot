from dataclasses import replace
from unittest.mock import patch
import uuid
import pytest
from crm import storage,store,tasks
from crm.contract import CRMError
from test_crm_core import crm_world


def create(w,assigned,due='2026-11-13T21:00:00Z',title='Call the client',work='call'):
    return store.command(w['owner'],dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='task',
        contract_ids=[w['contracts'][0]],base_revisions={},payload=dict(title=title,due_at=due,assignee_uuid=assigned,work_kind=work)))


def test_common_client_read_does_not_expose_other_employees_task_list(crm_world):
    w=crm_world;create(w,w['employee']);create(w,w['stranger'].employee_uuid)
    member=replace(w['member'],read_all_contracts=True)
    data=tasks.overview(member,status='all')
    assert data['total']==1 and data['tasks'][0]['assignee_uuid']==member.employee_uuid
    assert data['tasks'][0]['can_complete']
    for target in ('all',w['stranger'].employee_uuid):
        with pytest.raises(CRMError):tasks.overview(member,employee=target)
    manager=replace(member,role='manager')
    assert tasks.overview(manager,status='all')['total']==1
    assert tasks.overview(w['owner'],status='all')['total']==2


def test_today_and_overdue_use_server_time_in_viewer_timezone(crm_world):
    w=crm_world;create(w,w['employee']);create(w,w['employee'],due='2026-11-13T19:00:00Z')
    member=replace(w['member'],timezone='Asia/Tashkent')
    with patch('crm.tasks.now',return_value='2026-11-13T20:00:00Z'):
        data=tasks.overview(member,status='today')
        assert data['today']=='2026-11-14' and data['counts']==dict(all=2,open=2,today=2,overdue=1,done=0)
        assert len(data['tasks'])==2 and tasks.overview(member,status='overdue')['total']==1


def test_completion_moves_task_to_done_without_hiding_history(crm_world):
    w=crm_world;create(w,w['stranger'].employee_uuid)
    principal=replace(w['stranger'],read_all_contracts=True)
    row=tasks.overview(principal)['tasks'][0]
    store.command(principal,dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='task_complete',
        contract_ids=[row['contract_uuid']],base_revisions={},payload=dict(entity_id=row['entity_id'],entity_revision=row['revision'])))
    assert tasks.overview(principal)['total']==0
    done=tasks.overview(principal,status='done')
    assert done['total']==1 and not done['tasks'][0]['can_complete']
    with storage.connect() as conn:assert not tasks.grants(conn,principal)


def test_filtering_precedes_paging_and_task_counts_do_not_duplicate_contracts(crm_world):
    w=crm_world
    for i in range(5):create(w,w['employee'],title='Phone '+str(i))
    create(w,w['employee'],title='Field',work='visit')
    data=tasks.overview(w['member'],search='Phone',work='call',limit=2,offset=2)
    assert data['total']==5 and data['counts']['open']==5 and len(data['tasks'])==2 and data['has_more']
    assert all(row['contract_uuid']==w['contracts'][0] for row in data['tasks'])
    assert tasks.overview(w['member'],search='Field',work='call')['total']==0


def test_bad_filters_and_out_of_scope_employee_are_rejected(crm_world):
    for query in ({'status':'unknown'},{'limit':0},{'offset':-1},{'work':'payment'},{'search':'x'*201}):
        with pytest.raises(CRMError):tasks.overview(crm_world['member'],**query)

