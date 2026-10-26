"""Звонок -> снимок -> личная книга: регрессии обратной связи 07.10."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from aiohttp.test_utils import make_mocked_request
import db
import web
from pult import enrich
from test_review_soft_integrity import world, call, grid


def test_fact_and_callback_reach_card_and_excel_independently(world, monkeypatch):
    day = db.workday.today().isoformat()
    callback = (db.workday.today() + timedelta(days=2)).isoformat()
    call(monkeypatch, 'new-fields', 'Обещает оплатить sms', values={'soft_next_call_date': callback})
    row = db.get_client('A')
    assert row['soft_comm_date'] == day
    assert row['soft_next_call_date'] == callback
    assert row['overall_result'] == 'Обещает оплатить'
    data = web._person_full('A', 'ru', by_soft=True)
    assert data['soft']['comm_date'] == day
    assert data['soft']['next_call_date'] == callback
    exported = next(r for r in db.enrichment_rows() if r['contract_id'] == 'A')
    assert exported['call_status'] == 'Обещает оплатить'
    assert not exported['_bot_completed']
    schema = db.export_schema()
    keys = {'contract_id', 'overall_result', 'soft_call_status', 'soft_comm_date', 'soft_comment', 'forecast_sum', 'promise_date', 'forecast', 'extra_address'}
    columns = [f for f in schema if f['key'] in keys]
    book = [[f['title'] for f in columns], ['софт']*len(columns),
            ['A' if f['key'] == 'contract_id' else 'Не обработан' if f['key'] == 'overall_result' else None for f in columns]]
    plan = enrich.build_plan(book, schema, [{**exported, '_profile': 'soft'}], ledger={}, since=day, until=day)
    writes = {columns[col]['key']: val for _, col, val, _ in plan['writes']}
    assert writes['overall_result'] == 'Обещает оплатить'
    assert writes['soft_comm_date'] == db.workday.today()
    assert 'soft_next_call_date' not in writes
    full = next(r for r in db.registry_rows() if r['contract_id'] == 'A')
    assert full['overall_result'] == 'Обещает оплатить'
    assert full['soft_comm_date'] == day


def test_legacy_snapshot_is_corrected_without_rewriting_history(world):
    day = db.workday.today().isoformat()
    callback = (db.workday.today() + timedelta(days=2)).isoformat()
    raw = json.dumps({'soft_call_status': 'Оплачен sms', 'soft_comm_date': callback,
                      '_event_day': day, '_field_day': {'soft_comm_date': day}}, ensure_ascii=False)
    db.update_fields('A', {'soft_call_status': 'Оплачен sms', 'soft_comm_date': callback,
                          'bot_call_data': raw, 'bot_call_at': day+'T10:00:00'})
    result = db.bot_call_snapshot(db.get_client('A'))
    assert result['soft_comm_date'] == day
    assert result['soft_next_call_date'] == callback
    assert result['overall_result'] == 'Оплачен'
    assert db.get_client('A')['bot_call_data'] == raw
    assert next(r for r in db.registry_rows() if r['contract_id'] == 'A')['soft_comm_date'] == day


def test_old_queue_preserves_callback_and_office_timezone(world, monkeypatch):
    moment = datetime.combine(db.workday.today()-timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc).replace(hour=21, minute=30)
    event_day = (moment.date()+timedelta(days=1)).isoformat()
    callback = (moment.date()+timedelta(days=4)).isoformat()
    call(monkeypatch, 'old-queue', 'В работе', occurred_at=moment.timestamp()*1000,
         values={'soft_comm_date': callback})
    row = db.get_client('A')
    assert row['soft_comm_date'] == event_day
    assert row['soft_next_call_date'] == callback
    assert db.call_field_day(row, 'overall_result') == event_day


def test_stale_master_cannot_reset_processed_result(world, monkeypatch):
    call(monkeypatch, 'worked', 'Обещает оплатить')
    world[0].update(overall_result='Не обработан')
    db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert db.get_client('A')['overall_result'] == 'Обещает оплатить'
    assert db.bot_call_snapshot(db.get_client('A'))['overall_result'] == 'Обещает оплатить'
    # Даже после доставки общий дефолт старого master не становится офисной правкой.
    db.record_enrichment_deliveries([{'contract_id': 'A', 'key': 'soft_call_status',
        'value': 'Обещает оплатить', 'visit_date': db.workday.today().isoformat(), 'responsible': 'S1'}])
    world[0]['soft_call_status'] = 'Обещает оплатить'
    db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert db.get_client('A')['overall_result'] == 'Обещает оплатить'


def test_newer_visit_keeps_its_overall_result(world, monkeypatch):
    day = db.workday.today().isoformat()
    db.record_bot_visit('A', {'call_status': 'Оплачен'}, day, event_at=day+'T18:00:00')
    moment = datetime.fromisoformat(day+'T10:00:00+00:00')
    call(monkeypatch, 'late-result', 'Нет контакта', occurred_at=moment.timestamp()*1000)
    row = db.get_client('A')
    assert db.common_result(row)['overall_result'] == 'Оплачен'
    assert next(r for r in db.enrichment_rows() if r['contract_id'] == 'A')['call_status'] == 'Оплачен'
    assert next(r for r in db.registry_rows() if r['contract_id'] == 'A')['overall_result'] == 'Оплачен'


def test_payments_are_per_contract_and_other_soft_portfolio_is_excluded(world):
    for cid, amount in [('A', '1000'), ('B', '2000'), ('C', '9999')]:
        db.update_fields(cid, {'last_payment_sum': amount, 'last_payment_date': '2026-10-06'})
    data = web._person_full('A', 'ru', by_soft=True)
    assert {p['contract_id']:p['amount'] for p in data['payments']} == {'A':'1000', 'B':'2000'}
    assert all(p['date'] == '2026-10-06' for p in data['payments'])


def test_callback_clear_removes_from_queue_plan_without_clearing_fact(world, monkeypatch):
    call(monkeypatch, 'planned', values={'soft_next_call_date': '2026-10-09'})
    call(monkeypatch, 'clear', values={'soft_next_call_date': ''}, cleared_keys=['soft_next_call_date'])
    data = web._person_full('A', 'ru', by_soft=True)
    assert data['soft']['next_call_date'] == ''
    assert data['soft']['comm_date'] == db.workday.today().isoformat()


def test_invalid_callback_does_not_lose_call(world, monkeypatch):
    response = call(monkeypatch, 'bad-date', values={'soft_next_call_date': 'invalid'})
    assert response['ok']
    assert response['rejected']['soft_next_call_date'] == 'format'
    assert db.get_client('A')['soft_comm_date'] == db.workday.today().isoformat()
