import base64
import json
import os
from pathlib import Path
import tempfile
import uuid

from python_calamine import CalamineWorkbook
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from crm import assignments, logistics, shared_auth, storage, store, work_portfolio
from crm.contract import CRMError, canonical
from test_crm_core import call_body, crm_world
from test_crm_postgres import pg_world


def create_staff(conn, name, uid):
    employee = str(uuid.uuid4())
    conn.execute('INSERT INTO crm_employees VALUES(?,?,?,?,?)', (employee, employee, name, 'fixture', 1))
    conn.execute('INSERT INTO crm_actor_links VALUES(?,?,?,?)', (uid, employee, 'fixture', 1))
    conn.execute('INSERT INTO crm_memberships VALUES(?,?,?,?,?,?,?,?)',
                 (str(uuid.uuid4()), employee, 'team-A', 'employee', '["call"]',
                  'Asia/Tashkent', '2020-01-01T00:00:00+00:00', None))
    return employee


def setup(w):
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('distribution_mode','isolated') "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        conn.execute('INSERT INTO crm_meta VALUES(?,?)', ('deployment_namespace', shared_auth.NAMESPACE))
        logist = create_staff(conn, 'Logistic', 303)
        second = create_staff(conn, 'Second logistic', 304)
        courier = create_staff(conn, 'Courier', 305)
        manifest = {'UZ': {'logist': [logist], 'call': [w['member'].employee_uuid,w['stranger'].employee_uuid],
                           'delivery': [courier]},
                    'TJ': {'logist': [second], 'call': [], 'delivery': []}}
        plan = logistics.configure_staff(conn, manifest)
        assert plan['role_changes'] == {'employee → courier': 1, 'employee → logistic': 2}
        logistics.configure_staff(conn, manifest, plan['selection_sha256'])
        for country,cid in zip(('Узбекистан','Таджикистан'),w['contracts']):
            row = conn.execute('SELECT fields_json FROM crm_contracts WHERE contract_uuid=?',(cid,)).fetchone()
            values = canonical(dict(json.loads(row[0]),country=country,responsible_soft='Ordinary Soft'))
            conn.execute('UPDATE crm_contracts SET fields_json=?,baseline_json=? WHERE contract_uuid=?',(values,values,cid))
    return [shared_auth.principal({'uid':uid,'role':'employee'}) for uid in (303,304,305)]


def source(country='UZ', partner='creditor-0', phone=''):
    return dict(country=country, workbook_sha256='a'*64, since='2026-01-01', until='2026-01-31',
                rows=[dict(line=2,number='DUPLICATE', partner=partner, courier='Courier',
                           phone=phone, transferred_on='2026-01-01')])


def upload(principal, body):
    plan = logistics.preview(principal,body)
    command = dict(source=body,proof=plan['proof'],operation_id=str(uuid.uuid4()))
    result = logistics.apply(principal, command)
    assert logistics.apply(principal, command) == result
    return result


def change(principal, row, **values):
    return logistics.update(principal,dict(country=row['country'],operation_id=str(uuid.uuid4()),
        selection=[dict(contract_uuid=row['contract_uuid'], revision=row['revision'])],values=values))


def test_legacy_manager_signin_opens_only_the_logistics_workspace(crm_world):
    from crm import access, workspace

    setup(crm_world)
    principal = shared_auth.principal({'uid': 304, 'role': 'manager', 'server_operator': True})
    assert principal.role == 'logistic' and principal.capabilities == {'logistics'}
    assert not access.reads_all(principal) and not principal.server_operator
    menu = workspace.manifest(principal)
    assert menu['defaultModule'] == 'logistics'
    assert [module['id'] for module in menu['tree']] == ['logistics', 'connection']
    assert logistics.read(principal, country='TJ')['total'] == 0
    with pytest.raises(CRMError) as denied:
        logistics.read(principal, country='UZ')
    assert denied.value.status == 403


def test_pool_delivery_call_reassignment_and_export(crm_world):
    w=crm_world
    logist, other_logist, courier=setup(w)
    cid=w['contracts'][0]
    assert logist.role == 'logistic' and not logist.read_all_contracts
    assert upload(logist,source())['imported'] == 1
    assert logistics.preview(logist,source())['counts'] == {'existing':1}
    assert logistics.read(logist,country='UZ',phones='without')['total'] == 1
    assert work_portfolio.read(courier,work='delivery')['total_contracts']==1
    body=dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='delivery',contract_ids=[cid],
              base_revisions={},payload=dict(delivered=True,delivery_address='Synthetic address',
                  delivery_phone='+998 90 123 45 67',delivery_comment='Envelope delivered'))
    store.command(courier,body)
    row=logistics.read(logist,country='UZ',phones='with')['rows'][0]
    assert row['available_phone']=='+998 90 123 45 67'
    assert row['delivery']['delivery_result']=='Конверт передан'
    old=assignments.changes(w['stranger'])['cursor']
    change(logist,row,caller_uuid=w['stranger'].employee_uuid)
    assert assignments.changes(w['stranger'],**old)['dirty']
    portfolio=work_portfolio.read(w['stranger'],work='logistics')
    assert portfolio['total_contracts']==1
    assigned=portfolio['contracts'][0]
    assert assigned['work_kinds'] == ['logistics']
    assert assigned['logistics_call']['phone']=='+998 90 123 45 67'
    with pytest.raises(CRMError):
        store.command(w['stranger'],dict(call_body(w),contract_ids=[cid]))
    call=dict(contract_uuid=cid,revision=assigned['logistics_call']['revision'],
              operation_id=str(uuid.uuid4()),status='Нет контакта',comment='Courier number tested')
    receipt=logistics.record_call(w['stranger'],call)
    assert logistics.record_call(w['stranger'],call)==receipt
    assert logistics.read(logist,country='UZ')['rows'][0]['call_comment']=='Courier number tested'
    workbook=logistics.export(logist,country='UZ',caller=w['stranger'].employee_uuid)
    assert len(workbook['rows'])==1 and workbook['rows'][0][9]=='Нет контакта'
    downloaded=logistics.download(logist,country='UZ',caller=w['stranger'].employee_uuid)
    content=base64.b64decode(downloaded['content_base64'])
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/downloaded['filename'];path.write_bytes(content)
        with CalamineWorkbook.from_path(str(path)) as book:
            rows=book.get_sheet_by_name(workbook['title']).to_python()
            assert len(rows)==2 and rows[1][9]=='Нет контакта'
    assert downloaded['rows']==1 and downloaded['sha256']==__import__('hashlib').sha256(content).hexdigest()
    cursor=assignments.changes(w['stranger'])['cursor']
    current=logistics.read(logist,country='UZ')['rows'][0]
    change(logist,current,caller_uuid=w['member'].employee_uuid)
    assert assignments.changes(w['stranger'],**cursor)['dirty']
    assert work_portfolio.read(w['stranger'],work='logistics')['total_contracts']==0
    assert work_portfolio.read(w['member'],work='logistics')['total_contracts']==1
    with pytest.raises(CRMError):
        logistics.record_call(w['stranger'],dict(call,operation_id=str(uuid.uuid4()),revision=receipt['revision']))
    with pytest.raises(CRMError):
        logistics.read(other_logist,country='UZ')
    with storage.connect(readonly=True) as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_logistics_calls').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM crm_assignments').fetchone()[0]==4
        values=json.loads(conn.execute('SELECT fields_json FROM crm_contracts WHERE contract_uuid=?',(cid,)).fetchone()[0])
        assert values['responsible_soft']=='Ordinary Soft' and values['soft_comment']=='Base'


def test_manager_retains_manager_role_when_logistics_is_added(crm_world):
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('distribution_mode','isolated')")
        conn.execute('INSERT INTO crm_meta VALUES(?,?)', ('deployment_namespace', shared_auth.NAMESPACE))
        manager = create_staff(conn, 'Manager', 306)
        conn.execute("UPDATE crm_memberships SET role='manager',capabilities_json='[\"analytics\",\"visit\"]' WHERE employee_uuid=?", (manager,))
        manifest = {'TJ': {'logist': [manager], 'call': [], 'delivery': []}}
        plan = logistics.configure_staff(conn, manifest)
        assert plan['role_changes'] == {}
        logistics.configure_staff(conn, manifest, plan['selection_sha256'])
    member = shared_auth.principal({'uid': 306, 'role': 'manager'})
    assert member.role == 'manager' and 'logistics' in member.capabilities
    with storage.connect(readonly=True) as conn:
        assert logistics.countries(conn, member) == ['TJ']
    assert 'analytics' in member.capabilities and 'visit' in member.capabilities


def test_excel_courier_is_linked_to_existing_account_without_registration(crm_world):
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('distribution_mode','isolated')")
        conn.execute('INSERT INTO crm_meta VALUES(?,?)', ('deployment_namespace', shared_auth.NAMESPACE))
        logist = create_staff(conn, 'Logistic', 303)
        courier = create_staff(conn, 'Courier', 305)
        conn.execute("UPDATE crm_memberships SET role='courier',capabilities_json='[\"delivery\"]' WHERE employee_uuid=?", (courier,))
        contract = crm_world['contracts'][0]
        row = conn.execute('SELECT fields_json FROM crm_contracts WHERE contract_uuid=?', (contract,)).fetchone()
        values = canonical(dict(json.loads(row[0]), country='Узбекистан', responsible_soft='Ordinary Soft'))
        conn.execute('UPDATE crm_contracts SET fields_json=?,baseline_json=? WHERE contract_uuid=?', (values, values, contract))
        manifest = {'UZ': {'logist': [logist], 'call': [], 'delivery': []}}
        plan = logistics.configure_staff(conn, manifest)
        logistics.configure_staff(conn, manifest, plan['selection_sha256'])
    result = upload(shared_auth.principal({'uid': 303, 'role': 'employee'}), source())
    assert result['imported'] == 1
    with storage.connect(readonly=True) as conn:
        assert conn.execute("SELECT courier_uuid FROM crm_logistics_cases WHERE country='UZ'").fetchone()[0] == courier
        assert conn.execute("SELECT 1 FROM crm_logistics_staff WHERE country='UZ' AND work='delivery' AND employee_uuid=?",
                            (courier,)).fetchone()
        assert conn.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0


def test_unassigned_case_is_not_reported_as_courier_work(crm_world):
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('distribution_mode','isolated')")
        conn.execute('INSERT INTO crm_meta VALUES(?,?)', ('deployment_namespace', shared_auth.NAMESPACE))
        logist = create_staff(conn, 'Logistic', 303)
        row = conn.execute('SELECT fields_json FROM crm_contracts WHERE contract_uuid=?', (crm_world['contracts'][0],)).fetchone()
        values = canonical(dict(json.loads(row[0]), country='Узбекистан', responsible_soft='Ordinary Soft'))
        conn.execute('UPDATE crm_contracts SET fields_json=?,baseline_json=? WHERE contract_uuid=?', (values, values, crm_world['contracts'][0]))
        manifest = {'UZ': {'logist': [logist], 'call': [], 'delivery': []}}
        plan = logistics.configure_staff(conn, manifest)
        logistics.configure_staff(conn, manifest, plan['selection_sha256'])
    principal = shared_auth.principal({'uid': 303, 'role': 'employee'})
    body = source()
    body['rows'][0]['courier'] = ''
    upload(principal, body)
    assert logistics.export(principal, country='UZ')['rows'][0][-1] == 'Не назначено'


def test_roster_can_offer_existing_staff_by_country(crm_world, monkeypatch):
    with storage.connect() as conn:
        employee = create_staff(conn, 'Existing Soft', 307)
        courier = create_staff(conn, 'Existing Courier', 308)
        conn.execute("UPDATE crm_memberships SET role='courier',capabilities_json='[\"delivery\"]' WHERE employee_uuid=?", (courier,))
        from crm import team_territories
        monkeypatch.setattr(team_territories, 'staff', lambda principal, rows, shared: {
            employee: {'country': 'UZ'}, courier: {'country': 'UZ'}})
        assert logistics.roster(conn, 'UZ') == {'call': {employee: 'Existing Soft'}, 'delivery': {courier: 'Existing Courier'}}


def test_country_mismatch_stale_preview_and_atomic_assignment(crm_world):
    w=crm_world
    logist, other_logist, _=setup(w)
    assert logistics.preview(logist,source(partner='creditor-1'))['counts']=={'review':1}
    assert logistics.preview(logist,source(partner=''))['counts']=={'review':1}
    with pytest.raises(CRMError):
        logistics.preview(logist,source(country='TJ',partner='creditor-1'))
    good=source(phone='998901234567')
    reviewed=logistics.preview(logist,good)
    forged=dict(good,rows=[dict(good['rows'][0],phone='998904444444')])
    with pytest.raises(CRMError,match='предпросмотр'):
        logistics.apply(logist,dict(source=forged,proof=reviewed['proof'],operation_id=str(uuid.uuid4())))
    upload(logist,good)
    upload(other_logist,source(country='TJ',partner='creditor-1'))
    uz=logistics.read(logist,country='UZ')['rows'][0]
    tj=logistics.read(other_logist,country='TJ')['rows'][0]
    with pytest.raises(CRMError):
        logistics.update(logist,dict(country='UZ',operation_id=str(uuid.uuid4()),
            selection=[{k:r[k] for k in ('contract_uuid','revision')} for r in (uz,tj)],
            values=dict(caller_uuid=w['stranger'].employee_uuid)))
    assert logistics.read(logist,country='UZ')['rows'][0]['caller_uuid'] is None
    change(logist,uz,caller_uuid=w['stranger'].employee_uuid)
    with pytest.raises(CRMError):
        change(logist,uz,caller_uuid=w['member'].employee_uuid)
    now=logistics.read(logist,country='UZ')['rows'][0]
    change(logist,now,close=True)
    assert work_portfolio.read(w['stranger'],work='logistics')['total_contracts']==0
    assert logistics.read(logist,country='UZ',state='closed')['total']==1


@pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_DSN_FILE'), reason='Isolated PostgreSQL')
@pytest.mark.parametrize('case', [test_pool_delivery_call_reassignment_and_export,
                                  test_country_mismatch_stale_preview_and_atomic_assignment])
def test_logistics_pipeline_on_postgres(pg_world, case):
    case(pg_world)


@pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_APP_DSN_FILE'), reason='Restricted PostgreSQL role')
def test_logistics_uses_restricted_app_role(pg_world, monkeypatch):
    logist, _, _ = setup(pg_world)
    with storage.connect() as conn:
        namespace = conn.execute('SELECT current_schema()').fetchone()[0]
        conn.execute('GRANT USAGE ON SCHEMA ' + namespace + ' TO crm_app')
        conn.execute('GRANT SELECT ON ALL TABLES IN SCHEMA ' + namespace + ' TO crm_app')
        conn.execute('GRANT INSERT ON crm_commands,crm_audit TO crm_app')
        conn.execute('GRANT USAGE ON ALL SEQUENCES IN SCHEMA ' + namespace + ' TO crm_app')
        conn.execute((Path(__file__).parents[1] / 'tools/crm_runtime_permissions.sql').read_text())
    parts = urlsplit(Path(os.environ['HARD_CRM_TEST_APP_DSN_FILE']).read_text().strip())
    query = dict(parse_qsl(parts.query), options='-csearch_path=' + namespace)
    monkeypatch.setenv('HARD_CRM_DATABASE_URL', urlunsplit(parts._replace(query=urlencode(query))))
    with storage.connect(readonly=True) as conn:
        identity = conn.execute("SELECT current_user,current_schema,current_schemas(true)").fetchone()
        assert identity[0] == 'crm_app', identity
        allowed = conn.execute("SELECT has_table_privilege(?, 'UPDATE')",
                               (f'"{namespace}".crm_logistics_staff',)).fetchone()
        assert not allowed[0], identity
    member = pg_world['member']
    assert upload(logist, source(phone='998901234567'))['imported'] == 1
    case = logistics.read(logist, country='UZ')['rows'][0]
    change(logist, case, caller_uuid=member.employee_uuid)
    row = work_portfolio.read(member, work='logistics')['contracts'][0]
    logistics.record_call(member, dict(contract_uuid=row['contract_uuid'],
        revision=row['logistics_call']['revision'], operation_id=str(uuid.uuid4()),
        status='Нет контакта', comment='Restricted runtime'))
    assert logistics.export(logist, country='UZ')['rows'][0][10] == 'Restricted runtime'
