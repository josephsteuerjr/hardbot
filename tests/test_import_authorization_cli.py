import json
import os
import sqlite3
from pathlib import Path

import pytest

import db
import exchange_contract as contract
import import_authorization as cli


def _test_subset_proof(*, approval_id: str = "cli-approval-1") -> dict:
    values = {
        "contract_id": "CLI-KD-1",
        "responsible": "CLI Агент",
        "client": "CLI Клиент",
    }
    grid = [
        [field.title for field in db.FIELDS],
        [field.source for field in db.FIELDS],
        [values.get(field.key) for field in db.FIELDS],
    ]
    return contract.build_test_subset_source_proof(
        grid,
        approval_id=approval_id,
        contracts=[
            {
                "contract_id": "CLI-KD-1",
                "responsible": "CLI Агент",
            }
        ],
    )


@pytest.fixture
def authorization_db(monkeypatch, tmp_path):
    database = tmp_path / "hardbot.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    return database


def _write_proof(path: Path, proof: dict) -> None:
    path.write_text(
        json.dumps(proof, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _stdout_json(capsys) -> dict:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def _stderr_json(capsys) -> dict:
    captured = capsys.readouterr()
    assert captured.out == ""
    return json.loads(captured.err)


def test_register_and_status_use_db_path_and_are_idempotent(
    authorization_db,
    tmp_path,
    capsys,
):
    proof_path = tmp_path / "proof.json"
    _write_proof(proof_path, _test_subset_proof())

    assert cli.main(
        [
            "register",
            "--source-kind",
            contract.IMPORT_SOURCE_TEST_SUBSET,
            "--proof",
            str(proof_path),
            "--ttl-hours",
            "12",
        ]
    ) == 0
    registered = _stdout_json(capsys)
    assert registered["ok"] is True
    assert registered["command"] == "register"
    assert registered["authorization"]["status"] == "pending"
    assert registered["authorization"]["idempotent"] is False
    digest = registered["authorization"]["proof_digest"]
    assert str(authorization_db) not in json.dumps(registered)

    assert cli.main(
        [
            "register",
            "--source-kind",
            contract.IMPORT_SOURCE_TEST_SUBSET,
            "--proof",
            str(proof_path),
        ]
    ) == 0
    repeated = _stdout_json(capsys)
    assert repeated["authorization"]["proof_digest"] == digest
    assert repeated["authorization"]["idempotent"] is True

    assert cli.main(
        [
            "status",
            "--source-kind",
            contract.IMPORT_SOURCE_TEST_SUBSET,
            "--proof-digest",
            digest,
        ]
    ) == 0
    status = _stdout_json(capsys)
    assert status["found"] is True
    assert status["authorization"]["proof_digest"] == digest
    assert "proof_json" not in status["authorization"]

    with sqlite3.connect(authorization_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM import_receipts"
        ).fetchone()[0] == 1


def test_source_kind_is_exact_and_status_checks_the_kind(
    authorization_db,
    tmp_path,
    capsys,
):
    proof_path = tmp_path / "proof.json"
    _write_proof(proof_path, _test_subset_proof())

    assert cli.main(
        [
            "register",
            "--source-kind",
            "TEST_SUBSET",
            "--proof",
            str(proof_path),
        ]
    ) == 2
    rejected = _stderr_json(capsys)
    assert rejected["error"]["code"] == "source_kind_invalid"

    assert cli.main(
        [
            "register",
            "--source-kind",
            contract.IMPORT_SOURCE_TEST_SUBSET,
            "--proof",
            str(proof_path),
        ]
    ) == 0
    digest = _stdout_json(capsys)["authorization"]["proof_digest"]

    assert cli.main(
        [
            "status",
            "--source-kind",
            contract.IMPORT_SOURCE_PUBLISHED_MASTER,
            "--proof-digest",
            digest,
        ]
    ) == 2
    mismatch = _stderr_json(capsys)
    assert mismatch["error"]["code"] == "source_kind_mismatch"


def test_clear_pending_requires_explicit_flag_and_preserves_consumed_audit(
    authorization_db,
    tmp_path,
    capsys,
):
    pending_path = tmp_path / "pending.json"
    consumed_path = tmp_path / "consumed.json"
    _write_proof(pending_path, _test_subset_proof(approval_id="pending"))
    _write_proof(consumed_path, _test_subset_proof(approval_id="consumed"))

    pending = db.register_import_authorization(
        json.loads(pending_path.read_text(encoding="utf-8")),
        source_kind=contract.IMPORT_SOURCE_TEST_SUBSET,
    )
    consumed = db.register_import_authorization(
        json.loads(consumed_path.read_text(encoding="utf-8")),
        source_kind=contract.IMPORT_SOURCE_TEST_SUBSET,
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE import_receipts SET status='consumed', "
            "consumed_at='2026-07-29T12:00:00+00:00' "
            "WHERE proof_digest=?",
            (consumed["proof_digest"],),
        )
        conn.commit()

    assert cli.main(["clear-pending"]) == 2
    refused = _stderr_json(capsys)
    assert refused["error"]["code"] == "confirmation_required"
    assert db.import_authorization_status(pending["proof_digest"]) is not None

    assert cli.main(
        ["clear-pending", "--confirm-clear-pending"]
    ) == 0
    cleared = _stdout_json(capsys)
    assert cleared == {
        "cleared": 1,
        "command": "clear-pending",
        "ok": True,
    }
    assert db.import_authorization_status(pending["proof_digest"]) is None
    assert (
        db.import_authorization_status(consumed["proof_digest"])["status"]
        == "consumed"
    )


def test_proof_must_be_regular_non_symlink_and_no_db_write_occurs(
    authorization_db,
    tmp_path,
    capsys,
):
    target = tmp_path / "real-proof.json"
    link = tmp_path / "proof-link.json"
    _write_proof(target, _test_subset_proof())
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")

    assert cli.main(
        [
            "register",
            "--source-kind",
            contract.IMPORT_SOURCE_TEST_SUBSET,
            "--proof",
            str(link),
        ]
    ) == 2
    refused = _stderr_json(capsys)
    assert refused["error"]["code"] == "proof_file_not_regular"
    with sqlite3.connect(authorization_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM import_receipts"
        ).fetchone()[0] == 0


def test_invalid_proof_json_never_echoes_its_contents(
    authorization_db,
    tmp_path,
    capsys,
):
    secret = "DO_NOT_ECHO_THIS_SECRET"
    proof_path = tmp_path / "bad.json"
    proof_path.write_text(
        '{"password":"' + secret + '","password":"duplicate"}',
        encoding="utf-8",
    )

    assert cli.main(
        [
            "register",
            "--source-kind",
            contract.IMPORT_SOURCE_TEST_SUBSET,
            "--proof",
            str(proof_path),
        ]
    ) == 2
    raw = capsys.readouterr()
    assert raw.out == ""
    assert secret not in raw.err
    assert json.loads(raw.err)["error"]["code"] == "proof_json_invalid"


def test_status_not_found_is_json_success(authorization_db, capsys):
    assert cli.main(
        [
            "status",
            "--source-kind",
            contract.IMPORT_SOURCE_TEST_SUBSET,
            "--proof-digest",
            "0" * 64,
        ]
    ) == 0
    status = _stdout_json(capsys)
    assert status == {
        "authorization": None,
        "command": "status",
        "found": False,
        "ok": True,
    }
