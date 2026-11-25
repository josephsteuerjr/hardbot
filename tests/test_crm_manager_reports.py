import json
from dataclasses import replace
from datetime import date
import pytest
from crm import manager_reports,storage,assignments,three_hour
from crm.contract import CRMError,now
from test_crm_core import crm_world
from test_crm_assignments import allocation_world,preview,apply


def test_three_hour_promise_counts_the_recorded_amount():
    today=date(2026,11,23)
    records=[dict(client='Один',soft_call_status='Обещает оплатить',soft_comm_date=today.isoformat(),
                  forecast_sum='123.45',agreed_payment='Обещает оплатить клиент')]
    assert three_hour.metrics(records,today,False)[2:4]==[1,'123.45']


def test_country_reports_isolate_totals_and_snapshot_authority(crm_world, monkeypatch):
    w = allocation_world(crm_world)
    manager = w['managers'][0]
    monkeypatch.setattr(three_hour, 'country_scopes', lambda conn, p: {
        'tj': frozenset({w['employee']}), 'uz': frozenset({w['target']}),
    })
    assert [item['code'] for item in manager_reports.read(manager, options='1')['countries']] == ['tj','uz']
    tj = manager_reports.read(manager, country='tj')
    uz = manager_reports.read(manager, country='uz')
    assert tj['report']['country'] == 'tj' and uz['report']['country'] == 'uz'
    for sheet in tj['report']['sheets']:
        assert {row['employee_uuid'] for row in sheet['rows']} == {w['employee']}
    for sheet in uz['report']['sheets']:
        assert all(row['employee_uuid'] == w['target'] for row in sheet['rows'])
    saved = tj['snapshots'][0]['snapshot_id']
    for sheet in tj['snapshots'][0]['sheets']:
        for row in sheet['rows']:
            assert 'current' in row and 'yesterday' in row
            assert 'current_records' not in row and 'yesterday_records' not in row
    assert manager_reports.read(manager, snapshot_id=saved, country='tj')['report'] == json.loads(json.dumps(tj['report']))
    with pytest.raises(CRMError, match='Срез недоступен'):
        manager_reports.read(manager, snapshot_id=saved, country='uz')
    with pytest.raises(CRMError, match='Отчёт по этой стране недоступен'):
        manager_reports.read(manager, country='other')


def test_report_presets_are_bounded_and_schedule_without_saved_sessions(crm_world, monkeypatch):
    from base64 import b64decode
    from hashlib import sha256
    from io import BytesIO
    from python_calamine import CalamineWorkbook
    from crm import report_presets

    world = allocation_world(crm_world)
    manager = world['managers'][0]
    monkeypatch.setattr(three_hour, 'country_scopes', lambda conn, p: {'uz': frozenset({world['employee']})})
    monkeypatch.setattr(report_presets, 'active_managers', lambda: {manager.actor_uid: 'manager'})
    monkeypatch.setattr(report_presets.shared_auth, 'principal', lambda identity: manager)
    with storage.connect() as conn:
        conn.execute("UPDATE crm_meta SET value='cutover' WHERE key='distribution_mode'")
    assert report_presets.schedule() == dict(captured=1,archived=1,denied=0)
    assert report_presets.schedule() == dict(captured=0,archived=0,denied=0)
    with storage.connect(readonly=True) as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_background_authorities').fetchone()[0]==0
    for preset in ('daily','three-hour'):
        result=report_presets.download(manager,preset=preset)
        content=b64decode(result['content_base64'])
        assert sha256(content).hexdigest()==result['sha256']
        with CalamineWorkbook.from_filelike(BytesIO(content)) as book:
            if preset=='daily':
                assert 'Звонки' in book.get_sheet_by_name('Сводка').to_python()[0]
            else:
                assert book.sheet_names[:2]==['Узбекистан · звонки','Узбекистан · SMS']
    with pytest.raises(CRMError):
        report_presets.download(manager,preset='three-hour',country='tj')
    with pytest.raises(CRMError):
        report_presets.download(world['member'],preset='daily')


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
    manager_reports.tick({(p.actor_uid,p.grant_id):p})
    assert assignments.job(p,job_id=result['job_id'])['applied_slots']==1
    with storage.connect() as c:c.execute("UPDATE crm_memberships SET role='observer' WHERE employee_uuid=?",(p.employee_uuid,))
    manager_reports.tick({(p.actor_uid,p.grant_id):p})
    assert assignments.job(p,job_id=result['job_id'])['applied_slots']==1
    with storage.connect() as c:c.execute("UPDATE crm_memberships SET role='manager' WHERE employee_uuid=?",(p.employee_uuid,))
    manager_reports.tick({(p.actor_uid,p.grant_id):p})
    assert assignments.job(p,job_id=result['job_id'])['state']=='completed'


def test_report_current_assignment_obeys_removal(crm_world):
    w=allocation_world(crm_world);p=w['managers'][0]
    apply(w,preview(w,'remove',['call'],None))
    report=three_hour.report(p)
    assert all(not row['current_records'] for sheet in report['sheets'] for row in sheet['rows'])


def test_monthly_report_uses_team_scope_without_distribution_access(crm_world):
    world=allocation_world(crm_world)
    manager=world['managers'][0]
    report_viewer=replace(manager,capabilities=manager.capabilities-{'assign'},read_all_contracts=True)
    with storage.connect() as conn:
        conn.execute("DELETE FROM crm_meta WHERE key='distribution_mode'")
    result=assignments.statistics(report_viewer)
    assert result['contracts']==len(world['contracts'])
    assert {row['employee_uuid'] for row in result['staff']}==manager.employee_scope
    with pytest.raises(CRMError):
        assignments.statistics(report_viewer,employee_uuid=world['target'],kind='call')
    with pytest.raises(CRMError):
        assignments.statistics(replace(report_viewer,capabilities=frozenset()))
    with pytest.raises(CRMError):
        assignments.preview(report_viewer,{})


def test_team_results_keep_original_author_and_scope_when_distribution_is_disabled(crm_world):
    from crm import store
    from test_crm_core import call_body

    w=allocation_world(crm_world);p=w['managers'][0]
    store.command(w['member'],call_body(w))
    apply(w,preview(w))
    with storage.connect() as conn:
        conn.execute("DELETE FROM crm_meta WHERE key='distribution_mode'")

    report=assignments.statistics(p,period='today')
    assert report['since']==report['through']
    assert {r['employee_uuid']:r['call'] for r in report['staff']}=={w['employee']:1,p.employee_uuid:0}
    detail=assignments.statistics(p,period='today',employee_uuid=w['employee'],kind='call')
    assert detail['total']==1 and len(detail['rows'][0]['contracts'])==2
    yesterday=assignments.statistics(p,period='yesterday')['since']
    dates=dict(period='custom',date_from=yesterday,date_to=report['through'])
    custom=assignments.statistics(p,**dates)
    assert (custom['since'],custom['through'])==(yesterday,report['through'])
    assert {r['employee_uuid']:r['call'] for r in custom['staff']}=={w['employee']:1,p.employee_uuid:0}
    assert assignments.statistics(p,employee_uuid=w['employee'],kind='call',**dates)==detail
    for invalid in ({**dates,'date_from':None},{**dates,'date_from':'2020-01-01'},
                    {**dates,'date_to':'2099-01-01'},{**dates,'period':'week'}):
        with pytest.raises(CRMError):
            assignments.statistics(p,**invalid)
    assert all(r['call']==0 for r in assignments.statistics(p,period='yesterday')['staff'])
    with pytest.raises(CRMError,match='Сотрудник недоступен'):
        assignments.statistics(p,period='today',employee_uuid=w['target'],kind='call')
    with pytest.raises(CRMError,match='доступны руководителю'):
        assignments.statistics(w['member'],period='today')


def test_city_restricts_both_totals_and_direct_detail(crm_world, monkeypatch):
    from crm import store, team_territories
    from test_crm_core import call_body
    w=allocation_world(crm_world)
    manager=w['managers'][0]
    store.command(w['member'],call_body(w))
    def geography(principal,rows,*,shared):
        return {r['employee_uuid']:dict(name=r['name'],country='uz',
                offices=['Ташкент' if r['employee_uuid']==w['employee'] else 'Андижан']) for r in rows}
    monkeypatch.setattr(team_territories,'staff',geography)
    filters=dict(period='today',country='uz',office='Ташкент')
    result=assignments.statistics(manager,**filters)
    assert result['territories']=={'uz':['Андижан','Ташкент']}
    assert [(r['employee_uuid'],r['call']) for r in result['staff']]==[(w['employee'],1)]
    assert assignments.statistics(manager,employee_uuid=w['employee'],kind='call',**filters)['total']==1
    assert not assignments.statistics(manager,period='today',country='tj')['staff']
    with pytest.raises(CRMError,match='Сотрудник недоступен'):
        assignments.statistics(manager,employee_uuid=manager.employee_uuid,kind='call',**filters)


def test_visit_workbook_follows_period_roster_and_contract_scope(crm_world, monkeypatch):
    from base64 import b64decode
    from hashlib import sha256
    from io import BytesIO
    from uuid import uuid4

    from python_calamine import CalamineWorkbook
    from crm import store, team_visits_export

    w = allocation_world(crm_world)
    manager = w['managers'][0]
    cid = w['contracts'][0]
    store.command(w['member'], dict(
        schema_version=1, kind='visit', operation_id=str(uuid4()), contract_ids=[cid],
        payload=dict(values=dict(visit_comment='=1+1'), field_visit=dict(point=dict(lat=4, lon=5))),
    ))
    report = assignments.statistics(manager, period='today')
    assert sum(r['visit'] for r in report['staff']) == 1
    writer = next(r['name'] for r in report['staff'] if r['visit'])

    def read(**filters):
        result = assignments.statistics(manager, period='today', download='visits', **filters)
        payload = b64decode(result['content_base64'])
        assert sha256(payload).hexdigest() == result['sha256']
        with CalamineWorkbook.from_filelike(BytesIO(payload)) as book:
            assert book.sheet_names == ['Сводка', 'Выезды', 'По дням']
            return {name: book.get_sheet_by_name(name).to_python() for name in book.sheet_names}

    tables = read(employee_search=writer.upper())
    assert len(tables['Выезды']) == 2
    assert tables['Выезды'][1][3] == writer
    assert tables['Выезды'][1][9] == '=1+1'
    assert tables['Выезды'][1][12] == '4, 5'
    assert tables['Сводка'][-1][3] == tables['По дням'][1][1] == 1
    assert len(read(employee_search='unmatched-name')['Выезды']) == 1
    assert len(assignments.statistics(manager, period='yesterday', download='visits')['content_base64']) > 0
    with pytest.raises(CRMError):
        assignments.statistics(w['member'], period='today', download='visits')

    with storage.connect() as conn:
        conn.execute('UPDATE crm_memberships SET capabilities_json=? WHERE employee_uuid=?',
                     (json.dumps(['analytics']), manager.employee_uuid))

    def readable_contracts(conn, principal):
        assert 'assign' not in principal.capabilities
        return set()

    monkeypatch.setattr(team_visits_export.access, 'reads_all', lambda principal: False)
    monkeypatch.setattr(team_visits_export.access, 'readable_contracts', readable_contracts)
    restricted = read()['Выезды'][1]
    assert restricted[4:6] == ['', '']
    assert restricted[9] == restricted[12] == ''
    assert restricted[13] == 'Часть сведений ограничена'


def test_baseline_status_visible_only_when_current_value_has_legacy_sync_provenance():
    from crm.store import previous_statuses

    key = 'soft_call_status'
    baseline = {key: 'Рассрочка'}
    fields = {key: 'В работе'}
    provenance = {'_fields': {key: {'event_id': 'sync'}}}
    happened = '2026-11-23T05:00:00Z'
    sync = dict(kind='source_sync', origin='legacy', contract_ids=['cid'], occurred_at=happened)
    assert previous_statuses(baseline, fields, provenance, {'sync': sync}, 'cid') == [
        dict(key=key, baseline='Рассрочка', current='В работе', exchanged_at=happened),
    ]
    assert not previous_statuses(baseline, fields, provenance, {'sync': sync}, 'other')
    assert not previous_statuses(baseline, fields, provenance, {'sync': {**sync, 'kind': 'call'}}, 'cid')
    assert not previous_statuses(baseline, {key:'Оплачено'}, provenance, {'sync': sync}, 'cid')
