"""Exact distribution scenarios on PostgreSQL, with commit-order races."""
import inspect
import os
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
import pytest
from crm import access, assignments, storage, store
from crm.contract import CRMError
from test_crm_postgres import pg_world
import test_crm_assignments as cases
import test_crm_manager_card as card_cases
import test_crm_manager_reports as report_cases
import test_crm_analyst as analyst_cases

pytestmark=pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_DSN_FILE'),reason='Requires isolated PostgreSQL QA database')

CASES=[f for module in (cases,card_cases,report_cases,analyst_cases) for n,f in vars(module).items() if n.startswith('test_') and callable(f) and set(inspect.signature(f).parameters)=={'crm_world'}]


def test_report_snapshots_and_independent_jobs_in_postgres(pg_world,monkeypatch):
    report_cases.test_snapshot_is_immutable_scoped_and_no_fabricated_past(pg_world,monkeypatch)


def test_independent_jobs_in_postgres(pg_world,monkeypatch):
    report_cases.test_server_finishes_packs_without_renderer_and_rechecks_revocation(pg_world,monkeypatch)

@pytest.mark.parametrize('case',CASES,ids=lambda f:f.__name__)
def test_distribution_in_postgres(pg_world,case):
    case(pg_world)


def test_two_managers_one_commit_and_one_conflict(pg_world):
    w=cases.allocation_world(pg_world);previews=[cases.preview(w,manager=i) for i in (0,1)]
    gate=threading.Barrier(2)
    def commit(i):
        gate.wait()
        try:return cases.apply(w,previews[i],manager=i)[0]['status']
        except CRMError as e:return e.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(commit,[0,1]))==['applied','assignment_conflict']
    with storage.connect() as c:assert c.execute('SELECT COUNT(*) FROM crm_assignment_changes').fetchone()[0]==2


def test_work_permission_remains_valid_until_commit_before_transfer(pg_world,monkeypatch):
    w=cases.allocation_world(pg_world);p=cases.preview(w)
    checked=threading.Event();release=threading.Event();original=access.contracts
    def permission(conn,principal,*args,**kw):
        rows=original(conn,principal,*args,**kw)
        if principal.actor_uid==w['member'].actor_uid and kw.get('capability')=='call':
            checked.set();assert release.wait(10)
        return rows
    monkeypatch.setattr(access,'contracts',permission)
    with ThreadPoolExecutor(2) as pool:
        writer=pool.submit(store.command,w['member'],cases.call_body(w))
        assert checked.wait(5)
        transfer=pool.submit(cases.apply,w,p)
        try:
            time.sleep(.15);assert not transfer.done()
        finally:release.set()
        assert writer.result(10)['status']=='accepted'
        assert transfer.result(10)[0]['status']=='applied'
    with pytest.raises(CRMError):store.command(w['member'],cases.call_body(w))
    with storage.connect() as c:assert c.execute("SELECT COUNT(*) FROM crm_events WHERE kind='call'").fetchone()[0]==1


@pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_APP_DSN_FILE'),reason='Requires existing restricted application role')
def test_full_distribution_under_select_only_identity_role(pg_world,monkeypatch):
    import psycopg
    w=cases.allocation_world(pg_world)
    with storage.connect() as owner:
        namespace=owner.execute('SELECT current_schema()').fetchone()[0]
        import re
        assert re.fullmatch('crm_test_[a-f0-9]{32}',namespace)
        owner.execute('GRANT USAGE ON SCHEMA '+namespace+' TO crm_app')
        owner.execute('GRANT SELECT ON ALL TABLES IN SCHEMA '+namespace+' TO crm_app')
        for table in ('crm_assignment_heads','crm_assignment_operations','crm_assignment_previews','crm_assignment_changes',
                      'crm_task_grant_revocations','crm_assignment_jobs','crm_assignment_packs','crm_assignments','crm_events','crm_event_contracts','crm_commands','crm_outbox','crm_audit','crm_background_authorities','crm_report_snapshots'):
            owner.execute('GRANT INSERT ON '+table+' TO crm_app')
        for table in ('crm_assignment_heads','crm_assignment_jobs','crm_assignment_packs','crm_assignments','crm_contracts','crm_background_authorities'):
            owner.execute('GRANT UPDATE ON '+table+' TO crm_app')
        owner.execute('GRANT USAGE ON ALL SEQUENCES IN SCHEMA '+namespace+' TO crm_app')
    parts=urlsplit(Path(os.environ['HARD_CRM_TEST_APP_DSN_FILE']).read_text().strip())
    query=[(k,v) for k,v in parse_qsl(parts.query) if k!='options']+[('options','-csearch_path='+namespace)]
    monkeypatch.setenv('HARD_CRM_DATABASE_URL',urlunsplit((parts.scheme,parts.netloc,'/crm_qa',urlencode(query),'')))
    with storage.connect() as c:
        assert c.execute('SELECT current_user').fetchone()[0]=='crm_app'
        for table in ('crm_actor_links','crm_employees','crm_memberships','crm_managed_contracts','crm_distribution_managers'):
            assert not c.execute("SELECT has_table_privilege(current_user,?,'UPDATE')",(namespace+'.'+table,)).fetchone()[0]
    p=cases.preview(w);result,body=cases.apply(w,p)
    assert assignments.commit(w['managers'][0],body)==result
    cases.apply(w,cases.preview(w,'remove',['call','visit'],None))
    assert assignments.statistics(w['managers'][0])['unassigned_slots']==4
    cases.apply(w,cases.preview(w,'assign',['call','visit']))
    assert assignments.statistics(w['managers'][0])['unassigned_slots']==0
    assert assignments.changes(w['member'])['dirty']
    from crm import manager_reports
    assert len(manager_reports.read(w['managers'][0])['snapshots'])==1
    monkeypatch.setattr(assignments,'MAX_SLOTS',1)
    job,_=cases.apply(w,cases.preview(w))
    verified={w['managers'][0].actor_uid:w['managers'][0]}
    manager_reports.tick(verified);manager_reports.tick(verified)
    assert assignments.job(w['managers'][0],job_id=job['job_id'])['state']=='completed'
    with storage.connect() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('UPDATE crm_memberships SET role=role')
