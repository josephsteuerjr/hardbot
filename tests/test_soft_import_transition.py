"""Import a real ownership transition without restoring the former Hard slice."""

import pytest

import db
import exchange_contract as contract
from fields import FIELDS
from pult.core import registry_import as ri


def grid(rows):
    return [[field.title for field in FIELDS] + ['ответственный Сотрудник СОФТ'],
            [field.source for field in FIELDS] + ['софт'],
            *[[row.get(field.key) for field in FIELDS] + [row.get('responsible_soft')]
              for row in rows]]


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'transition.db')
    db.init_db()
    rows = [{'contract_id': cid, 'client': 'Synthetic client',
             'responsible': hard, 'responsible_soft': soft}
            for cid, hard, soft in [('A', 'H1', 'S1'), ('B', 'H2', 'S1'), ('C', 'H1', 'S2')]]
    assert not db.import_grid(grid(rows), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)['blocked']
    return rows


def proof_for(rows, approval='soft-transition'):
    workbook = grid(rows)
    proof = contract.build_test_subset_source_proof(
        workbook, approval_id=approval, contracts=ri.scope_from_grid(workbook))
    return workbook, proof


def test_soft_only_row_has_exact_empty_hard_owner_in_proof(registry):
    rows = [{**registry[0], 'responsible': ''}]
    workbook, proof = proof_for(rows)
    verified = contract.validate_import_source_proof(
        proof, source_kind=db.IMPORT_SOURCE_TEST_SUBSET, grid=workbook)
    assert verified['scope'] == {'A': ''}
    result = db.import_grid(workbook, source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
                            source_proof=proof, import_profile='soft', preview_only=True)
    assert not result['blocked'], result
    assert db.get_client('A')['responsible'] == 'H1'

    changed = grid([{**rows[0], 'responsible_soft': 'S9'}])
    with pytest.raises(contract.ExchangeContractError, match='another workbook grid'):
        contract.validate_import_source_proof(proof, source_kind=db.IMPORT_SOURCE_TEST_SUBSET, grid=changed)
    wrong_owner = contract.build_test_subset_source_proof(
        workbook, approval_id='wrong-hard-owner', contracts=[{'contract_id': 'A', 'responsible': 'H1'}])
    rejected = db.import_grid(workbook, source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
                              source_proof=wrong_owner, import_profile='soft', preview_only=True)
    assert rejected['blocked']
    assert rejected['scope_mismatch']['owner_mismatch_contract_ids'] == ['A']


@pytest.mark.parametrize('owner', [None, ' ', ' H1', 0, False])
def test_empty_owner_permission_does_not_accept_invalid_proof_values(owner):
    workbook, proof = proof_for([{'contract_id': 'A', 'responsible': 'H1', 'responsible_soft': 'S1'}])
    proof['contracts'][0]['responsible'] = owner
    with pytest.raises(contract.ExchangeContractError):
        contract.validate_import_source_proof(proof, source_kind=db.IMPORT_SOURCE_TEST_SUBSET, grid=workbook)


def test_full_both_slice_removes_present_blank_hard_owner_and_proves_effect(registry):
    workbook, proof = proof_for([{**registry[0], 'responsible': ''}])
    options = dict(source_kind=db.IMPORT_SOURCE_TEST_SUBSET, source_proof=proof,
                   import_profile='both', full_registry=True, authoritative_employees=['H1', 'S1'])
    preview = db.import_grid(workbook, **options, preview_only=True)
    assert not preview['blocked'], preview
    assert preview['auto_unassigned'] == 3  # A Hard blank, B Soft absent, C Hard absent
    assert preview['auto_unassigned_by_employee'] == {'H1': 2, 'Софт: S1': 1}
    assert preview['no_responsible'] == 1
    assert db.get_client('A')['responsible'] == 'H1'
    result = db.import_grid(workbook, **options, require_source_proof=True, expected_auto_unassigned=3,
                            expected_auto_unassigned_digest=preview['auto_unassigned_digest'])
    assert not result['blocked'], result
    assert result['auto_unassigned_digest'] == preview['auto_unassigned_digest']
    for cid, hard, soft in [('A', '', 'S1'), ('B', 'H2', ''), ('C', '', 'S2')]:
        row = db.get_client(cid)
        assert row['responsible'] == hard and row['responsible_soft'] == soft
    row = db.get_client('A')
    assert row['unassigned_from'] == 'H1'
    assert row['unassigned_reason'] == db.UNASSIGNED_REASON_ABSENT_FROM_REGISTRY
    with db.connect() as connection:
        events = connection.execute("SELECT * FROM assignment_events WHERE contract_id='A' AND profile='hard'").fetchall()
    assert any(event['prev_employee'] == 'H1' and event['employee'] == '' for event in events)


@pytest.mark.parametrize('profile,soft_owner', [('hard', 'S1'), ('soft', ''), ('both', '')])
def test_partial_blank_hard_owner_is_preserved_and_soft_clear_is_explicit(registry, profile, soft_owner):
    workbook, proof = proof_for([{**registry[0], 'responsible': '', 'responsible_soft': ''}])
    partial = db.import_grid(workbook, source_kind=db.IMPORT_SOURCE_TEST_SUBSET, source_proof=proof,
                             import_profile=profile)
    assert not partial['blocked'], partial
    assert db.get_client('A')['responsible'] == 'H1'
    assert db.get_client('A')['responsible_soft'] == soft_owner


def test_full_soft_blank_owner_clears_only_soft_and_reports_effect(registry):
    workbook, proof = proof_for([{**registry[0], 'responsible': '', 'responsible_soft': ''}])
    soft_only = db.import_grid(workbook, source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
                              import_profile='soft', full_registry=True, authoritative_employees=['S1'])
    assert not soft_only['blocked'], soft_only
    assert soft_only['auto_unassigned'] == 2
    assert db.get_client('A')['responsible'] == 'H1'
    assert db.get_client('A')['responsible_soft'] == ''


def test_present_blank_owner_does_not_clear_unselected_employee(registry):
    workbook, proof = proof_for([{**registry[0], 'responsible': ''}])
    result = db.import_grid(workbook, source_kind=db.IMPORT_SOURCE_TEST_SUBSET, source_proof=proof,
                            import_profile='both', full_registry=True, authoritative_employees=['S1'])
    assert not result['blocked'], result
    assert result['auto_unassigned'] == 1
    assert db.get_client('A')['responsible'] == 'H1'
    assert db.get_client('C')['responsible'] == 'H1'


def test_repeated_blank_keeps_auto_unassignment_until_owner_returns(registry):
    workbook, _ = proof_for([{**registry[0], 'responsible': ''}])
    options = dict(source_kind=db.IMPORT_SOURCE_TEST_SUBSET, import_profile='both',
                   full_registry=True, authoritative_employees=['H1', 'S1'])
    assert not db.import_grid(workbook, **options)['blocked']
    repeated = db.import_grid(workbook, **options)
    assert not repeated['blocked'] and repeated['auto_unassigned'] == 0
    assert db.get_client('A')['unassigned_from'] == 'H1'
    restored = db.import_grid(grid([registry[0]]), **options)
    assert not restored['blocked'], restored
    assert db.get_client('A')['responsible'] == 'H1'
    assert db.get_client('A')['unassigned_from'] == ''
