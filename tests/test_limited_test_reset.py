import gzip
import json
import sqlite3
import tarfile
from pathlib import Path

import db
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
            [db.BY_KEY["contract_id"].title, db.BY_KEY["client"].title],
            ["RESET-1", "Тестовый Должник"],
        ]
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

    post = reset.apply_reset(
        archive,
        confirm_token=manifest["confirm_token"],
        app_stopped=True,
    )
    assert not any(post["post_reset_counts"].values())
    assert list(photos.iterdir()) == []
    assert (
        archive / "photos-before-reset" / "RESET-1" / "photo.jpg"
    ).read_bytes() == b"\xff\xd8\xfftest-photo"
    assert (archive / reset.POST_RESET_MANIFEST).is_file()


def test_reset_blocks_if_database_changed_after_snapshot(monkeypatch, tmp_path):
    database, photos = _isolate(monkeypatch, tmp_path)
    _seed_all_tables(photos)
    archive = database.parent / "backups" / "changed-after-snapshot"
    manifest = reset.create_snapshot(archive)

    db.set_setting("changed_after_snapshot", "yes")
    with pytest.raises(reset.ResetBlocked, match="изменилась"):
        reset.apply_reset(
            archive,
            confirm_token=manifest["confirm_token"],
            app_stopped=True,
        )

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

    reset._write_json(
        archive / reset.APPLY_STARTED_MANIFEST,
        {
            "kind": "hardbot_limited_test_apply_started",
            "confirm_token": manifest["confirm_token"],
        },
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
        app_stopped=True,
    )
    assert recovered["database_quick_check"] == "ok"
    assert recovered["counts"] == manifest["counts"]
    assert (photos / "RESET-1" / "photo.jpg").is_file()
    assert (archive / reset.RECOVERY_MANIFEST).is_file()
