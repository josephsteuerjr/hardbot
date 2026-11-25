import os,sqlite3
import db
from pathlib import Path
import pytest
from crm import storage,schema,primary_fence,runtime
from test_crm_postgres import pg_world
from test_crm_assignments import allocation_world,apply,preview
PRIMARY_CONNECT=db.connect

pytestmark=pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_DSN_FILE'),reason='Isolated PostgreSQL')


def test_schema_four_upgrade_preserves_work_and_adds_runtime(pg_world):
    with storage.connect() as c:
        before=c.execute('SELECT COUNT(*) FROM crm_contracts').fetchone()[0]
        for name in ('crm_primary_pending','crm_primary_fences','crm_runtime_status','crm_background_credentials'):c.execute('DROP TABLE '+name)
        c.execute("UPDATE crm_meta SET value='4' WHERE key='schema_version'")
        schema.initialize(c)
        assert c.execute('SELECT COUNT(*) FROM crm_contracts').fetchone()[0]==before
        assert c.execute("SELECT value FROM crm_meta WHERE key='schema_version'").fetchone()[0]==str(schema.VERSION)
        schema.initialize(c)


def test_schema_seven_upgrade_adds_empty_logistics_tables(pg_world):
    with storage.connect() as c:
        before=c.execute('SELECT COUNT(*) FROM crm_contracts').fetchone()[0]
        for name in ('crm_logistics_calls','crm_logistics_cases','crm_logistics_staff'):
            c.execute('DROP TABLE '+name)
        c.execute("UPDATE crm_meta SET value='7' WHERE key='schema_version'")
        schema.initialize(c)
        assert c.execute('SELECT COUNT(*) FROM crm_contracts').fetchone()[0]==before
        for name in ('crm_logistics_staff','crm_logistics_cases','crm_logistics_calls'):
            assert c.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]==0
        assert c.execute("SELECT value FROM crm_meta WHERE key='schema_version'").fetchone()[0]==str(schema.VERSION)


def test_cutover_projection_and_runtime_under_restricted_application(pg_world,tmp_path,monkeypatch):
    if not os.environ.get('HARD_CRM_TEST_APP_DSN_FILE'):pytest.skip('Restricted role required')
    from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
    import psycopg
    w=allocation_world(pg_world);path=tmp_path/'primary.sqlite3';monkeypatch.setenv('HARD_CRM_PRIMARY_DB',str(path))
    with sqlite3.connect(path) as p:
        p.execute('CREATE TABLE clients(contract_id TEXT PRIMARY KEY,partner TEXT,responsible TEXT,responsible_soft TEXT)')
        p.execute('CREATE TABLE calls(id INTEGER PRIMARY KEY,contract_id TEXT)')
        p.executemany('INSERT INTO clients VALUES(?,?,?,?)',[('R'+str(i),'P'+str(i),'','Employee') for i in range(2)])
    with storage.connect() as c:
        namespace=c.execute('SELECT current_schema()').fetchone()[0]
        for i,cid in enumerate(w['contracts']):c.execute('UPDATE crm_contracts SET legacy_id=?,creditor_id=? WHERE contract_uuid=?',('R'+str(i),'registry-partner:P'+str(i),cid))
        c.execute('GRANT USAGE ON SCHEMA '+namespace+' TO crm_app')
        c.execute('GRANT SELECT ON ALL TABLES IN SCHEMA '+namespace+' TO crm_app')
        c.execute((Path(__file__).parents[1]/'tools/crm_runtime_permissions.sql').read_text())
        exchange = c.execute("SELECT has_table_privilege('crm_exchange','crm_primary_fences','SELECT') AS can_read,"
                             "has_table_privilege('crm_exchange','crm_primary_fences','UPDATE') AS can_write").fetchone()
        assert exchange[0] and not exchange[1]
        c.execute('GRANT INSERT ON crm_audit TO crm_app');c.execute('GRANT USAGE ON ALL SEQUENCES IN SCHEMA '+namespace+' TO crm_app')
    primary_fence.activate(w['contracts'])
    parts=urlsplit(Path(os.environ['HARD_CRM_TEST_APP_DSN_FILE']).read_text().strip())
    query=[(k,v) for k,v in parse_qsl(parts.query) if k!='options']+[('options','-csearch_path='+namespace)]
    monkeypatch.setenv('HARD_CRM_DATABASE_URL',urlunsplit((parts.scheme,parts.netloc,'/crm_qa',urlencode(query),'')))
    apply(w,preview(w));assert primary_fence.project_pending()['projected']==2
    runtime.remember(w['managers'][0].actor_uid,'sealed-synthetic');runtime.status(True,'',{})
    with storage.connect() as c:
        assert c.execute('SELECT current_user').fetchone()[0]=='crm_app'
        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('UPDATE crm_primary_fences SET fence_id=fence_id')
    with sqlite3.connect(path) as p:assert p.execute('SELECT responsible_soft FROM clients ORDER BY contract_id').fetchall()==[('New employee',)]*2


def test_import_distribution_work_mirror_and_report_form_one_pipeline(pg_world,monkeypatch,record_property,tmp_path):
    from dataclasses import replace
    from crm import legacy_worker,store,manager_reports
    from crm.contract import canonical
    from test_crm_primary_fence import setup
    from test_crm_core import call_body
    from fields import FIELDS
    w,path=setup(pg_world,monkeypatch)
    with monkeypatch.context() as local:
        local.setattr(db,'connect',PRIMARY_CONNECT);local.setattr(db,'DB_PATH',path)
        incoming=dict(contract_id='R0',partner='P0',debt_total='321.25')
        grid=[[f.title for f in FIELDS],[f.source for f in FIELDS],[incoming.get(f.key) for f in FIELDS]]
        report=db.import_grid(grid,source_kind=db.IMPORT_SOURCE_TEST_SUBSET)
        assert not report['blocked'] and report['updated']==1
    legacy_worker.tick(path,[])
    primary_fence.activate(w['contracts']);apply(w,preview(w));primary_fence.project_pending()
    with storage.connect() as c:c.execute('INSERT INTO crm_actor_links VALUES(?,?,?,1)',(500,w['target'],'fixture'))
    body=call_body(w);accepted=store.command(w['new_member'],body)
    assert store.command(w['new_member'],body)==accepted
    mirrored=legacy_worker.tick(path,[]);assert mirrored['outgoing']['delivered']==1
    legacy_worker.tick(path,[])
    with sqlite3.connect(path) as p:
        assert p.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
        assert p.execute("SELECT responsible_soft,debt_total FROM clients WHERE contract_id='R0'").fetchone()==('New employee','321.25')
    with storage.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM crm_events WHERE kind='call'").fetchone()[0]==1
    manager=replace(w['managers'][0],employee_scope=w['managers'][0].employee_scope|{w['target']})
    result=manager_reports.read(manager)['report']
    assert result['sheets']
    record_property('crm_pipeline_report',canonical(result))
    if os.environ.get('HARD_CRM_TEST_PORTABLE_XLSX')=='1':
        from pult.core.portable_xlsx import write_report
        import uuid
        path=Path(os.environ.get('HARD_CRM_TEST_ARTIFACT_DIR',str(tmp_path)))/('crm-linux-report-'+uuid.uuid4().hex+'.xlsx')
        saved=write_report(result,path,'three-hour');assert saved['verified']
        record_property('crm_linux_report',canonical(saved))
