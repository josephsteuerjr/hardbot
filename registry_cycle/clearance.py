"""Evidence-only gate between registry export and destructive hardbot reset.

The module deliberately does not import, run, or reproduce Excel transfer
logic.  It validates artifacts produced by the existing ``pult.enrich`` and
``registry_cycle`` flows, then emits a digest-bound clearance receipt.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pult import enrich as pult_enrich
import limited_test_reset as hardbot_reset

from exchange_contract import (
    CLEARANCE_RECEIPT_SCHEMA,
    CONTRACT_KEY,
    OFFICE_DIRECT_EDIT_KEYS,
    OFFICE_RESULT_SNAPSHOT_KEY,
    PROVENANCE_MARKER,
    SNAPSHOT_KEYS,
    SNAPSHOT_KIND,
    contract_binding,
)
from .cycle_lock import CycleLock
from .publishing import (
    assert_no_incomplete_publications,
    incomplete_publish_journals,
)
from .snapshot import load_snapshot
from .staging import load_stage


RECEIPT_SCHEMA = CLEARANCE_RECEIPT_SCHEMA
ENRICHMENT_SCHEMA = pult_enrich.EVIDENCE_SCHEMA
ENRICHMENT_MAP_SCHEMA = pult_enrich.FILE_MAP_SCHEMA
DATA_EXCHANGE_CONTRACT = contract_binding()
RECEIPT_NAME = "registry-clearance.json"
SNAPSHOT_MANIFEST_NAME = "snapshot_manifest.json"
SAFE_EXPORT_NAME = "safe-enrichment.json.gz"
RAW_EXPORT_NAME = "raw-diagnostic.json.gz"
BACKUP_NAME = "hardbot-pre-reset.sqlite3"
SCHEMA_NAME = "export-schema.json"
PHOTO_MANIFEST_NAME = "photos-manifest.json.gz"
PHOTO_ARCHIVE_NAME = "photos-before-reset.tar.gz"
EXPECTED_SNAPSHOT_ARTIFACTS = frozenset(
    {
        BACKUP_NAME,
        SAFE_EXPORT_NAME,
        RAW_EXPORT_NAME,
        SCHEMA_NAME,
        PHOTO_MANIFEST_NAME,
        PHOTO_ARCHIVE_NAME,
    }
)

VISIT_KEYS = SNAPSHOT_KEYS

if Path(hardbot_reset.__file__).resolve() != (
    Path(__file__).resolve().parents[1]
    / "hardbot"
    / "limited_test_reset.py"
).resolve():
    raise RuntimeError("limited_test_reset imported outside this checkout")


class ClearanceBlocked(RuntimeError):
    """Fail-closed evidence validation error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _is_reparse(stat_result: os.stat_result) -> bool:
    return bool(
        getattr(stat_result, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ClearanceBlocked(f"{label}: файл недоступен: {path}") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or _is_reparse(before)
        or path.is_symlink()
    ):
        raise ClearanceBlocked(f"{label}: нужен обычный non-reparse файл: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ClearanceBlocked(f"{label}: безопасное открытие не удалось: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ClearanceBlocked(f"{label}: файл подменён при открытии: {path}")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ClearanceBlocked(f"{label}: файл изменился во время чтения: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _sha256(path: Path, *, label: str = "artifact") -> str:
    return hashlib.sha256(_read_regular_bytes(path, label=label)).hexdigest()


def _regular_file(raw: str | os.PathLike[str], *, label: str) -> Path:
    path = Path(raw)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ClearanceBlocked(f"{label}: файл недоступен: {path}") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or _is_reparse(info)
        or path.is_symlink()
    ):
        raise ClearanceBlocked(f"{label}: нужен обычный non-reparse файл: {path}")
    resolved = path.resolve()
    if resolved.parent.is_symlink():
        raise ClearanceBlocked(f"{label}: symlink parent запрещён: {resolved.parent}")
    return resolved


def _directory(raw: str | os.PathLike[str], *, label: str) -> Path:
    path = Path(raw)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ClearanceBlocked(f"{label}: каталог недоступен: {path}") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or _is_reparse(info)
        or path.is_symlink()
    ):
        raise ClearanceBlocked(f"{label}: нужен существующий каталог: {path}")
    return path.resolve()


def _load_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(_read_regular_bytes(path, label=label).decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ClearanceBlocked(f"{label}: некорректный JSON: {path}") from exc


def _load_json_gz(path: Path, *, label: str) -> Any:
    try:
        return json.loads(gzip.decompress(_read_regular_bytes(path, label=label)))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ClearanceBlocked(f"{label}: некорректный JSON.gz: {path}") from exc


def _hex(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise ClearanceBlocked(f"{label}: нужен lowercase SHA-256")
    return value


def _integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ClearanceBlocked(f"{label}: нужно неотрицательное целое")
    return value


def _strings(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ClearanceBlocked(f"{label}: нужен список строк")
    if len(value) != len(set(value)):
        raise ClearanceBlocked(f"{label}: дубли запрещены")
    return value


def _contract_id(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _valid_iso_date(value: Any) -> bool:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def _sqlite_state(
    database: Path,
    *,
    expected_tables: set[str],
    counted_tables: set[str],
) -> tuple[dict[str, int], str]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        actual_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if quick != "ok" or integrity != "ok" or actual_tables != expected_tables:
            raise ClearanceBlocked(
                "SQLite backup integrity/schema contract не пройден: "
                f"quick={quick}, integrity={integrity}, "
                f"unknown={sorted(actual_tables - expected_tables)}, "
                f"missing={sorted(expected_tables - actual_tables)}"
            )
        counts: dict[str, int] = {}
        digest = hashlib.sha256()
        for table in sorted(expected_tables):
            if table in counted_tables:
                counts[table] = int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
                )
            columns = [
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
            digest.update(
                json.dumps([table, columns], ensure_ascii=False).encode("utf-8")
            )
            for row in connection.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid'
            ):
                digest.update(
                    json.dumps(
                        list(row),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                )
                digest.update(b"\n")
        return counts, digest.hexdigest()
    finally:
        connection.close()


def _backup_export_rows(
    database: Path,
    field_keys: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            str(row[1])
            for row in connection.execute('PRAGMA table_info("clients")')
        }
        required = set(field_keys) | {
            "bot_visit_at",
            "bot_visit_data",
            "office_edit_data",
            "tips",
        }
        if not required <= columns:
            raise ClearanceBlocked(
                "SQLite clients не содержит полей export/provenance: "
                + ", ".join(sorted(required - columns))
            )
        rows = connection.execute("SELECT * FROM clients ORDER BY client").fetchall()
    finally:
        connection.close()

    raw_rows: list[dict[str, Any]] = []
    safe_rows: list[dict[str, Any]] = []
    bot_keys = set(VISIT_KEYS) - {"tips"}
    office_keys = {
        OFFICE_RESULT_SNAPSHOT_KEY,
        *OFFICE_DIRECT_EDIT_KEYS,
    }
    for row in rows:
        raw = {key: row[key] for key in field_keys}
        safe = dict(raw)
        for key in bot_keys:
            if key in safe:
                safe[key] = None
        snapshot: dict[str, Any] | None = None
        try:
            payload = json.loads(row["bot_visit_data"] or "")
            completed_at = str(row["bot_visit_at"] or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
            completed_at = ""
        if (
            isinstance(payload, dict)
            and _valid_iso_date(completed_at)
            and str(payload.get("visit_date") or "") == completed_at
        ):
            snapshot = {"visit_date": completed_at}
            for key in VISIT_KEYS:
                if key == "visit_date":
                    continue
                value = payload.get(key)
                if not _blank(value):
                    snapshot[key] = str(value).strip()
            try:
                edits = json.loads(row["office_edit_data"] or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                edits = None
            if (
                isinstance(edits, dict)
                and str(edits.get("visit_at") or "") == completed_at
                and isinstance(edits.get("fields"), dict)
            ):
                for key, value in edits["fields"].items():
                    if key in office_keys and not _blank(value):
                        snapshot[key] = str(value).strip()
            tips = str(snapshot.get("tips") or row["tips"] or "").strip()
            if tips:
                snapshot["tips"] = tips
        safe[PROVENANCE_MARKER] = snapshot is not None
        if snapshot:
            safe.update(snapshot)
        raw_rows.append(raw)
        safe_rows.append(safe)
    return safe_rows, raw_rows


def _snapshot_evidence(snapshot_raw: str | os.PathLike[str]) -> dict[str, Any]:
    snapshot = _directory(snapshot_raw, label="snapshot")
    manifest_path = _regular_file(
        snapshot / SNAPSHOT_MANIFEST_NAME,
        label="snapshot manifest",
    )
    manifest = _load_json(manifest_path, label="snapshot manifest")
    if (
        manifest.get("kind") != SNAPSHOT_KIND
        or manifest.get("data_exchange_contract") != DATA_EXCHANGE_CONTRACT
    ):
        raise ClearanceBlocked(
            "Hardbot snapshot несовместим с data-exchange contract"
        )
    artifacts = manifest.get("artifacts")
    counts = manifest.get("counts")
    if not isinstance(artifacts, dict) or not isinstance(counts, dict):
        raise ClearanceBlocked("Snapshot manifest не содержит artifacts/counts")
    if set(artifacts) != EXPECTED_SNAPSHOT_ARTIFACTS:
        raise ClearanceBlocked("Snapshot manifest содержит неточный artifact set")
    if (
        set(counts) != hardbot_reset.RESET_TABLES
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counts.values()
        )
    ):
        raise ClearanceBlocked("Snapshot counts имеют неверную schema")
    clients = _integer(counts.get("clients"), label="snapshot counts.clients")
    database_digest = _hex(
        manifest.get("database_state_digest"),
        label="snapshot database_state_digest",
    )
    safe_hash = _hex(
        artifacts.get(SAFE_EXPORT_NAME),
        label="snapshot safe export SHA",
    )
    raw_hash = _hex(
        artifacts.get(RAW_EXPORT_NAME),
        label="snapshot raw export SHA",
    )
    safe_path = _regular_file(snapshot / SAFE_EXPORT_NAME, label="safe export")
    raw_path = _regular_file(snapshot / RAW_EXPORT_NAME, label="raw export")
    for name, claimed in artifacts.items():
        artifact = _regular_file(snapshot / name, label=f"snapshot artifact {name}")
        if _sha256(artifact, label=f"snapshot artifact {name}") != _hex(
            claimed,
            label=f"snapshot artifact {name} SHA",
        ):
            raise ClearanceBlocked(f"Snapshot artifact SHA не совпал: {name}")
    if _sha256(safe_path) != safe_hash or _sha256(raw_path) != raw_hash:
        raise ClearanceBlocked("Safe/raw export SHA не совпал со snapshot manifest")

    backup_path = _regular_file(snapshot / BACKUP_NAME, label="SQLite backup")
    backup_counts, backup_digest = _sqlite_state(
        backup_path,
        expected_tables=set(hardbot_reset.EXPECTED_TABLES),
        counted_tables=set(hardbot_reset.RESET_TABLES),
    )
    if backup_counts != counts or backup_digest != database_digest:
        raise ClearanceBlocked(
            "SQLite backup counts/logical digest не совпали со snapshot manifest"
        )
    schema_path = _regular_file(snapshot / SCHEMA_NAME, label="export schema")
    schema = _load_json(schema_path, label="export schema")
    if (
        not isinstance(schema, list)
        or not schema
        or any(
            not isinstance(item, dict) or not isinstance(item.get("key"), str)
            for item in schema
        )
    ):
        raise ClearanceBlocked("Export schema некорректна")
    field_keys = [item["key"] for item in schema]
    if len(field_keys) != len(set(field_keys)) or CONTRACT_KEY not in field_keys:
        raise ClearanceBlocked("Export schema содержит duplicate/missing keys")

    safe = _load_json_gz(safe_path, label="safe export")
    raw = _load_json_gz(raw_path, label="raw export")
    if (
        safe.get("kind") != "safe_enrichment"
        or raw.get("kind") != "raw_diagnostic"
        or safe.get("data_exchange_contract") != DATA_EXCHANGE_CONTRACT
        or raw.get("data_exchange_contract") != DATA_EXCHANGE_CONTRACT
    ):
        raise ClearanceBlocked("Safe/raw export имеет неизвестный kind")
    safe_rows = safe.get("rows")
    raw_rows = raw.get("rows")
    if not isinstance(safe_rows, list) or not isinstance(raw_rows, list):
        raise ClearanceBlocked("Safe/raw export rows должны быть списками")
    if len(safe_rows) != clients or len(raw_rows) != clients:
        raise ClearanceBlocked("Safe/raw row count не совпал с snapshot clients")
    if manifest.get("safe_rows") != clients or manifest.get("raw_rows") != clients:
        raise ClearanceBlocked("Snapshot manifest safe/raw counts не совпали")
    expected_safe, expected_raw = _backup_export_rows(backup_path, field_keys)
    if raw_rows != expected_raw or safe_rows != expected_safe:
        raise ClearanceBlocked(
            "Safe/raw exports не являются exact projection SQLite backup"
        )

    safe_by_id: dict[str, dict[str, Any]] = {}
    raw_by_id: dict[str, dict[str, Any]] = {}
    for label, rows, target in (
        ("safe", safe_rows, safe_by_id),
        ("raw", raw_rows, raw_by_id),
    ):
        for row in rows:
            if not isinstance(row, dict):
                raise ClearanceBlocked(f"{label}: строка export не является object")
            contract = _contract_id(row.get(CONTRACT_KEY))
            if not contract or contract in target:
                raise ClearanceBlocked(f"{label}: пустой или duplicate contract_id")
            target[contract] = row
    if set(safe_by_id) != set(raw_by_id):
        raise ClearanceBlocked("Safe/raw export содержат разные множества КД")

    completed: list[str] = []
    completed_by_owner: dict[str, list[str]] = {}
    orphan_completed: list[str] = []
    invalid_completed = 0
    unprovenanced_visit_rows = 0
    for contract, safe_row in safe_by_id.items():
        marker = safe_row.get(PROVENANCE_MARKER)
        if not isinstance(marker, bool):
            raise ClearanceBlocked(f"Safe export не содержит boolean provenance: {contract}")
        raw_row = raw_by_id[contract]
        if (
            set(safe_row) - {PROVENANCE_MARKER, "tips"}
            != set(raw_row) - {"tips"}
        ):
            raise ClearanceBlocked(
                f"Safe/raw имеют разные поля вне service projection: {contract}"
            )
        for key, value in safe_row.items():
            if key in {*VISIT_KEYS, PROVENANCE_MARKER}:
                continue
            if key in raw_row and value != raw_row[key]:
                raise ClearanceBlocked(
                    f"Safe/raw расходятся вне visit-проекции: {contract}/{key}"
                )
        if marker:
            completed.append(contract)
            if not _valid_iso_date(safe_row.get("visit_date")):
                invalid_completed += 1
            if not str(safe_row.get("responsible") or "").strip():
                orphan_completed.append(contract)
            else:
                completed_by_owner.setdefault(
                    str(safe_row["responsible"]).strip(),
                    [],
                ).append(contract)
        elif any(not _blank(safe_row.get(key)) for key in VISIT_KEYS):
            unprovenanced_visit_rows += 1
    completed.sort()
    for owner_contracts in completed_by_owner.values():
        owner_contracts.sort()
    orphan_completed.sort()
    if invalid_completed:
        raise ClearanceBlocked(
            f"Завершённые визиты с невалидной датой: {invalid_completed}"
        )
    if orphan_completed:
        raise ClearanceBlocked(
            "Завершённые визиты без ответственного нельзя очистить: "
            f"{len(orphan_completed)}"
        )
    if unprovenanced_visit_rows:
        raise ClearanceBlocked(
            "Safe export содержит visit-поля без provenance: "
            f"{unprovenanced_visit_rows}"
        )

    return {
        "snapshot_dir": str(snapshot),
        "manifest": manifest,
        "manifest_sha256": _sha256(manifest_path),
        "database_state_digest": database_digest,
        "safe_export_sha256": safe_hash,
        "raw_export_sha256": raw_hash,
        "data_exchange_contract_sha256": DATA_EXCHANGE_CONTRACT["sha256"],
        "clients": clients,
        "safe_rows": len(safe_rows),
        "raw_rows": len(raw_rows),
        "completed_contract_ids": completed,
        "completed_by_owner": completed_by_owner,
        "completed_contract_ids_digest": _digest(completed),
        "invalid_completed": invalid_completed,
        "orphan_completed": len(orphan_completed),
        "unprovenanced_visit_rows": unprovenanced_visit_rows,
        "schema": schema,
        "safe_by_id": safe_by_id,
    }


def enrichment_template(snapshot_raw: str | os.PathLike[str]) -> dict[str, Any]:
    """Return a non-authorizing owner→files map for ``pult.enrich``."""

    snapshot = _snapshot_evidence(snapshot_raw)
    return {
        "schema": ENRICHMENT_MAP_SCHEMA,
        "snapshot": {
            "manifest_sha256": snapshot["manifest_sha256"],
            "database_state_digest": snapshot["database_state_digest"],
            "safe_export_sha256": snapshot["safe_export_sha256"],
            "raw_export_sha256": snapshot["raw_export_sha256"],
            "data_exchange_contract_sha256": snapshot[
                "data_exchange_contract_sha256"
            ],
        },
        "owners": [
            {
                "responsible": responsible,
                "contract_ids": contracts,
                "files": [],
            }
            for responsible, contracts in sorted(
                snapshot["completed_by_owner"].items(),
                key=lambda item: item[0].casefold(),
            )
        ],
    }


def _validate_file_result(
    item: Any,
    *,
    completed: set[str],
) -> tuple[str, str, set[str], dict[str, Any]]:
    if not isinstance(item, dict):
        raise ClearanceBlocked("enrichment files[] должен содержать objects")
    required = {
        "responsible",
        "path",
        "pre_sha256",
        "post_sha256",
        "backup_path",
        "backup_sha256",
        "owner_contract_ids",
        "filled_cases",
        "filled_contract_ids",
        "matched_cases",
        "matched_contract_ids",
        "changed_cases",
        "cells_written",
        "errors",
        "visit_cols_absent",
        "missing_contract_ids",
        "post_plan",
    }
    if set(item) != required:
        raise ClearanceBlocked(
            "enrichment file result имеет неточный набор полей: "
            f"missing={sorted(required - set(item))}, "
            f"extra={sorted(set(item) - required)}"
        )
    path = _regular_file(item["path"], label="enriched personal file")
    path_text = str(path)
    responsible = str(item["responsible"] or "").strip()
    if not responsible:
        raise ClearanceBlocked(f"{path.name}: responsible отсутствует")
    pre_hash = _hex(item["pre_sha256"], label=f"{path.name} pre_sha256")
    post_hash = _hex(item["post_sha256"], label=f"{path.name} post_sha256")
    owner_contracts = set(
        _strings(
            item["owner_contract_ids"],
            label=f"{path.name} owner_contract_ids",
        )
    )
    filled_contracts = set(
        _strings(
            item["filled_contract_ids"],
            label=f"{path.name} filled_contract_ids",
        )
    )
    if owner_contracts != filled_contracts or not owner_contracts <= completed:
        raise ClearanceBlocked(f"{path.name}: owner/filled coverage не exact")
    filled = _integer(item["filled_cases"], label=f"{path.name} filled_cases")
    matched_count = _integer(
        item["matched_cases"],
        label=f"{path.name} matched_cases",
    )
    changed = _integer(item["changed_cases"], label=f"{path.name} changed_cases")
    writes = _integer(item["cells_written"], label=f"{path.name} cells_written")
    matched_list = _strings(
        item["matched_contract_ids"],
        label=f"{path.name} matched_contract_ids",
    )
    matched = set(matched_list)
    missing = set(
        _strings(
            item["missing_contract_ids"],
            label=f"{path.name} missing_contract_ids",
        )
    )
    if (
        not matched <= owner_contracts
        or not missing <= owner_contracts
        or matched & missing
        or matched | missing != owner_contracts
    ):
        raise ClearanceBlocked(f"{path.name}: coverage содержит чужие/пересекающиеся КД")
    if matched_count != len(matched) or changed > matched_count or writes < changed:
        raise ClearanceBlocked(f"{path.name}: несогласованные matched/changed/writes")
    if filled != matched_count + len(missing):
        raise ClearanceBlocked(f"{path.name}: filled_cases не равен matched+missing")
    if item["errors"] != [] or item["visit_cols_absent"] != []:
        raise ClearanceBlocked(f"{path.name}: enrichment содержит errors/missing columns")

    backup_raw = item["backup_path"]
    backup_hash_raw = item["backup_sha256"]
    if writes:
        if not isinstance(backup_raw, str) or not isinstance(backup_hash_raw, str):
            raise ClearanceBlocked(f"{path.name}: для записи обязателен backup")
        backup = _regular_file(backup_raw, label=f"{path.name} backup")
        backup_hash = _hex(backup_hash_raw, label=f"{path.name} backup_sha256")
        if backup == path or _sha256(backup) != backup_hash or backup_hash != pre_hash:
            raise ClearanceBlocked(f"{path.name}: backup не доказывает exact pre-state")
        if pre_hash == post_hash:
            raise ClearanceBlocked(f"{path.name}: заявлены writes без изменения SHA")
    else:
        if backup_raw is not None or backup_hash_raw is not None or pre_hash != post_hash:
            raise ClearanceBlocked(
                f"{path.name}: zero-write result должен иметь pre=post и без backup"
            )

    post = item["post_plan"]
    post_required = {
        "filled_cases",
        "filled_contract_ids",
        "matched_cases",
        "matched_contract_ids",
        "changed_cases",
        "cells_written",
        "errors",
        "visit_cols_absent",
        "missing_contract_ids",
    }
    if not isinstance(post, dict) or set(post) != post_required:
        raise ClearanceBlocked(f"{path.name}: post_plan имеет неточную schema")
    post_matched = set(
        _strings(
            post["matched_contract_ids"],
            label=f"{path.name} post_plan matched_contract_ids",
        )
    )
    post_missing = set(
        _strings(
            post["missing_contract_ids"],
            label=f"{path.name} post_plan missing_contract_ids",
        )
    )
    post_filled = set(
        _strings(
            post["filled_contract_ids"],
            label=f"{path.name} post_plan filled_contract_ids",
        )
    )
    if (
        _integer(post["filled_cases"], label="post_plan filled_cases") != filled
        or post_filled != owner_contracts
        or _integer(post["matched_cases"], label="post_plan matched_cases")
        != matched_count
        or post_matched != matched
        or post_missing != missing
        or _integer(post["changed_cases"], label="post_plan changed_cases") != 0
        or _integer(post["cells_written"], label="post_plan cells_written") != 0
        or post["errors"] != []
        or post["visit_cols_absent"] != []
    ):
        raise ClearanceBlocked(f"{path.name}: post-plan не нулевой/неидемпотентный")
    return path_text, responsible, matched, {
        "path": path_text,
        "responsible": responsible,
        "pre_sha256": pre_hash,
        "post_sha256": post_hash,
        "backup_sha256": backup_hash_raw,
        "filled_cases": filled,
        "matched_cases": matched_count,
        "changed_cases": changed,
        "cells_written": writes,
        "missing_cases": len(missing),
        "post_plan_writes": 0,
    }


def _validate_enrichment(
    evidence_raw: str | os.PathLike[str],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    path = _regular_file(evidence_raw, label="enrichment evidence")
    evidence = _load_json(path, label="enrichment evidence")
    if not isinstance(evidence, dict) or evidence.get("schema") != ENRICHMENT_SCHEMA:
        raise ClearanceBlocked("Неизвестная schema enrichment evidence")
    expected_top = {
        "schema",
        "data_exchange_contract",
        "created_at",
        "snapshot",
        "completed_contract_ids",
        "owners",
        "expected_files",
        "files",
        "receipt_digest",
    }
    if set(evidence) != expected_top:
        raise ClearanceBlocked("Enrichment evidence имеет неточный набор полей")
    if evidence.get("data_exchange_contract") != DATA_EXCHANGE_CONTRACT:
        raise ClearanceBlocked("Enrichment evidence использует другой data contract")
    claimed_receipt_digest = _hex(
        evidence["receipt_digest"],
        label="enrichment receipt_digest",
    )
    unsigned = {
        key: value for key, value in evidence.items() if key != "receipt_digest"
    }
    if _digest(unsigned) != claimed_receipt_digest:
        raise ClearanceBlocked("Enrichment canonical receipt digest не совпал")
    binding = evidence.get("snapshot")
    expected_binding = {
        "manifest_sha256": snapshot["manifest_sha256"],
        "database_state_digest": snapshot["database_state_digest"],
        "safe_export_sha256": snapshot["safe_export_sha256"],
        "raw_export_sha256": snapshot["raw_export_sha256"],
        "data_exchange_contract_sha256": snapshot[
            "data_exchange_contract_sha256"
        ],
    }
    if binding != expected_binding:
        raise ClearanceBlocked("Enrichment evidence относится к другому snapshot")

    evidence_completed = _strings(
        evidence["completed_contract_ids"],
        label="completed_contract_ids",
    )
    if evidence_completed != snapshot["completed_contract_ids"]:
        raise ClearanceBlocked("Enrichment evidence не покрывает exact safe provenance set")
    completed = set(evidence_completed)
    owners_raw = evidence.get("owners")
    if not isinstance(owners_raw, list):
        raise ClearanceBlocked("Enrichment owners должен быть списком")
    expected_by_owner = {
        owner: contracts
        for owner, contracts in snapshot["completed_by_owner"].items()
    }
    receipt_by_owner: dict[str, dict[str, Any]] = {}
    owner_files: dict[str, set[str]] = {}
    for item in owners_raw:
        if not isinstance(item, dict) or set(item) != {
            "responsible",
            "contract_ids",
            "files",
        }:
            raise ClearanceBlocked("Enrichment owner имеет неверную schema")
        responsible = str(item["responsible"] or "").strip()
        if not responsible or responsible in receipt_by_owner:
            raise ClearanceBlocked("Enrichment owner пустой/duplicate")
        contracts = _strings(
            item["contract_ids"],
            label=f"{responsible} contract_ids",
        )
        if contracts != expected_by_owner.get(responsible):
            raise ClearanceBlocked(
                f"Enrichment owner не совпал с safe provenance: {responsible}"
            )
        files_for_owner = {
            str(_regular_file(value, label=f"{responsible} personal file"))
            for value in _strings(item["files"], label=f"{responsible} files")
        }
        if not files_for_owner:
            raise ClearanceBlocked(f"Enrichment owner без файлов: {responsible}")
        receipt_by_owner[responsible] = item
        owner_files[responsible] = files_for_owner
    if set(receipt_by_owner) != set(expected_by_owner):
        raise ClearanceBlocked("Enrichment owners не покрывают exact safe owners")

    expected_files_raw = _strings(evidence["expected_files"], label="expected_files")
    expected_files = {
        str(_regular_file(item, label="expected personal file"))
        for item in expected_files_raw
    }
    if len(expected_files) != len(expected_files_raw):
        raise ClearanceBlocked("expected_files содержит duplicate resolved paths")
    files = evidence.get("files")
    if not isinstance(files, list):
        raise ClearanceBlocked("enrichment files должен быть списком")

    actual_files: set[str] = set()
    covered: set[str] = set()
    summaries: list[dict[str, Any]] = []
    for item in files:
        file_path, responsible, matched, summary = _validate_file_result(
            item,
            completed=completed,
        )
        if file_path in actual_files:
            raise ClearanceBlocked(f"Duplicate enrichment result: {file_path}")
        duplicate_cases = covered & matched
        if duplicate_cases:
            raise ClearanceBlocked(
                f"КД покрыт несколькими personal-файлами: {len(duplicate_cases)}"
            )
        actual_files.add(file_path)
        if (
            responsible not in owner_files
            or file_path not in owner_files[responsible]
        ):
            raise ClearanceBlocked(
                f"Enrichment file не принадлежит owner receipt: {file_path}"
            )
        covered.update(matched)
        summaries.append(summary)
    if actual_files != expected_files:
        raise ClearanceBlocked("files[] не совпадает с exact expected_files")
    uncovered = completed - covered
    if uncovered:
        raise ClearanceBlocked(
            f"Enrichment не покрыл завершённые визиты: {len(uncovered)}"
        )
    if completed and not files:
        raise ClearanceBlocked("Есть завершённые визиты, но enrichment files пуст")
    union_owner_files = (
        set().union(*owner_files.values()) if owner_files else set()
    )
    if union_owner_files != expected_files:
        raise ClearanceBlocked("Owner file sets не совпали с expected_files")
    if sum(len(values) for values in owner_files.values()) != len(expected_files):
        raise ClearanceBlocked("Personal-файл назначен нескольким owners")

    return {
        "evidence_path": str(path),
        "evidence_sha256": _sha256(path),
        "expected_files": len(expected_files),
        "completed_cases": len(completed),
        "covered_cases": len(covered),
        "uncovered_cases": 0,
        "duplicate_coverage_cases": 0,
        "orphan_completed_cases": snapshot["orphan_completed"],
        "failed_files": 0,
        "missing_columns": 0,
        "post_plan_writes": 0,
        "files": sorted(summaries, key=lambda item: item["path"].casefold()),
    }


def _registry_file(
    raw: str | os.PathLike[str],
    *,
    label: str,
) -> tuple[Path, dict[str, Any], str]:
    path = _regular_file(raw, label=label)
    payload = _load_json(path, label=label)
    if not isinstance(payload, dict):
        raise ClearanceBlocked(f"{label}: JSON root должен быть object")
    return path, payload, _sha256(path)


def _fingerprint_dict(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "size",
        "mtime_ns",
        "sha256",
    }:
        raise ClearanceBlocked(f"{label}: fingerprint schema некорректна")
    size = _integer(value["size"], label=f"{label} size")
    mtime_ns = _integer(value["mtime_ns"], label=f"{label} mtime_ns")
    sha = _hex(value["sha256"], label=f"{label} sha256")
    return {"size": size, "mtime_ns": mtime_ns, "sha256": sha}


def _verify_file_fingerprint(
    raw: str | os.PathLike[str],
    claimed: Any,
    *,
    label: str,
) -> Path:
    path = _regular_file(raw, label=label)
    expected = _fingerprint_dict(claimed, label=label)
    info = path.stat()
    actual_sha = _sha256(path, label=label)
    if (
        info.st_size != expected["size"]
        or info.st_mtime_ns != expected["mtime_ns"]
        or actual_sha != expected["sha256"]
    ):
        raise ClearanceBlocked(f"{label}: fingerprint файла изменился: {path}")
    return path


def _strict_plan(
    raw: str | os.PathLike[str],
    *,
    label: str,
    require_zero: bool,
) -> tuple[Path, dict[str, Any], str]:
    path, payload, sha = _registry_file(raw, label=label)
    required = {
        "master_path",
        "master_fingerprint",
        "changes",
        "projections",
        "issues",
        "month_rollover",
        "blocked",
        "digest",
    }
    if set(payload) != required:
        raise ClearanceBlocked(f"{label}: plan schema неточная")
    digest = _hex(payload["digest"], label=f"{label} digest")
    unsigned = {key: value for key, value in payload.items() if key != "digest"}
    if _digest(unsigned) != digest:
        raise ClearanceBlocked(f"{label}: canonical digest не совпал")
    if (
        payload["blocked"] is not False
        or payload["issues"] != []
        or not isinstance(payload["changes"], list)
        or not isinstance(payload["projections"], list)
    ):
        raise ClearanceBlocked(f"{label}: план blocked/issues/schema")
    if require_zero and payload["changes"]:
        raise ClearanceBlocked(f"{label}: post-run должен иметь 0 changes")
    _fingerprint_dict(
        payload["master_fingerprint"],
        label=f"{label} master_fingerprint",
    )
    return path, payload, sha


def _strict_snapshot(
    raw: str | os.PathLike[str],
    *,
    label: str,
    require_live_match: bool,
) -> tuple[Path, Any, dict[str, Any], str]:
    path, payload, sha = _registry_file(raw, label=label)
    if set(payload) != {
        "run_id",
        "run_dir",
        "master",
        "personals",
        "baseline",
    }:
        raise ClearanceBlocked(f"{label}: snapshot schema неточная")
    try:
        snapshot = load_snapshot(path)
    except Exception as exc:
        raise ClearanceBlocked(f"{label}: snapshot loader отказал: {exc}") from exc
    if (
        path.parent.resolve() != snapshot.run_dir.resolve()
        or payload["run_id"] != snapshot.run_id
    ):
        raise ClearanceBlocked(f"{label}: run_dir/run_id mismatch")
    _verify_file_fingerprint(
        snapshot.master_copy,
        payload["master"]["fingerprint"],
        label=f"{label} master copy",
    )
    if require_live_match:
        _verify_file_fingerprint(
            snapshot.master_original,
            payload["master"]["fingerprint"],
            label=f"{label} live master",
        )
    seen_entries: set[str] = set()
    for captured, raw_personal in zip(
        snapshot.personals,
        payload["personals"],
        strict=True,
    ):
        entry_id = captured.entry.entry_id
        if entry_id in seen_entries:
            raise ClearanceBlocked(f"{label}: duplicate entry_id {entry_id}")
        seen_entries.add(entry_id)
        if raw_personal.get("entry") != captured.entry.as_dict():
            raise ClearanceBlocked(f"{label}: personal entry mismatch {entry_id}")
        _verify_file_fingerprint(
            captured.copy_path,
            raw_personal["fingerprint"],
            label=f"{label} personal copy {entry_id}",
        )
        if require_live_match:
            _verify_file_fingerprint(
                captured.entry.path,
                raw_personal["fingerprint"],
                label=f"{label} live personal {entry_id}",
            )
    raw_baseline = payload.get("baseline")
    if snapshot.baseline_copy is not None:
        if not isinstance(raw_baseline, dict):
            raise ClearanceBlocked(f"{label}: baseline snapshot отсутствует")
        _verify_file_fingerprint(
            snapshot.baseline_copy,
            raw_baseline["fingerprint"],
            label=f"{label} baseline copy",
        )
    elif raw_baseline is not None:
        raise ClearanceBlocked(f"{label}: unexpected baseline")
    return path, snapshot, payload, sha


def _validate_plan_snapshot(
    plan: dict[str, Any],
    snapshot: Any,
    *,
    label: str,
) -> None:
    if (
        Path(plan["master_path"]).resolve() != snapshot.master_original.resolve()
        or plan["master_fingerprint"] != snapshot.master_fingerprint.as_dict()
        or len(plan["projections"]) != len(snapshot.personals)
    ):
        raise ClearanceBlocked(f"{label}: plan/snapshot master or count mismatch")
    by_entry = {item.entry.entry_id: item for item in snapshot.personals}
    seen: set[str] = set()
    required_projection = {
        "entry",
        "responsible",
        "columns",
        "rows",
        "preserved_cells",
        "employee_owned_preserved_cells",
        "handoff_cells",
        "rollover_cleared_cells",
        "retained_rows",
        "removed_rows",
        "source_fingerprint",
    }
    for projection in plan["projections"]:
        if not isinstance(projection, dict) or set(projection) != required_projection:
            raise ClearanceBlocked(f"{label}: projection schema неточная")
        entry = projection.get("entry")
        if not isinstance(entry, dict):
            raise ClearanceBlocked(f"{label}: projection entry отсутствует")
        entry_id = str(entry.get("entry_id") or "")
        captured = by_entry.get(entry_id)
        if (
            captured is None
            or entry_id in seen
            or entry != captured.entry.as_dict()
            or projection.get("source_fingerprint")
            != captured.fingerprint.as_dict()
        ):
            raise ClearanceBlocked(f"{label}: projection/snapshot mismatch {entry_id}")
        seen.add(entry_id)
    if seen != set(by_entry):
        raise ClearanceBlocked(f"{label}: plan не покрывает exact personals")


def _strict_stage(
    raw: str | os.PathLike[str],
    *,
    label: str,
    snapshot: Any,
    plan_digest: str,
) -> tuple[Path, Any, dict[str, Any], str]:
    path, payload, sha = _registry_file(raw, label=label)
    if set(payload) != {
        "run_id",
        "run_dir",
        "staging_dir",
        "plan_digest",
        "master",
        "personals",
    }:
        raise ClearanceBlocked(f"{label}: stage schema неточная")
    try:
        stage = load_stage(path)
    except Exception as exc:
        raise ClearanceBlocked(f"{label}: stage loader отказал: {exc}") from exc
    if (
        stage.run_id != snapshot.run_id
        or stage.run_dir.resolve() != snapshot.run_dir.resolve()
        or stage.staging_dir.resolve() != path.parent.resolve()
        or stage.plan_digest != plan_digest
        or len(stage.personals) != len(snapshot.personals)
    ):
        raise ClearanceBlocked(f"{label}: stage run/digest/count mismatch")
    expected = {
        item.entry.entry_id: item for item in snapshot.personals
    }
    if (
        stage.master.original_path.resolve() != snapshot.master_original.resolve()
        or stage.master.source_copy.resolve() != snapshot.master_copy.resolve()
        or stage.master.source_fingerprint != snapshot.master_fingerprint
    ):
        raise ClearanceBlocked(f"{label}: master stage/snapshot mismatch")
    for staged in stage.files:
        _verify_file_fingerprint(
            staged.source_copy,
            staged.source_fingerprint.as_dict(),
            label=f"{label} source {staged.entry_id or 'master'}",
        )
        _verify_file_fingerprint(
            staged.staged_path,
            staged.staged_fingerprint.as_dict(),
            label=f"{label} staged {staged.entry_id or 'master'}",
        )
    seen: set[str] = set()
    for staged in stage.personals:
        captured = expected.get(str(staged.entry_id or ""))
        if (
            captured is None
            or staged.entry_id in seen
            or staged.original_path.resolve() != captured.entry.path.resolve()
            or staged.source_copy.resolve() != captured.copy_path.resolve()
            or staged.source_fingerprint != captured.fingerprint
        ):
            raise ClearanceBlocked(f"{label}: personal stage/snapshot mismatch")
        seen.add(str(staged.entry_id))
    if seen != set(expected):
        raise ClearanceBlocked(f"{label}: stage не покрывает exact personals")
    return path, stage, payload, sha


def _strict_validation(
    raw: str | os.PathLike[str],
    *,
    label: str,
    run_id: str,
    plan_digest: str,
    checked_files: int,
) -> tuple[Path, dict[str, Any], str]:
    path, payload, sha = _registry_file(raw, label=label)
    if set(payload) != {
        "run_id",
        "plan_digest",
        "checked_files",
        "checked_master_cells",
        "checked_projection_cells",
        "issues",
        "blocked",
    }:
        raise ClearanceBlocked(f"{label}: validation schema неточная")
    if (
        payload["run_id"] != run_id
        or payload["plan_digest"] != plan_digest
        or payload["checked_files"] != checked_files
        or payload["blocked"] is not False
        or payload["issues"] != []
        or _integer(
            payload["checked_master_cells"],
            label=f"{label} checked_master_cells",
        ) < 0
        or _integer(
            payload["checked_projection_cells"],
            label=f"{label} checked_projection_cells",
        ) < 0
    ):
        raise ClearanceBlocked(f"{label}: validation не зелёный/exact")
    return path, payload, sha


def _validate_green_plan(
    payload: dict[str, Any],
    *,
    label: str,
    require_zero: bool,
) -> tuple[str, int]:
    digest = _hex(payload.get("digest"), label=f"{label} digest")
    if payload.get("blocked") is not False:
        raise ClearanceBlocked(f"{label}: blocked должен быть false")
    changes = payload.get("changes")
    projections = payload.get("projections")
    if not isinstance(changes, list) or not isinstance(projections, list):
        raise ClearanceBlocked(f"{label}: changes/projections должны быть списками")
    if require_zero and changes:
        raise ClearanceBlocked(f"{label}: post-run должен иметь 0 changes")
    return digest, 1 + len(projections)


def _validate_green_validation(
    payload: dict[str, Any],
    *,
    digest: str,
    expected_files: int,
    label: str,
) -> None:
    if (
        payload.get("blocked") is not False
        or payload.get("issues") != []
        or payload.get("plan_digest") != digest
        or payload.get("checked_files") != expected_files
    ):
        raise ClearanceBlocked(f"{label}: validation не зелёный/неполный")


def _validate_registry(
    *,
    workspace_raw: str | os.PathLike[str],
    plan_raw: str | os.PathLike[str],
    validation_raw: str | os.PathLike[str],
    publication_raw: str | os.PathLike[str] | None,
    post_snapshot_raw: str | os.PathLike[str],
    post_plan_raw: str | os.PathLike[str],
    post_validation_raw: str | os.PathLike[str],
    active_baseline_raw: str | os.PathLike[str],
    mode: str,
) -> dict[str, Any]:
    workspace = _directory(workspace_raw, label="registry workspace")
    pending = incomplete_publish_journals(workspace)
    if pending:
        raise ClearanceBlocked(
            "Есть незавершённые publish journals: "
            + ", ".join(str(item) for item in pending[:5])
        )
    if mode not in {"published", "already_zero"}:
        raise ClearanceBlocked("Registry mode: только published/already_zero")

    plan_path, plan, plan_sha = _registry_file(plan_raw, label="registry plan")
    validation_path, validation, validation_sha = _registry_file(
        validation_raw,
        label="registry validation",
    )
    plan_digest, expected_files = _validate_green_plan(
        plan,
        label="registry plan",
        require_zero=mode == "already_zero",
    )
    _validate_green_validation(
        validation,
        digest=plan_digest,
        expected_files=expected_files,
        label="registry validation",
    )
    if mode == "published" and not plan.get("changes"):
        raise ClearanceBlocked("Published mode не принимается для zero-change plan")

    publication_path: Path | None = None
    publication_sha: str | None = None
    publication: dict[str, Any] | None = None
    if mode == "published":
        if publication_raw is None:
            raise ClearanceBlocked("Published mode требует publish.json")
        publication_path, publication, publication_sha = _registry_file(
            publication_raw,
            label="registry publication",
        )
        if (
            publication.get("plan_digest") != plan_digest
            or publication.get("ok") is not True
            or publication.get("complete") is not True
            or publication.get("issues") != []
            or publication.get("rolled_back") != []
            or publication.get("sidecars") != []
            or not isinstance(publication.get("published"), list)
            or len(publication["published"]) != expected_files
            or not isinstance(publication.get("backups"), list)
            or len(publication["backups"]) != expected_files
        ):
            raise ClearanceBlocked("Publish receipt не доказывает полный commit")
    elif publication_raw is not None:
        raise ClearanceBlocked("already_zero не должен подменяться publish receipt")

    post_snapshot_path, post_snapshot, post_snapshot_sha = _registry_file(
        post_snapshot_raw,
        label="post-run snapshot",
    )
    post_plan_path, post_plan, post_plan_sha = _registry_file(
        post_plan_raw,
        label="post-run plan",
    )
    post_validation_path, post_validation, post_validation_sha = _registry_file(
        post_validation_raw,
        label="post-run validation",
    )
    post_digest, post_expected_files = _validate_green_plan(
        post_plan,
        label="post-run plan",
        require_zero=True,
    )
    _validate_green_validation(
        post_validation,
        digest=post_digest,
        expected_files=post_expected_files,
        label="post-run validation",
    )
    post_master = post_snapshot.get("master")
    post_baseline = post_snapshot.get("baseline")
    if not isinstance(post_master, dict) or not isinstance(post_baseline, dict):
        raise ClearanceBlocked("Post-run snapshot не содержит master/baseline")
    master_fingerprint = post_master.get("fingerprint")
    baseline_fingerprint = post_baseline.get("fingerprint")
    if not isinstance(master_fingerprint, dict) or not isinstance(
        baseline_fingerprint,
        dict,
    ):
        raise ClearanceBlocked("Post-run snapshot fingerprints отсутствуют")
    master_sha = _hex(
        master_fingerprint.get("sha256"),
        label="post-run master SHA",
    )
    baseline_snapshot_sha = _hex(
        baseline_fingerprint.get("sha256"),
        label="post-run baseline SHA",
    )
    if (
        not isinstance(post_plan.get("master_fingerprint"), dict)
        or post_plan["master_fingerprint"].get("sha256") != master_sha
    ):
        raise ClearanceBlocked("Post-run plan не относится к snapshot master")

    baseline_path = _regular_file(active_baseline_raw, label="active baseline")
    baseline_sha = _sha256(baseline_path)
    if baseline_sha != baseline_snapshot_sha:
        raise ClearanceBlocked("Active baseline SHA не совпал с post-run snapshot")
    baseline = _load_json_gz(baseline_path, label="active baseline")
    if (
        baseline.get("schema_version") != 1
        or not isinstance(baseline.get("records"), list)
        or baseline.get("master_path") != post_master.get("original")
    ):
        raise ClearanceBlocked("Active baseline schema/master contract не совпал")
    baseline_plan_digest = _hex(
        baseline.get("plan_digest"),
        label="active baseline plan_digest",
    )
    if mode == "published" and baseline_plan_digest != plan_digest:
        raise ClearanceBlocked("Active baseline не был активирован этим publish")
    if publication is not None:
        publication_baseline = publication.get("baseline")
        if (
            not isinstance(publication_baseline, str)
            or Path(publication_baseline).name != baseline_path.name
        ):
            raise ClearanceBlocked("Publish receipt указывает другой baseline")

    artifacts = {
        "plan": {"path": str(plan_path), "sha256": plan_sha},
        "validation": {
            "path": str(validation_path),
            "sha256": validation_sha,
        },
        "post_snapshot": {
            "path": str(post_snapshot_path),
            "sha256": post_snapshot_sha,
        },
        "post_plan": {"path": str(post_plan_path), "sha256": post_plan_sha},
        "post_validation": {
            "path": str(post_validation_path),
            "sha256": post_validation_sha,
        },
        "active_baseline": {
            "path": str(baseline_path),
            "sha256": baseline_sha,
        },
    }
    if publication_path is not None and publication_sha is not None:
        artifacts["publication"] = {
            "path": str(publication_path),
            "sha256": publication_sha,
        }
    return {
        "workspace": str(workspace),
        "mode": mode,
        "plan_digest": plan_digest,
        "plan_changes": len(plan["changes"]),
        "validation_checked_files": expected_files,
        "post_run_digest": post_digest,
        "post_run_changes": 0,
        "post_run_validation_checked_files": post_expected_files,
        "active_baseline_sha256": baseline_sha,
        "active_baseline_plan_digest": baseline_plan_digest,
        "post_run_master_sha256": master_sha,
        "unfinished_publish_journals": 0,
        "artifacts": artifacts,
    }


def _validate_registry_strict(
    *,
    workspace_raw: str | os.PathLike[str],
    registry_snapshot_raw: str | os.PathLike[str],
    plan_raw: str | os.PathLike[str],
    stage_raw: str | os.PathLike[str],
    validation_raw: str | os.PathLike[str],
    publication_raw: str | os.PathLike[str] | None,
    post_snapshot_raw: str | os.PathLike[str],
    post_plan_raw: str | os.PathLike[str],
    post_stage_raw: str | os.PathLike[str],
    post_validation_raw: str | os.PathLike[str],
    active_baseline_raw: str | os.PathLike[str],
    mode: str,
) -> dict[str, Any]:
    workspace = _directory(workspace_raw, label="registry workspace")
    pending = incomplete_publish_journals(workspace)
    if pending:
        raise ClearanceBlocked(
            "Есть незавершённые publish journals: "
            + ", ".join(str(item) for item in pending[:5])
        )
    if mode not in {"published", "already_zero"}:
        raise ClearanceBlocked("Registry mode: только published/already_zero")

    snapshot_path, registry_snapshot, snapshot_payload, snapshot_sha = (
        _strict_snapshot(
            registry_snapshot_raw,
            label="registry snapshot",
            require_live_match=False,
        )
    )
    plan_path, plan, plan_sha = _strict_plan(
        plan_raw,
        label="registry plan",
        require_zero=mode == "already_zero",
    )
    if plan_path.parent.resolve() != registry_snapshot.run_dir.resolve():
        raise ClearanceBlocked("Registry plan находится вне exact run_dir")
    _validate_plan_snapshot(plan, registry_snapshot, label="registry")
    plan_digest = plan["digest"]
    if mode == "published" and not plan["changes"]:
        raise ClearanceBlocked("Published mode не принимается для zero plan")
    stage_path, stage, _stage_payload, stage_sha = _strict_stage(
        stage_raw,
        label="registry stage",
        snapshot=registry_snapshot,
        plan_digest=plan_digest,
    )
    validation_path, _validation, validation_sha = _strict_validation(
        validation_raw,
        label="registry validation",
        run_id=registry_snapshot.run_id,
        plan_digest=plan_digest,
        checked_files=len(stage.files),
    )
    if validation_path.parent.resolve() != stage.staging_dir.resolve():
        raise ClearanceBlocked("Registry validation находится вне exact staging")

    baseline_path = _regular_file(active_baseline_raw, label="active baseline")
    baseline_sha = _sha256(baseline_path, label="active baseline")
    baseline = _load_json_gz(baseline_path, label="active baseline")
    if (
        not isinstance(baseline, dict)
        or set(baseline)
        != {
            "schema_version",
            "created_at",
            "master_path",
            "plan_digest",
            "records",
        }
        or baseline["schema_version"] != 1
        or not isinstance(baseline["records"], list)
        or Path(baseline["master_path"]).resolve()
        != registry_snapshot.master_original.resolve()
    ):
        raise ClearanceBlocked("Active baseline schema/master contract не совпал")
    baseline_plan_digest = _hex(
        baseline["plan_digest"],
        label="active baseline plan_digest",
    )

    publication_path: Path | None = None
    publication_sha: str | None = None
    if mode == "published":
        if publication_raw is None:
            raise ClearanceBlocked("Published mode требует publish.json")
        publication_path, publication, publication_sha = _registry_file(
            publication_raw,
            label="registry publication",
        )
        if set(publication) != {
            "run_id",
            "plan_digest",
            "started_at",
            "finished_at",
            "published",
            "sidecars",
            "backups",
            "rolled_back",
            "issues",
            "baseline",
            "ok",
            "complete",
        }:
            raise ClearanceBlocked("Publish receipt schema неточная")
        expected_publish = [
            *[str(item.original_path) for item in stage.personals],
            str(stage.master.original_path),
        ]
        backups = publication.get("backups")
        if (
            publication_path.parent.resolve() != stage.run_dir.resolve()
            or publication["run_id"] != stage.run_id
            or publication["plan_digest"] != plan_digest
            or publication["ok"] is not True
            or publication["complete"] is not True
            or not publication["finished_at"]
            or publication["issues"] != []
            or publication["rolled_back"] != []
            or publication["sidecars"] != []
            or publication["published"] != expected_publish
            or not isinstance(backups, list)
            or len(backups) != len(expected_publish)
            or Path(publication["baseline"]).resolve() != baseline_path.resolve()
            or baseline_plan_digest != plan_digest
        ):
            raise ClearanceBlocked("Publish receipt не доказывает exact full commit")
        publish_order = (*stage.personals, stage.master)
        for staged, backup_raw in zip(publish_order, backups, strict=True):
            _verify_file_fingerprint(
                backup_raw,
                staged.source_fingerprint.as_dict(),
                label=f"publish backup {staged.entry_id or 'master'}",
            )
    elif publication_raw is not None:
        raise ClearanceBlocked("already_zero не должен иметь publish receipt")

    post_snapshot_path, post_snapshot, post_snapshot_payload, post_snapshot_sha = (
        _strict_snapshot(
            post_snapshot_raw,
            label="post-run snapshot",
            require_live_match=True,
        )
    )
    if post_snapshot.master_original.resolve() != registry_snapshot.master_original.resolve():
        raise ClearanceBlocked("Post-run snapshot относится к другому master")
    post_plan_path, post_plan, post_plan_sha = _strict_plan(
        post_plan_raw,
        label="post-run plan",
        require_zero=True,
    )
    if post_plan_path.parent.resolve() != post_snapshot.run_dir.resolve():
        raise ClearanceBlocked("Post-run plan находится вне exact run_dir")
    _validate_plan_snapshot(post_plan, post_snapshot, label="post-run")
    post_stage_path, post_stage, _post_stage_payload, post_stage_sha = _strict_stage(
        post_stage_raw,
        label="post-run stage",
        snapshot=post_snapshot,
        plan_digest=post_plan["digest"],
    )
    post_validation_path, _post_validation, post_validation_sha = (
        _strict_validation(
            post_validation_raw,
            label="post-run validation",
            run_id=post_snapshot.run_id,
            plan_digest=post_plan["digest"],
            checked_files=len(post_stage.files),
        )
    )
    if post_validation_path.parent.resolve() != post_stage.staging_dir.resolve():
        raise ClearanceBlocked("Post validation находится вне exact staging")

    post_baseline = post_snapshot_payload.get("baseline")
    if (
        not isinstance(post_baseline, dict)
        or post_snapshot.baseline_copy is None
        or post_baseline.get("fingerprint", {}).get("sha256") != baseline_sha
    ):
        raise ClearanceBlocked("Post snapshot не привязан к active baseline")
    _verify_file_fingerprint(
        baseline_path,
        post_baseline["fingerprint"],
        label="live active baseline",
    )

    initial_staged_by_path = {
        str(item.original_path.resolve()).casefold(): item.staged_fingerprint
        for item in stage.files
    }
    post_captured_by_path = {
        str(post_snapshot.master_original.resolve()).casefold():
            post_snapshot.master_fingerprint,
        **{
            str(item.entry.path.resolve()).casefold(): item.fingerprint
            for item in post_snapshot.personals
        },
    }
    if set(initial_staged_by_path) != set(post_captured_by_path):
        raise ClearanceBlocked("Initial publish/post snapshot имеют разные file sets")
    for path_key, staged_fingerprint in initial_staged_by_path.items():
        captured = post_captured_by_path[path_key]
        if (
            staged_fingerprint.size != captured.size
            or staged_fingerprint.sha256 != captured.sha256
        ):
            raise ClearanceBlocked(
                "Post snapshot не равен initial validated staging: " + path_key
            )

    artifacts = {
        "registry_snapshot": {"path": str(snapshot_path), "sha256": snapshot_sha},
        "plan": {"path": str(plan_path), "sha256": plan_sha},
        "stage": {"path": str(stage_path), "sha256": stage_sha},
        "validation": {"path": str(validation_path), "sha256": validation_sha},
        "post_snapshot": {
            "path": str(post_snapshot_path),
            "sha256": post_snapshot_sha,
        },
        "post_plan": {"path": str(post_plan_path), "sha256": post_plan_sha},
        "post_stage": {"path": str(post_stage_path), "sha256": post_stage_sha},
        "post_validation": {
            "path": str(post_validation_path),
            "sha256": post_validation_sha,
        },
        "active_baseline": {
            "path": str(baseline_path),
            "sha256": baseline_sha,
        },
    }
    if publication_path is not None and publication_sha is not None:
        artifacts["publication"] = {
            "path": str(publication_path),
            "sha256": publication_sha,
        }
    return {
        "workspace": str(workspace),
        "mode": mode,
        "run_id": registry_snapshot.run_id,
        "plan_digest": plan_digest,
        "plan_changes": len(plan["changes"]),
        "validation_checked_files": len(stage.files),
        "post_run_id": post_snapshot.run_id,
        "post_run_digest": post_plan["digest"],
        "post_run_changes": 0,
        "post_run_validation_checked_files": len(post_stage.files),
        "active_baseline_sha256": baseline_sha,
        "active_baseline_plan_digest": baseline_plan_digest,
        "post_run_master_sha256": post_snapshot.master_fingerprint.sha256,
        "unfinished_publish_journals": 0,
        "artifacts": artifacts,
        "_initial_snapshot": registry_snapshot,
        "_post_snapshot": post_snapshot,
    }


def _xlsx_plan(
    path: Path,
    fields: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ClearanceBlocked("openpyxl нужен для проверки .xlsx/.xlsm") from exc
    try:
        workbook = load_workbook(
            path,
            read_only=True,
            data_only=False,
            keep_vba=path.suffix.casefold() == ".xlsm",
        )
    except Exception as exc:
        raise ClearanceBlocked(f"Excel snapshot не открывается: {path}: {exc}") from exc
    try:
        if pult_enrich.SHEET_NAME in workbook.sheetnames:
            sheet = workbook[pult_enrich.SHEET_NAME]
        else:
            title_keys = {
                pult_enrich.norm_title(item["title"]) for item in fields
            }
            sheet = max(
                workbook.worksheets,
                key=lambda candidate: max(
                    (
                        sum(
                            pult_enrich.norm_title(cell.value) in title_keys
                            for cell in row
                        )
                        for row in candidate.iter_rows(
                            min_row=1,
                            max_row=min(10, candidate.max_row),
                            values_only=False,
                        )
                    ),
                    default=0,
                ),
            )
        grid = [
            list(row)
            for row in sheet.iter_rows(values_only=True)
        ]
        return pult_enrich.build_plan(grid, fields, rows)
    finally:
        workbook.close()


def _workbook_plan(
    path: Path,
    fields: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    suffix = path.suffix.casefold()
    if suffix in {".xlsx", ".xlsm"}:
        return _xlsx_plan(path, fields, rows)
    if suffix not in {".xlsb", ".xls"}:
        raise ClearanceBlocked(f"Неизвестный Excel format evidence: {path}")
    try:
        pult_enrich._co_init()
        import xlwings as xw

        with pult_enrich.open_excel(xw) as app:
            app.display_alerts = False
            app.screen_updating = False
            return pult_enrich._plan_workbook(app, path, fields, rows)
    except Exception as exc:
        raise ClearanceBlocked(
            f"Excel/COM read-only verification не прошла: {path}: {exc}"
        ) from exc


def _verify_enrichment_in_registry(
    *,
    hardbot_snapshot: dict[str, Any],
    enrichment: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    initial = registry["_initial_snapshot"]
    post = registry["_post_snapshot"]
    initial_by_path = {
        str(item.entry.path.resolve()).casefold(): item
        for item in initial.personals
    }
    post_by_path = {
        str(item.entry.path.resolve()).casefold(): item
        for item in post.personals
    }
    expected_files = {
        str(Path(item["path"]).resolve()).casefold()
        for item in enrichment["files"]
    }
    if not expected_files <= set(initial_by_path) or not expected_files <= set(
        post_by_path
    ):
        raise ClearanceBlocked(
            "Enrichment files не входят в exact registry pre/post snapshots"
        )
    covered: set[str] = set()
    checked: list[dict[str, Any]] = []
    for evidence in enrichment["files"]:
        path_key = str(Path(evidence["path"]).resolve()).casefold()
        initial_item = initial_by_path[path_key]
        post_item = post_by_path[path_key]
        if (
            initial_item.fingerprint.sha256 != evidence["post_sha256"]
            or initial_item.fingerprint.size
            != _regular_file(
                initial_item.copy_path,
                label="initial enrichment snapshot copy",
            ).stat().st_size
        ):
            raise ClearanceBlocked(
                "Pult post SHA не равен входу registry_cycle: "
                + evidence["path"]
            )
        contracts = hardbot_snapshot["completed_by_owner"].get(
            evidence["responsible"]
        )
        if contracts is None:
            raise ClearanceBlocked("Enrichment owner отсутствует в safe snapshot")
        rows = [hardbot_snapshot["safe_by_id"][cid] for cid in contracts]
        plan = _workbook_plan(
            _regular_file(
                post_item.copy_path,
                label="post-run personal snapshot copy",
            ),
            hardbot_snapshot["schema"],
            rows,
        )
        matched = set(plan["matched_contract_ids"])
        if (
            plan["cells_written"] != 0
            or plan["changed_cases"] != 0
            or plan["visit_cols_absent"]
            or set(plan["filled_contract_ids"]) != set(contracts)
            or not matched <= set(contracts)
            or covered & matched
        ):
            raise ClearanceBlocked(
                "Final personal workbook не даёт exact zero enrichment plan: "
                + evidence["path"]
            )
        covered.update(matched)
        checked.append(
            {
                "path": evidence["path"],
                "post_snapshot_sha256": post_item.fingerprint.sha256,
                "matched_cases": len(matched),
                "zero_plan_writes": 0,
            }
        )
    completed = set(hardbot_snapshot["completed_contract_ids"])
    if covered != completed:
        raise ClearanceBlocked(
            "Final registry package не покрывает exact completed set: "
            f"covered={len(covered)}, expected={len(completed)}"
        )
    return {
        "checked_files": len(checked),
        "completed_cases": len(completed),
        "covered_cases": len(covered),
        "files": checked,
    }


def _collect(
    *,
    snapshot_dir: str | os.PathLike[str],
    enrichment_evidence: str | os.PathLike[str],
    workspace: str | os.PathLike[str],
    registry_snapshot: str | os.PathLike[str],
    plan: str | os.PathLike[str],
    stage: str | os.PathLike[str],
    validation: str | os.PathLike[str],
    publication: str | os.PathLike[str] | None,
    post_snapshot: str | os.PathLike[str],
    post_plan: str | os.PathLike[str],
    post_stage: str | os.PathLike[str],
    post_validation: str | os.PathLike[str],
    active_baseline: str | os.PathLike[str],
    mode: str,
) -> dict[str, Any]:
    snapshot = _snapshot_evidence(snapshot_dir)
    enrichment = _validate_enrichment(enrichment_evidence, snapshot)
    registry = _validate_registry_strict(
        workspace_raw=workspace,
        registry_snapshot_raw=registry_snapshot,
        plan_raw=plan,
        stage_raw=stage,
        validation_raw=validation,
        publication_raw=publication,
        post_snapshot_raw=post_snapshot,
        post_plan_raw=post_plan,
        post_stage_raw=post_stage,
        post_validation_raw=post_validation,
        active_baseline_raw=active_baseline,
        mode=mode,
    )
    final_enrichment = _verify_enrichment_in_registry(
        hardbot_snapshot=snapshot,
        enrichment=enrichment,
        registry=registry,
    )
    registry.pop("_initial_snapshot")
    registry.pop("_post_snapshot")
    return {
        "snapshot": {
            "directory": snapshot["snapshot_dir"],
            "manifest_sha256": snapshot["manifest_sha256"],
            "confirm_token": snapshot["manifest"].get("confirm_token"),
            "database_state_digest": snapshot["database_state_digest"],
            "safe_export_sha256": snapshot["safe_export_sha256"],
            "raw_export_sha256": snapshot["raw_export_sha256"],
            "data_exchange_contract_sha256": snapshot[
                "data_exchange_contract_sha256"
            ],
        },
        "provenance": {
            "clients": snapshot["clients"],
            "safe_rows": snapshot["safe_rows"],
            "raw_rows": snapshot["raw_rows"],
            "completed_cases": len(snapshot["completed_contract_ids"]),
            "completed_contract_ids_digest": snapshot[
                "completed_contract_ids_digest"
            ],
            "invalid_completed_cases": snapshot["invalid_completed"],
            "orphan_completed_cases": snapshot["orphan_completed"],
            "unprovenanced_visit_rows": snapshot[
                "unprovenanced_visit_rows"
            ],
        },
        "coverage": {
            key: enrichment[key]
            for key in (
                "expected_files",
                "completed_cases",
                "covered_cases",
                "uncovered_cases",
                "duplicate_coverage_cases",
                "orphan_completed_cases",
                "failed_files",
                "missing_columns",
                "post_plan_writes",
            )
        },
        "enrichment": {
            "evidence_path": enrichment["evidence_path"],
            "evidence_sha256": enrichment["evidence_sha256"],
            "files": enrichment["files"],
            "final_registry_verification": final_enrichment,
        },
        "registry": registry,
    }


def _write_json_atomic(path: Path, payload: Any) -> None:
    if path.exists() or path.is_symlink():
        raise ClearanceBlocked(f"Receipt target уже существует: {path}")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ClearanceBlocked(f"Небезопасный parent receipt: {parent}")
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    temporary = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        for _attempt in range(32):
            temporary = parent / (
                f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
            )
            try:
                descriptor = os.open(temporary, flags, 0o600)
                break
            except FileExistsError:
                temporary = None
        else:
            raise ClearanceBlocked("Не удалось создать unique temp receipt")
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        if os.name != "nt":
            path.chmod(0o600)
        if os.name != "nt":
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def issue_clearance(
    *,
    snapshot_dir: str | os.PathLike[str],
    enrichment_evidence: str | os.PathLike[str],
    workspace: str | os.PathLike[str],
    registry_snapshot: str | os.PathLike[str],
    plan: str | os.PathLike[str],
    stage: str | os.PathLike[str],
    validation: str | os.PathLike[str],
    publication: str | os.PathLike[str] | None,
    post_snapshot: str | os.PathLike[str],
    post_plan: str | os.PathLike[str],
    post_stage: str | os.PathLike[str],
    post_validation: str | os.PathLike[str],
    active_baseline: str | os.PathLike[str],
    mode: str,
    output: str | os.PathLike[str],
) -> dict[str, Any]:
    """Validate external evidence and atomically emit one reset clearance."""

    sections = _collect(
        snapshot_dir=snapshot_dir,
        enrichment_evidence=enrichment_evidence,
        workspace=workspace,
        registry_snapshot=registry_snapshot,
        plan=plan,
        stage=stage,
        validation=validation,
        publication=publication,
        post_snapshot=post_snapshot,
        post_plan=post_plan,
        post_stage=post_stage,
        post_validation=post_validation,
        active_baseline=active_baseline,
        mode=mode,
    )
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "data_exchange_contract": DATA_EXCHANGE_CONTRACT,
        "issued_at": _utc_now(),
        **sections,
    }
    receipt["clearance_digest"] = _digest(receipt)
    output_path = Path(output)
    _write_json_atomic(output_path, receipt)
    return receipt


def verify_clearance_receipt(
    receipt_raw: str | os.PathLike[str],
    *,
    snapshot_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Revalidate every local artifact referenced by an existing receipt."""

    receipt_path = _regular_file(receipt_raw, label="clearance receipt")
    receipt = _load_json(receipt_path, label="clearance receipt")
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("data_exchange_contract") != DATA_EXCHANGE_CONTRACT
    ):
        raise ClearanceBlocked("Неизвестная schema clearance receipt")
    claimed_digest = _hex(
        receipt.get("clearance_digest"),
        label="clearance_digest",
    )
    unsigned = {key: value for key, value in receipt.items() if key != "clearance_digest"}
    if _digest(unsigned) != claimed_digest:
        raise ClearanceBlocked("Clearance receipt canonical digest не совпал")

    snapshot = receipt.get("snapshot")
    enrichment = receipt.get("enrichment")
    registry = receipt.get("registry")
    if not all(isinstance(item, dict) for item in (snapshot, enrichment, registry)):
        raise ClearanceBlocked("Clearance receipt не содержит evidence sections")
    effective_snapshot = snapshot_dir or snapshot.get("directory")
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ClearanceBlocked("Clearance receipt не содержит registry artifacts")
    required_roles = {
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
    if registry.get("mode") == "published":
        required_roles.add("publication")
    if set(artifacts) != required_roles:
        raise ClearanceBlocked("Clearance registry artifact set неточный")
    artifact_paths = {}
    for role, item in artifacts.items():
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ClearanceBlocked(f"Registry artifact {role} имеет неверную schema")
        path = _regular_file(item["path"], label=f"registry artifact {role}")
        if _sha256(path) != _hex(item["sha256"], label=f"{role} SHA"):
            raise ClearanceBlocked(f"Registry artifact изменился: {role}")
        artifact_paths[role] = path

    sections = _collect(
        snapshot_dir=effective_snapshot,
        enrichment_evidence=enrichment.get("evidence_path"),
        workspace=registry.get("workspace"),
        registry_snapshot=artifact_paths["registry_snapshot"],
        plan=artifact_paths["plan"],
        stage=artifact_paths["stage"],
        validation=artifact_paths["validation"],
        publication=artifact_paths.get("publication"),
        post_snapshot=artifact_paths["post_snapshot"],
        post_plan=artifact_paths["post_plan"],
        post_stage=artifact_paths["post_stage"],
        post_validation=artifact_paths["post_validation"],
        active_baseline=artifact_paths["active_baseline"],
        mode=registry.get("mode"),
    )
    expected_unsigned = {
        "schema": RECEIPT_SCHEMA,
        "data_exchange_contract": DATA_EXCHANGE_CONTRACT,
        "issued_at": receipt.get("issued_at"),
        **sections,
    }
    if unsigned != expected_unsigned:
        raise ClearanceBlocked("Clearance receipt не совпал с повторной проверкой")
    return receipt


@contextmanager
def clearance_lease(receipt: dict[str, Any]):
    """Freeze registry publication state while a verified reset is in flight."""

    registry = receipt.get("registry")
    if not isinstance(registry, dict):
        raise ClearanceBlocked("Clearance receipt не содержит registry section")
    workspace = _directory(
        registry.get("workspace"),
        label="registry workspace",
    )

    def check_publication_state() -> None:
        assert_no_incomplete_publications(workspace)

    lock = CycleLock(
        workspace / "cycle.lock",
        stale_seconds=21600,
        before_stale_remove=check_publication_state,
    )
    try:
        with lock:
            check_publication_state()
            lock.assert_owned()
            yield lock
            lock.assert_owned()
            check_publication_state()
    except ClearanceBlocked:
        raise
    except Exception as exc:
        raise ClearanceBlocked(
            f"Registry clearance lease не получен/утрачен: {exc}"
        ) from exc


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--enrichment-evidence", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--registry-snapshot", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--mode", choices=("published", "already_zero"), required=True)
    parser.add_argument("--publication", type=Path)
    parser.add_argument("--post-snapshot", type=Path, required=True)
    parser.add_argument("--post-plan", type=Path, required=True)
    parser.add_argument("--post-stage", type=Path, required=True)
    parser.add_argument("--post-validation", type=Path, required=True)
    parser.add_argument("--active-baseline", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser(
        "enrichment-template",
        help="создать non-authorizing evidence template из hardbot snapshot",
    )
    template.add_argument("--snapshot-dir", type=Path, required=True)
    template.add_argument("--output", type=Path, required=True)
    issue = commands.add_parser("issue", help="проверить evidence и выдать receipt")
    _add_common_arguments(issue)
    issue.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="повторно проверить receipt/artifacts")
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--snapshot-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "enrichment-template":
            payload = enrichment_template(args.snapshot_dir)
            _write_json_atomic(args.output, payload)
        elif args.command == "issue":
            payload = issue_clearance(
                snapshot_dir=args.snapshot_dir,
                enrichment_evidence=args.enrichment_evidence,
                workspace=args.workspace,
                registry_snapshot=args.registry_snapshot,
                plan=args.plan,
                stage=args.stage,
                validation=args.validation,
                publication=args.publication,
                post_snapshot=args.post_snapshot,
                post_plan=args.post_plan,
                post_stage=args.post_stage,
                post_validation=args.post_validation,
                active_baseline=args.active_baseline,
                mode=args.mode,
                output=args.output,
            )
        else:
            payload = verify_clearance_receipt(
                args.receipt,
                snapshot_dir=args.snapshot_dir,
            )
        public = {
            "schema": payload.get("schema", ENRICHMENT_SCHEMA),
            "snapshot_manifest_sha256": (
                payload.get("snapshot") or {}
            ).get("manifest_sha256"),
            "clearance_digest": payload.get("clearance_digest"),
            "completed_cases": (payload.get("provenance") or {}).get(
                "completed_cases"
            ),
            "coverage": payload.get("coverage"),
            "registry": {
                key: value
                for key, value in (payload.get("registry") or {}).items()
                if key != "artifacts"
            },
        }
        print(json.dumps(public, ensure_ascii=False, indent=2))
        return 0
    except ClearanceBlocked as exc:
        print(f"ОТКАЗ: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
