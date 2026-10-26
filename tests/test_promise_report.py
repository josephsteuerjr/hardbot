"""Separate cohort/evaluation dates without changing the existing Soft report."""
import asyncio
from datetime import date
from io import BytesIO
import json

from aiohttp import web as aio_web
from aiohttp.test_utils import make_mocked_request
from openpyxl import load_workbook
import pytest

import db
import digest_data as dd
import promise_report as report
import web


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'promises.db')
    monkeypatch.setattr(db.workday,'today',lambda:date(2026,10,22))
    db.init_db()
    with db.connect() as conn:
        for cid,client,employee in [('001','Первый','S1'),('002','Первый','S1'),('003','Другой','S2')]:
            conn.execute('INSERT INTO clients(contract_id,client,birth_date,responsible,responsible_soft,partner,debt_today,country,city) VALUES(?,?,?,?,?,?,?,?,?)',
                         (cid,client,'1990-01-01','H1',employee,'Тест','1000','Узбекистан','Ташкент'))
        conn.commit()
    db.reset_role_caches()
    yield
    db.reset_role_caches()


def call(*,day='2026-10-19',due='2026-10-22',amount='100.25',employee='S1',rep='001',op='one'):
    return db.save_call_result(rep,100,employee,{'soft_call_status':'Обещает оплатить','promise_date':due,'forecast_sum':amount},
                              'Статус: Обещает оплатить;',op_id=op,on_date=day,saved_at=day+'T10:00:00')


def payment(amount,day,cid='001'):
    with db.connect() as conn:
        conn.execute('UPDATE clients SET last_payment_date=?,last_payment_sum=? WHERE contract_id=?',(day,str(amount),cid))
        db._remember_registry_payments(conn)
        conn.commit()


def params(**kw):
    return report.parameters(kw.pop('frm','2026-10-19'),kw.pop('to','2026-10-19'),kw.pop('asof','2026-10-22'),**kw)


def data(**kw):
    return report.data({},params(**kw),['S1'])


def test_later_payment_updates_cohort_without_redefining_old_period(world):
    call();payment('100.25','2026-10-21')
    old = dd._soft_employee_stats('S1',{'from':'2026-10-19','to':'2026-10-19','days':1})
    assert old['ptp']==1 and old['kp']==0
    assert data()['totals']['kept']==1
    assert data(asof='2026-10-20')['totals']['kept']==0


def test_selection_does_not_mix_later_days_and_filters_due_date(world):
    call();call(day='2026-10-20',due='2026-10-23',op='later')
    call(day='2026-10-19',due='2026-10-21',op='shorter',rep='003',employee='S2')
    result=report.data({},params(due='2026-10-22'),['S1','S2'])
    assert result['totals']['ptp']==1 and result['items'][0]['due']=='2026-10-22'
    assert result['calls']==2


def test_scope_excludes_foreign_calls_and_rejects_unknown_country(world):
    call();call(employee='S2',rep='003',op='foreign')
    assert data()['totals']['ptp']==1
    assert data(country='tj')['items']==[]
    with pytest.raises(ValueError):params(country='xx')


def test_due_today_is_interim_and_yesterday_is_closed(world):
    call();result=data()
    assert result['totals']['waiting']==1 and result['open_deadlines']
    assert not result['items'][0]['deadline_closed']
    call(rep='003',employee='S2',due='2026-10-21',op='expired')
    result=report.data({},params(),['S1','S2'])
    assert result['totals']['unpaid']==1
    assert sum(result['totals'][k] for k in report.STATUSES)==result['totals']['ptp']


def test_past_asof_includes_entire_deadline_day(world):
    call(due='2026-10-21');payment('100.25','2026-10-21')
    result=data(asof='2026-10-21')
    assert result['totals']['kept']==1 and result['items'][0]['deadline_closed']
    assert not result['open_deadlines']


def test_two_registry_payments_reconcile_cents_and_contract_group_once(world):
    call();payment('40.10','2026-10-19');payment('60.15','2026-10-21',cid='002')
    result=data()
    assert result['totals']['ptp']==1 and result['totals']['kept']==1
    assert result['items'][0]['received']==100.25 and result['items'][0]['remaining']==0


def test_partial_late_and_manual_evidence_are_distinct(world):
    call();payment('25.25','2026-10-20');payment('75','2026-10-23')
    db.record_reported_payment('manual','001','S1',100,paid_at='2026-10-21',amount=100.25,currency='₽',created_at='2026-10-21T10:00:00')
    result=data()
    assert result['totals']['kept']==0 and result['totals']['partial']==1
    assert result['items'][0]['received']==25.25 and result['items'][0]['remaining']==75


def test_subsequent_owner_promise_claims_payment_without_leaking_foreign_author(world):
    call()
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='S2' WHERE contract_id IN ('001','002')");conn.commit()
    call(day='2026-10-20',employee='S2',op='new-owner')
    payment('100.25','2026-10-21')
    result=data()
    assert result['totals']['ptp']==1 and result['totals']['kept']==0
    assert [r['employee'] for r in result['items']]==['S1']


def test_missing_snapshot_amount_is_not_repaired_from_current_card(world):
    call(amount='')
    with db.connect() as conn:
        conn.execute("UPDATE clients SET forecast_sum='100.25',promise_date='2026-10-22'");conn.commit()
    payment('100.25','2026-10-21')
    result=data()
    assert result['totals']['incomplete']==1 and result['totals']['kept']==0


def test_explicit_roster_soft_alias_is_used_once_without_name_guessing(world):
    import staff_roster
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='S1 SOFT' WHERE responsible_soft='S1'");conn.commit()
    call(employee='S1 SOFT')
    item=dict(employee='S1',office='Ташкент',direction='uz',products=['hard','soft'],product_names={'soft':'S1 SOFT'})
    preview=staff_roster.preview([item]);assert staff_roster.apply([item],preview['sha256'],by='synthetic')['ok']
    db.set_employee_city('S1 SOFT','Софт-офис')
    result=report.data({},params(),['S1','S1 SOFT'])
    assert result['totals']['ptp']==1 and result['items'][0]['employee']=='S1'
    assert result['items'][0]['author']=='S1 SOFT'
    assert result['items'][0]['office']=='Софт-офис'


def test_future_source_date_is_not_a_freshness_watermark_or_mutated(world):
    payment('100','2026-11-05');payment('20','2026-10-21',cid='003')
    assert db.last_payment_asof()=='2026-10-21'
    fresh=db.payment_data_freshness()
    assert fresh['future_dated_rows']==1 and not fresh['completeness_verified']
    assert db.get_client('001')['last_payment_date']=='2026-11-05'
    with db.connect() as conn:
        conn.execute("INSERT INTO import_receipts(proof_digest,source_kind,source_id,sequence,proof_json,status,registered_at,expires_at,consumed_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     ('pending','test','test',1,'{}','pending','2026-10-22','2026-10-23','2026-10-22T10:00:00+00:00'))
        conn.commit()
    assert not db.payment_data_freshness()['last_import_at']
    with db.connect() as conn:
        conn.execute("UPDATE import_receipts SET status='consumed'");conn.commit()
    assert db.payment_data_freshness()['last_import_at']=='2026-10-22T10:00:00+00:00'


@pytest.mark.parametrize('kwargs',[{'frm':'2026-10-20'},{'asof':'2026-10-18'},{'asof':'2026-10-23'},{'due':'bad'},{'frm':'19.10.2026'}])
def test_invalid_parameters_are_rejected(world,kwargs):
    with pytest.raises(ValueError):params(**kwargs)


def test_xlsx_has_typed_details_auditable_formulas_and_cached_totals(world):
    call();payment('100.25','2026-10-21')
    payload=report.xlsx({},params(),['S1'])
    values=load_workbook(BytesIO(payload),data_only=True)
    formulas=load_workbook(BytesIO(payload),data_only=False)
    assert values.sheetnames==['Выполнение обещаний','Обещания']
    detail=values['Обещания'];assert detail['D4'].value=='001'
    assert detail['F4'].value.date()==date(2026,10,19)
    assert detail['H4'].value==100.25 and detail['I4'].value==100.25 and detail['J4'].value==0
    sheet=values.worksheets[0]
    head=next(r[0].row for r in sheet if r[0].value=='Сотрудник')
    assert sheet.cell(head+1,4).value==1 and sheet.cell(head+1,5).value==1
    assert sheet.cell(head+1,10).value==1
    assert formulas.worksheets[0].cell(head+1,4).value.startswith('=COUNTIFS(')
    assert not any(cell.data_type=='e' for ws in values for row in ws for cell in row)


def test_xlsx_does_not_add_money_of_different_currencies(world,monkeypatch):
    call();call(employee='S2',rep='003',op='second')
    monkeypatch.setattr(report.soft_reporting.debt,'unit',lambda partners:'UZS' if 'Другой' in partners else '₽')
    with db.connect() as conn:
        conn.execute("UPDATE clients SET partner='Другой' WHERE contract_id='003'");conn.commit()
    workbook=load_workbook(BytesIO(report.xlsx({},params(),['S1','S2'])),data_only=True)
    total=next(row for row in workbook.worksheets[0] if row[0].value=='Итого')
    assert total[3].value==2 and total[10].value is None and total[11].value is None


def test_endpoint_reuses_manager_gate_and_separate_dates(world,monkeypatch):
    call();payment('100.25','2026-10-21')
    monkeypatch.setattr(web,'_manager_gate',lambda request:(100,{},['S1']))
    monkeypatch.setattr(web,'_range_team',lambda mgr,team,rng:team)
    request=make_mocked_request('GET','/api/manager/promise_report?from=2026-10-19&to=2026-10-19&asof=2026-10-22')
    response=asyncio.run(web._manager_promise_report(request))
    assert json.loads(response.text)['totals']['kept']==1
    request=make_mocked_request('GET','/api/manager/promise_report.xlsx?from=2026-10-19&to=2026-10-19&asof=2026-10-22&employee=S2')
    with pytest.raises(aio_web.HTTPNotFound):asyncio.run(web._manager_promise_report(request))
    request=make_mocked_request('GET','/api/manager/promise_report?from=2026-10-19&to=2026-10-19&asof=bad')
    with pytest.raises(aio_web.HTTPBadRequest):asyncio.run(web._manager_promise_report(request))


def test_unauthenticated_endpoint_is_rejected_before_reading_data(world):
    request=make_mocked_request('GET','/api/manager/promise_report?from=2026-10-19&to=2026-10-19&asof=2026-10-22')
    with pytest.raises((aio_web.HTTPUnauthorized,aio_web.HTTPForbidden)):
        asyncio.run(web._manager_promise_report(request))
