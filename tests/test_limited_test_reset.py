import gzip
import hashlib
import json
import sqlite3
import tarfile
from pathlib import Path

import db
import exchange_contract as contract
import limited_test_reset as reset
import pytest


def _isolate(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = data_dir / "hardbot.db"
    photos = data_dir / "photos"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(reset, "PHOTO_DIR", photos)
    db.init_db()
    return database, photos


def _seed_all_tables(photos: Path) -> None:
    db.import_grid(
        [
            [
                db.BY_KEY["contract_id"].title,
                db.BY_KEY["client"].title,
                db.BY_KEY["responsible"].title,
            ],
            ["RESET-1", "Тестовый Должник", "Сотрудник"],
        ],
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO visits(contract_id, agent_id, created_at, payload, op_id) "
            "VALUES ('RESET-1', 10, '2026-07-29', '{}', 'visit-reset-1')"
        )
        conn.execute(
            "INSERT INTO users(telegram_id, name, created_at) "
            "VALUES (10, 'Тестер', '2026-07-29')"
        )
        conn.execute(
            "INSERT INTO agent_bind(telegram_id, employee, created_at) "
            "VALUES (10, 'Сотрудник', '2026-07-29')"
        )
        conn.execute(
            "INSERT INTO audit(ts, actor, action, target) "
            "VALUES ('2026-07-29', 10, 'view_card', 'RESET-1')"
        )
        conn.execute(
            "INSERT INTO markers(telegram_id, kind, client_rep, lat, lon, op_id) "
            "VALUES (10, 'case', 'RESET-1', 1, 2, 'marker-reset-1')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('period_from', 'x')"
        )
        conn.execute(
            "INSERT INTO managers(telegram_id, name, cities, employees, created_at) "
            "VALUES (20, 'Руководитель', '[]', '[]', '2026-07-29')"
        )
        conn.execute(
            "INSERT INTO app_links(code, telegram_id, created_at) "
            "VALUES ('link-reset', 10, '2026-07-29')"
        )
        conn.execute(
            "INSERT INTO app_tokens(token, telegram_id, created_at, expires_at) "
            "VALUES ('token-reset', 10, '2026-07-29', '2026-08-29')"
        )
        conn.execute(
            "INSERT INTO photo_geo(contract_id, file, telegram_id) "
            "VALUES ('RESET-1', 'RESET-1/photo.jpg', 10)"
        )
        conn.execute(
            "INSERT INTO report_chats(chat_id, title, enabled, removed, added_at) "
            "VALUES (-100, 'Тест', 1, 0, '2026-07-29')"
        )
        conn.execute(
            "INSERT INTO report_chat_employees(chat_id, employee) "
            "VALUES (-100, 'Сотрудник')"
        )
        conn.commit()
    photo_dir = photos / "RESET-1"
    photo_dir.mkdir(parents=True)
    (photo_dir / "photo.jpg").write_bytes(b"\xff\xd8\xfftest-photo")


def _install_clearance(archive: Path, manifest: dict) -> tuple[str, str]:
    completed = 0
    unsigned = {
        "schema": reset.CLEARANCE_RECEIPT_SCHEMA,
        "data_exchange_contract": reset.DATA_EXCHANGE_CONTRACT,
        "issued_at": "2026-07-29T00:00:00+00:00",
        "snapshot": {
            "directory": str(archive),
            "manifest_sha256": reset._sha256_file(
                archive / reset.SNAPSHOT_MANIFEST
            ),
            "confirm_token": manifest["confirm_token"],
            "database_state_digest": manifest["database_state_digest"],
            "safe_export_sha256": manifest["artifacts"][
                reset.SAFE_EXPORT_NAME
            ],
            "raw_export_sha256": manifest["artifacts"][
                reset.RAW_EXPORT_NAME
            ],
            "data_exchange_contract_sha256": reset.DATA_EXCHANGE_CONTRACT[
                "sha256"
            ],
        },
        "provenance": {
            "clients": manifest["counts"]["clients"],
            "safe_rows": manifest["safe_rows"],
            "raw_rows": manifest["raw_rows"],
            "completed_cases": completed,
            "completed_contract_ids_digest": hashlib.sha256(b"[]").hexdigest(),
            "invalid_completed_cases": 0,
            "orphan_completed_cases": 0,
            "unprovenanced_visit_rows": 0,
        },
        "coverage": {
            "expected_files": 0,
            "completed_cases": completed,
            "covered_cases": completed,
            "uncovered_cases": 0,
            "duplicate_coverage_cases": 0,
            "orphan_completed_cases": 0,
            "failed_files": 0,
            "missing_columns": 0,
            "post_plan_writes": 0,
        },
        "enrichment": {
            "evidence_path": "test",
            "evidence_sha256": "1" * 64,
            "files": [],
        },
        "registry": {
            "mode": "already_zero",
            "plan_digest": "4" * 64,
            "post_run_changes": 0,
            "post_run_digest": "5" * 64,
            "unfinished_publish_journals": 0,
            "active_baseline_sha256": "2" * 64,
            "active_baseline_plan_digest": "6" * 64,
            "post_run_master_sha256": "7" * 64,
            "artifacts": {
                role: {"path": f"test-{role}", "sha256": "3" * 64}
                for role in (
                    "registry_snapshot",
                    "plan",
                    "stage",
                    "validation",
                    "post_snapshot",
                    "post_plan",
                    "post_stage",
                    "post_validation",
                    "active_baseline",
                )
            },
        },
    }
    digest = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    receipt = {**unsigned, "clearance_digest": digest}
    path = archive / reset.CLEARANCE_RECEIPT_NAME
    reset._write_json(path, receipt)
    return digest, reset._sha256_file(path)


def _install_intent(
    archive: Path,
    manifest: dict,
    clearance_digest: str,
    clearance_sha: str,
) -> str:
    unsigned = {
        "schema": reset.ORCHESTRATION_INTENT_SCHEMA,
        "created_at_utc": "2026-07-29T00:00:00Z",
        "nonce": "8" * 64,
        "archive": str(archive),
        "confirm_token": manifest["confirm_token"],
        "snapshot_manifest_sha256": reset._sha256_file(
            archive / reset.SNAPSHOT_MANIFEST
        ),
        "clearance_digest": clearance_digest,
        "clearance_receipt_sha256": clearance_sha,
        "container_id": "container-test-id",
        "image_id": "sha256:" + "9" * 64,
        "app_stopped": True,
    }
    digest = reset._canonical_digest(unsigned)
    reset._write_json(
        archive / reset.ORCHESTRATION_INTENT,
        {**unsigned, "intent_digest": digest},
    )
    return digest


def _authority(archive: Path, manifest: dict) -> tuple[str, str, str]:
    clearance_digest, clearance_sha = _install_clearance(
        archive,
        manifest,
    )
    intent_digest = _install_intent(
        archive,
        manifest,
        clearance_digest,
        clearance_sha,
    )
    return clearance_digest, clearance_sha, intent_digest


def _install_apply_started(
    archive: Path,
    manifest: dict,
    clearance_digest: str,
    clearance_sha: str,
    intent_digest: str,
) -> None:
    intent = reset._load_verified_intent(
        archive,
        manifest,
        clearance_digest=clearance_digest,
        clearance_receipt_sha256=clearance_sha,
        claimed_intent_digest=intent_digest,
    )
    binding = reset._reset_binding(
        archive,
        manifest,
        clearance_digest=clearance_digest,
        clearance_receipt_sha256=clearance_sha,
        intent=intent,
    )
    reset._write_json(
        archive / reset.APPLY_STARTED_MANIFEST,
        reset._signed_receipt(
            {
                "schema": "hardbot.reset-apply-started.v2",
                "kind": "hardbot_limited_test_apply_started",
                "started_at": "2026-07-29T00:00:00+00:00",
                **binding,
            }
        ),
    )


def _apply(archive: Path, manifest: dict):
    clearance_digest, clearance_sha, intent_digest = _authority(
        archive,
        manifest,
    )
    return reset.apply_reset(
        archive,
        confirm_token=manifest["confirm_token"],
        clearance_digest=clearance_digest,
        clearance_receipt_sha256=clearance_sha,
        orchestration_intent_digest=intent_digest,
        app_stopped=True,
    )


def test_snapshot_and_reset_cover_all_tables_and_quarantine_photos(
    monkeypatch,
    tmp_path,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "limited-test-reset"

    manifest = reset.create_snapshot(archive)
    assert manifest["counts"]["clients"] == 1
    assert manifest["safe_rows"] == manifest["raw_rows"] == 1
    assert manifest["photos"]["files"] == 1

    backup = sqlite3.connect(
        f"file:{archive / reset.BACKUP_NAME}?mode=ro",
        uri=True,
    )
    try:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 1
    finally:
        backup.close()

    with gzip.open(archive / reset.SAFE_EXPORT_NAME, "rt", encoding="utf-8") as f:
        safe = json.load(f)
    assert safe["kind"] == "safe_enrichment"
    assert safe["rows"][0]["contract_id"] == "RESET-1"
    with tarfile.open(archive / reset.PHOTO_ARCHIVE_NAME, "r:gz") as packed:
        assert packed.getnames() == ["RESET-1/photo.jpg"]
        extracted = packed.extractfile("RESET-1/photo.jpg")
        assert extracted is not None
        assert extracted.read() == b"\xff\xd8\xfftest-photo"

    post = _apply(archive, manifest)
    assert not any(post["post_reset_counts"].values())
    assert list(photos.iterdir()) == []
    assert (
        archive / "photos-before-reset" / "RESET-1" / "photo.jpg"
    ).read_bytes() == b"\xff\xd8\xfftest-photo"
    assert (archive / reset.POST_RESET_MANIFEST).is_file()


def test_reset_invalidates_pending_but_preserves_consumed_import_ledger(
    monkeypatch,
    tmp_path,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    grid = [
        [
            db.BY_KEY["contract_id"].title,
            db.BY_KEY["client"].title,
            db.BY_KEY["responsible"].title,
        ],
        [
            db.BY_KEY["contract_id"].source,
            db.BY_KEY["client"].source,
            db.BY_KEY["responsible"].source,
        ],
        ["RESET-1", "Тестовый Должник", "Сотрудник"],
    ]
    consumed_proof = contract.build_test_subset_source_proof(
        grid,
        approval_id="reset-consumed",
        contracts=[
            {"contract_id": "RESET-1", "responsible": "Сотрудник"}
        ],
    )
    consumed = db.register_import_authorization(
        consumed_proof,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    assert db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        source_proof=consumed_proof,
        require_source_proof=True,
    )["blocked"] is False

    pending_proof = contract.build_test_subset_source_proof(
        grid,
        approval_id="reset-pending",
        contracts=[
            {"contract_id": "RESET-1", "responsible": "Сотрудник"}
        ],
    )
    pending = db.register_import_authorization(
        pending_proof,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    archive = database.parent / "backups" / "reset-import-ledger"
    manifest = reset.create_snapshot(archive)

    post = _apply(archive, manifest)

    assert not any(post["post_reset_counts"].values())
    assert post["import_authorizations"]["pending_invalidated"] == 1
    assert post["import_authorizations"]["pending"] == 0
    assert post["import_authorizations"]["consumed"] == 1
    assert post["import_authorizations"]["heads"] == 1
    assert db.import_authorization_status(
        consumed["proof_digest"]
    )["status"] == "consumed"
    assert db.import_authorization_status(pending["proof_digest"]) is None
    with db.connect() as connection:
        head = connection.execute(
            "SELECT source_id, proof_digest FROM import_source_heads"
        ).fetchone()
    assert head["source_id"] == "test-subset:reset-consumed"
    assert head["proof_digest"] == consumed["proof_digest"]


def test_reset_blocks_if_database_changed_after_snapshot(monkeypatch, tmp_path):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "changed-after-snapshot"
    manifest = reset.create_snapshot(archive)
    clearance_digest, clearance_sha, intent_digest = _authority(
        archive,
        manifest,
    )

    db.set_setting("changed_after_snapshot", "yes")
    with pytest.raises(reset.ResetBlocked, match="изменилась"):
        reset.apply_reset(
            archive,
            confirm_token=manifest["confirm_token"],
            clearance_digest=clearance_digest,
            clearance_receipt_sha256=clearance_sha,
            orchestration_intent_digest=intent_digest,
            app_stopped=True,
        )

    assert db.get_client("RESET-1") is not None
    assert (photos / "RESET-1" / "photo.jpg").is_file()


def test_reset_requires_exact_clearance_receipt(monkeypatch, tmp_path):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "missing-clearance"
    manifest = reset.create_snapshot(archive)

    with pytest.raises(reset.ResetBlocked, match="clearance receipt"):
        reset.apply_reset(
            archive,
            confirm_token=manifest["confirm_token"],
            clearance_digest="1" * 64,
            clearance_receipt_sha256="2" * 64,
            orchestration_intent_digest="3" * 64,
            app_stopped=True,
        )

    assert db.get_client("RESET-1") is not None
    assert (photos / "RESET-1" / "photo.jpg").is_file()


def test_running_snapshot_blocks_if_database_changes_mid_capture(
    monkeypatch,
    tmp_path,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "raced-snapshot"
    original = reset._photo_manifest
    calls = 0

    def mutate_during_snapshot(root_raw=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            db.set_setting("changed_during_snapshot", "yes")
        return original(root_raw)

    monkeypatch.setattr(reset, "_photo_manifest", mutate_during_snapshot)
    with pytest.raises(reset.ResetBlocked, match="изменилась во время snapshot"):
        reset.create_snapshot(archive)

    assert not archive.exists()
    assert db.get_client("RESET-1") is not None
    assert (photos / "RESET-1" / "photo.jpg").is_file()


def test_snapshot_blocks_unknown_table(monkeypatch, tmp_path):
    database, _photos = _isolate(monkeypatch, tmp_path)
    with db.connect() as conn:
        conn.execute("CREATE TABLE future_state(id INTEGER)")
        conn.commit()

    with pytest.raises(reset.ResetBlocked, match="unknown=.*future_state"):
        reset.create_snapshot(database.parent / "backups" / "unknown-schema")


@pytest.mark.parametrize("mutation", ["missing_artifact", "forged_token"])
def test_reset_revalidates_manifest_artifact_set_and_confirm_token(
    monkeypatch,
    tmp_path,
    mutation,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / f"tampered-{mutation}"
    manifest = reset.create_snapshot(archive)
    clearance_digest, clearance_sha, intent_digest = _authority(
        archive,
        manifest,
    )
    manifest_path = archive / reset.SNAPSHOT_MANIFEST
    tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "missing_artifact":
        tampered["artifacts"].pop(reset.RAW_EXPORT_NAME)
        expected = "набор артефактов"
    else:
        tampered["confirm_token"] = "0" * 64
        expected = "Confirm-token"
    reset._write_json(manifest_path, tampered)

    with pytest.raises(reset.ResetBlocked, match=expected):
        reset.apply_reset(
            archive,
            confirm_token=manifest["confirm_token"],
            clearance_digest=clearance_digest,
            clearance_receipt_sha256=clearance_sha,
            orchestration_intent_digest=intent_digest,
            app_stopped=True,
        )

    assert db.get_client("RESET-1") is not None
    assert (photos / "RESET-1" / "photo.jpg").is_file()


def test_recover_incomplete_reset_restores_database_and_photos(
    monkeypatch,
    tmp_path,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "interrupted-reset"
    manifest = reset.create_snapshot(archive)
    clearance_digest, clearance_sha, intent_digest = _authority(
        archive,
        manifest,
    )
    _install_apply_started(
        archive,
        manifest,
        clearance_digest,
        clearance_sha,
        intent_digest,
    )
    archived_photos = archive / "photos-before-reset"
    photos.replace(archived_photos)
    photos.mkdir()
    with db.connect() as conn:
        for table in reset.DELETE_ORDER:
            conn.execute(f'DELETE FROM "{table}"')
        conn.commit()
    conn.close()

    recovered = reset.recover_incomplete_reset(
        archive,
        confirm_token=manifest["confirm_token"],
        orchestration_intent_digest=intent_digest,
        app_stopped=True,
    )
    assert recovered["database_quick_check"] == "ok"
    assert recovered["counts"] == manifest["counts"]
    assert recovered["orchestration_intent_digest"] == intent_digest
    assert recovered["snapshot_manifest_sha256"] == reset._sha256_file(
        archive / reset.SNAPSHOT_MANIFEST
    )
    assert recovered["receipt_digest"] == reset._canonical_digest(
        {
            key: value
            for key, value in recovered.items()
            if key != "receipt_digest"
        }
    )
    assert (photos / "RESET-1" / "photo.jpg").is_file()
    assert (archive / reset.RECOVERY_MANIFEST).is_file()


def test_direct_apply_is_blocked_without_controller_intent(
    monkeypatch,
    tmp_path,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "no-controller-intent"
    manifest = reset.create_snapshot(archive)
    clearance_digest, clearance_sha = _install_clearance(archive, manifest)

    with pytest.raises(reset.ResetBlocked, match="orchestration intent"):
        reset.apply_reset(
            archive,
            confirm_token=manifest["confirm_token"],
            clearance_digest=clearance_digest,
            clearance_receipt_sha256=clearance_sha,
            orchestration_intent_digest="a" * 64,
            app_stopped=True,
        )

    assert db.get_client("RESET-1") is not None
    assert (photos / "RESET-1" / "photo.jpg").is_file()


def test_manifest_verifier_rejects_symlinked_child_artifact(
    monkeypatch,
    tmp_path,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "symlink-artifact"
    reset.create_snapshot(archive)
    artifact = archive / reset.RAW_EXPORT_NAME
    saved = archive / "raw-saved.json.gz"
    artifact.replace(saved)
    try:
        artifact.symlink_to(saved)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(reset.ResetBlocked, match="non-reparse"):
        reset._load_verified_manifest(archive)


def test_manifest_verifier_rejects_self_rehashed_logically_changed_backup(
    monkeypatch,
    tmp_path,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "logical-backup-tamper"
    manifest = reset.create_snapshot(archive)
    backup = archive / reset.BACKUP_NAME
    connection = sqlite3.connect(backup)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
            ("tampered-after-snapshot", "yes"),
        )
        connection.commit()
    finally:
        connection.close()
    manifest["artifacts"][reset.BACKUP_NAME] = reset._sha256_file(backup)
    token_source = {
        "artifacts": manifest["artifacts"],
        "counts": manifest["counts"],
        "database_state_digest": manifest["database_state_digest"],
        "photos_digest": manifest["photos"]["digest"],
        "photos_root_exists": manifest["photos"]["root_exists"],
    }
    manifest["confirm_token"] = hashlib.sha256(
        json.dumps(
            token_source,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    reset._write_json(archive / reset.SNAPSHOT_MANIFEST, manifest)

    with pytest.raises(reset.ResetBlocked, match="logical digest"):
        reset._load_verified_manifest(archive)


class _SyntheticPowerLoss(BaseException):
    pass


@pytest.mark.parametrize(
    "crash_point",
    [
        "after_apply_started",
        "after_photo_quarantine",
        "after_database_reset",
    ],
)
def test_crash_before_terminal_receipt_recovers_exact_pre_state(
    monkeypatch,
    tmp_path,
    crash_point,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / f"crash-{crash_point}"
    manifest = reset.create_snapshot(archive)
    clearance_digest, clearance_sha, intent_digest = _authority(
        archive,
        manifest,
    )

    def crash(name):
        if name == crash_point:
            raise _SyntheticPowerLoss(name)

    monkeypatch.setattr(reset, "_fault_point", crash)
    with pytest.raises(_SyntheticPowerLoss, match=crash_point):
        reset.apply_reset(
            archive,
            confirm_token=manifest["confirm_token"],
            clearance_digest=clearance_digest,
            clearance_receipt_sha256=clearance_sha,
            orchestration_intent_digest=intent_digest,
            app_stopped=True,
        )

    recovered = reset.recover_incomplete_reset(
        archive,
        confirm_token=manifest["confirm_token"],
        orchestration_intent_digest=intent_digest,
        app_stopped=True,
    )
    with db.connect() as conn:
        counts = reset._table_counts(conn)
        digest = reset._database_state_digest(conn)
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    conn.close()
    current_photos = reset._photo_manifest()
    assert recovered["kind"] == "hardbot_limited_test_recovery"
    assert counts == manifest["counts"]
    assert digest == manifest["database_state_digest"]
    assert quick == "ok"
    assert current_photos["digest"] == manifest["photos"]["digest"]
    assert current_photos["root_exists"] == manifest["photos"]["root_exists"]
    assert (photos / "RESET-1" / "photo.jpg").read_bytes() == b"\xff\xd8\xfftest-photo"
    assert not (archive / reset.POST_RESET_MANIFEST).exists()


def test_crash_after_durable_terminal_receipt_is_exact_post_state(
    monkeypatch,
    tmp_path,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "crash-after-post"
    manifest = reset.create_snapshot(archive)
    clearance_digest, clearance_sha, intent_digest = _authority(
        archive,
        manifest,
    )

    def crash(name):
        if name == "after_post_receipt":
            raise _SyntheticPowerLoss(name)

    monkeypatch.setattr(reset, "_fault_point", crash)
    with pytest.raises(_SyntheticPowerLoss, match="after_post_receipt"):
        reset.apply_reset(
            archive,
            confirm_token=manifest["confirm_token"],
            clearance_digest=clearance_digest,
            clearance_receipt_sha256=clearance_sha,
            orchestration_intent_digest=intent_digest,
            app_stopped=True,
        )

    with db.connect() as conn:
        counts = reset._table_counts(conn)
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    conn.close()
    assert not any(counts.values())
    assert quick == "ok"
    assert list(photos.iterdir()) == []
    assert (archive / reset.POST_RESET_MANIFEST).is_file()
    assert (
        archive / "photos-before-reset" / "RESET-1" / "photo.jpg"
    ).read_bytes() == b"\xff\xd8\xfftest-photo"
    with pytest.raises(reset.ResetBlocked, match="завершён"):
        reset.recover_incomplete_reset(
            archive,
            confirm_token=manifest["confirm_token"],
            orchestration_intent_digest=intent_digest,
            app_stopped=True,
        )


def test_post_receipt_write_ambiguity_never_rolls_back_behind_receipt(
    monkeypatch,
    tmp_path,
):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "post-write-ambiguity"
    manifest = reset.create_snapshot(archive)
    clearance_digest, clearance_sha, intent_digest = _authority(
        archive,
        manifest,
    )
    original_write = reset._write_json

    def ambiguous_write(path, payload):
        original_write(path, payload)
        if Path(path).name == reset.POST_RESET_MANIFEST:
            raise OSError("simulated directory fsync acknowledgement loss")

    monkeypatch.setattr(reset, "_write_json", ambiguous_write)
    with pytest.raises(OSError, match="acknowledgement loss"):
        reset.apply_reset(
            archive,
            confirm_token=manifest["confirm_token"],
            clearance_digest=clearance_digest,
            clearance_receipt_sha256=clearance_sha,
            orchestration_intent_digest=intent_digest,
            app_stopped=True,
        )

    post = json.loads(
        (archive / reset.POST_RESET_MANIFEST).read_text(encoding="utf-8")
    )
    assert post["schema"] == "hardbot.reset-post.v2"
    assert post["orchestration_intent_digest"] == intent_digest
    with db.connect() as connection:
        assert not any(reset._table_counts(connection).values())
    assert list(photos.iterdir()) == []
    assert not (archive / reset.RECOVERY_MANIFEST).exists()
