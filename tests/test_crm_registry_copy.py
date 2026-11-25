import json
from pathlib import Path
import pytest
import db
from crm import registry_import, storage, schema
from crm.contract import CRMError, canonical
from crm.access import Principal


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.delenv('HARD_CRM_SHARED', raising=False)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'registry.db')
    db.init_db()
    with storage.connect() as conn:
        schema.initialize(conn)
    rows = [
        {'sheet': 'Registry', 'row': 3, 'values': {'contract_id': 'SAME-ID', 'partner': 'A',
         'client': 'Same name', 'debt_total': 0, 'soft_comment': '', 'birth_date': '2000-01-02'},
         'extra_columns': [{'column': 12, 'title': 'Unknown', 'value': 'Retained'}]},
        {'sheet': 'Registry', 'row': 4, 'values': {'contract_id': 'SAME-ID', 'partner': 'B',
         'client': 'Same name', 'debt_total': '1.234', 'soft_comment': None}, 'extra_columns': []},
    ]
    path = tmp_path / 'rows.jsonl'
    def write(values):
        path.write_text(''.join(canonical(row) + '\n' for row in values), encoding='utf-8')
        return dict(workbook_sha256='a' * 64, rows_sha256=registry_import.file_sha(path), sheet='Registry', row_count=len(values))
    return path, rows, write


def test_registry_copy_preserves_rows_unknown_columns_zero_and_null(registry):
    path, rows, write = registry
    metadata = write(rows)
    result = registry_import.load(path, metadata)
    assert result['contracts'] == 2 and result['people'] == 2 and result['created_work_events'] == 0
    assert registry_import.load(path, metadata) == result
    with storage.connect() as conn:
        original = [json.loads(r[0]) for r in conn.execute('SELECT record_json FROM crm_registry_rows ORDER BY source_row')]
        assert original == rows
        contracts = list(conn.execute('SELECT * FROM crm_contracts ORDER BY creditor_id'))
        assert len({r['person_uuid'] for r in contracts}) == 2
        assert json.loads(contracts[0]['fields_json'])['debt_total'] == '0.00'
        assert json.loads(contracts[0]['fields_json'])['soft_comment'] == ''
        assert json.loads(contracts[1]['fields_json'])['soft_comment'] is None
        assert json.loads(contracts[1]['fields_json'])['debt_total'] == '1.234'
        assert all(r['currency'] is None for r in contracts)
        assert conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM crm_outbox').fetchone()[0] == 0


def test_registry_duplicate_pair_rolls_back_everything(registry):
    path, rows, write = registry
    rows[1]['values']['partner'] = 'A'
    with pytest.raises(CRMError) as exc:
        registry_import.load(path, write(rows))
    assert exc.value.code == 'duplicate_contract'
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_contracts').fetchone()[0] == 0


def test_registry_manifest_mismatch_and_second_source_are_rejected(registry):
    path, rows, write = registry
    metadata = write(rows)
    with pytest.raises(CRMError): registry_import.load(path, {**metadata, 'rows_sha256': 'b' * 64})
    registry_import.load(path, metadata)
    rows[0]['values']['soft_comment'] = 'Different source'
    with pytest.raises(CRMError) as exc:
        registry_import.load(path, write(rows))
    assert exc.value.code == 'import_not_empty'


def test_registry_count_mismatch_rolls_back_the_transaction(registry):
    path, rows, write = registry
    metadata = write(rows)
    with pytest.raises(CRMError): registry_import.load(path, {**metadata, 'row_count': 3})
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_contracts').fetchone()[0] == 0


def test_gzip_transport_has_identical_digest_and_exact_retained_rows(registry):
    import gzip
    path, rows, write = registry
    metadata = write(rows)
    compressed = path.with_suffix('.jsonl.gz')
    compressed.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    assert registry_import.file_sha(compressed) == metadata['rows_sha256']
    assert registry_import.load(compressed, metadata)['contracts'] == 2
    with storage.connect() as conn:
        assert [json.loads(r[0]) for r in conn.execute('SELECT record_json FROM crm_registry_rows ORDER BY source_row')] == rows


def test_unresolved_numeric_values_export_as_exact_text_and_keep_original_type(registry):
    path, rows, write = registry
    rows[0]['values']['debt_total'] = 123.456789
    result = registry_import.load(path, write(rows))
    assert result['untyped_fields'] >= 1
    with storage.connect() as conn:
        row = conn.execute("SELECT c.fields_json,r.record_json FROM crm_contracts c JOIN crm_registry_rows r USING(contract_uuid) WHERE c.creditor_id='registry-partner:A'").fetchone()
        assert json.loads(row[0])['debt_total'] == '123.456789'
        assert json.loads(row[1])['values']['debt_total'] == 123.456789
