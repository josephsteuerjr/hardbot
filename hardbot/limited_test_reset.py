"""Контролируемая подготовка пустой БД для ограниченного теста.

Это намеренно отдельный операторский CLI, а не HTTP endpoint и не расширение
``db.wipe_registry()``. Reset разрешён только после:

1. остановки основного контейнера приложения;
2. создания полного SQLite backup, safe/raw экспортов и манифеста фото;
3. независимой проверки этих артефактов;
4. повторного предъявления одноразового confirm-token из snapshot manifest.

Фото не удаляются: каталог атомарно переносится внутрь reset-архива. При ошибке
БД и каталог фото восстанавливаются из snapshot до возврата ненулевого кода.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import db
from photostore import PHOTO_DIR as DEFAULT_PHOTO_DIR


EXPECTED_TABLES = {
    "clients",
    "visits",
    "users",
    "agent_bind",
    "audit",
    "markers",
    "settings",
    "managers",
    "app_links",
    "app_tokens",
    "photo_geo",
    "report_chats",
    "report_chat_employees",
}

# Сначала зависимые/операционные сущности, затем registry и identity/config.
DELETE_ORDER = (
    "report_chat_employees",
    "report_chats",
    "photo_geo",
    "app_tokens",
    "app_links",
    "managers",
    "markers",
    "audit",
    "agent_bind",
    "users",
    "visits",
    "clients",
    "settings",
)

SNAPSHOT_MANIFEST = "snapshot_manifest.json"
POST_RESET_MANIFEST = "post_reset_manifest.json"
APPLY_STARTED_MANIFEST = "apply_started_manifest.json"
RECOVERY_MANIFEST = "recovery_manifest.json"
BACKUP_NAME = "hardbot-pre-reset.sqlite3"
SAFE_EXPORT_NAME = "safe-enrichment.json.gz"
RAW_EXPORT_NAME = "raw-diagnostic.json.gz"
SCHEMA_NAME = "export-schema.json"
PHOTO_MANIFEST_NAME = "photos-manifest.json.gz"
PHOTO_ARCHIVE_NAME = "photos-before-reset.tar.gz"
EXPECTED_ARTIFACTS = frozenset(
    {
        BACKUP_NAME,
        SAFE_EXPORT_NAME,
        RAW_EXPORT_NAME,
        SCHEMA_NAME,
        PHOTO_MANIFEST_NAME,
        PHOTO_ARCHIVE_NAME,
    }
)

# Точка подменяется только в изолированных тестах.
PHOTO_DIR = DEFAULT_PHOTO_DIR


class ResetBlocked(RuntimeError):
    """Fail-closed отказ без изменения данных."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _private_mode(temporary)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _write_json_gz(path: Path, payload: Any) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                mtime=0,
            ) as zipped:
                zipped.write(encoded)
            raw.flush()
            os.fsync(raw.fileno())
        _private_mode(temporary)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    if os.name != "posix" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_mode(path: Path) -> None:
    try:
        path.chmod(0o600 if path.is_file() else 0o700)
    except OSError:
        # На Windows ACL не отображаются в POSIX mode; тесты всё равно изолированы.
        pass


def _validated_archive_dir(raw: str | os.PathLike[str], *, create: bool) -> Path:
    data_dir = Path(db.DB_PATH).resolve().parent
    backup_root = (data_dir / "backups").resolve()
    archive = Path(raw).resolve()
    try:
        archive.relative_to(backup_root)
    except ValueError as exc:
        raise ResetBlocked(
            f"Reset-архив должен находиться внутри {backup_root}"
        ) from exc
    if archive == backup_root:
        raise ResetBlocked("Корневой каталог backups нельзя использовать как reset-архив")
    if create:
        archive.mkdir(parents=True, exist_ok=False)
        _private_mode(archive)
    elif not archive.is_dir():
        raise ResetBlocked(f"Reset-архив не найден: {archive}")
    return archive


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _require_known_schema(conn: sqlite3.Connection) -> None:
    actual = _user_tables(conn)
    if actual != EXPECTED_TABLES:
        unknown = sorted(actual - EXPECTED_TABLES)
        missing = sorted(EXPECTED_TABLES - actual)
        raise ResetBlocked(
            "Схема БД не совпадает с проверенной retention-матрицей: "
            f"unknown={unknown}, missing={missing}"
        )


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    _require_known_schema(conn)
    return {
        table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in sorted(EXPECTED_TABLES)
    }


def _database_state_digest(conn: sqlite3.Connection) -> str:
    """Логический digest всех таблиц, независимый от WAL/page layout."""
    _require_known_schema(conn)
    digest = hashlib.sha256()
    for table in sorted(EXPECTED_TABLES):
        columns = [
            str(row[1])
            for row in conn.execute(f'PRAGMA table_info("{table}")')
        ]
        digest.update(
            json.dumps([table, columns], ensure_ascii=False).encode("utf-8")
        )
        query = f'SELECT * FROM "{table}" ORDER BY rowid'
        for row in conn.execute(query):
            digest.update(
                json.dumps(
                    list(row),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            )
            digest.update(b"\n")
    return digest.hexdigest()


def _online_backup(target: Path) -> None:
    if target.exists():
        raise ResetBlocked(f"Backup уже существует: {target}")
    source = db.connect()
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    _private_mode(target)
    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        quick = check.execute("PRAGMA quick_check").fetchone()[0]
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if quick != "ok" or integrity != "ok":
        raise ResetBlocked(
            f"SQLite backup не прошёл проверку: quick={quick}, integrity={integrity}"
        )


def _photo_manifest(root_raw: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(PHOTO_DIR if root_raw is None else root_raw)
    entries: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
            if path.is_symlink():
                raise ResetBlocked(f"Symlink в photo storage запрещён: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            entries.append(
                {
                    "path": relative,
                    "size": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
    canonical = json.dumps(
        entries,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "root": str(root),
        "files": len(entries),
        "bytes": sum(int(entry["size"]) for entry in entries),
        "digest": hashlib.sha256(canonical).hexdigest(),
        "entries": entries,
    }


def _create_photo_archive(path: Path, entries: list[dict[str, Any]]) -> None:
    root = Path(PHOTO_DIR)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with tarfile.open(temporary, "w:gz") as archive:
            for entry in entries:
                relative = str(entry["path"])
                source = root / Path(relative)
                info = archive.gettarinfo(str(source), arcname=relative)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                with source.open("rb") as stream:
                    archive.addfile(info, stream)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        _private_mode(temporary)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def create_snapshot(archive_raw: str | os.PathLike[str]) -> dict[str, Any]:
    """Создать полный pre-reset пакет, не меняя рабочие данные."""
    archive = _validated_archive_dir(archive_raw, create=True)
    backup = archive / BACKUP_NAME
    try:
        _online_backup(backup)

        conn = db.connect()
        try:
            counts = _table_counts(conn)
            state_digest = _database_state_digest(conn)
            quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            conn.close()
        if quick_check != "ok":
            raise ResetBlocked(f"Рабочая БД не прошла quick_check: {quick_check}")

        safe_rows = db.enrichment_rows()
        raw_rows = db.registry_rows()
        schema = db.export_schema()
        photos = _photo_manifest()

        safe_path = archive / SAFE_EXPORT_NAME
        raw_path = archive / RAW_EXPORT_NAME
        schema_path = archive / SCHEMA_NAME
        photo_path = archive / PHOTO_MANIFEST_NAME
        photo_archive_path = archive / PHOTO_ARCHIVE_NAME
        _write_json_gz(
            safe_path,
            {
                "kind": "safe_enrichment",
                "created_at": _utc_now(),
                "rows": safe_rows,
            },
        )
        _write_json_gz(
            raw_path,
            {
                "kind": "raw_diagnostic",
                "created_at": _utc_now(),
                "rows": raw_rows,
            },
        )
        _write_json(schema_path, schema)
        _write_json_gz(photo_path, photos)
        _create_photo_archive(photo_archive_path, photos["entries"])

        if len(safe_rows) != counts["clients"] or len(raw_rows) != counts["clients"]:
            raise ResetBlocked(
                "Число строк экспортов не совпало с таблицей clients: "
                f"safe={len(safe_rows)}, raw={len(raw_rows)}, "
                f"clients={counts['clients']}"
            )

        artifacts = {
            BACKUP_NAME: _sha256_file(backup),
            SAFE_EXPORT_NAME: _sha256_file(safe_path),
            RAW_EXPORT_NAME: _sha256_file(raw_path),
            SCHEMA_NAME: _sha256_file(schema_path),
            PHOTO_MANIFEST_NAME: _sha256_file(photo_path),
            PHOTO_ARCHIVE_NAME: _sha256_file(photo_archive_path),
        }
        token_source = {
            "artifacts": artifacts,
            "counts": counts,
            "database_state_digest": state_digest,
            "photos_digest": photos["digest"],
        }
        confirm_token = hashlib.sha256(
            json.dumps(
                token_source,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        manifest = {
            "kind": "hardbot_limited_test_pre_reset",
            "created_at": _utc_now(),
            "database": str(Path(db.DB_PATH).resolve()),
            "database_state_digest": state_digest,
            "database_quick_check": quick_check,
            "counts": counts,
            "safe_rows": len(safe_rows),
            "raw_rows": len(raw_rows),
            "photos": {
                "files": photos["files"],
                "bytes": photos["bytes"],
                "digest": photos["digest"],
            },
            "artifacts": artifacts,
            "confirm_token": confirm_token,
        }
        _write_json(archive / SNAPSHOT_MANIFEST, manifest)
        return manifest
    except Exception:
        # Не оставлять полуготовый каталог, который можно ошибочно принять за backup.
        shutil.rmtree(archive, ignore_errors=True)
        raise


def _load_verified_manifest(archive: Path) -> dict[str, Any]:
    manifest_path = archive / SNAPSHOT_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ResetBlocked(f"Некорректный snapshot manifest: {manifest_path}") from exc
    if manifest.get("kind") != "hardbot_limited_test_pre_reset":
        raise ResetBlocked("Неизвестный тип snapshot manifest")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != EXPECTED_ARTIFACTS:
        raise ResetBlocked(
            "Snapshot manifest содержит неполный или неожиданный набор артефактов"
        )
    counts = manifest.get("counts")
    if (
        not isinstance(counts, dict)
        or set(counts) != EXPECTED_TABLES
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counts.values()
        )
    ):
        raise ResetBlocked("Snapshot manifest содержит некорректные counts")
    photos = manifest.get("photos")
    state_digest = manifest.get("database_state_digest")
    if (
        not isinstance(photos, dict)
        or not isinstance(photos.get("digest"), str)
        or len(photos["digest"]) != 64
        or not isinstance(state_digest, str)
        or len(state_digest) != 64
    ):
        raise ResetBlocked("Snapshot manifest содержит некорректные digests")
    for name, expected in artifacts.items():
        if not isinstance(expected, str) or len(expected) != 64:
            raise ResetBlocked(f"Некорректный SHA-256 артефакта: {name}")
        path = archive / name
        if not path.is_file() or _sha256_file(path) != expected:
            raise ResetBlocked(f"Артефакт отсутствует или повреждён: {name}")
    token_source = {
        "artifacts": artifacts,
        "counts": counts,
        "database_state_digest": state_digest,
        "photos_digest": photos["digest"],
    }
    expected_token = hashlib.sha256(
        json.dumps(
            token_source,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if manifest.get("confirm_token") != expected_token:
        raise ResetBlocked("Confirm-token snapshot manifest не соответствует содержимому")
    return manifest


def _restore_photos(archived_photos: Path) -> None:
    photo_root = Path(PHOTO_DIR)
    if photo_root.exists():
        shutil.rmtree(photo_root)
    if archived_photos.exists():
        os.replace(archived_photos, photo_root)


def _restore_database(backup: Path) -> None:
    database = Path(db.DB_PATH)
    source = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    destination = sqlite3.connect(database)
    try:
        source.backup(destination)
        destination.commit()
        destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        destination.close()
        source.close()


def apply_reset(
    archive_raw: str | os.PathLike[str],
    *,
    confirm_token: str,
    app_stopped: bool,
) -> dict[str, Any]:
    """Очистить все application tables и переместить фото в reset-архив."""
    if not app_stopped:
        raise ResetBlocked(
            "Основной контейнер должен быть остановлен; предъявите --app-stopped"
        )
    archive = _validated_archive_dir(archive_raw, create=False)
    manifest = _load_verified_manifest(archive)
    if not confirm_token or confirm_token != manifest.get("confirm_token"):
        raise ResetBlocked("Confirm-token не совпал со snapshot manifest")
    if (archive / POST_RESET_MANIFEST).exists():
        raise ResetBlocked("Этот snapshot уже использован для reset")

    conn = db.connect()
    try:
        _require_known_schema(conn)
        current_counts = _table_counts(conn)
        current_digest = _database_state_digest(conn)
    finally:
        conn.close()
    if current_counts != manifest["counts"] or current_digest != manifest[
        "database_state_digest"
    ]:
        raise ResetBlocked(
            "БД изменилась после snapshot; требуется новый snapshot и новая проверка"
        )

    current_photos = _photo_manifest()
    if current_photos["digest"] != manifest["photos"]["digest"]:
        raise ResetBlocked(
            "Photo storage изменился после snapshot; требуется новый snapshot"
        )

    photo_root = Path(PHOTO_DIR)
    archived_photos = archive / "photos-before-reset"
    backup = archive / BACKUP_NAME
    photos_moved = False
    database_changed = False
    try:
        _write_json(
            archive / APPLY_STARTED_MANIFEST,
            {
                "kind": "hardbot_limited_test_apply_started",
                "started_at": _utc_now(),
                "confirm_token": manifest["confirm_token"],
            },
        )
        if archived_photos.exists():
            raise ResetBlocked(f"Photo quarantine уже существует: {archived_photos}")
        if photo_root.exists():
            os.replace(photo_root, archived_photos)
            photos_moved = True
        photo_root.mkdir(parents=True, exist_ok=True)
        _private_mode(photo_root)

        conn = db.connect()
        try:
            _require_known_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            for table in DELETE_ORDER:
                conn.execute(f'DELETE FROM "{table}"')
            if conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='sqlite_sequence'"
            ).fetchone():
                conn.execute("DELETE FROM sqlite_sequence")
            conn.commit()
            database_changed = True
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("quick_check после reset не вернул ok")
            post_counts = _table_counts(conn)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        nonzero = {name: count for name, count in post_counts.items() if count}
        if nonzero:
            raise RuntimeError(f"После reset остались строки: {nonzero}")
        if any(photo_root.iterdir()):
            raise RuntimeError("Новый photo storage не пуст")

        post = {
            "kind": "hardbot_limited_test_post_reset",
            "completed_at": _utc_now(),
            "pre_reset_counts": manifest["counts"],
            "post_reset_counts": post_counts,
            "database_quick_check": "ok",
            "photos_quarantined": current_photos["files"],
            "photo_quarantine": str(archived_photos),
            "rollback_database": str(backup),
        }
        _write_json(archive / POST_RESET_MANIFEST, post)
        return post
    except Exception:
        if database_changed:
            _restore_database(backup)
        if photos_moved:
            archived_manifest = _photo_manifest(archived_photos)
            if archived_manifest["digest"] != manifest["photos"]["digest"]:
                raise RuntimeError(
                    "Photo quarantine повреждён; автоматический rollback запрещён"
                )
            _restore_photos(archived_photos)
        raise


def recover_incomplete_reset(
    archive_raw: str | os.PathLike[str],
    *,
    confirm_token: str,
    app_stopped: bool,
) -> dict[str, Any]:
    """Restore the pre-reset snapshot after an interrupted destructive process."""

    if not app_stopped:
        raise ResetBlocked(
            "Основной контейнер должен быть остановлен; предъявите --app-stopped"
        )
    archive = _validated_archive_dir(archive_raw, create=False)
    manifest = _load_verified_manifest(archive)
    if not confirm_token or confirm_token != manifest.get("confirm_token"):
        raise ResetBlocked("Confirm-token не совпал со snapshot manifest")
    if (archive / POST_RESET_MANIFEST).exists():
        raise ResetBlocked("Reset завершён; incomplete recovery запрещён")
    started = archive / APPLY_STARTED_MANIFEST
    if not started.is_file():
        raise ResetBlocked("Нет durable evidence начала destructive reset")

    backup = archive / BACKUP_NAME
    archived_photos = archive / "photos-before-reset"
    if archived_photos.exists():
        archived_manifest = _photo_manifest(archived_photos)
        if archived_manifest["digest"] != manifest["photos"]["digest"]:
            raise ResetBlocked("Photo quarantine не совпал со snapshot manifest")
    _restore_database(backup)
    if archived_photos.exists():
        _restore_photos(archived_photos)

    conn = db.connect()
    try:
        counts = _table_counts(conn)
        digest = _database_state_digest(conn)
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    photos = _photo_manifest()
    if (
        counts != manifest["counts"]
        or digest != manifest["database_state_digest"]
        or quick != "ok"
        or photos["digest"] != manifest["photos"]["digest"]
    ):
        raise RuntimeError("Interrupted reset recovery не восстановил pre-reset state")
    result = {
        "kind": "hardbot_limited_test_recovery",
        "completed_at": _utc_now(),
        "counts": counts,
        "database_quick_check": quick,
        "photos": {
            "files": photos["files"],
            "bytes": photos["bytes"],
            "digest": photos["digest"],
        },
    }
    _write_json(archive / RECOVERY_MANIFEST, result)
    return result


def current_status() -> dict[str, Any]:
    conn = db.connect()
    try:
        counts = _table_counts(conn)
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    photos = _photo_manifest()
    return {
        "database": str(Path(db.DB_PATH).resolve()),
        "quick_check": quick,
        "counts": counts,
        "photos": {
            "files": photos["files"],
            "bytes": photos["bytes"],
            "digest": photos["digest"],
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot", help="backup + safe/raw/photo manifests")
    snapshot.add_argument("--archive", required=True)
    apply = sub.add_parser("apply", help="очистить после внешней проверки snapshot")
    apply.add_argument("--archive", required=True)
    apply.add_argument("--confirm-token", required=True)
    apply.add_argument("--app-stopped", action="store_true")
    recover = sub.add_parser(
        "recover",
        help="восстановить pre-reset state после оборванного apply",
    )
    recover.add_argument("--archive", required=True)
    recover.add_argument("--confirm-token", required=True)
    recover.add_argument("--app-stopped", action="store_true")
    sub.add_parser("status", help="read-only counts/checks")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "snapshot":
            result = create_snapshot(args.archive)
        elif args.command == "apply":
            result = apply_reset(
                args.archive,
                confirm_token=args.confirm_token,
                app_stopped=args.app_stopped,
            )
        elif args.command == "recover":
            result = recover_incomplete_reset(
                args.archive,
                confirm_token=args.confirm_token,
                app_stopped=args.app_stopped,
            )
        else:
            result = current_status()
        # Никаких строк реестра/путей фото в stdout: только проверяемая сводка.
        public = {
            key: value
            for key, value in result.items()
            if key not in {"confirm_token", "artifacts", "database_state_digest"}
        }
        if "confirm_token" in result:
            public["confirm_token"] = result["confirm_token"]
        print(json.dumps(public, ensure_ascii=False, indent=2))
        return 0
    except ResetBlocked as exc:
        print(f"ОТКАЗ: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
