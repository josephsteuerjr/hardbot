from datetime import date, datetime

import pytest

import db
import edit_tracking as tracking
from fields import FIELDS


def seed(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'tracking.db')
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES('T-1','Test','Hard','Soft')")
        conn.commit()


def row():
    with db.connect() as conn:
        return conn.execute("SELECT * FROM clients WHERE contract_id='T-1'").fetchone()


@pytest.mark.parametrize('value', [date(2026, 10, 29), datetime(2026, 10, 29), '29.10.2026', '2026-10-29', 46324])
def test_real_excel_date(value):
    assert tracking.canonical_day(value) == '2026-10-29'


@pytest.mark.parametrize('value', [True, '31.02.2026', 'yesterday', -1, float('inf')])
def test_invalid_date(value):
    with pytest.raises(ValueError):
        tracking.canonical_day(value)


def test_contextual_headers():
    assert tracking.columns(['Номер КД / ID', tracking.CAPTION, 'Дата выезда', tracking.CAPTION, 'статус звонка софт']) == {1: tracking.EXCEL_KEYS[0], 3: tracking.EXCEL_KEYS[1]}
    with pytest.raises(ValueError):
        tracking.columns([tracking.CAPTION, 'Клиент'])
    with pytest.raises(ValueError):
        tracking.columns([tracking.CAPTION, 'Дата выезда', tracking.CAPTION, 'Дата выезда'])
    assert tracking.logical_headers([tracking.CAPTION, 'Дата выезда', tracking.CAPTION, 'статус звонка софт']) == ['Дата ручной правки выезда', 'Дата выезда', 'Дата ручной правки звонка', 'статус звонка софт']
    assert tracking.columns(['Дата выезда', 'статус звонка софт']) == {}


def test_operator_recognizes_both_dates_without_extra_column_mapping():
    from pult.core import header_map
    header_map.forget()
    result = header_map.analyze([['Номер КД / ID', tracking.CAPTION, 'Дата выезда', tracking.CAPTION, 'статус звонка софт']])
    assert {item['key'] for item in result['recognized']} >= set(tracking.EXCEL_KEYS)
    assert result['unknown'] == []


def test_call_edit_dedup_and_unchanged_result(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    kwargs = dict(contract_id='T-1', agent_id=1, employee='Soft', values={'soft_call_status': 'Нет контакта'}, payload='test', on_date='2026-10-29')
    db.save_call_result(**kwargs, op_id='one', saved_at='2026-10-29T08:00:00')
    assert row()['app_call_edited_at'] == '2026-10-29T08:00:00'
    db.save_call_result(**kwargs, op_id='one', saved_at='2026-10-29T09:00:00')
    db.save_call_result(**kwargs, op_id='two', saved_at='2026-10-29T10:00:00')
    assert row()['app_call_edited_at'] == '2026-10-29T08:00:00'
    assert row()['app_visit_edited_at'] is None
    assert row()['excel_call_edited_on'] is None


def test_visit_edit_and_late_event(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    kwargs = dict(contract_id='T-1', agent_id=1, employee='Hard', values={'visit_comment': 'new'}, payload='test', on_date='2026-10-29')
    db.save_visit_result(**kwargs, op_id='one', saved_at='2026-10-29T08:00:00')
    assert row()['app_visit_edited_at'] == '2026-10-29T08:00:00'
    db.save_visit_result(**{**kwargs, 'values': {'visit_comment': 'old'}}, op_id='two', saved_at='2026-10-29T07:00:00')
    assert row()['app_visit_edited_at'] == '2026-10-29T08:00:00'
    assert row()['app_call_edited_at'] is None


def test_accepted_draft_changes_stamp_only_the_result_block(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    db.update_person('T-1', {'visit_comment': 'accepted draft'}, result_edit_at='2026-10-29T08:00:00')
    assert row()['app_visit_edited_at'] == '2026-10-29T08:00:00'
    db.update_person('T-1', {'visit_comment': 'accepted draft'}, result_edit_at='2026-10-29T09:00:00')
    db.update_person('T-1', {'reminder_date': '2026-10-30', 'geo_lat': '40'}, result_edit_at='2026-10-29T09:00:00')
    assert row()['app_visit_edited_at'] == '2026-10-29T08:00:00'
    assert row()['app_call_edited_at'] is None
    db.update_person('T-1', {'soft_comment': 'accepted soft draft'}, by_soft=True, result_edit_at='2026-10-29T10:00:00')
    assert row()['app_call_edited_at'] == '2026-10-29T10:00:00'
    assert row()['excel_visit_edited_on'] is None


def test_payment_result_changes_call_block_without_inventing_a_call(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    kwargs = dict(paid_at='2026-10-29', amount=100, currency='USD', result_status='Оплачен')
    db.record_reported_payment('pay-one', 'T-1', 'Soft', 1, created_at='2026-10-29T10:00:00',
        expected_revision=db.payment_card_revision([row()]), **kwargs)
    assert row()['app_call_edited_at'] == '2026-10-29T10:00:00'
    assert row()['bot_call_at'] is None
    db.record_reported_payment('pay-two', 'T-1', 'Soft', 1, created_at='2026-10-29T11:00:00',
        expected_revision=db.payment_card_revision([row()]), **kwargs)
    assert row()['app_call_edited_at'] == '2026-10-29T10:00:00'


def test_late_legacy_result_does_not_invent_edit_time_from_fact_day(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    db.save_call_result('T-1', 1, 'Soft', {'soft_call_status': 'Нет контакта'}, 'legacy',
        op_id='legacy-call', on_date='2026-01-01')
    assert row()['bot_call_at'] == '2026-01-01T00:00:00'
    assert row()['app_call_edited_at'] is None
    db.save_visit_result('T-1', 1, 'Hard', {'visit_comment': 'legacy'}, 'legacy',
        op_id='legacy-visit', on_date='2026-01-01')
    assert row()['app_visit_edited_at'] is None


def test_import_manual_dates_preserves_app_dates(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    headers = [f.title for f in FIELDS]
    tags = [f.source for f in FIELDS]
    values = ['T-1' if f.key == 'contract_id' else 'Hard' if f.key == 'responsible' else None for f in FIELDS]
    for anchor, tag in [('Дата выезда', 'хард'), ('статус звонка софт', 'софт')]:
        index = headers.index(anchor)
        headers.insert(index, tracking.CAPTION)
        tags.insert(index, tag)
        values.insert(index, date(2026, 10, 29))
    grid = [[None] * len(headers), headers, tags, values]
    result = db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not result['blocked'], result
    assert row()['excel_visit_edited_on'] == '2026-10-29'
    assert row()['excel_call_edited_on'] == '2026-10-29'
    assert row()['app_visit_edited_at'] is None
    assert row()['app_call_edited_at'] is None
