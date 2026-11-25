import json
import sqlite3
import uuid

import pytest
from crm import assignments, primary_fence as fence, storage, store
from crm.contract import CRMError
from test_crm_core import crm_world, call_body
from test_crm_assignments import allocation_world, preview, apply
from test_crm_legacy_exchange import world as legacy_world


def setup(w,monkeypatch):
    path=legacy_world(w);w=allocation_world(w)
    monkeypatch.setenv('HARD_CRM_PRIMARY_DB',str(path))
    return w,path


def test_raw_old_writer_blocked_while_unmanaged_contract_and_import_work(crm_world,monkeypatch):
    w,path=setup(crm_world,monkeypatch)
    receipt=fence.activate(w['contracts'])
    assert fence.activate(w['contracts'])==receipt
    with sqlite3.connect(path) as c:
        for table in ('calls','visits'):
            with pytest.raises(sqlite3.IntegrityError,match='перенесена'):
                c.execute('INSERT INTO '+table+'(contract_id,agent_id) VALUES(?,?)',('R0',101))
        c.execute("INSERT INTO clients(contract_id,client) VALUES('unmanaged','Other')")
        c.execute("INSERT INTO calls(contract_id,agent_id) VALUES('unmanaged',101)")
        c.execute("UPDATE clients SET debt_total='222' WHERE contract_id='R0'")
        assert c.execute("SELECT debt_total FROM clients WHERE contract_id='R0'").fetchone()[0]=='222'
    with storage.connect() as c:assert assignments.readiness(c,w['managers'][0])['environment']=='cutover'


def test_reviewed_cutover_keeps_the_verified_soft_name_and_rechecks_the_source(crm_world, monkeypatch):
    world, path = setup(crm_world, monkeypatch)
    with storage.connect() as conn:
        conn.execute("UPDATE crm_employees SET staff_id='authority-staff:staff-one' WHERE employee_uuid=?",
                     (world['member'].employee_uuid,))
        conn.execute("UPDATE crm_employees SET name='Soft employee' WHERE employee_uuid=?",
                     (world['stranger'].employee_uuid,))
    with sqlite3.connect(path) as primary:
        primary.execute("INSERT INTO employee_identities(id,display_name) VALUES('staff-one','Employee')")
        primary.execute("INSERT INTO staff_roster(employee_id,source_sha256,product_names) VALUES(?,?,?)",
                        ('staff-one', 'fixture', json.dumps({'soft':'Soft employee'})))
        primary.execute("UPDATE clients SET responsible_soft='Soft employee' WHERE contract_id='R0'")
    ids = sorted(world['contracts'])
    with storage.connect(readonly=True) as conn, fence.connect(readonly=True) as primary:
        rows = fence._cutover_rows(conn, primary, ids)
    review = fence.cutover_review(rows)
    assert review['assignment_differences'] == {'responsible':0, 'responsible_soft':0}
    with sqlite3.connect(path) as primary:
        primary.execute("UPDATE clients SET responsible_soft='SOFT EMPLOYEE' WHERE contract_id='R0'")
    with pytest.raises(CRMError) as error:
        fence.activate(ids, expected_sha256=review['selection_sha256'])
    assert error.value.code == 'review_stale'
    with sqlite3.connect(path) as primary:
        primary.execute("UPDATE clients SET responsible_soft='Soft employee' WHERE contract_id='R0'")
    assert fence.activate(ids, expected_sha256=review['selection_sha256'])['contracts'] == len(ids)
    with sqlite3.connect(path) as primary:
        assert primary.execute("SELECT responsible_soft FROM clients WHERE contract_id='R0'").fetchone()[0] == 'Soft employee'


def test_assignment_projects_and_old_source_cannot_return_previous_employee(crm_world,monkeypatch):
    w,path=setup(crm_world,monkeypatch);fence.activate(w['contracts'])
    result,body=apply(w,preview(w))
    assert result['projection_status']=='pending'
    assert fence.project_pending()['projected']==2
    assert fence.project_pending()['projected']==0
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT responsible_soft FROM clients WHERE contract_id='R0'").fetchone()[0]=='New employee'
        c.execute("UPDATE clients SET responsible_soft='Employee',debt_total='222' WHERE contract_id='R0'")
        assert c.execute("SELECT responsible_soft,debt_total FROM clients WHERE contract_id='R0'").fetchone()==('New employee','222')
        assert c.execute('SELECT COUNT(*) FROM crm_primary_source_conflicts').fetchone()[0]==0
        c.execute("UPDATE clients SET responsible_soft='Unexpected source' WHERE contract_id='R0'")
        assert c.execute('SELECT COUNT(*) FROM crm_primary_source_conflicts').fetchone()[0]==1
        assert c.execute("SELECT responsible_soft FROM clients WHERE contract_id='R0'").fetchone()[0]=='New employee'
        assert not c.execute('SELECT 1 FROM crm_primary_permit').fetchone()
    assert assignments.commit(w['managers'][0],body)==result
    with pytest.raises(CRMError):store.command(w['member'],call_body(w))


def test_missing_trigger_and_unactivated_contract_fail_closed(crm_world,monkeypatch):
    w,path=setup(crm_world,monkeypatch);fence.activate(w['contracts'][:1])
    from crm import access,registry
    with storage.connect() as c:
        assert access.writable_actions(c,w['member'],w['contracts'][0])
        assert not access.writable_actions(c,w['member'],w['contracts'][1])
        row=c.execute('SELECT * FROM crm_contracts WHERE contract_uuid=?',(w['contracts'][1],)).fetchone()
        assert not registry.writable(c,w['managers'][0],row)
    with pytest.raises(CRMError) as e:preview(w)
    assert e.value.code=='contract_not_cutover'
    fence.activate(w['contracts'])
    with sqlite3.connect(path) as c:c.execute('DROP TRIGGER crm_primary_guard_calls_insert')
    with storage.connect() as c:assert not assignments.readiness(c,w['managers'][0])['enabled']
    with storage.connect() as c:assert not access.writable_actions(c,w['member'],w['contracts'][0])
    with pytest.raises(CRMError):preview(w)
    # Repeated activation must not certify changed/missing trigger definitions.
    with pytest.raises(CRMError):fence.activate(w['contracts'])


def test_project_failure_preserves_pending_and_rolls_back_permit(crm_world,monkeypatch):
    w,path=setup(crm_world,monkeypatch);fence.activate(w['contracts']);apply(w,preview(w))
    with sqlite3.connect(path) as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE clients SET partner='changed' WHERE contract_id='R0'")
        c.execute('DROP TRIGGER crm_primary_guard_identity')
    assert not fence.project_pending()['ready']
    with storage.connect() as c:assert c.execute('SELECT COUNT(*) FROM crm_primary_pending').fetchone()[0]==2
    with sqlite3.connect(path) as c:
        assert not c.execute('SELECT 1 FROM crm_primary_permit').fetchone()
        with pytest.raises(sqlite3.IntegrityError):c.execute("INSERT INTO calls(contract_id) VALUES('R0')")


def test_primary_committed_crm_commit_lost_retries_same_fence(crm_world,monkeypatch):
    w,path=setup(crm_world,monkeypatch);first=fence.activate(w['contracts'])
    # State equivalent to losing the canonical transaction after the primary commit.
    with storage.connect() as c:
        c.execute('DELETE FROM crm_primary_fences')
        c.execute("DELETE FROM crm_meta WHERE key='primary_fence_id'")
        c.execute("UPDATE crm_meta SET value='isolated' WHERE key='distribution_mode'")
    assert fence.activate(w['contracts'])==first


def test_native_work_mirror_uses_transaction_scoped_permit(crm_world,monkeypatch):
    from crm import legacy_mirror
    w,path=setup(crm_world,monkeypatch);fence.activate(w['contracts'])
    accepted=store.command(w['member'],call_body(w))
    result=legacy_mirror.deliver(path,{w['member'].employee_uuid})
    assert result[0]['state']=='delivered'
    with sqlite3.connect(path) as c:
        assert not c.execute('SELECT 1 FROM crm_primary_permit').fetchone()
        assert c.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
        with pytest.raises(sqlite3.IntegrityError):c.execute("INSERT INTO calls(contract_id) VALUES('R0')")


def test_registry_edit_mirrors_without_a_fictitious_call_or_visit(crm_world,monkeypatch):
    from crm import registry,legacy_mirror
    from test_crm_employee_registry import edit
    w,path=setup(crm_world,monkeypatch);fence.activate(w['contracts'])
    registry.edit(w['member'],edit(w,{'soft_comment':'Corrected'}))
    result=legacy_mirror.deliver(path,set())
    assert result[0]['state']=='delivered'
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT soft_comment FROM clients WHERE contract_id='R0'").fetchone()[0]=='Corrected'
        assert c.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM visits').fetchone()[0]==0


def test_real_source_import_keeps_projection_and_updates_business_data(crm_world,monkeypatch):
    import db
    from fields import FIELDS
    w,path=setup(crm_world,monkeypatch);fence.activate(w['contracts']);apply(w,preview(w));fence.project_pending()
    monkeypatch.setattr(db,'DB_PATH',path)
    incoming=dict(contract_id='R0',partner='P0',responsible_soft='Employee',debt_total='999.25')
    data=[[f.title for f in FIELDS],[f.source for f in FIELDS],[incoming.get(f.key) for f in FIELDS]]
    result=db.import_grid(data,source_kind=db.IMPORT_SOURCE_TEST_SUBSET)
    assert result['updated']==1 and result['crm_source_rows']==1,result
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT responsible_soft,debt_total FROM clients WHERE contract_id='R0'").fetchone()==('New employee','999.25')
        assert not c.execute('SELECT 1 FROM crm_primary_permit').fetchone()


def test_old_exact_retry_survives_cutover_but_new_work_and_changed_retry_do_not(crm_world,monkeypatch):
    import db
    w,path=setup(crm_world,monkeypatch)
    with sqlite3.connect(path) as c:c.execute("UPDATE clients SET responsible='Employee' WHERE contract_id='R0'")
    call=dict(contract_id='R0',agent_id=101,employee='Employee',values={'soft_comment':'Old call'},payload='Synthetic',op_id='old-call',on_date=db.workday.today().isoformat())
    visit=dict(contract_id='R0',agent_id=101,employee='Employee',values={'visit_comment':'Old visit'},payload='Synthetic',op_id='old-visit')
    with monkeypatch.context() as local:
        local.setattr(db,'DB_PATH',path)
        db.save_call_result(**call);db.save_visit_result(**visit)
    fence.activate(w['contracts']);apply(w,preview(w));fence.project_pending()
    monkeypatch.setattr(db,'DB_PATH',path)
    assert db.save_call_result(**call)['dedup']
    assert not db.save_visit_result(**visit)
    for fn,body in [(db.save_call_result,call),(db.save_visit_result,visit)]:
        with pytest.raises(ValueError):fn(**dict(body,op_id='new-operation'))
        with pytest.raises(ValueError):fn(**dict(body,values={'soft_comment':'Changed','visit_comment':'Changed'}))
    with sqlite3.connect(path) as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE clients SET soft_comment='Old client write' WHERE contract_id='R0'")


def test_changed_office_work_and_missing_source_assignment_are_visible_conflicts(crm_world,monkeypatch):
    w,path=setup(crm_world,monkeypatch);fence.activate(w['contracts'])
    with fence.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        source={'R0':{'contract_id':'R0','soft_comment':'Conflicting office result','debt_total':'321'}}
        fence.import_rows(c,source)
        assert c.execute("SELECT soft_comment,debt_total FROM clients WHERE contract_id='R0'").fetchone()[:]==('Base','321')
        assert fence.source_removal(c,'R0','responsible_soft')
    assert fence.sync_source_conflicts(limit=1)==1
    assert fence.sync_source_conflicts(limit=1)==1
    assert fence.sync_source_conflicts()==0
    with storage.connect() as c:
        assert {r[0] for r in c.execute('SELECT kind FROM crm_migration_issues')}=={'primary_work_conflict','primary_assignment_conflict'}
        assert c.execute('SELECT COUNT(*) FROM crm_migration_issues').fetchone()[0]==2


def test_other_canonical_instance_cannot_adopt_an_existing_primary_fence(crm_world,monkeypatch):
    w,path=setup(crm_world,monkeypatch);fence.activate(w['contracts'])
    with storage.connect() as c:c.execute("UPDATE crm_meta SET value='other-instance' WHERE key='instance_id'")
    with pytest.raises(CRMError):fence.activate(w['contracts'])
    with storage.connect() as c:assert not fence.ready(c)
