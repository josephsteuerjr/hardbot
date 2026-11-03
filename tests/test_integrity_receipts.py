"""Operation identity and delivery acknowledgements survive retries independently of text."""
import asyncio
import json

import pytest
from aiohttp import web as aiohttp_web
from aiohttp.test_utils import make_mocked_request
import db
import web as webmod


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'isolated.db')
    db.init_db()
    with db.connect() as conn:
        for cid in ('IR-A', 'IR-B'):
            conn.execute('INSERT INTO clients(contract_id,client,partner,responsible,responsible_soft,birth_date) '
                         'VALUES (?,?,?,?,?,?)', (cid, 'Synthetic Person', 'Synthetic Creditor', 'QA Hard', 'QA Soft', '1990-01-01'))
        conn.commit()


def visit(op='visit-operation', **change):
    args = dict(op_id=op, on_date=db.workday.today().isoformat(), saved_at='2026-11-03T04:00:00',
                lat=40.5, lon=70.5)
    args.update(change)
    return db.save_visit_result('IR-A', 101, 'QA Hard',
                                {'visit_comment': 'Identical text', 'tips': 'Original'}, 'Same display payload', **args)


def test_visit_exact_retry_preserves_one_immutable_event_and_original_scope():
    assert visit() is True
    assert visit() is False
    with db.connect() as conn:
        rows = conn.execute('SELECT * FROM visits').fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]['values_json'])['tips'] == 'Original'
    assert set(json.loads(rows[0]['contracts_json'])) == {'IR-A', 'IR-B'}


@pytest.mark.parametrize('change', [{'lat': 41.0}, {'saved_at': '2026-11-03T05:00:00'}, {'on_date': '2026-11-02'}])
def test_same_visit_id_with_changed_request_is_rejected(change):
    visit()
    before = dict(db.get_client('IR-A'))
    with pytest.raises(ValueError, match='другого результата'):
        visit(**change)
    assert dict(db.get_client('IR-A')) == before


def test_field_absent_from_display_payload_is_still_part_of_visit_identity():
    visit()
    with pytest.raises(ValueError, match='другого результата'):
        db.save_visit_result('IR-A', 101, 'QA Hard', {'visit_comment': 'Identical text', 'tips': 'Changed'},
                             'Same display payload', op_id='visit-operation',
                             on_date=db.workday.today().isoformat(), saved_at='2026-11-03T04:00:00', lat=40.5, lon=70.5)


def test_legacy_visit_without_request_evidence_is_not_falsely_acknowledged():
    db.log_visit('IR-A', 101, 'Same display payload', op_id='visit-operation')
    with pytest.raises(ValueError, match='полный состав старой операции неизвестен'):
        visit()
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM visits').fetchone()[0] == 1


def test_different_operation_ids_with_same_call_text_are_two_facts():
    for op in ('call-one', 'call-two'):
        outcome = db.save_call_result('IR-A', 102, 'QA Soft', {'soft_comment': 'Same result'}, 'Same',
                                      op_id=op, on_date=db.workday.today().isoformat())
        assert outcome['dedup'] is False
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 2


def entry(value='Same text', day='2026-11-02'):
    return dict(contract_id='IR-A', key='visit_comment', value=value, visit_date=day,
                responsible='QA Hard', file='C:/Synthetic/Hard.xlsx')


def test_replayed_receipt_does_not_replace_a_later_delivery():
    first = [entry()]
    assert db.record_enrichment_deliveries(first, 'receipt-one') == 1
    assert db.record_enrichment_deliveries([entry('Later value', '2026-11-03')], 'receipt-two') == 1
    before = db.enrichment_book_ledger()
    assert db.record_enrichment_deliveries(first, 'receipt-one') == 1
    assert db.enrichment_book_ledger() == before
    assert db.enrichment_receipt('receipt-one')['recorded'] == 1


def test_receipt_id_cannot_acknowledge_changed_payload():
    db.record_enrichment_deliveries([entry()], 'immutable')
    before = db.enrichment_book_ledger()
    with pytest.raises(ValueError, match='другого состава'):
        db.record_enrichment_deliveries([entry(day='2026-11-03')], 'immutable')
    assert db.enrichment_book_ledger() == before


def test_identical_text_from_two_deliveries_keeps_two_receipts():
    db.record_enrichment_deliveries([entry()], 'older')
    db.record_enrichment_deliveries([entry(day='2026-11-03')], 'newer')
    book = db.enrichment_book_ledger()['c:/synthetic/hard.xlsx']['IR-A']['visit_comment']
    assert book['receipt_digest'] == 'newer' and book['visit_date'] == '2026-11-03'
    assert db.enrichment_receipt('older')['request_hash'] != db.enrichment_receipt('newer')['request_hash']


def test_receipt_status_http_uses_admin_auth_and_returns_exact_identity(monkeypatch):
    db.record_enrichment_deliveries([entry()], 'status-test')
    checked = []
    monkeypatch.setattr(webmod, '_admin', lambda request: checked.append(request.path))
    request = make_mocked_request('GET', '/admin/enrichment/ledger.json?books=1&receipt_digest=status-test')
    result = asyncio.run(webmod._admin_enrichment_ledger(request))
    body = json.loads(result.body)
    assert checked == ['/admin/enrichment/ledger.json']
    assert body['receipt']['receipt_digest'] == 'status-test' and body['receipt']['recorded'] == 1
    def deny(request):
        raise aiohttp_web.HTTPForbidden()
    monkeypatch.setattr(webmod, '_admin', deny)
    with pytest.raises(aiohttp_web.HTTPForbidden):
        asyncio.run(webmod._admin_enrichment_ledger(request))


def test_receipt_survives_database_reinitialization():
    db.record_enrichment_deliveries([entry()], 'restart')
    before = db.enrichment_receipt('restart')
    db.init_db()
    assert db.enrichment_receipt('restart') == before
