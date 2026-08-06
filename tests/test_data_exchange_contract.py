import hashlib
import json
from pathlib import Path

import pytest

import db
import exchange_contract as contract
import limited_test_reset as reset
import web
from pult import enrich
from registry_cycle import clearance


def _isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()


def _master_grid(contract_id: str, *, client: str) -> list[list]:
    values = {
        "contract_id": contract_id,
        "responsible": "Агент Master",
        "client": client,
    }
    return [
        [field.title for field in db.FIELDS],
        [field.source for field in db.FIELDS],
        [values.get(field.key) for field in db.FIELDS],
    ]


def _master_proof(
    grid: list[list],
    *,
    finished_at: str,
    marker: str,
) -> dict:
    file_sha = hashlib.sha256(
        f"master:{marker}".encode("utf-8")
    ).hexdigest()
    publication = {
        "run_id": f"published-master-{marker}",
        "source_id": contract.PUBLISHED_MASTER_SOURCE_ID,
        "publication_seq": contract.publication_sequence(finished_at),
        "plan_digest": hashlib.sha256(
            f"plan:{marker}".encode("utf-8")
        ).hexdigest(),
        "finished_at": finished_at,
        "publish_receipt_sha256": hashlib.sha256(
            f"publish:{marker}".encode("utf-8")
        ).hexdigest(),
        "stage_receipt_sha256": hashlib.sha256(
            f"stage:{marker}".encode("utf-8")
        ).hexdigest(),
        "journal_sha256": hashlib.sha256(
            f"journal:{marker}".encode("utf-8")
        ).hexdigest(),
        "baseline_sha256": hashlib.sha256(
            f"baseline:{marker}".encode("utf-8")
        ).hexdigest(),
        "master_sha256": file_sha,
    }
    return contract.build_published_master_source_proof(
        grid,
        file_sha256=file_sha,
        publication=publication,
    )


def test_every_exchange_consumer_uses_one_versioned_contract():
    binding = contract.contract_binding()

    assert binding == enrich.DATA_EXCHANGE_CONTRACT
    assert binding == reset.DATA_EXCHANGE_CONTRACT
    assert binding == clearance.DATA_EXCHANGE_CONTRACT
    assert enrich.EVIDENCE_SCHEMA == contract.ENRICHMENT_EVIDENCE_SCHEMA
    assert enrich.FILE_MAP_SCHEMA == contract.ENRICHMENT_FILE_MAP_SCHEMA
    assert reset.CLEARANCE_RECEIPT_SCHEMA == contract.CLEARANCE_RECEIPT_SCHEMA
    assert clearance.RECEIPT_SCHEMA == contract.CLEARANCE_RECEIPT_SCHEMA


def test_contract_binding_sha_is_exact_raw_contract_file_sha():
    raw_sha256 = hashlib.sha256(contract.CONTRACT_PATH.read_bytes()).hexdigest()

    assert contract.CONTRACT_SHA256 == raw_sha256
    assert contract.contract_binding() == {
        "schema": contract.CONTRACT_SCHEMA,
        "version": contract.CONTRACT_VERSION,
        "sha256": raw_sha256,
    }


def test_form_snapshot_enrichment_and_import_semantics_cannot_drift_apart():
    assert tuple(web.FORM_DROPDOWNS) == contract.FORM_DROPDOWN_KEYS
    assert tuple(web.FORM_TEXT) == contract.FORM_TEXT_KEYS
    assert web.FORM_PROTOCOL_VERSION == contract.FORM_PROTOCOL_VERSION
    assert web._meta("ru")["formProtocolVersion"] == (
        contract.FORM_PROTOCOL_VERSION
    )
    assert web._meta("ru")["dataExchangeContract"] == (
        contract.contract_binding()
    )
    assert db.BOT_SNAPSHOT_KEYS == contract.SNAPSHOT_KEYS
    assert db.BOT_VISIT_MARKER == contract.PROVENANCE_MARKER
    assert db.IMPORT_SOURCE_KINDS == contract.IMPORT_SOURCE_KINDS
    assert enrich.IMPORT_SOURCE_PUBLISHED_MASTER == (
        contract.IMPORT_SOURCE_PUBLISHED_MASTER
    )
    assert tuple(enrich.VISIT_KEYS) == contract.ENRICHMENT_SOURCE_KEYS
    historical = next(
        field for field in db.export_schema()
        if field["key"] == "historical_visit_comment"
    )
    assert historical["title"] == "Историчный комментарий выезда"
    assert historical["source"] == "сх"
    assert historical["title"] not in contract.WORKBOOK_ALLOWED_IGNORED_HEADERS
    assert historical["key"] not in contract.FORM_TEXT_KEYS
    assert historical["key"] not in contract.OFFICE_DIRECT_EDIT_KEYS
    assert historical["key"] not in contract.ENRICHMENT_SOURCE_KEYS
    assert "visit_comment" not in contract.OFFICE_DIRECT_EDIT_KEYS
    assert contract.OFFICE_ECHO_ONLY_KEYS == ("visit_comment",)

    rules = {
        source: (target, merge)
        for source, target, merge in contract.ENRICHMENT_WRITE_RULES
    }
    assert rules[contract.OFFICE_RESULT_SNAPSHOT_KEY] == (
        contract.OFFICE_RESULT_SOURCE_KEY,
        "replace",
    )
    assert rules["tips"] == ("extra_address", "unique_lines")
    assert rules["visit_comment"] == ("visit_comment", "preserve_text")
    assert not {
        target for target, _merge in rules.values()
    } & contract.ENRICHMENT_FORBIDDEN_TARGETS

    for source, target, _merge in contract.ENRICHMENT_WRITE_RULES:
        assert source == "tips" or source in db.BY_KEY
        assert target in db.BY_KEY

    miniapp = (
        Path(__file__).parents[1] / "hardbot" / "miniapp" / "index.html"
    ).read_text(encoding="utf-8")
    assert "const FORM_VERSION=2" not in miniapp
    assert "STATE.meta.formProtocolVersion" in miniapp


def test_export_schema_and_server_export_are_contract_bound():
    fields = db.export_schema()
    contract.verify_export_schema(fields)
    enrich.validate_server_export(
        {
            "data_exchange_contract": contract.contract_binding(),
            "fields": fields,
            "rows": [],
        }
    )
    with pytest.raises(ValueError, match="несовместимый контракт"):
        enrich.validate_server_export({"fields": fields, "rows": []})


def test_external_test_subset_requires_exact_contracts_and_owners():
    db.init_db()
    values = {
        "contract_id": "CONTRACT-SCOPE-1",
        "responsible": "Агент Scope",
        "client": "Клиент",
    }
    grid = [
        [field.title for field in db.FIELDS],
        [field.source for field in db.FIELDS],
        [values.get(field.key) for field in db.FIELDS],
    ]
    proof = contract.build_test_subset_source_proof(
        grid,
        approval_id="limited-test-scope-20260729",
        contracts=[
            {
                "contract_id": "CONTRACT-SCOPE-1",
                "responsible": "Агент Scope",
            }
        ],
    )
    db.register_import_authorization(
        proof,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    accepted = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        source_proof=proof,
        require_source_proof=True,
    )
    assert accepted["blocked"] is False
    assert accepted["source_proof_digest"]

    wrong_owner = contract.build_test_subset_source_proof(
        grid,
        approval_id="limited-test-scope-20260729",
        contracts=[
            {
                "contract_id": "CONTRACT-SCOPE-1",
                "responsible": "Другой агент",
            }
        ],
    )
    rejected = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        source_proof=wrong_owner,
        require_source_proof=True,
    )
    assert rejected["blocked"] is True
    assert rejected["scope_mismatch"]["owner_mismatch_contract_ids"] == [
        "CONTRACT-SCOPE-1"
    ]


def test_empty_test_subset_proof_can_represent_zero_row_employee_slice():
    grid = [
        [field.title for field in db.FIELDS],
        [field.source for field in db.FIELDS],
    ]
    proof = contract.build_test_subset_source_proof(
        grid,
        approval_id="zero-row-employee-slice",
        contracts=[],
    )

    verified = contract.validate_import_source_proof(
        proof,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        grid=grid,
    )
    assert verified["scope"] == {}


def test_external_published_master_requires_grid_and_publication_binding():
    db.init_db()
    values = {
        "contract_id": "CONTRACT-MASTER-1",
        "responsible": "Агент Master",
        "client": "Клиент",
    }
    grid = [
        [field.title for field in db.FIELDS],
        [field.source for field in db.FIELDS],
        [values.get(field.key) for field in db.FIELDS],
    ]
    publication = {
        "run_id": "published-master-test",
        "source_id": contract.PUBLISHED_MASTER_SOURCE_ID,
        "publication_seq": contract.publication_sequence(
            "2026-07-29T04:05:57"
        ),
        "plan_digest": "1" * 64,
        "finished_at": "2026-07-29T04:05:57",
        "publish_receipt_sha256": "2" * 64,
        "stage_receipt_sha256": "3" * 64,
        "journal_sha256": "4" * 64,
        "baseline_sha256": "6" * 64,
        "master_sha256": "5" * 64,
    }
    proof = contract.build_published_master_source_proof(
        grid,
        file_sha256="5" * 64,
        publication=publication,
    )
    db.register_import_authorization(
        proof,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    accepted = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=proof,
        require_source_proof=True,
    )
    assert accepted["blocked"] is False

    changed_grid = [*grid, [None] * len(db.FIELDS)]
    rejected = db.import_grid(
        changed_grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=proof,
        require_source_proof=True,
    )
    assert rejected["blocked"] is True
    assert "another workbook grid" in rejected["error"]


def test_unregistered_proof_now_self_authorizes_and_stays_idempotent(
    monkeypatch,
    tmp_path,
):
    """Незарегистрированный proof больше не отбивается — он регистрируется сам.

    Требование «разрешение выдаётся отдельным актом на сервере» снято владельцем
    03.08.2026. Важно, что при этом НЕ потерялось: расписка всё равно создаётся и
    гасится, поэтому повтор того же файла остаётся идемпотентным, а не пишет
    второй раз.
    """
    _isolated_db(monkeypatch, tmp_path)
    grid = _master_grid("UNREGISTERED-1", client="Не должен появиться")
    proof = _master_proof(
        grid,
        finished_at="2026-07-29T12:00:00+00:00",
        marker="unregistered",
    )

    report = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=proof,
        require_source_proof=True,
    )

    assert report["blocked"] is False
    assert report["inserted"] == 1
    assert db.get_client("UNREGISTERED-1") is not None

    replay = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=proof,
        require_source_proof=True,
    )
    assert replay.get("idempotent_replay") is True
    assert replay["inserted"] == 0


def test_self_approved_test_subset_is_recorded_as_self_issued(monkeypatch, tmp_path):
    """Клиент теперь может выдать разрешение себе сам — но это видно в аудите.

    Раньше такой proof отбивался. Владелец снял требование; цена размена в том,
    что admin-ключа достаточно для загрузки, и единственное, что остаётся, —
    честная запись о том, кто выдал разрешение.
    """
    _isolated_db(monkeypatch, tmp_path)
    values = {
        "contract_id": "FAKE-APPROVAL-1",
        "responsible": "Агент Fake",
        "client": "Не должен появиться",
    }
    grid = [
        [field.title for field in db.FIELDS],
        [field.source for field in db.FIELDS],
        [values.get(field.key) for field in db.FIELDS],
    ]
    forged = contract.build_test_subset_source_proof(
        grid,
        approval_id="self-approved-by-http-client",
        contracts=[
            {
                "contract_id": values["contract_id"],
                "responsible": values["responsible"],
            }
        ],
    )

    report = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        source_proof=forged,
        require_source_proof=True,
    )

    assert report["blocked"] is False
    assert db.get_client(values["contract_id"]) is not None
    with db.connect() as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT action FROM audit WHERE action LIKE 'import_%'")]
    assert "import_self_authorized" in actions


def test_master_receipts_are_one_shot_idempotent_and_monotonic(
    monkeypatch,
    tmp_path,
):
    _isolated_db(monkeypatch, tmp_path)
    first_grid = _master_grid("LEDGER-1", client="Первая публикация")
    first = _master_proof(
        first_grid,
        finished_at="2026-07-29T12:01:00+00:00",
        marker="ledger-1",
    )
    db.register_import_authorization(
        first,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    imported = db.import_grid(
        first_grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=first,
        require_source_proof=True,
    )
    assert imported["inserted"] == 1

    replay = db.import_grid(
        first_grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=first,
        require_source_proof=True,
    )
    assert replay["blocked"] is False
    assert replay["idempotent_replay"] is True
    assert replay["inserted"] == 0
    assert replay["updated"] == 0

    second_grid = _master_grid("LEDGER-1", client="Вторая публикация")
    second = _master_proof(
        second_grid,
        finished_at="2026-07-29T12:02:00+00:00",
        marker="ledger-2",
    )
    db.register_import_authorization(
        second,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    advanced = db.import_grid(
        second_grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=second,
        require_source_proof=True,
    )
    assert advanced["updated"] == 1
    assert db.get_client("LEDGER-1")["client"] == "Вторая публикация"

    stale = db.import_grid(
        first_grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=first,
        require_source_proof=True,
    )
    assert stale["blocked"] is True
    assert "older than" in stale["error"]
    assert db.get_client("LEDGER-1")["client"] == "Вторая публикация"


def test_same_source_sequence_cannot_be_registered_with_another_receipt(
    monkeypatch,
    tmp_path,
):
    _isolated_db(monkeypatch, tmp_path)
    grid = _master_grid("COLLISION-1", client="Клиент")
    first = _master_proof(
        grid,
        finished_at="2026-07-29T12:03:00+00:00",
        marker="collision-a",
    )
    second = _master_proof(
        grid,
        finished_at="2026-07-29T12:03:00+00:00",
        marker="collision-b",
    )
    db.register_import_authorization(
        first,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )

    with pytest.raises(
        contract.ExchangeContractError,
        match="source sequence",
    ):
        db.register_import_authorization(
            second,
            source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        )


def test_expired_authorization_is_fail_closed(monkeypatch, tmp_path):
    _isolated_db(monkeypatch, tmp_path)
    grid = _master_grid("EXPIRED-1", client="Клиент")
    proof = _master_proof(
        grid,
        finished_at="2026-07-29T12:04:00+00:00",
        marker="expired",
    )
    registered = db.register_import_authorization(
        proof,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    with db.connect() as connection:
        connection.execute(
            "UPDATE import_receipts SET expires_at=? WHERE proof_digest=?",
            ("2000-01-01T00:00:00+00:00", registered["proof_digest"]),
        )
        connection.commit()

    report = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=proof,
        require_source_proof=True,
    )
    assert report["blocked"] is True
    assert "expired" in report["error"]
    assert db.get_client("EXPIRED-1") is None


def test_clear_pending_preserves_consumed_receipt_and_source_head(
    monkeypatch,
    tmp_path,
):
    _isolated_db(monkeypatch, tmp_path)
    first_grid = _master_grid("CLEAR-1", client="Импортирован")
    first = _master_proof(
        first_grid,
        finished_at="2026-07-29T12:05:00+00:00",
        marker="clear-consumed",
    )
    first_registration = db.register_import_authorization(
        first,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    db.import_grid(
        first_grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=first,
        require_source_proof=True,
    )

    pending_grid = _master_grid("CLEAR-1", client="Не импортирован")
    pending = _master_proof(
        pending_grid,
        finished_at="2026-07-29T12:06:00+00:00",
        marker="clear-pending",
    )
    pending_registration = db.register_import_authorization(
        pending,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )

    assert db.clear_pending_import_authorizations() == 1
    assert (
        db.import_authorization_status(pending_registration["proof_digest"])
        is None
    )
    assert db.import_authorization_status(
        first_registration["proof_digest"]
    )["status"] == "consumed"
    replay = db.import_grid(
        first_grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        source_proof=first,
        require_source_proof=True,
    )
    assert replay["idempotent_replay"] is True


@pytest.mark.parametrize("bump_version", [False, True])
def test_unreviewed_contract_bytes_are_rejected(monkeypatch, tmp_path, bump_version):
    payload = json.loads(contract.CONTRACT_BYTES.decode("utf-8"))
    if bump_version:
        payload["version"] += 1
    else:
        payload["enrichment"]["writes"][1]["target"] = "call_status"
    candidate = tmp_path / "data_exchange_contract.json"
    candidate.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "CONTRACT_PATH", candidate)

    with pytest.raises(
        contract.ExchangeContractError,
        match="not approved",
    ):
        contract._load()
