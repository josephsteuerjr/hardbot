import json
from dataclasses import replace
import pytest
from crm import manager_reports,storage,assignments,three_hour
from crm.contract import CRMError,now
from test_crm_core import crm_world
from test_crm_assignments import allocation_world,preview,apply


def test_snapshot_is_immutable_scoped_and_no_fabricated_past(crm_world,monkeypatch):
    w=allocation_world(crm_world);p=w['managers'][0]
    first=manager_reports.read(p)
    assert len(first['snapshots'])==1
    sid=first['snapshots'][0]['snapshot_id'];captured=manager_reports.read(p,snapshot_id=sid)['report']
    with storage.connect() as c:
        c.execute("UPDATE crm_contracts SET fields_json=?",(json.dumps({'client':'Changed','soft_call_status':'В работе','soft_comm_date':now()[:10],'debt_today':'900'}),))
    again=manager_reports.read(p)
    assert len(again['snapshots'])==1
    assert manager_reports.read(p,snapshot_id=sid)['report']==captured
    assert manager_reports.read(p,day='2000-01-01')['snapshots']==[]
    with pytest.raises(CRMError):manager_reports.read(w['managers'][1],snapshot_id=sid)
    with pytest.raises(CRMError):manager_reports.read(w['member'])
    with pytest.raises(Exception):
        with storage.connect() as c:c.execute('DELETE FROM crm_report_snapshots')


def test_server_finishes_packs_without_renderer_and_rechecks_revocation(crm_world,monkeypatch):
    w=allocation_world(crm_world);p=w['managers'][0]
    monkeypatch.setattr(assignments,'MAX_SLOTS',1)
    result,_=apply(w,preview(w))
    assert result['status']=='job_started'
    manager_reports.tick({p.actor_uid:p})
    assert assignments.job(p,job_id=result['job_id'])['applied_slots']==1
    with storage.connect() as c:c.execute("UPDATE crm_memberships SET role='observer' WHERE employee_uuid=?",(p.employee_uuid,))
    manager_reports.tick({p.actor_uid:p})
    assert assignments.job(p,job_id=result['job_id'])['applied_slots']==1
    with storage.connect() as c:c.execute("UPDATE crm_memberships SET role='manager' WHERE employee_uuid=?",(p.employee_uuid,))
    manager_reports.tick({p.actor_uid:p})
    assert assignments.job(p,job_id=result['job_id'])['state']=='completed'


def test_report_current_assignment_obeys_removal(crm_world):
    w=allocation_world(crm_world);p=w['managers'][0]
    apply(w,preview(w,'remove',['call'],None))
    report=three_hour.report(p)
    assert all(not row['current_records'] for sheet in report['sheets'] for row in sheet['rows'])
