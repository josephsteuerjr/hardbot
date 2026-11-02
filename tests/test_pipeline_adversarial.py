"""Adversarial round trips. Synthetic clients and isolated SQLite only."""
import json
from datetime import datetime, time, timedelta, timezone

import pytest
import db
from test_review_soft_integrity import world, call, grid
from test_enrichment_hotfix import _deliver_current


def apply_preview(rows, **options):
    args = dict(source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER, **options)
    preview = db.import_grid(grid(rows), preview_only=True, **args)
    result = db.import_grid(grid(rows),
        expected_soft_office_effect_digest=preview['soft_office_effect_digest'],
        expected_comment_effect_digest=preview['comment_effect_digest'], **args)
    return preview, result


@pytest.mark.parametrize('manual', [None, 'same', 'future'])
def test_partial_same_day_echo_does_not_rollback_call(world, monkeypatch, manual):
    call(monkeypatch, 'fresh', 'Обещает оплатить', values={
        'soft_comment': 'Old text\nFresh promise', 'forecast_sum': '12000'})
    current = _deliver_current()
    world[0].update(soft_call_status='Обещает оплатить',
        soft_comm_date=current['_call_day'], soft_comment='Old text', forecast_sum='300')
    if manual:
        world[0]['excel_call_edited_on'] = (db.workday.today() + timedelta(days=manual == 'future')).isoformat()
    _, result = apply_preview(world, allow_soft_office_edits=True)
    assert not result['blocked']
    assert db.bot_call_snapshot(db.get_client('A'))['soft_comment'] == 'Old text\nFresh promise'
    assert db.bot_call_snapshot(db.get_client('B'))['forecast_sum'] == '12000'
    assert result['soft_office_applied'] == {}


def test_single_foreign_creditor_row_cannot_hijack_existing_contract(world):
    previous = dict(db.get_client('A'))
    world[0].update(partner='Another creditor', client='Another person', responsible='Other employee')
    for preview in (True, False):
        result = db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
                                preview_only=preview)
        assert result['blocked'] and result['contract_identity_conflicts']
        assert dict(db.get_client('A')) == previous


@pytest.mark.parametrize('delivered', ['old_value', 'old_event', 'exact'])
def test_clear_requires_delivery_of_this_result_not_any_old_receipt(world, delivered):
    day = db.workday.today().isoformat()
    db.record_bot_visit('A', {'call_status': 'В работе', 'visit_comment': 'Current visit'}, day)
    db.record_enrichment_deliveries([dict(contract_id='A', key='visit_comment',
        value='Old visit' if delivered == 'old_value' else 'Current visit',
        visit_date='2020-01-01' if delivered == 'old_event' else day,
        responsible='H1', file='U:/Synthetic/H1.xlsb')])
    world[0].update(visit_comment=None, visit_date=None)
    _, result = apply_preview(world, replace_current_visit_comments=True)
    assert not result['blocked']
    if delivered == 'exact':
        assert db.get_client('A')['visit_comment'] == ''
    else:
        assert db.get_client('A')['visit_comment'] == 'Current visit'
        assert result['current_comments_protected'] == 2


def test_proven_later_office_day_can_correct_comment(world, monkeypatch):
    yesterday = db.workday.today() - timedelta(days=1)
    call(monkeypatch, 'yesterday', values={'soft_comment': 'Original'},
        occurred_at=datetime.combine(yesterday, time(8), tzinfo=timezone.utc).timestamp()*1000)
    current = _deliver_current()
    world[0].update(soft_call_status=current['soft_call_status'], soft_comm_date=current['_call_day'],
        soft_comment='Office correction', excel_call_edited_on=db.workday.today().isoformat())
    _, result = apply_preview(world, allow_soft_office_edits=True)
    assert not result['blocked']
    assert db.bot_call_snapshot(db.get_client('B'))['soft_comment'] == 'Office correction'
    assert json.loads(db.get_client('A')['bot_call_data'])['soft_comment'] == 'Original'


def test_conflicting_sibling_edits_rollback_whole_import(world, monkeypatch):
    yesterday = db.workday.today() - timedelta(days=1)
    call(monkeypatch, 'yesterday', values={'soft_comment': 'Original'},
        occurred_at=datetime.combine(yesterday, time(8), tzinfo=timezone.utc).timestamp()*1000)
    for cid in ('A', 'B'):
        current = _deliver_current(cid)
        world[ord(cid)-ord('A')].update(soft_call_status=current['soft_call_status'],
            soft_comm_date=current['_call_day'], soft_comment='Correction '+cid,
            excel_call_edited_on=db.workday.today().isoformat())
    for preview in (True, False):
        result = db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
            allow_soft_office_edits=True, expected_soft_office_effect_digest='0'*64, preview_only=preview)
        assert result['blocked'] and result['office_conflicts']
        assert db.bot_call_snapshot(db.get_client('A'))['soft_comment'] == 'Original'
        assert db.bot_call_snapshot(db.get_client('B'))['soft_comment'] == 'Original'


def test_changed_operation_payload_without_revision_is_not_acknowledged(world, monkeypatch):
    from aiohttp import web
    call(monkeypatch, 'one-operation', values={'soft_comment': 'First'})
    before = [dict(r) for r in db.calls_for_person('A')]
    with pytest.raises(web.HTTPConflict):
        call(monkeypatch, 'one-operation', values={'soft_comment': 'Different'})
    assert [dict(r) for r in db.calls_for_person('A')] == before
    assert db.bot_call_snapshot(db.get_client('A'))['soft_comment'] == 'First'


def test_identical_comment_status_and_day_do_not_authorize_stale_amount(world, monkeypatch):
    call(monkeypatch, 'latest-amount', 'Обещает оплатить', values={
        'soft_comment': 'Same comment', 'forecast_sum': '12000'})
    current = _deliver_current()
    world[0].update(soft_call_status=current['soft_call_status'],
        soft_comm_date=current['_call_day'], soft_comment='Same comment', forecast_sum='9000')
    _, result = apply_preview(world, allow_soft_office_edits=True)
    assert not result['blocked']
    assert db.bot_call_snapshot(db.get_client('A'))['forecast_sum'] == '12000'


def test_current_month_visit_date_does_not_prove_comment_delivery(world):
    day = db.workday.today().isoformat()
    db.record_bot_visit('A', {'call_status': 'В работе', 'visit_comment': 'Undelivered fresh visit'}, day)
    world[0].update(visit_date=day, visit_comment=None)
    _, result = apply_preview(world, replace_current_visit_comments=True)
    assert not result['blocked']
    assert db.get_client('A')['visit_comment'] == 'Undelivered fresh visit'
