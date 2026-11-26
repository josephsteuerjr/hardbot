"""Primary registration projection on a disposable PostgreSQL cluster."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

import crm_registration
import db
from crm import assignments as distribution, grant_admin, grants, postgres_schema, shared_auth, storage, work_portfolio
from crm.access import Principal
from crm.contract import identifier, now, CRMError
from test_crm_registration import REGISTRATION, assignments, enabled, pause, phone
from test_pc_first_accounts import PASSWORD, http, world


@pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_REGISTRATION_EXCHANGE_DSN_FILE'),
                    reason='Requires a disposable PostgreSQL cluster with crm_exchange')
def test_approved_registration_opens_its_canonical_identity_and_requested_city(world,monkeypatch):
    from psycopg.conninfo import conninfo_to_dict
    from tools.crm_migrate_registry import migrate

    owner=Path(os.environ['HARD_CRM_TEST_DSN_FILE']).read_text().strip()
    exchange=Path(os.environ['HARD_CRM_TEST_REGISTRATION_EXCHANGE_DSN_FILE']).read_text().strip()
    assert conninfo_to_dict(owner)['dbname']==conninfo_to_dict(exchange)['dbname']=='crm_qa'
    assert conninfo_to_dict(owner)['user']=='crm_qa' and conninfo_to_dict(exchange)['user']=='crm_exchange'
    assignments()
    staff = [('Own Employee','tj','Душанбе'),('Foreign Employee','uz','Самарканд'),('Zero Portfolio','tj','Худжанд')]
    with db.connect() as primary:
        primary.execute("UPDATE clients SET partner='fixture' WHERE contract_id='CRM-REG-001'")
        ids={r['display_name']:r['id'] for r in primary.execute('SELECT id,display_name FROM employee_identities')}
        for name,country,office in staff:
            primary.execute('UPDATE staff_roster SET direction=?,office=?,offices=?,manager_uid=NULL WHERE employee_id=?',
                            (country,office,json.dumps([office]),ids[name]))
    with storage.Postgres(owner) as conn:
        assert conn.execute("SELECT to_regclass('public.crm_meta'),to_regnamespace('crm_registry')").fetchone()=={ 'to_regclass':None,'to_regnamespace':None }
        postgres_schema.initialize(conn)
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?)",(shared_auth.NAMESPACE,))
        for name,_,_ in staff:
            key='authority-staff:'+ids[name]
            conn.execute('INSERT INTO crm_employees VALUES(?,?,?,?,?)',
                         (identifier('staff',key),key,name,'synthetic-registration',int(name!='Own Employee')))
        at=now()
        for number,(name,country,_) in enumerate(staff[:2],1):
            person=identifier('person',f'registration-test-{number}')
            contract=identifier('contract',f'registration-test-{number}')
            assignee=identifier('staff','authority-staff:'+ids[name])
            conn.execute('INSERT INTO crm_people VALUES(?,?,?,?)',(person,'Synthetic Customer','test',at))
            conn.execute('INSERT INTO crm_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (contract,person,'registry-partner:fixture',f'REG-{number}',f'CRM-REG-{number:03d}','TJS' if country=='tj' else 'UZS',
                          '{}','{}','{}','{}',at))
            conn.execute('INSERT INTO crm_assignments VALUES(?,?,?,?,?,?,?)',
                         (identifier('assignment',contract),assignee,contract,'visit',at,None,'test'))
    migrate(db.DB_PATH,owner)
    with storage.Postgres(owner) as conn:
        conn.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO crm_app')
        conn.execute('GRANT INSERT ON crm_commands,crm_audit TO crm_app')
        conn.execute('GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO crm_app')
        conn.execute((Path(__file__).parents[1] / 'tools/crm_runtime_permissions.sql').read_text())
        conn.execute('UPDATE crm_registry.migration_receipt SET active=true')
    monkeypatch.setenv('HARD_REGISTRY_DATABASE_URL_FILE',os.environ['HARD_CRM_TEST_REGISTRATION_EXCHANGE_DSN_FILE'])
    pause(1)
    status,request=http('POST',REGISTRATION,dict(role='manager',employee='New City Manager',username='crm-city',
        password=PASSWORD,audience='crm-mobile',device=phone('city'),country='tj',city='Душанбе'))
    assert status==201 and request['pending']
    assert crm_registration.approve('crm-city','principal:synthetic-admin')['active']
    status,manager=http('POST','/api/account/login',dict(username='crm-city',password=PASSWORD,
                                audience='crm-mobile',device=phone('city')))
    assert status==200 and manager['action']=='ok'
    monkeypatch.setenv('HARD_CRM_DATABASE_URL_FILE',os.environ['HARD_CRM_TEST_APP_DSN_FILE'])
    monkeypatch.setenv('HARD_CRM_SHARED','1')
    supervisor=shared_auth.principal(dict(uid=manager['uid'],role='manager'))
    assert supervisor.employee_scope=={supervisor.employee_uuid}
    invite=crm_registration.issue_employee_code('Own Employee')
    status,registered=http('POST',REGISTRATION,dict(role='employee',employee='Own Employee',username='crm-assigned',
        password=PASSWORD,audience='crm-mobile',device=phone('assigned'),code=invite))
    assert status==201 and not registered['pending']
    status,employee=http('POST','/api/account/login',dict(username='crm-assigned',password=PASSWORD,
                                  audience='crm-mobile',device=phone('assigned')))
    assert status==200 and employee['action']=='ok'
    worker=shared_auth.principal(dict(uid=employee['uid'],role='employee'))
    supervisor=shared_auth.principal(dict(uid=manager['uid'],role='manager'))
    assert worker.employee_uuid==identifier('staff','authority-staff:'+ids['Own Employee'])
    assert 'visit' in worker.capabilities and 'call' not in worker.capabilities
    assert supervisor.employee_scope=={supervisor.employee_uuid,worker.employee_uuid}
    assert not supervisor.all_contracts and not supervisor.read_all_contracts
    assert [r['external_id'] for r in work_portfolio.read(worker,work='visit')['contracts']]==['REG-1']
    outside=identifier('staff','authority-staff:'+ids['Foreign Employee'])
    with pytest.raises(CRMError):
        work_portfolio.read(supervisor,employee=outside)
    from tools.crm_runtime import rollback_schema_9
    with monkeypatch.context() as operator:
        operator.setenv('HARD_CRM_DATABASE_URL_FILE',os.environ['HARD_CRM_TEST_DSN_FILE'])
        preview = rollback_schema_9()
        assert not preview['eligible'] and preview['blockers']['registered_accounts'] == 2
        with pytest.raises(ValueError, match='registrations'):
            rollback_schema_9(preview['rollback_sha256'])
    manager_grant = dict(role='manager', territory='employees', employees=[worker.employee_uuid, outside],
                         capabilities=['visit','assign'], is_default=True)
    writer = os.environ.get('HARD_CRM_TEST_GRANT_ADMIN_DSN_FILE')
    if writer:
        with storage.Postgres(owner) as conn:
            conn.execute((Path(__file__).parents[1] / 'tools/crm_grant_admin_permissions.sql').read_text())
        admin = Principal(700700700, supervisor.employee_uuid, supervisor.team_id, 'owner', frozenset(), frozenset())
        monkeypatch.setenv('HARD_CRM_GRANT_ADMIN_DSN_FILE',writer)
        request = dict(operation='preview', actor_uid=manager['uid'], base_revision=0, operation_id=str(uuid4()),
                       grants=[manager_grant,dict(role='logistic', territory='self', capabilities=['logistics'])])
        checked = grant_admin.preview(admin,request)
        from test_crm_grant_admin import apply_body
        applied = grant_admin.apply(admin,apply_body(checked))
        assert applied['production_access_changed']
        assert len(applied['result_grants']) == 2
        assert grant_admin.apply(admin,apply_body(checked)) == applied
    else:
        with storage.Postgres(owner) as conn:
            grants.upsert(conn, manager['uid'], **manager_grant)
            grants.set_model(conn, manager['uid'], 2)
    with storage.Postgres(owner) as conn:
        conn.execute("INSERT INTO crm_meta VALUES('distribution_mode','isolated')")
    scoped = shared_auth.principal(dict(uid=manager['uid'],role='manager',account_bound=True))
    own_contract, unrelated_contract = [identifier('contract',f'registration-test-{i}') for i in (1,2)]
    with storage.connect(readonly=True) as conn:
        assert distribution.readiness(conn,scoped)['enabled']
        assert distribution.allowed_slots(conn,scoped,[own_contract,unrelated_contract]) == {(own_contract,'visit')}
        assert outside not in distribution.targets(conn,scoped)
    selection = dict(kind='contracts', ids=[own_contract])
    for action, target in [('remove',None), ('assign',worker.employee_uuid)]:
        preview = distribution.preview(scoped,dict(schema_version=1,action=action,
            employee_uuid=target,work_kinds=['visit'],selection=selection,comment='Synthetic confirmation'))
        result = distribution.commit(scoped,dict(schema_version=1,operation_id=str(uuid4()),
                                                 preview_id=preview['preview_id']))
        assert result['status'] == 'applied'
    assert work_portfolio.read(worker,work='visit')['total_contracts']==1
    with storage.Postgres(exchange) as conn:
        assert not conn.execute("SELECT has_table_privilege(current_user,'public.crm_actor_links','UPDATE')").fetchone()[0]
