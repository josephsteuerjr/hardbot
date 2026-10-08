"""Приёмка требований встречи 08.10 на отдельной синтетической базе."""
import asyncio
import json
from datetime import timedelta
from io import BytesIO

import pytest
from aiohttp.test_utils import make_mocked_request
from openpyxl import load_workbook

import db
import digest_data as dd
import enrich
import soft_reporting as sr
import web

DAY = "2026-10-06"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'meeting.db')
    db.init_db()
    with db.connect() as conn:
        for cid, client, soft in [('A', 'Клиент один', 'S1'), ('B', 'Клиент один', 'S1'), ('C', 'Клиент другой', 'S2')]:
            conn.execute('INSERT INTO clients(contract_id,client,birth_date,responsible,responsible_soft,partner,debt_today) VALUES(?,?,?,?,?,?,?)',
                         (cid, client, '1990-01-01', 'H1', soft, 'Тест', '1000'))
        conn.commit()
    db.reset_role_caches()
    yield
    db.reset_role_caches()


def save(monkeypatch, values, op='one', occurred=None):
    monkeypatch.setattr(web, '_auth', lambda req: (100, 'S1'))
    body = dict(rep='A', kind='call', finish=True, values=values,
                op_id=op, form_version=web.FORM_PROTOCOL_VERSION, input_keys=list(values),
                cleared_keys=[k for k, v in values.items() if v == ''])
    if occurred is not None: body['occurred_at'] = occurred
    req = make_mocked_request('POST', '/api/save')
    async def data(): return body
    req.json = data
    return json.loads(asyncio.run(web._save(req)).text)


def call(day=DAY, status='Обещает оплатить', op='one', rep='A', emp='S1', **values):
    return db.save_call_result(rep, 100, emp, {'soft_call_status': status, **values}, 'Статус: '+status+';',
        op_id=op, on_date=day, saved_at=day+'T10:00:00')


def stats(frm=DAY, to='2026-10-08', emp='S1'):
    return dd._soft_employee_stats(emp, {'from': frm, 'to': to, 'days': 3})


def payment(amount, day, cid='A'):
    with db.connect() as conn:
        conn.execute('UPDATE clients SET last_payment_date=?,last_payment_sum=? WHERE contract_id=?', (day, str(amount), cid))
        db._remember_registry_payments(conn)
        conn.commit()


def test_meeting_and_callback_use_shared_registry_date(world, monkeypatch):
    planned = (db.workday.today()+timedelta(days=7)).isoformat()
    out = save(monkeypatch, {'soft_call_status':'Встреча в офисе', 'soft_next_call_date':planned})
    assert out['ok'] and out['person']['soft']['next_call_date'] == planned
    row = db.get_client('A')
    assert row['meeting_date'] == planned and row['soft_comm_date'] != planned
    assert row['overall_result'] == 'Встреча в офисе'
    assert next(r for r in db.registry_rows() if r['contract_id']=='A')['meeting_date'] == planned
    cleared = save(monkeypatch, {'soft_call_status':'В работе', 'soft_next_call_date':''}, 'clear')
    assert cleared['person']['soft']['next_call_date'] == ''
    assert db.get_client('A')['meeting_date'] == ''


def test_extra_number_accumulates_and_enriches_existing_column(world, monkeypatch):
    values = {'soft_call_status':'Нет контакта', 'soft_extra_phone':'+000 123456789 — контакт'}
    save(monkeypatch, values)
    save(monkeypatch, values)
    save(monkeypatch, {**values,'soft_extra_phone':'+000 987654321 — контакт'}, 'two')
    text = web._person_full('A','ru',by_soft=True)['soft']['extra_phone']
    assert text.count('123456789') == 1 and '987654321' in text
    exported = next(r for r in db.enrichment_rows() if r['contract_id']=='A')
    schema = db.export_schema()
    keys = {'contract_id', 'overall_result', 'soft_call_status','soft_comm_date','soft_comment', 'forecast_sum','promise_date','forecast','extra_address','meeting_date'}
    columns = [f for f in schema if f['key'] in keys]
    grid = [[f['title'] for f in columns],['сх']*len(columns),['A' if f['key']=='contract_id' else None for f in columns]]
    plan = enrich.build_plan(grid,schema,[{**exported,'_profile':'soft'}],ledger={})
    extra_column = next(i for i,f in enumerate(columns) if f['key']=='extra_address')
    assert any(w[1]==extra_column and '123456789' in str(w[2]) for w in plan['writes'])
    assert '123456789' in next(r for r in db.registry_rows() if r['contract_id']=='A')['extra_address']
    assert '000123456789' in web._person_brief(db.persons_for_soft('S1')[0])['tels']


@pytest.mark.parametrize('office_limit', ['0', '5'])
def test_soft_promise_is_fixed_three_days_even_if_office_setting_differs(world, monkeypatch, office_limit):
    db.set_setting('promise_ahead_days', office_limit)
    out = save(monkeypatch, {'soft_call_status':'Обещает оплатить', 'promise_date':(db.workday.today()+timedelta(days=4)).isoformat()})
    assert out['ok'] and out['rejected']['promise_date'] == 'too_far:3'
    assert len(db.soft_calls_for_employee('S1')) == 1


def test_old_and_new_meeting_headers_have_same_meaning(world):
    schema = db.export_schema()
    title = next(f['title'] for f in schema if f['key']=='meeting_date')
    assert title == 'Дата встречи/Дата перезвона'
    for header in (title, 'Дата встречи'):
        columns = [f for f in schema]
        grid = [[header if f['key']=='meeting_date' else f['title'] for f in columns]]
        assert enrich.find_header(grid,schema)[1]['meeting_date'] == next(i for i,f in enumerate(columns) if f['key']=='meeting_date')


def test_rpc_exactly_matches_template_including_sms_and_third_party(world):
    for i, status in enumerate(sr.STATUSES): call(status=status, op=str(i))
    call(status='Оплачен sms', op='sms')
    data = stats()['operation']
    assert data['rpc'] == 8 and data['ptp'] == 1 and data['kp'] == 0
    assert sum(data['statuses'].values()) == 15 and data['other'] == 0
    assert data['statuses']['Передана инф 3 лицу'] == 1


def test_kp_requires_whole_promise_by_deadline_and_preserves_import_history(world):
    call(promise_date='2026-10-08', forecast_sum='100.25')
    payment('40.10','2026-10-06')
    op = stats()['operation']
    assert op['kp']==0 and op['partial']==1
    payment('60.15','2026-10-07')
    op = stats()['operation']
    assert op['kp']==1 and op['partial']==0
    assert op['kp_money'][0]['sum'] == 100.25
    payment('60.15','2026-10-07')
    assert len(db.registry_payments_for(['A']))==2
    assert stats()['operation']['kp']==1


def test_late_and_manual_payments_do_not_fulfil_promise(world):
    call(promise_date='2026-10-07', forecast_sum='100')
    db.record_reported_payment('manual','A','S1',100,paid_at='2026-10-07',amount=100,currency='₽',created_at='2026-10-07T10:00:00')
    payment('100','2026-10-08')
    assert stats()['operation']['kp']==0


def test_repeat_promises_get_one_payment_only_once(world):
    call(promise_date='2026-10-08',forecast_sum='100',op='first')
    call(day='2026-10-07',promise_date='2026-10-08',forecast_sum='100',op='second')
    payment('100','2026-10-08')
    op = stats()['operation']
    assert op['ptp']==2 and op['kp']==1
    assert [p['state'] for p in op['promises']].count('kept')==1


def test_corrected_zero_payment_revokes_kp_without_duplicating_history(world):
    call(promise_date='2026-10-08',forecast_sum='100')
    payment('100','2026-10-08')
    assert stats()['kp']==1
    payment('0','2026-10-08')
    assert stats()['kp']==0
    assert len(db.registry_payments_for(['A']))==1


def test_import_preview_does_not_create_payment_history(world):
    grid=[['Номер КД / ID','ответственный Сотрудник','Дата последнего платежа','Сумма последнего платежа'],
          ['A','H1','2026-10-07','100.25']]
    db.import_grid(grid,source_kind=db.IMPORT_SOURCE_TEST_SUBSET,preview_only=True)
    assert db.registry_payments_for(['A'])==[]
    assert not db.import_grid(grid,source_kind=db.IMPORT_SOURCE_TEST_SUBSET).get('blocked')
    assert db.registry_payments_for(['A'])[0]['amount']==100.25


def test_money_of_mixed_currency_promises_stays_separate(world,monkeypatch):
    monkeypatch.setattr(sr.debt,'unit',lambda partners:'UZS' if 'Другой' in partners else '₽')
    with db.connect() as conn:
        conn.execute("UPDATE clients SET partner='Другой',responsible_soft='S1' WHERE contract_id='C'")
        conn.commit()
    call(promise_date='2026-10-08',forecast_sum='100',op='rub')
    call(rep='C',promise_date='2026-10-08',forecast_sum='100',op='uzs')
    money=stats()['operation']['ptp_money']
    assert {p['unit'] for p in money}=={'UZS','₽'}
    output=dd.stats_xlsx({},dd.stats_range('custom',frm=DAY,to='2026-10-08'),employees=['S1'])
    ws=load_workbook(BytesIO(output),data_only=True)['Софт · операции']
    assert isinstance(ws['T4'].value,str) and ws['U4'].value is None


def test_transfer_cannot_credit_same_payment_to_two_employees(world):
    call(promise_date='2026-10-08',forecast_sum='100')
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='S2' WHERE contract_id IN ('A','B')")
        conn.commit()
    call(day='2026-10-07',emp='S2',promise_date='2026-10-08',forecast_sum='100',op='new-owner')
    payment('100','2026-10-08')
    assert stats(emp='S1')['operation']['kp']==0
    assert stats(emp='S2')['operation']['kp']==1


def test_all_time_includes_activity_older_than_custom_limit(world):
    call(day='2024-01-01',status='Нет контакта')
    rng = dd.stats_range('all', today=db._as_date('2026-10-08'))
    assert rng['from']=='2024-01-01' and rng['days']>dd.STATS_MAX_DAYS
    assert rng['shift']==0 and rng['next'] is None


def test_report_layout_formulas_and_cached_values_follow_template(world):
    call(promise_date='2026-10-08',forecast_sum='100.25')
    payment('100.25','2026-10-08')
    rng = dd.stats_range('custom',frm=DAY,to='2026-10-08')
    output = dd.stats_xlsx({},rng,employees=['S1'])
    formulas = load_workbook(BytesIO(output),data_only=False)['Софт · операции']
    assert formulas['N2'].value=='Встреча в офисе'
    assert formulas['E4'].value=='=SUM(F4:S4)+AD4'
    assert formulas['Y4'].value=='=F4+I4+N4+P4+Q4+R4+S4'
    assert formulas['AA4'].value=='=IFERROR(S4/Y4,"")'
    values = load_workbook(BytesIO(output),data_only=True)['Софт · операции']
    assert [values[c].value for c in ('E4','S4','T4','V4','W4','Y4')]==[1,1,100.25,1,100.25,1]
    assert values['AB4'].value==1


def test_daily_and_all_time_reports_use_their_own_payment_cutoff(world):
    call(promise_date='2026-10-08',forecast_sum='100')
    payment('100','2026-10-08')
    daily=load_workbook(BytesIO(dd.daily_report_xlsx({},DAY,employees=['S1'])),data_only=True)
    assert daily['Софт · операции']['S4'].value==1
    assert daily['Софт · операции']['V4'].value==0
    cumulative=load_workbook(BytesIO(dd.stats_xlsx({},dd.stats_range('all',today=db._as_date('2026-10-08')),employees=['S1'])),data_only=True)
    assert cumulative['Софт · операции']['V4'].value==1
