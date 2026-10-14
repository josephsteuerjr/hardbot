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
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import stat
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import db
from exchange_contract import (
    CLEARANCE_RECEIPT_SCHEMA,
    SNAPSHOT_KIND,
    contract_binding,
)
from photostore import PHOTO_DIR as DEFAULT_PHOTO_DIR


RESET_TABLES = {
    "office_actions", "office_action_contracts",
    "call_review_history", "call_reviews", "notification_attempts", "notification_outbox",
    "registry_payments",
    # Журнал доставки пушей: telegram_id руководителей и ФИО сотрудников — это
    # ПД, переживать сброс ограниченного теста они не должны (в отличие от
    # training_rows, где ПД нет по построению).
    "push_log",
    # Контрольный обзвон безопасника ссылается на id выездов: выезды уходят
    # сбросом — уходят и проверки по ним.
    "control_checks",
    "clients",
    # Дополнительные поля реестра привязаны к КД (client_extra.contract_id).
    # Кейсы сброс уносит, значит и их дополнительные значения — иначе после
    # следующего импорта в карточке всплыли бы данные исчезнувшего договора.
    "client_extra",
    "visits",
    # Звонки софтников привязаны к КД, как визиты: кейсы сброс уносит — уносит
    # и журнал звонков по ним.
    "calls",
    "users",
    "agent_bind",
    "audit",
    "markers",
    # Заметки выездника пишутся ПРО КЕЙСЫ, а кейсы сброс уносит. Пережившая
    # сброс заметка — это текст про человека, которого в системе больше нет:
    # пользы ноль, а персональные данные лежат. В отличие от треков (там ценность
    # в самих маршрутах и времени в пути) сохранять тут нечего.
    "notes",
    "settings",
    "managers",
    # Офисы сотрудников — такой же служебный факт, как состав руководителей:
    # ограниченный тест начинается с чистого штата.
    "employee_city",
    "app_links",
    "app_tokens",
    # Токены приложения выездника на ПК — тот же выданный доступ, что app_tokens.
    "agent_devices",
    "photo_geo",
    # Следы выезда (точки телефона при открытой карточке) и адреса, выученные
    # по подтверждённым выездам: и то и другое — ПРО КЕЙСЫ, которые сброс
    # уносит. В отличие от треков ценность не в маршрутах, а в привязке к
    # конкретному должнику, и переживать сброс им незачем.
    "visit_presence",
    "addr_candidates",
    # Реестр доставленного обогащения: «что пульт сам положил в книгу» по КД.
    # Сброс уносит кейсы; память о доставке по исчезнувшим КД после повторного
    # импорта выдавала бы правку офиса за свою старую запись.
    "enrichment_ledger",
    "enrichment_book_ledger",
    # Чеки об оплате привязаны к КД карточки, куда их приложили: кейсы сброс
    # уносит — уносят и доказательства платежей по ним. Как заметки, а не как
    # треки: ценность чека в привязке к конкретному должнику.
    "payment_evidence",
    # Журнал закреплений ссылается на КД, которых после сброса нет: события
    # о исчезнувших договорах вводят в заблуждение следующий тест.
    "assignment_events",
    "report_chats",
    "report_chat_employees",
}

# Import receipts are a server-side anti-replay trust anchor, not test data.
# Consumed receipts and source heads must survive a limited-test reset; otherwise
# an old published master could become "new" again immediately after cleanup.
# Pending authorizations are invalidated atomically during reset.
RETAINED_TABLES = {
    "employee_identities", "employee_aliases",  # staff identity survives registry reset; no portfolio or login grant
    "import_receipts",
    "import_source_heads",
    # Корпус для обучения скоринга — это НЕ портфель выездника. Сюда загружается
    # общий реестр целиком, отдельной операцией, и стирать его вместе с раздачей
    # нельзя: каждый сброс обнулял бы всё, на чём модель училась. Персональных
    # данных в нём нет по построению (только признаки и результат, номер договора
    # хэшем), поэтому и с точки зрения приватности сохранять его безопасно.
    "training_rows",
    # Треки поездок. Решение владельца: они переживают сброс портфелей — иначе
    # каждый цикл теряет ровно те данные, ради которых треки и заводились
    # (фактическое время в пути, проезжаемость дорог, длительность визита).
    # ВНИМАНИЕ: в отличие от training_rows это ПД сотрудника (его перемещения).
    # Чтобы вернуть прежнее поведение — перенести строку в RESET_TABLES.
    "track_points",
    # Привязки пульта к устройствам. Это не портфель и не ПД должника, а доступ
    # оператора: сброс чистит данные взыскания, а не выбивает пульт наружу
    # посреди процедуры. Отозвать устройство — отдельное явное действие, и оно
    # доступно мастер-ключом в любой момент. Токены лежат хэшами.
    "admin_devices",
    # Коды привязки. По той же причине, что и устройства: это доступ оператора,
    # а не данные взыскания. Плюс отдельная: код одноразовый, и стирание
    # обменянных кодов означало бы, что тот же код можно предъявить второй раз.
    "admin_enroll_codes",
    # Компьютеры с пультом руководителя — доступ, как и устройства пульта
    # аналитика: сброс чистит данные взыскания, а не выбивает руководителя.
    "manager_devices",
}
EXPECTED_TABLES = RESET_TABLES | RETAINED_TABLES

# Сначала зависимые/операционные сущности, затем registry и identity/config.
DELETE_ORDER = (
    "office_action_contracts", "office_actions",
    "call_review_history", "call_reviews", "notification_attempts", "notification_outbox",
    "registry_payments",
    "push_log",       # телеметрия, зависимостей нет — но не в DELETE_ORDER
                      # значит RuntimeError «после reset остались строки»
    "control_checks",  # до visits: проверки ссылаются на выезды
    "report_chat_employees",
    "report_chats",
    "photo_geo",
    "visit_presence",
    "addr_candidates",
    "enrichment_ledger",
    "enrichment_book_ledger",
    "payment_evidence",
    "assignment_events",
    "notes",          # заметки объявлены сбрасываемыми — но чистить их забыли,
                      # и сброс падал целиком, как только у кого-то появлялась
                      # первая заметка (список RESET_TABLES и порядок удаления
                      # сверяются между собой)
    "app_tokens",
    "agent_devices",
    "app_links",
    "managers",
    "employee_city",
    "markers",
    "audit",
    "agent_bind",
    "users",
    "visits",
    "calls",
    "client_extra",
    "clients",
    "settings",
)

SNAPSHOT_MANIFEST = "snapshot_manifest.json"
POST_RESET_MANIFEST = "post_reset_manifest.json"
APPLY_STARTED_MANIFEST = "apply_started_manifest.json"
RECOVERY_MANIFEST = "recovery_manifest.json"
ORCHESTRATION_INTENT = "orchestration_apply_intent"
ORCHESTRATION_INTENT_SCHEMA = "hardbot.reset-orchestration-apply-intent.v2"
BACKUP_NAME = "hardbot-pre-reset.sqlite3"
SAFE_EXPORT_NAME = "safe-enrichment.json.gz"
RAW_EXPORT_NAME = "raw-diagnostic.json.gz"
SCHEMA_NAME = "export-schema.json"
PHOTO_MANIFEST_NAME = "photos-manifest.json.gz"
PHOTO_ARCHIVE_NAME = "photos-before-reset.tar.gz"
CLEARANCE_RECEIPT_NAME = "registry-clearance.json"
PREPARE_RECEIPT_NAME = "reset-prepare.json"
ARCHIVE_ONLY_AUTHORIZATION_NAME = "archive-only-authorization.json"
ARCHIVE_ONLY_AUTHORIZATION_SCHEMA = (
    "hardbot.archive-only-reset-authorization.v1"
)
ARCHIVE_ONLY_AUTHORIZATION_KIND = "obsolete_dataset_archive_only"
AUTHORITY_MODE_REGISTRY = "registry_clearance"
AUTHORITY_MODE_ARCHIVE_ONLY = "archive_only_owner"
AUTHORITY_MODES = {
    AUTHORITY_MODE_REGISTRY,
    AUTHORITY_MODE_ARCHIVE_ONLY,
}
LEGACY_APPLY_STARTED_SCHEMA = "hardbot.reset-apply-started.v2"
APPLY_STARTED_SCHEMA = "hardbot.reset-apply-started.v3"
POST_RESET_SCHEMA = "hardbot.reset-post.v3"
RECOVERY_SCHEMA = "hardbot.reset-recovery.v3"
DATA_EXCHANGE_CONTRACT = contract_binding()
# Recovery-only compatibility for snapshots created before the 2026-07-29
# exchange_contract.py SHA-shadowing fix. The buggy loader returned the last
# legacy bootstrap hash as the contract-file SHA. It must never authorize a
# new apply, but a durable v2 apply-started receipt must still be recoverable.
LEGACY_V2_RECOVERY_DATA_EXCHANGE_CONTRACT = {
    "schema": "hardbot.data-exchange-contract.v1",
    "version": 1,
    "sha256": (
        "4cf6781ca18c03cbc04ac339de9eeca9a"
        "f69e85ac6a94e1134a636076a32551b"
    ),
}
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


def _archive_only_decision() -> dict[str, Any]:
    return {
        "dataset_status": "obsolete",
        "registry_transfer": "explicitly_not_required_by_owner",
        "archive_required": True,
        "clear_application_data": True,
        "preserve_consumed_import_receipts": True,
        "preserve_import_source_heads": True,
        "invalidate_pending_import_authorizations": True,
    }


class ResetBlocked(RuntimeError):
    """Fail-closed отказ без изменения данных."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_size,
        right.st_mtime_ns,
    )


@contextmanager
def _open_regular_read(path: Path, *, label: str):
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ResetBlocked(f"{label} недоступен: {path}") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or _is_reparse(before)
        or path.is_symlink()
    ):
        raise ResetBlocked(f"{label} должен быть обычным non-reparse файлом: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ResetBlocked(f"{label} не удалось безопасно открыть: {path}") from exc
    stream = os.fdopen(descriptor, "rb")
    try:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or (before.st_dev, before.st_ino)
            != (opened.st_dev, opened.st_ino)
        ):
            raise ResetBlocked(f"{label} подменён при открытии: {path}")
        yield stream
        after = os.fstat(stream.fileno())
        if not _same_file_state(opened, after):
            raise ResetBlocked(f"{label} изменился во время чтения: {path}")
        try:
            current = os.lstat(path)
        except OSError as exc:
            raise ResetBlocked(f"{label} исчез во время чтения: {path}") from exc
        if not _same_file_state(opened, current):
            raise ResetBlocked(f"{label} заменён во время чтения: {path}")
    finally:
        stream.close()


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    with _open_regular_read(path, label=label) as stream:
        return stream.read()


def _sha256_file(path: Path, *, label: str = "artifact") -> str:
    digest = hashlib.sha256()
    with _open_regular_read(path, label=label) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _require_safe_parent(path: Path) -> Path:
    parent = path.parent
    try:
        parent_info = os.lstat(parent)
    except OSError as exc:
        raise ResetBlocked(f"Unsafe artifact parent: {parent}") from exc
    if (
        not parent.exists()
        or not parent.is_dir()
        or parent.is_symlink()
        or not stat.S_ISDIR(parent_info.st_mode)
        or _is_reparse(parent_info)
    ):
        raise ResetBlocked(f"Небезопасный родитель артефакта: {parent}")
    target_info = os.lstat(path) if path.exists() or path.is_symlink() else None
    if (
        path.is_symlink()
        or (path.exists() and not path.is_file())
        or (
            target_info is not None
            and (
                not stat.S_ISREG(target_info.st_mode)
                or _is_reparse(target_info)
            )
        )
    ):
        raise ResetBlocked(f"Небезопасный target артефакта: {path}")
    return parent


def _open_unique_temp(path: Path, *, mode: int = 0o600):
    """Open a same-directory O_EXCL temp so replace cannot cross filesystems."""

    parent = _require_safe_parent(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    for _attempt in range(32):
        temporary = parent / (
            f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
        )
        try:
            descriptor = os.open(temporary, flags, mode)
        except FileExistsError:
            continue
        return temporary, os.fdopen(descriptor, "wb")
    raise ResetBlocked(f"Не удалось создать уникальный temp для {path}")


def _fsync_file(path: Path) -> None:
    # Windows FlushFileBuffers rejects a read-only CRT descriptor.
    descriptor = os.open(path, os.O_RDWR if os.name == "nt" else os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json(path: Path, payload: Any) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    temporary = None
    try:
        temporary, stream = _open_unique_temp(path)
        with stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _private_mode(temporary)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _write_json_gz(path: Path, payload: Any) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary = None
    try:
        temporary, raw = _open_unique_temp(path)
        with raw:
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
        if temporary is not None:
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
    data_dir = Path(os.path.abspath(Path(db.DB_PATH).parent))
    backup_root_raw = data_dir / "backups"
    archive_raw = Path(os.path.abspath(Path(raw)))
    if archive_raw.parent != backup_root_raw:
        raise ResetBlocked(
            f"Reset archive must be a direct child of {backup_root_raw}"
        )
    try:
        data_info = os.lstat(data_dir)
    except OSError as exc:
        raise ResetBlocked(f"Data directory is unavailable: {data_dir}") from exc
    if (
        not stat.S_ISDIR(data_info.st_mode)
        or _is_reparse(data_info)
        or data_dir.is_symlink()
    ):
        raise ResetBlocked(f"Unsafe data directory: {data_dir}")
    if backup_root_raw.is_symlink() or archive_raw.is_symlink():
        raise ResetBlocked("Symlink в пути reset-архива запрещён")
    backup_root = backup_root_raw.resolve()
    archive = archive_raw.resolve()
    try:
        archive.relative_to(backup_root)
    except ValueError as exc:
        raise ResetBlocked(
            f"Reset-архив должен находиться внутри {backup_root}"
        ) from exc
    if archive == backup_root:
        raise ResetBlocked("Корневой каталог backups нельзя использовать как reset-архив")
    if create:
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_info = os.lstat(backup_root)
        if (
            backup_root.is_symlink()
            or not backup_root.is_dir()
            or _is_reparse(backup_info)
        ):
            raise ResetBlocked(f"Небезопасный backup root: {backup_root}")
        archive.mkdir(parents=True, exist_ok=False)
        archive_info = os.lstat(archive)
        if not stat.S_ISDIR(archive_info.st_mode) or _is_reparse(archive_info):
            raise ResetBlocked(f"Unsafe reset archive: {archive}")
        _private_mode(archive)
        _fsync_directory(archive)
        _fsync_directory(backup_root)
        _fsync_directory(backup_root.parent)
    elif (
        not archive.is_dir()
        or archive.is_symlink()
        or _is_reparse(os.lstat(archive))
        or _is_reparse(os.lstat(backup_root))
    ):
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
        for table in sorted(RESET_TABLES)
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


def _retained_import_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return a non-sensitive proof that the anti-replay ledger is coherent."""

    _require_known_schema(conn)
    orphan = conn.execute(
        "SELECT h.source_id FROM import_source_heads AS h "
        "LEFT JOIN import_receipts AS r "
        "ON r.proof_digest=h.proof_digest "
        "AND r.source_id=h.source_id AND r.sequence=h.sequence "
        "AND r.status='consumed' "
        "WHERE r.proof_digest IS NULL LIMIT 1"
    ).fetchone()
    if orphan is not None:
        raise ResetBlocked(
            "Anti-replay source head не подтверждён consumed import receipt"
        )

    receipt_columns = (
        "proof_digest",
        "source_kind",
        "source_id",
        "sequence",
        "proof_json",
        "status",
        "registered_at",
        "expires_at",
        "consumed_at",
        "result_json",
        "result_digest",
    )
    consumed = [
        list(row)
        for row in conn.execute(
            "SELECT " + ",".join(receipt_columns) + " "
            "FROM import_receipts WHERE status='consumed' "
            "ORDER BY source_id, sequence, proof_digest"
        )
    ]
    invalid_status = conn.execute(
        "SELECT status FROM import_receipts "
        "WHERE status NOT IN ('pending','consumed') LIMIT 1"
    ).fetchone()
    if invalid_status is not None:
        raise ResetBlocked(
            "Anti-replay ledger содержит неизвестный статус receipt"
        )
    pending = int(
        conn.execute(
            "SELECT COUNT(*) FROM import_receipts WHERE status='pending'"
        ).fetchone()[0]
    )
    heads = [
        list(row)
        for row in conn.execute(
            "SELECT source_id,sequence,proof_digest,imported_at "
            "FROM import_source_heads ORDER BY source_id"
        )
    ]
    return {
        "pending": pending,
        "consumed": len(consumed),
        "consumed_digest": _canonical_digest(consumed),
        "heads": len(heads),
        "heads_digest": _canonical_digest(heads),
    }


def _online_backup(target: Path) -> None:
    if target.exists():
        raise ResetBlocked(f"Backup уже существует: {target}")
    _require_safe_parent(target)
    source = db.connect()
    destination = sqlite3.connect(target)
    try:
        destination.execute("PRAGMA synchronous=FULL")
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    _private_mode(target)
    _fsync_file(target)
    _fsync_directory(target.parent)
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
    if root.is_symlink():
        raise ResetBlocked(f"Symlink в photo storage запрещён: {root}")
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
        "root_exists": root.exists(),
        "files": len(entries),
        "bytes": sum(int(entry["size"]) for entry in entries),
        "digest": hashlib.sha256(canonical).hexdigest(),
        "entries": entries,
    }


def _create_photo_archive(path: Path, entries: list[dict[str, Any]]) -> None:
    root = Path(PHOTO_DIR)
    temporary = None
    try:
        temporary, raw = _open_unique_temp(path)
        with raw:
            with tarfile.open(fileobj=raw, mode="w:gz") as archive:
                for entry in entries:
                    relative = str(entry["path"])
                    source = root / Path(relative)
                    info = archive.gettarinfo(str(source), arcname=relative)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    with source.open("rb") as stream:
                        archive.addfile(info, stream)
            raw.flush()
            os.fsync(raw.fileno())
        _private_mode(temporary)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _snapshot_export_rows(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build both exports from the exact online-backup connection.

    ``db.registry_rows``/``db.enrichment_rows`` normally open the live DB.
    During a running prepare-clearance snapshot that would create a race
    between the backup and exports, so the same canonical projection is
    evaluated against rows read from the immutable backup.
    """

    rows = conn.execute("SELECT * FROM clients ORDER BY client").fetchall()
    raw_rows: list[dict[str, Any]] = []
    safe_rows: list[dict[str, Any]] = []
    for raw in rows:
        registry = {field.key: raw[field.key] for field in db.FIELDS}
        raw_rows.append(registry)
        safe = dict(registry)
        for key in db.BOT_VISIT_KEYS:
            if key in safe:
                safe[key] = None
        snapshot = db._merged_visit(raw)
        safe[db.BOT_VISIT_MARKER] = snapshot is not None
        if snapshot:
            tips = str(snapshot.get("tips") or raw["tips"] or "").strip()
            if tips:
                snapshot = {**snapshot, "tips": tips}
            safe.update(snapshot)
        safe_rows.append(safe)
    return safe_rows, raw_rows


def create_snapshot(archive_raw: str | os.PathLike[str]) -> dict[str, Any]:
    """Создать полный pre-reset пакет, не меняя рабочие данные."""
    archive = _validated_archive_dir(archive_raw, create=True)
    backup = archive / BACKUP_NAME
    try:
        _online_backup(backup)

        conn = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            counts = _table_counts(conn)
            state_digest = _database_state_digest(conn)
            quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
            safe_rows, raw_rows = _snapshot_export_rows(conn)
        finally:
            conn.close()
        if quick_check != "ok":
            raise ResetBlocked(f"SQLite backup не прошёл quick_check: {quick_check}")

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
                "data_exchange_contract": DATA_EXCHANGE_CONTRACT,
                "created_at": _utc_now(),
                "rows": safe_rows,
            },
        )
        _write_json_gz(
            raw_path,
            {
                "kind": "raw_diagnostic",
                "data_exchange_contract": DATA_EXCHANGE_CONTRACT,
                "created_at": _utc_now(),
                "rows": raw_rows,
            },
        )
        _write_json(schema_path, schema)
        _write_json_gz(photo_path, photos)
        _create_photo_archive(photo_archive_path, photos["entries"])
        photos_after_archive = _photo_manifest()
        if (
            photos_after_archive["root_exists"] != photos["root_exists"]
            or photos_after_archive["files"] != photos["files"]
            or photos_after_archive["bytes"] != photos["bytes"]
            or photos_after_archive["digest"] != photos["digest"]
        ):
            raise ResetBlocked(
                "Photo storage изменился во время snapshot; повторите prepare-clearance"
            )

        if len(safe_rows) != counts["clients"] or len(raw_rows) != counts["clients"]:
            raise ResetBlocked(
                "Число строк экспортов не совпало с таблицей clients: "
                f"safe={len(safe_rows)}, raw={len(raw_rows)}, "
                f"clients={counts['clients']}"
            )
        live = db.connect()
        try:
            live_counts = _table_counts(live)
            live_digest = _database_state_digest(live)
        finally:
            live.close()
        final_photos = _photo_manifest()
        if live_counts != counts or live_digest != state_digest:
            raise ResetBlocked(
                "Рабочая БД изменилась во время snapshot; повторите prepare-clearance"
            )
        if (
            final_photos["root_exists"] != photos["root_exists"]
            or final_photos["digest"] != photos["digest"]
        ):
            raise ResetBlocked(
                "Photo storage изменился во время snapshot; повторите prepare-clearance"
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
            "data_exchange_contract": DATA_EXCHANGE_CONTRACT,
            "artifacts": artifacts,
            "counts": counts,
            "database_state_digest": state_digest,
            "photos_digest": photos["digest"],
            "photos_root_exists": photos["root_exists"],
        }
        confirm_token = hashlib.sha256(
            json.dumps(
                token_source,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        manifest = {
            "kind": SNAPSHOT_KIND,
            "data_exchange_contract": DATA_EXCHANGE_CONTRACT,
            "created_at": _utc_now(),
            "database": str(Path(db.DB_PATH).resolve()),
            "database_state_digest": state_digest,
            "database_quick_check": quick_check,
            "counts": counts,
            "safe_rows": len(safe_rows),
            "raw_rows": len(raw_rows),
            "photos": {
                "root_exists": photos["root_exists"],
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
        _fsync_directory(archive.parent)
        raise


def _load_json_regular(path: Path, *, label: str) -> Any:
    try:
        return json.loads(
            _read_regular_bytes(path, label=label).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ResetBlocked(f"Invalid {label}: {path}") from exc


def _load_json_gz_regular(path: Path, *, label: str) -> Any:
    try:
        return json.loads(
            gzip.decompress(_read_regular_bytes(path, label=label)).decode(
                "utf-8"
            )
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ResetBlocked(f"Invalid {label}: {path}") from exc


def _verify_photo_archive(
    archive_path: Path,
    entries: list[dict[str, Any]],
) -> None:
    expected: dict[str, dict[str, Any]] = {}
    for item in entries:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "size", "sha256"}
            or not isinstance(item["path"], str)
            or not item["path"]
            or isinstance(item["size"], bool)
            or not isinstance(item["size"], int)
            or item["size"] < 0
            or not isinstance(item["sha256"], str)
            or len(item["sha256"]) != 64
        ):
            raise ResetBlocked("Photo manifest entry has invalid schema")
        relative = Path(item["path"])
        parts = tuple(item["path"].replace("\\", "/").split("/"))
        if (
            relative.is_absolute()
            or any(part in {"", ".", ".."} for part in parts)
            or item["path"] in expected
        ):
            raise ResetBlocked("Photo manifest contains unsafe/duplicate path")
        expected[item["path"]] = {
            "size": item["size"],
            "sha256": item["sha256"],
        }

    actual: dict[str, dict[str, Any]] = {}
    try:
        with _open_regular_read(
            archive_path,
            label="photo archive",
        ) as raw:
            with tarfile.open(fileobj=raw, mode="r:gz") as packed:
                for member in packed.getmembers():
                    parts = tuple(member.name.replace("\\", "/").split("/"))
                    if (
                        not member.isfile()
                        or member.name.startswith("/")
                        or any(part in {"", ".", ".."} for part in parts)
                        or member.name in actual
                    ):
                        raise ResetBlocked(
                            "Photo archive contains unsafe/duplicate entry"
                        )
                    extracted = packed.extractfile(member)
                    if extracted is None:
                        raise ResetBlocked("Photo archive entry is unreadable")
                    digest = hashlib.sha256()
                    size = 0
                    for block in iter(
                        lambda: extracted.read(1024 * 1024),
                        b"",
                    ):
                        digest.update(block)
                        size += len(block)
                    actual[member.name] = {
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
    except ResetBlocked:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise ResetBlocked("Photo archive is invalid") from exc
    if actual != expected:
        raise ResetBlocked("Photo archive does not match photo manifest")


def _verify_backup_and_exports(
    archive: Path,
    manifest: dict[str, Any],
    *,
    expected_contract_binding: dict[str, Any],
) -> None:
    backup = archive / BACKUP_NAME
    before_sha = _sha256_file(backup, label="SQLite backup")
    connection = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        _require_known_schema(connection)
        counts = _table_counts(connection)
        state_digest = _database_state_digest(connection)
        safe_rows, raw_rows = _snapshot_export_rows(connection)
    finally:
        connection.close()
    if _sha256_file(backup, label="SQLite backup") != before_sha:
        raise ResetBlocked("SQLite backup changed during verification")
    if (
        quick != "ok"
        or integrity != "ok"
        or counts != manifest["counts"]
        or state_digest != manifest["database_state_digest"]
    ):
        raise ResetBlocked(
            "SQLite backup integrity/count/logical digest mismatch"
        )

    schema = _load_json_regular(
        archive / SCHEMA_NAME,
        label="export schema",
    )
    if schema != db.export_schema():
        raise ResetBlocked("Export schema is not the deployed DB schema")
    safe = _load_json_gz_regular(
        archive / SAFE_EXPORT_NAME,
        label="safe export",
    )
    raw = _load_json_gz_regular(
        archive / RAW_EXPORT_NAME,
        label="raw export",
    )
    if (
        not isinstance(safe, dict)
        or not isinstance(raw, dict)
        or safe.get("kind") != "safe_enrichment"
        or raw.get("kind") != "raw_diagnostic"
        or safe.get("data_exchange_contract") != expected_contract_binding
        or raw.get("data_exchange_contract") != expected_contract_binding
        or safe.get("rows") != safe_rows
        or raw.get("rows") != raw_rows
    ):
        raise ResetBlocked(
            "Safe/raw exports are not exact projections of SQLite backup"
        )


def _load_verified_manifest_with_contract(
    archive: Path,
    *,
    expected_contract_binding: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = archive / SNAPSHOT_MANIFEST
    manifest = _load_json_regular(
        manifest_path,
        label="snapshot manifest",
    )
    if (
        manifest.get("kind") != SNAPSHOT_KIND
        or manifest.get("data_exchange_contract")
        != expected_contract_binding
    ):
        raise ResetBlocked(
            "Snapshot manifest несовместим с deployed data-exchange contract"
        )
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != EXPECTED_ARTIFACTS:
        raise ResetBlocked(
            "Snapshot manifest содержит неполный или неожиданный набор артефактов"
        )
    counts = manifest.get("counts")
    if (
        not isinstance(counts, dict)
        or set(counts) != RESET_TABLES
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counts.values()
        )
    ):
        raise ResetBlocked("Snapshot manifest содержит некорректные counts")
    if (
        manifest.get("safe_rows") != counts["clients"]
        or manifest.get("raw_rows") != counts["clients"]
    ):
        raise ResetBlocked("Snapshot manifest safe/raw counts не совпали с clients")
    photos = manifest.get("photos")
    state_digest = manifest.get("database_state_digest")
    if (
        not isinstance(photos, dict)
        or not isinstance(photos.get("root_exists"), bool)
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
    _verify_backup_and_exports(
        archive,
        manifest,
        expected_contract_binding=expected_contract_binding,
    )
    photo_artifact = _load_json_gz_regular(
        archive / PHOTO_MANIFEST_NAME,
        label="photo manifest",
    )
    if (
        photo_artifact.get("root_exists") != photos["root_exists"]
        or photo_artifact.get("files") != photos.get("files")
        or photo_artifact.get("bytes") != photos.get("bytes")
        or photo_artifact.get("digest") != photos["digest"]
    ):
        raise ResetBlocked("Photo manifest artifact не совпал со snapshot manifest")
    entries = photo_artifact.get("entries")
    if not isinstance(entries, list):
        raise ResetBlocked("Photo manifest entries must be a list")
    _verify_photo_archive(archive / PHOTO_ARCHIVE_NAME, entries)
    token_source = {
        "data_exchange_contract": expected_contract_binding,
        "artifacts": artifacts,
        "counts": counts,
        "database_state_digest": state_digest,
        "photos_digest": photos["digest"],
        "photos_root_exists": photos["root_exists"],
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


def _load_verified_manifest(archive: Path) -> dict[str, Any]:
    """Verify every new operation against only the current contract binding."""

    return _load_verified_manifest_with_contract(
        archive,
        expected_contract_binding=DATA_EXCHANGE_CONTRACT,
    )


def _load_verified_recovery_manifest(
    archive: Path,
    *,
    apply_started_schema: str,
) -> dict[str, Any]:
    """Allow the historical buggy binding only to undo a durable v2 apply."""

    if apply_started_schema not in {
        LEGACY_APPLY_STARTED_SCHEMA,
        APPLY_STARTED_SCHEMA,
    }:
        raise ResetBlocked("Apply-started receipt has an unknown schema")
    preview = _load_json_regular(
        archive / SNAPSHOT_MANIFEST,
        label="snapshot manifest",
    )
    if not isinstance(preview, dict):
        raise ResetBlocked("Snapshot manifest has an invalid schema")
    binding = preview.get("data_exchange_contract")
    if binding == DATA_EXCHANGE_CONTRACT:
        expected = DATA_EXCHANGE_CONTRACT
    elif (
        apply_started_schema == LEGACY_APPLY_STARTED_SCHEMA
        and binding == LEGACY_V2_RECOVERY_DATA_EXCHANGE_CONTRACT
    ):
        expected = LEGACY_V2_RECOVERY_DATA_EXCHANGE_CONTRACT
    else:
        raise ResetBlocked(
            "Snapshot manifest is incompatible with recovery contract"
        )
    return _load_verified_manifest_with_contract(
        archive,
        expected_contract_binding=expected,
    )


def _load_verified_clearance(
    archive: Path,
    manifest: dict[str, Any],
    *,
    claimed_digest: str,
    claimed_receipt_sha256: str,
) -> dict[str, Any]:
    """Validate the portable reset binding; local CLI validates source files."""

    def valid_sha(value: Any) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value)
        )

    path = archive / CLEARANCE_RECEIPT_NAME
    if path.is_symlink() or not path.is_file():
        raise ResetBlocked("Verified registry clearance receipt отсутствует")
    if _sha256_file(path) != claimed_receipt_sha256:
        raise ResetBlocked("Clearance receipt SHA-256 не совпал")
    receipt = _load_json_regular(path, label="registry clearance")
    if (
        receipt.get("schema") != CLEARANCE_RECEIPT_SCHEMA
        or receipt.get("data_exchange_contract") != DATA_EXCHANGE_CONTRACT
    ):
        raise ResetBlocked("Неизвестная schema registry clearance")
    embedded_digest = receipt.get("clearance_digest")
    unsigned = {
        key: value
        for key, value in receipt.items()
        if key != "clearance_digest"
    }
    actual_digest = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        not claimed_digest
        or claimed_digest != embedded_digest
        or actual_digest != embedded_digest
    ):
        raise ResetBlocked("Canonical registry clearance digest не совпал")

    snapshot = receipt.get("snapshot")
    provenance = receipt.get("provenance")
    coverage = receipt.get("coverage")
    enrichment = receipt.get("enrichment")
    registry = receipt.get("registry")
    if not all(
        isinstance(item, dict)
        for item in (snapshot, provenance, coverage, enrichment, registry)
    ):
        raise ResetBlocked("Clearance receipt содержит неполные evidence sections")
    expected_snapshot = {
        "manifest_sha256": _sha256_file(archive / SNAPSHOT_MANIFEST),
        "confirm_token": manifest["confirm_token"],
        "database_state_digest": manifest["database_state_digest"],
        "safe_export_sha256": manifest["artifacts"][SAFE_EXPORT_NAME],
        "raw_export_sha256": manifest["artifacts"][RAW_EXPORT_NAME],
        "data_exchange_contract_sha256": DATA_EXCHANGE_CONTRACT["sha256"],
    }
    if any(snapshot.get(key) != value for key, value in expected_snapshot.items()):
        raise ResetBlocked("Registry clearance относится к другому reset snapshot")
    if (
        provenance.get("clients") != manifest["counts"]["clients"]
        or provenance.get("safe_rows") != manifest["safe_rows"]
        or provenance.get("raw_rows") != manifest["raw_rows"]
        or provenance.get("completed_cases") != coverage.get("completed_cases")
    ):
        raise ResetBlocked("Registry clearance provenance counts не совпали")
    zero_invariants = {
        "invalid_completed_cases": provenance.get("invalid_completed_cases"),
        "orphan_completed_cases": provenance.get("orphan_completed_cases"),
        "unprovenanced_visit_rows": provenance.get(
            "unprovenanced_visit_rows"
        ),
        "uncovered_cases": coverage.get("uncovered_cases"),
        "duplicate_coverage_cases": coverage.get("duplicate_coverage_cases"),
        "coverage_orphan_completed_cases": coverage.get(
            "orphan_completed_cases"
        ),
        "failed_files": coverage.get("failed_files"),
        "missing_columns": coverage.get("missing_columns"),
        "post_plan_writes": coverage.get("post_plan_writes"),
        "post_run_changes": registry.get("post_run_changes"),
        "unfinished_publish_journals": registry.get(
            "unfinished_publish_journals"
        ),
    }
    if any(value != 0 for value in zero_invariants.values()):
        raise ResetBlocked(f"Registry clearance zero-invariants нарушены: {zero_invariants}")
    if coverage.get("covered_cases") != coverage.get("completed_cases"):
        raise ResetBlocked("Registry clearance coverage неполный")
    mode = registry.get("mode")
    expected_roles = {
        "registry_snapshot",
        "plan",
        "stage",
        "validation",
        "post_snapshot",
        "post_plan",
        "post_stage",
        "post_validation",
        "active_baseline",
    }
    if mode == "published":
        expected_roles.add("publication")
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != expected_roles:
        raise ResetBlocked("Registry clearance artifact set неточный")
    for role, item in artifacts.items():
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or not isinstance(item.get("path"), str)
            or not item["path"]
            or not valid_sha(item.get("sha256"))
        ):
            raise ResetBlocked(f"Registry clearance artifact некорректен: {role}")
    if (
        not valid_sha(enrichment.get("evidence_sha256"))
        or not valid_sha(provenance.get("completed_contract_ids_digest"))
        or mode not in {"published", "already_zero"}
        or not valid_sha(registry.get("plan_digest"))
        or not valid_sha(registry.get("post_run_digest"))
        or not valid_sha(registry.get("active_baseline_sha256"))
        or not valid_sha(registry.get("active_baseline_plan_digest"))
        or not valid_sha(registry.get("post_run_master_sha256"))
    ):
        raise ResetBlocked("Registry clearance evidence hashes неполны")
    return receipt


def _valid_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _load_verified_archive_only_authorization(
    archive: Path,
    manifest: dict[str, Any],
    *,
    claimed_digest: str,
    claimed_receipt_sha256: str,
) -> dict[str, Any]:
    """Validate the separate owner decision for an obsolete archived dataset."""

    path = archive / ARCHIVE_ONLY_AUTHORIZATION_NAME
    if path.is_symlink() or not path.is_file():
        raise ResetBlocked("Archive-only owner authorization отсутствует")
    if (
        not _valid_sha(claimed_receipt_sha256)
        or _sha256_file(path) != claimed_receipt_sha256
    ):
        raise ResetBlocked("Archive-only authorization SHA-256 не совпал")
    receipt = _load_json_regular(
        path,
        label="archive-only owner authorization",
    )
    base_required = {
        "schema",
        "kind",
        "authorized_at_utc",
        "nonce",
        "data_exchange_contract",
        "snapshot",
        "prepare",
        "decision",
        "authorization_digest",
    }
    if not isinstance(receipt, dict) or set(receipt) != base_required:
        raise ResetBlocked("Archive-only authorization имеет неточную schema")
    authorized_at = receipt.get("authorized_at_utc")
    try:
        datetime.strptime(str(authorized_at or ""), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ResetBlocked(
            "Archive-only authorization timestamp некорректен"
        ) from exc
    if (
        receipt.get("schema") != ARCHIVE_ONLY_AUTHORIZATION_SCHEMA
        or receipt.get("kind") != ARCHIVE_ONLY_AUTHORIZATION_KIND
        or receipt.get("data_exchange_contract") != DATA_EXCHANGE_CONTRACT
        or not _valid_sha(receipt.get("nonce"))
        or receipt.get("decision") != _archive_only_decision()
    ):
        raise ResetBlocked(
            "Archive-only authorization identity/decision mismatch"
        )
    embedded_digest = receipt.get("authorization_digest")
    unsigned = {
        key: value
        for key, value in receipt.items()
        if key != "authorization_digest"
    }
    if (
        not _valid_sha(claimed_digest)
        or embedded_digest != claimed_digest
        or _canonical_digest(unsigned) != claimed_digest
    ):
        raise ResetBlocked("Archive-only authorization digest не совпал")

    backup = archive / BACKUP_NAME
    connection = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        retained = _retained_import_state(connection)
    finally:
        connection.close()
    expected_snapshot = {
        "manifest_sha256": _sha256_file(
            archive / SNAPSHOT_MANIFEST,
            label="snapshot manifest",
        ),
        "confirm_token": manifest["confirm_token"],
        "database_state_digest": manifest["database_state_digest"],
        "counts": manifest["counts"],
        "artifacts": manifest["artifacts"],
        "photos": manifest["photos"],
        "import_authorizations": retained,
    }
    if receipt.get("snapshot") != expected_snapshot:
        raise ResetBlocked(
            "Archive-only authorization относится к другому reset snapshot"
        )
    prepare = receipt.get("prepare")
    expected_remote_archive = (
        PurePosixPath("/opt/hardbot2/data/backups") / archive.name
    )
    if (
        not archive.name.startswith("limited-test-archive-only-")
        or not isinstance(prepare, dict)
        or set(prepare)
        != {
            "receipt_sha256",
            "remote_archive",
            "container_id",
            "image_id",
            "release_git_head",
        }
        or not _valid_sha(prepare.get("receipt_sha256"))
        or prepare.get("remote_archive") != str(expected_remote_archive)
        or len(str(prepare.get("container_id") or "")) != 64
        or any(
            character not in "0123456789abcdef"
            for character in str(prepare.get("container_id") or "")
        )
        or not str(prepare.get("image_id") or "").startswith("sha256:")
        or len(str(prepare.get("image_id"))) != 71
        or any(
            character not in "0123456789abcdef"
            for character in str(prepare.get("image_id"))[7:]
        )
        or len(str(prepare.get("release_git_head") or "")) not in {40, 64}
        or any(
            character not in "0123456789abcdef"
            for character in str(prepare.get("release_git_head") or "")
        )
    ):
        raise ResetBlocked(
            "Archive-only authorization prepare/runtime binding некорректен"
        )
    prepare_path = archive / PREPARE_RECEIPT_NAME
    if prepare_path.is_symlink() or not prepare_path.is_file():
        raise ResetBlocked("Immutable reset prepare receipt отсутствует")
    if _sha256_file(
        prepare_path,
        label="reset prepare receipt",
    ) != prepare["receipt_sha256"]:
        raise ResetBlocked("Reset prepare receipt SHA-256 не совпал")
    prepare_receipt = _load_json_regular(
        prepare_path,
        label="reset prepare receipt",
    )
    prepare_required = {
        "schema",
        "prepared_at_utc",
        "remote_archive",
        "local_archive",
        "snapshot_manifest_sha256",
        "confirm_token",
        "container_id",
        "image_id",
        "release_git_head",
    }
    try:
        datetime.strptime(
            str(prepare_receipt.get("prepared_at_utc") or ""),
            "%Y%m%dT%H%M%SZ",
        )
    except (AttributeError, ValueError) as exc:
        raise ResetBlocked("Reset prepare receipt timestamp некорректен") from exc
    if (
        not isinstance(prepare_receipt, dict)
        or set(prepare_receipt) != prepare_required
        or prepare_receipt.get("schema") != "hardbot.reset-prepare.v1"
        or prepare_receipt.get("remote_archive")
        != str(expected_remote_archive)
        or not str(prepare_receipt.get("local_archive") or "")
        or prepare_receipt.get("snapshot_manifest_sha256")
        != expected_snapshot["manifest_sha256"]
        or prepare_receipt.get("confirm_token") != manifest["confirm_token"]
        or any(
            prepare_receipt.get(key) != prepare.get(key)
            for key in ("container_id", "image_id", "release_git_head")
        )
    ):
        raise ResetBlocked(
            "Immutable reset prepare receipt binding не совпал"
        )
    return receipt


def _load_verified_intent(
    archive: Path,
    manifest: dict[str, Any],
    *,
    clearance_digest: str,
    clearance_receipt_sha256: str,
    claimed_intent_digest: str,
    authority_mode: str,
) -> dict[str, Any]:
    path = archive / ORCHESTRATION_INTENT
    intent = _load_json_regular(path, label="orchestration intent")
    required = {
        "schema",
        "created_at_utc",
        "nonce",
        "archive",
        "confirm_token",
        "snapshot_manifest_sha256",
        "clearance_digest",
        "clearance_receipt_sha256",
        "authority_mode",
        "container_id",
        "image_id",
        "app_stopped",
        "intent_digest",
    }
    if not isinstance(intent, dict) or set(intent) != required:
        raise ResetBlocked("Orchestration intent has an inexact schema")
    embedded = intent.get("intent_digest")
    unsigned = {
        key: value
        for key, value in intent.items()
        if key != "intent_digest"
    }
    if (
        intent.get("schema") != ORCHESTRATION_INTENT_SCHEMA
        or not _valid_sha(embedded)
        or embedded != claimed_intent_digest
        or _canonical_digest(unsigned) != embedded
        or intent.get("archive") != str(archive)
        or intent.get("confirm_token") != manifest["confirm_token"]
        or intent.get("snapshot_manifest_sha256")
        != _sha256_file(
            archive / SNAPSHOT_MANIFEST,
            label="snapshot manifest",
        )
        or intent.get("clearance_digest") != clearance_digest
        or intent.get("clearance_receipt_sha256")
        != clearance_receipt_sha256
        or intent.get("authority_mode") != authority_mode
        or authority_mode not in AUTHORITY_MODES
        or intent.get("app_stopped") is not True
        or not _valid_sha(intent.get("nonce"))
        or not str(intent.get("container_id") or "")
        or not str(intent.get("image_id") or "").startswith("sha256:")
    ):
        raise ResetBlocked("Orchestration intent binding/digest mismatch")
    intent["_sha256"] = _sha256_file(path, label="orchestration intent")
    return intent


def _reset_binding(
    archive: Path,
    manifest: dict[str, Any],
    *,
    clearance_digest: str,
    clearance_receipt_sha256: str,
    intent: dict[str, Any],
    authority_mode: str,
) -> dict[str, Any]:
    if authority_mode not in AUTHORITY_MODES:
        raise ResetBlocked("Неизвестный reset authority mode")
    return {
        "snapshot_manifest_sha256": _sha256_file(
            archive / SNAPSHOT_MANIFEST,
            label="snapshot manifest",
        ),
        "confirm_token": manifest["confirm_token"],
        "clearance_digest": clearance_digest,
        "clearance_receipt_sha256": clearance_receipt_sha256,
        "authority_mode": authority_mode,
        "orchestration_intent_digest": intent["intent_digest"],
        "orchestration_intent_sha256": intent["_sha256"],
        "orchestration_nonce": intent["nonce"],
        "container_id": intent["container_id"],
        "image_id": intent["image_id"],
    }


def _signed_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "receipt_digest": _canonical_digest(payload)}


def _load_apply_started(
    archive: Path,
    manifest: dict[str, Any],
    *,
    claimed_intent_digest: str,
) -> dict[str, Any]:
    path = archive / APPLY_STARTED_MANIFEST
    started = _load_json_regular(path, label="apply-started receipt")
    base_required = {
        "schema",
        "kind",
        "started_at",
        "snapshot_manifest_sha256",
        "confirm_token",
        "clearance_digest",
        "clearance_receipt_sha256",
        "orchestration_intent_digest",
        "orchestration_intent_sha256",
        "orchestration_nonce",
        "container_id",
        "image_id",
        "receipt_digest",
    }
    if not isinstance(started, dict):
        raise ResetBlocked("Apply-started receipt has an inexact schema")
    if started.get("schema") == APPLY_STARTED_SCHEMA:
        required = base_required | {"authority_mode"}
        authority_mode = started.get("authority_mode")
    elif started.get("schema") == LEGACY_APPLY_STARTED_SCHEMA:
        # Before archive-only existed, every v2 receipt was registry-cleared.
        required = base_required
        authority_mode = AUTHORITY_MODE_REGISTRY
    else:
        raise ResetBlocked("Apply-started receipt has an unknown schema")
    if set(started) != required:
        raise ResetBlocked("Apply-started receipt has an inexact schema")
    unsigned = {
        key: value
        for key, value in started.items()
        if key != "receipt_digest"
    }
    if (
        started.get("kind") != "hardbot_limited_test_apply_started"
        or authority_mode not in AUTHORITY_MODES
        or not _valid_sha(started.get("receipt_digest"))
        or _canonical_digest(unsigned) != started["receipt_digest"]
        or started.get("snapshot_manifest_sha256")
        != _sha256_file(
            archive / SNAPSHOT_MANIFEST,
            label="snapshot manifest",
        )
        or started.get("confirm_token") != manifest["confirm_token"]
        or started.get("orchestration_intent_digest")
        != claimed_intent_digest
        or not _valid_sha(started.get("clearance_digest"))
        or not _valid_sha(started.get("clearance_receipt_sha256"))
        or not _valid_sha(started.get("orchestration_intent_sha256"))
        or not _valid_sha(started.get("orchestration_nonce"))
        or not str(started.get("container_id") or "")
        or not str(started.get("image_id") or "").startswith("sha256:")
    ):
        raise ResetBlocked("Apply-started receipt binding/digest mismatch")
    return {**started, "authority_mode": authority_mode}


def _sync_database_storage() -> None:
    database = Path(db.DB_PATH)
    for path in (
        database,
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
    ):
        if path.is_symlink():
            raise ResetBlocked(f"Symlink SQLite storage запрещён: {path}")
        if path.exists():
            if not path.is_file():
                raise ResetBlocked(f"Некорректный SQLite storage: {path}")
            _fsync_file(path)
    _fsync_directory(database.parent)


def _durable_photo_quarantine(photo_root: Path, archived_photos: Path) -> bool:
    if photo_root.is_symlink():
        raise ResetBlocked(f"Symlink photo storage запрещён: {photo_root}")
    if archived_photos.exists() or archived_photos.is_symlink():
        raise ResetBlocked(f"Photo quarantine уже существует: {archived_photos}")
    if not photo_root.exists():
        return False
    if not photo_root.is_dir():
        raise ResetBlocked(f"Photo storage не является каталогом: {photo_root}")
    if archived_photos.parent.is_symlink() or not archived_photos.parent.is_dir():
        raise ResetBlocked(
            f"Небезопасный родитель photo quarantine: {archived_photos.parent}"
        )
    os.replace(photo_root, archived_photos)
    _fsync_directory(photo_root.parent)
    if archived_photos.parent != photo_root.parent:
        _fsync_directory(archived_photos.parent)
    return True


def _restore_photos(
    archived_photos: Path,
    *,
    root_existed: bool,
    expected_digest: str,
) -> None:
    photo_root = Path(PHOTO_DIR)
    if photo_root.is_symlink():
        raise ResetBlocked(f"Symlink photo storage запрещён: {photo_root}")
    if not archived_photos.exists():
        current = _photo_manifest(photo_root)
        if current["digest"] != expected_digest:
            raise ResetBlocked(
                "Photo quarantine отсутствует, а live photo storage не равен pre-state"
            )
        if current["root_exists"] == root_existed:
            return
        if root_existed:
            raise ResetBlocked("Исходный photo storage отсутствует в quarantine")
        if photo_root.exists():
            if not photo_root.is_dir() or any(photo_root.iterdir()):
                raise ResetBlocked(
                    "Новый photo storage не пуст; автоматическое удаление запрещено"
                )
            photo_root.rmdir()
            _fsync_directory(photo_root.parent)
        return
    if photo_root.exists():
        if not photo_root.is_dir() or any(photo_root.iterdir()):
            raise ResetBlocked(
                "Новый photo storage не пуст; автоматическое удаление запрещено"
            )
        photo_root.rmdir()
        _fsync_directory(photo_root.parent)
    if archived_photos.exists():
        if archived_photos.is_symlink() or not archived_photos.is_dir():
            raise ResetBlocked(f"Некорректный photo quarantine: {archived_photos}")
        os.replace(archived_photos, photo_root)
        _fsync_directory(archived_photos.parent)
        if photo_root.parent != archived_photos.parent:
            _fsync_directory(photo_root.parent)
        _fsync_directory(photo_root)


def _restore_database(backup: Path) -> None:
    database = Path(db.DB_PATH)
    if (
        not backup.is_file()
        or backup.is_symlink()
        or database.is_symlink()
        or not database.parent.is_dir()
        or database.parent.is_symlink()
    ):
        raise ResetBlocked("Небезопасный SQLite backup/target для recovery")
    source = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    destination = sqlite3.connect(database, timeout=30)
    try:
        destination.execute("PRAGMA synchronous=FULL")
        destination.execute("PRAGMA busy_timeout=30000")
        source.backup(destination)
        destination.commit()
        destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Recovery SQLite quick_check не вернул ok")
    finally:
        destination.close()
        source.close()
    _sync_database_storage()
    # Кэш ролей: на живой процесс бота операторский CLI не влияет (контейнер
    # остановлен по процедуре) — строка нужна внутрипроцессным прогонам pytest
    # и на случай вызова сброса из живого приложения.
    db.reset_role_caches()


def _fault_point(_name: str) -> None:
    """No-op production hook; tests replace it to emulate abrupt power loss."""


def _apply_authorized_reset(
    archive_raw: str | os.PathLike[str],
    *,
    confirm_token: str,
    clearance_digest: str,
    clearance_receipt_sha256: str,
    orchestration_intent_digest: str,
    app_stopped: bool,
    authority_mode: str,
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
    if authority_mode == AUTHORITY_MODE_REGISTRY:
        clearance = _load_verified_clearance(
            archive,
            manifest,
            claimed_digest=clearance_digest,
            claimed_receipt_sha256=clearance_receipt_sha256,
        )
        verified_clearance_digest = clearance["clearance_digest"]
    elif authority_mode == AUTHORITY_MODE_ARCHIVE_ONLY:
        clearance = _load_verified_archive_only_authorization(
            archive,
            manifest,
            claimed_digest=clearance_digest,
            claimed_receipt_sha256=clearance_receipt_sha256,
        )
        verified_clearance_digest = clearance["authorization_digest"]
    else:
        raise ResetBlocked("Неизвестный reset authority mode")
    intent = _load_verified_intent(
        archive,
        manifest,
        clearance_digest=verified_clearance_digest,
        clearance_receipt_sha256=clearance_receipt_sha256,
        claimed_intent_digest=orchestration_intent_digest,
        authority_mode=authority_mode,
    )
    if authority_mode == AUTHORITY_MODE_ARCHIVE_ONLY:
        prepare = clearance["prepare"]
        if (
            prepare["container_id"] != intent["container_id"]
            or prepare["image_id"] != intent["image_id"]
        ):
            raise ResetBlocked(
                "Archive-only authorization runtime не совпал с orchestration intent"
            )
    binding = _reset_binding(
        archive,
        manifest,
        clearance_digest=verified_clearance_digest,
        clearance_receipt_sha256=clearance_receipt_sha256,
        intent=intent,
        authority_mode=authority_mode,
    )
    if any(
        (archive / marker_name).exists()
        or (archive / marker_name).is_symlink()
        for marker_name in (
            POST_RESET_MANIFEST,
            APPLY_STARTED_MANIFEST,
            RECOVERY_MANIFEST,
        )
    ):
        raise ResetBlocked("Этот snapshot уже использован для reset")

    conn = db.connect()
    try:
        _require_known_schema(conn)
        current_counts = _table_counts(conn)
        current_digest = _database_state_digest(conn)
        retained_before = _retained_import_state(conn)
    finally:
        conn.close()
    if current_counts != manifest["counts"] or current_digest != manifest[
        "database_state_digest"
    ]:
        raise ResetBlocked(
            "БД изменилась после snapshot; требуется новый snapshot и новая проверка"
        )

    current_photos = _photo_manifest()
    if (
        current_photos["digest"] != manifest["photos"]["digest"]
        or current_photos["root_exists"]
        != manifest["photos"]["root_exists"]
    ):
        raise ResetBlocked(
            "Photo storage изменился после snapshot; требуется новый snapshot"
        )

    photo_root = Path(PHOTO_DIR)
    archived_photos = archive / "photos-before-reset"
    backup = archive / BACKUP_NAME
    photos_moved = False
    apply_started = False
    terminal_committed = False
    try:
        _write_json(
            archive / APPLY_STARTED_MANIFEST,
            _signed_receipt({
                "schema": APPLY_STARTED_SCHEMA,
                "kind": "hardbot_limited_test_apply_started",
                "started_at": _utc_now(),
                **binding,
            }),
        )
        apply_started = True
        _fault_point("after_apply_started")
        photos_moved = _durable_photo_quarantine(photo_root, archived_photos)
        photo_root.mkdir(parents=True, exist_ok=True)
        _private_mode(photo_root)
        _fsync_directory(photo_root)
        _fsync_directory(photo_root.parent)
        _fault_point("after_photo_quarantine")

        conn = db.connect()
        try:
            _require_known_schema(conn)
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            for table in DELETE_ORDER:
                conn.execute(f'DELETE FROM "{table}"')
            pending_invalidated = conn.execute(
                "DELETE FROM import_receipts WHERE status='pending'"
            ).rowcount
            if conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='sqlite_sequence'"
            ).fetchone():
                conn.execute("DELETE FROM sqlite_sequence")
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("quick_check после reset не вернул ok")
            post_counts = _table_counts(conn)
            retained_after = _retained_import_state(conn)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        _sync_database_storage()
        db.reset_role_caches()   # managers очищены — индекс адресатов заново
        _fault_point("after_database_reset")

        nonzero = {name: count for name, count in post_counts.items() if count}
        if nonzero:
            raise RuntimeError(f"После reset остались строки: {nonzero}")
        if (
            retained_after["pending"] != 0
            or pending_invalidated != retained_before["pending"]
            or {
                key: retained_after[key]
                for key in (
                    "consumed",
                    "consumed_digest",
                    "heads",
                    "heads_digest",
                )
            }
            != {
                key: retained_before[key]
                for key in (
                    "consumed",
                    "consumed_digest",
                    "heads",
                    "heads_digest",
                )
            }
        ):
            raise RuntimeError(
                "Anti-replay ledger не сохранил consumed receipts/source heads "
                "или не очистил pending authorizations"
            )
        if any(photo_root.iterdir()):
            raise RuntimeError("Новый photo storage не пуст")

        post = _signed_receipt({
            "schema": POST_RESET_SCHEMA,
            "kind": "hardbot_limited_test_post_reset",
            "completed_at": _utc_now(),
            "pre_reset_counts": manifest["counts"],
            "post_reset_counts": post_counts,
            "database_quick_check": "ok",
            "import_authorizations": {
                "pending_invalidated": pending_invalidated,
                **retained_after,
            },
            "photos_quarantined": current_photos["files"],
            "photo_quarantine": str(archived_photos),
            "rollback_database": str(backup),
            **binding,
        })
        try:
            _write_json(archive / POST_RESET_MANIFEST, post)
            terminal_committed = True
        except Exception:
            # If replace succeeded but the directory fsync/reporting failed,
            # the exact terminal receipt must win over rollback.  On power
            # loss it either survives with the post-state or disappears and
            # the durable apply-started receipt drives recovery.
            try:
                durable = _load_json_regular(
                    archive / POST_RESET_MANIFEST,
                    label="post-reset receipt",
                )
            except ResetBlocked:
                durable = None
            if durable == post:
                terminal_committed = True
            raise
        _fault_point("after_post_receipt")
        return post
    except Exception:
        if terminal_committed:
            raise
        if apply_started:
            _restore_database(backup)
        if photos_moved:
            archived_manifest = _photo_manifest(archived_photos)
            if archived_manifest["digest"] != manifest["photos"]["digest"]:
                raise RuntimeError(
                    "Photo quarantine повреждён; автоматический rollback запрещён"
                )
        if apply_started:
            _restore_photos(
                archived_photos,
                root_existed=bool(manifest["photos"]["root_exists"]),
                expected_digest=str(manifest["photos"]["digest"]),
            )
            conn = db.connect()
            try:
                recovered_counts = _table_counts(conn)
                recovered_digest = _database_state_digest(conn)
                recovered_quick = conn.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                conn.close()
            recovered_photos = _photo_manifest()
            if (
                recovered_counts != manifest["counts"]
                or recovered_digest != manifest["database_state_digest"]
                or recovered_quick != "ok"
                or recovered_photos["digest"] != manifest["photos"]["digest"]
                or recovered_photos["root_exists"]
                != manifest["photos"]["root_exists"]
            ):
                raise RuntimeError(
                    "Автоматический rollback reset не восстановил exact pre-state"
                )
            _write_json(
                archive / RECOVERY_MANIFEST,
                _signed_receipt({
                    "schema": RECOVERY_SCHEMA,
                    "kind": "hardbot_limited_test_recovery",
                    "completed_at": _utc_now(),
                    "reason": "automatic_apply_rollback",
                    "counts": recovered_counts,
                    "database_quick_check": recovered_quick,
                    "photos": {
                        "root_exists": recovered_photos["root_exists"],
                        "files": recovered_photos["files"],
                        "bytes": recovered_photos["bytes"],
                        "digest": recovered_photos["digest"],
                    },
                    **binding,
                }),
            )
        raise


def apply_reset(
    archive_raw: str | os.PathLike[str],
    *,
    confirm_token: str,
    clearance_digest: str,
    clearance_receipt_sha256: str,
    orchestration_intent_digest: str,
    app_stopped: bool,
) -> dict[str, Any]:
    """Apply the existing registry-clearance reset path unchanged."""

    return _apply_authorized_reset(
        archive_raw,
        confirm_token=confirm_token,
        clearance_digest=clearance_digest,
        clearance_receipt_sha256=clearance_receipt_sha256,
        orchestration_intent_digest=orchestration_intent_digest,
        app_stopped=app_stopped,
        authority_mode=AUTHORITY_MODE_REGISTRY,
    )


def apply_archive_only_reset(
    archive_raw: str | os.PathLike[str],
    *,
    confirm_token: str,
    authorization_digest: str,
    authorization_receipt_sha256: str,
    orchestration_intent_digest: str,
    app_stopped: bool,
) -> dict[str, Any]:
    """Clear an explicitly obsolete dataset after exact archive authorization."""

    return _apply_authorized_reset(
        archive_raw,
        confirm_token=confirm_token,
        clearance_digest=authorization_digest,
        clearance_receipt_sha256=authorization_receipt_sha256,
        orchestration_intent_digest=orchestration_intent_digest,
        app_stopped=app_stopped,
        authority_mode=AUTHORITY_MODE_ARCHIVE_ONLY,
    )


def recover_incomplete_reset(
    archive_raw: str | os.PathLike[str],
    *,
    confirm_token: str,
    orchestration_intent_digest: str,
    app_stopped: bool,
) -> dict[str, Any]:
    """Restore the pre-reset snapshot after an interrupted destructive process."""

    if not app_stopped:
        raise ResetBlocked(
            "Основной контейнер должен быть остановлен; предъявите --app-stopped"
        )
    archive = _validated_archive_dir(archive_raw, create=False)
    if (
        (archive / POST_RESET_MANIFEST).exists()
        or (archive / POST_RESET_MANIFEST).is_symlink()
    ):
        raise ResetBlocked("Reset завершён; incomplete recovery запрещён")
    started = archive / APPLY_STARTED_MANIFEST
    if not started.is_file():
        raise ResetBlocked("Нет durable evidence начала destructive reset")
    started_preview = _load_json_regular(
        started,
        label="apply-started receipt",
    )
    if not isinstance(started_preview, dict):
        raise ResetBlocked("Apply-started receipt has an inexact schema")
    started_schema = started_preview.get("schema")
    manifest = _load_verified_recovery_manifest(
        archive,
        apply_started_schema=str(started_schema or ""),
    )
    if not confirm_token or confirm_token != manifest.get("confirm_token"):
        raise ResetBlocked("Confirm-token не совпал со snapshot manifest")
    started_payload = _load_apply_started(
        archive,
        manifest,
        claimed_intent_digest=orchestration_intent_digest,
    )
    if started_payload.get("schema") != started_schema:
        raise ResetBlocked(
            "Apply-started receipt changed during recovery verification"
        )
    binding = {
        key: started_payload[key]
        for key in (
            "snapshot_manifest_sha256",
            "confirm_token",
            "clearance_digest",
            "clearance_receipt_sha256",
            "authority_mode",
            "orchestration_intent_digest",
            "orchestration_intent_sha256",
            "orchestration_nonce",
            "container_id",
            "image_id",
        )
    }
    backup = archive / BACKUP_NAME
    archived_photos = archive / "photos-before-reset"
    if archived_photos.exists():
        archived_manifest = _photo_manifest(archived_photos)
        if archived_manifest["digest"] != manifest["photos"]["digest"]:
            raise ResetBlocked("Photo quarantine не совпал со snapshot manifest")
    _restore_database(backup)
    _restore_photos(
        archived_photos,
        root_existed=bool(manifest["photos"]["root_exists"]),
        expected_digest=str(manifest["photos"]["digest"]),
    )

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
        or photos["root_exists"] != manifest["photos"]["root_exists"]
    ):
        raise RuntimeError("Interrupted reset recovery не восстановил pre-reset state")
    result = _signed_receipt({
        "schema": RECOVERY_SCHEMA,
        "kind": "hardbot_limited_test_recovery",
        "completed_at": _utc_now(),
        "reason": "operator_interrupted_recovery",
        "counts": counts,
        "database_quick_check": quick,
        "photos": {
            "root_exists": photos["root_exists"],
            "files": photos["files"],
            "bytes": photos["bytes"],
            "digest": photos["digest"],
        },
        **binding,
    })
    _write_json(archive / RECOVERY_MANIFEST, result)
    return result


def current_status() -> dict[str, Any]:
    conn = db.connect()
    try:
        counts = _table_counts(conn)
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        import_authorizations = _retained_import_state(conn)
    finally:
        conn.close()
    photos = _photo_manifest()
    return {
        "database": str(Path(db.DB_PATH).resolve()),
        "quick_check": quick,
        "counts": counts,
        "import_authorizations": import_authorizations,
        "photos": {
            "root_exists": photos["root_exists"],
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
    apply.add_argument("--clearance-digest", required=True)
    apply.add_argument("--clearance-receipt-sha256", required=True)
    apply.add_argument("--orchestration-intent-digest", required=True)
    apply.add_argument("--app-stopped", action="store_true")
    archive_apply = sub.add_parser(
        "apply-archive-only",
        help="очистить obsolete dataset после отдельного owner authorization",
    )
    archive_apply.add_argument("--archive", required=True)
    archive_apply.add_argument("--confirm-token", required=True)
    archive_apply.add_argument("--authorization-digest", required=True)
    archive_apply.add_argument(
        "--authorization-receipt-sha256",
        required=True,
    )
    archive_apply.add_argument(
        "--orchestration-intent-digest",
        required=True,
    )
    archive_apply.add_argument("--app-stopped", action="store_true")
    recover = sub.add_parser(
        "recover",
        help="восстановить pre-reset state после оборванного apply",
    )
    recover.add_argument("--archive", required=True)
    recover.add_argument("--confirm-token", required=True)
    recover.add_argument("--orchestration-intent-digest", required=True)
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
                clearance_digest=args.clearance_digest,
                clearance_receipt_sha256=args.clearance_receipt_sha256,
                orchestration_intent_digest=args.orchestration_intent_digest,
                app_stopped=args.app_stopped,
            )
        elif args.command == "apply-archive-only":
            result = apply_archive_only_reset(
                args.archive,
                confirm_token=args.confirm_token,
                authorization_digest=args.authorization_digest,
                authorization_receipt_sha256=(
                    args.authorization_receipt_sha256
                ),
                orchestration_intent_digest=args.orchestration_intent_digest,
                app_stopped=args.app_stopped,
            )
        elif args.command == "recover":
            result = recover_incomplete_reset(
                args.archive,
                confirm_token=args.confirm_token,
                orchestration_intent_digest=args.orchestration_intent_digest,
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
