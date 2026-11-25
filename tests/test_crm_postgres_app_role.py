"""Exercise real crm_app credentials against an owned synthetic QA schema only."""
import os,re
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from pathlib import Path
import pytest
from crm import registry,storage,store,active_time
from test_crm_postgres import pg_world
from test_crm_employee_registry import edit
from test_crm_active_time import interval

pytestmark=pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_APP_DSN_FILE'),reason='Requires isolated QA and existing restricted app credentials')

def test_registry_and_activity_preserve_readonly_identity_grants(pg_world,monkeypatch):
    import psycopg
    with storage.connect() as owner:
        namespace=owner.execute('SELECT current_schema()').fetchone()[0]
        assert re.fullmatch('crm_test_[a-f0-9]{32}',namespace)
        owner.execute('GRANT USAGE ON SCHEMA '+namespace+' TO crm_app')
        owner.execute('GRANT SELECT ON ALL TABLES IN SCHEMA '+namespace+' TO crm_app')
        for table in ('crm_events','crm_event_contracts','crm_audit','crm_commands','crm_active_intervals','crm_clock_anchors'):
            owner.execute('GRANT INSERT ON '+table+' TO crm_app')
        owner.execute('GRANT UPDATE ON crm_contracts TO crm_app')
        owner.execute('GRANT USAGE ON ALL SEQUENCES IN SCHEMA '+namespace+' TO crm_app')
    # Prepare an anchored historical fixture under the QA owner.
    activity=interval(pg_world)
    app_dsn=Path(os.environ['HARD_CRM_TEST_APP_DSN_FILE']).read_text().strip()
    parts=urlsplit(app_dsn);assert parts.scheme in {'postgres','postgresql'}
    query=[(k,v) for k,v in parse_qsl(parts.query) if k!='options']+[('options','-csearch_path='+namespace)]
    monkeypatch.setenv('HARD_CRM_DATABASE_URL',urlunsplit((parts.scheme,parts.netloc,'/crm_qa',urlencode(query),'')))
    with storage.connect(readonly=True) as app:
        assert app.execute('SELECT current_database(),current_user').fetchone()[0]=='crm_qa'
        assert app.execute('SELECT current_user').fetchone()[0]=='crm_app'
        for table in ('crm_actor_links','crm_employees','crm_memberships'):
            assert not app.execute("SELECT has_table_privilege(current_user,?,'UPDATE')",(namespace+'.'+table,)).fetchone()[0]
    before=registry.read(pg_world['member'])
    assert before['rows']
    body=edit(pg_world)
    result=registry.edit(pg_world['member'],body)
    assert result['status']=='accepted'
    assert registry.edit(pg_world['member'],body)==result
    active_time.ingest(pg_world['member'],activity)
    assert any(row['fields']['soft_comment']=='Исправленный текст' for row in registry.read(pg_world['member'])['rows'])
    with storage.connect() as app:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute('UPDATE crm_actor_links SET active=0')
