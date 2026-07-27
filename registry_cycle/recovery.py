"""Аддитивное восстановление личных реестров из доказательных копий.

Модуль намеренно отделён от обычной сборки/разборки:

* живые книги сначала фиксируются обычным :mod:`registry_cycle.snapshot`;
* архивы и SMB Previous Versions копируются в локальный run-dir;
* план допускает запись только в пустую текущую ячейку того же КД и того же
  ответственного;
* отличающиеся исторические значения не угадываются, а уходят в конфликт;
* Excel открывает только локальный staging, публикация выполняется отдельно.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
import re
import secrets
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import ConfigBundle, load_config_bundle
from .errors import SafetyViolation
from .files import stable_copy
from .excel_host import (
    ExcelHost,
    range_has_formulas,
    sheet_by_name,
    sheet_filter_signature,
    workbook_untouched_signatures,
)
from .models import FileFingerprint, Issue, WorkbookSnapshot
from .normalize import (
    comparable_value,
    is_blank,
    json_value,
    norm_name,
    norm_title,
)
from .reader import read_portfolio
from .snapshot import SnapshotSet, load_snapshot
from .staging import _revealed_rows, excel_safe_value


@dataclass(frozen=True, slots=True)
class RecoveryField:
    title: str
    profiles: tuple[str, ...]
    max_length: int
    append_only: bool
    not_before: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecoveryConfig:
    archive_root: Path
    archive_points: tuple[dict[str, str], ...]
    vss: dict[str, Any]
    extra_sources: dict[str, Any]
    fields: tuple[RecoveryField, ...]
    safety: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    entry_id: str
    label: str
    kind: str
    original_path: Path
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class CapturedSource:
    entry_id: str
    label: str
    kind: str
    original_path: Path
    copy_path: Path
    captured_at: datetime
    fingerprint: FileFingerprint

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "label": self.label,
            "kind": self.kind,
            "original_path": str(self.original_path),
            "copy_path": str(self.copy_path),
            "captured_at": self.captured_at.isoformat(),
            "fingerprint": self.fingerprint.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class RecoveryWrite:
    entry_id: str
    original_path: Path
    source_copy: Path
    sheet: str
    row: int
    column: int
    title: str
    contract_id: str
    responsible: str
    before: Any
    after: str
    provenance_path: Path
    provenance_label: str
    provenance_at: datetime
    confidence: str
    observations: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "original_path": str(self.original_path),
            "source_copy": str(self.source_copy),
            "sheet": self.sheet,
            "row_index": self.row,
            "column_index": self.column,
            "row": self.row + 1,
            "column": self.column + 1,
            "title": self.title,
            "contract_id": self.contract_id,
            "responsible": self.responsible,
            "before": json_value(self.before),
            "after": self.after,
            "provenance_path": str(self.provenance_path),
            "provenance_label": self.provenance_label,
            "provenance_at": self.provenance_at.isoformat(),
            "confidence": self.confidence,
            "observations": self.observations,
        }


@dataclass(slots=True)
class RecoveryPlan:
    run_id: str
    snapshot_path: Path
    writes: list[RecoveryWrite]
    issues: list[Issue]
    blocked_entries: set[str]
    source_manifest_path: Path

    @property
    def digest(self) -> str:
        payload = {
            "run_id": self.run_id,
            "snapshot_path": str(self.snapshot_path),
            "writes": [item.as_dict() for item in self.writes],
            "issues": [item.as_dict() for item in self.issues],
            "blocked_entries": sorted(self.blocked_entries),
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @property
    def blocked(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "snapshot_path": str(self.snapshot_path),
            "source_manifest_path": str(self.source_manifest_path),
            "writes": [item.as_dict() for item in self.writes],
            "issues": [item.as_dict() for item in self.issues],
            "blocked_entries": sorted(self.blocked_entries),
            "blocked": self.blocked,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class RecoveryStagedFile:
    entry_id: str
    original_path: Path
    source_copy: Path
    staged_path: Path
    source_fingerprint: FileFingerprint
    staged_fingerprint: FileFingerprint
    writes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "original_path": str(self.original_path),
            "source_copy": str(self.source_copy),
            "staged_path": str(self.staged_path),
            "source_fingerprint": self.source_fingerprint.as_dict(),
            "staged_fingerprint": self.staged_fingerprint.as_dict(),
            "writes": self.writes,
        }


@dataclass(frozen=True, slots=True)
class RecoveryStage:
    run_id: str
    run_dir: Path
    plan_digest: str
    files: tuple[RecoveryStagedFile, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "plan_digest": self.plan_digest,
            "files": [item.as_dict() for item in self.files],
        }


@dataclass(slots=True)
class RecoveryValidation:
    run_id: str
    plan_digest: str
    checked_files: int = 0
    checked_cells: int = 0
    expected_changes: int = 0
    issues: list[Issue] | None = None

    def __post_init__(self) -> None:
        if self.issues is None:
            self.issues = []

    @property
    def blocked(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues or [])

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "checked_files": self.checked_files,
            "checked_cells": self.checked_cells,
            "expected_changes": self.expected_changes,
            "issues": [issue.as_dict() for issue in self.issues or []],
            "blocked": self.blocked,
        }


@dataclass(slots=True)
class RecoveryMasterAudit:
    run_id: str
    plan_digest: str
    master_path: Path
    master_fingerprint: FileFingerprint
    checked_cells: int
    counts: dict[str, int]
    conflicts: list[dict[str, Any]]

    @property
    def blocked(self) -> bool:
        return bool(self.conflicts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "master_path": str(self.master_path),
            "master_fingerprint": self.master_fingerprint.as_dict(),
            "checked_cells": self.checked_cells,
            "counts": self.counts,
            "conflicts": self.conflicts,
            "blocked": self.blocked,
        }


@dataclass(slots=True)
class RecoveryPublishResult:
    run_id: str
    plan_digest: str
    published: list[str]
    backups: list[str]
    rolled_back: list[str]
    issues: list[Issue]
    notification: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return bool(self.published) and not any(
            issue.severity == "error" for issue in self.issues
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "published": self.published,
            "backups": self.backups,
            "rolled_back": self.rolled_back,
            "issues": [issue.as_dict() for issue in self.issues],
            "notification": self.notification,
            "ok": self.ok,
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SafetyViolation(f"нет recovery-конфигурации: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SafetyViolation(
            f"битый recovery.json: строка {exc.lineno}, {exc.msg}"
        ) from exc


def load_recovery_config(bundle: ConfigBundle) -> RecoveryConfig:
    path = bundle.config_dir / "recovery.json"
    raw = _read_json(path)
    if int(raw.get("schema_version", 0)) != 1:
        raise SafetyViolation("неподдерживаемая schema_version в recovery.json")
    archive = raw.get("archive") or {}
    fields = tuple(
        RecoveryField(
            title=str(item["title"]),
            profiles=tuple(str(profile) for profile in item["profiles"]),
            max_length=int(item["max_length"]),
            append_only=bool(item.get("append_only", False)),
            not_before=(
                datetime.fromisoformat(str(item["not_before"]))
                if item.get("not_before")
                else None
            ),
        )
        for item in raw.get("fields") or []
    )
    if not fields:
        raise SafetyViolation("в recovery.json нет полей восстановления")
    known_profiles = set(bundle.fields.profile_tags)
    unknown = {
        profile
        for field in fields
        for profile in field.profiles
        if profile not in known_profiles
    }
    if unknown:
        raise SafetyViolation(
            f"recovery.json ссылается на неизвестные profiles: {sorted(unknown)}"
        )
    titles = [norm_title(field.title) for field in fields]
    if len(titles) != len(set(titles)):
        raise SafetyViolation("в recovery.json повторяются заголовки")
    points = tuple(dict(point) for point in archive.get("points") or [])
    for point in points:
        try:
            datetime.fromisoformat(str(point["at"]))
            str(point["folder"])
            str(point["label"])
        except (KeyError, ValueError) as exc:
            raise SafetyViolation(f"битая archive.points: {point}") from exc
    return RecoveryConfig(
        archive_root=Path(bundle.render(str(archive["root"]))),
        archive_points=points,
        vss=dict(raw.get("vss") or {}),
        extra_sources=dict(raw.get("extra_sources") or {}),
        fields=fields,
        safety=dict(raw.get("safety") or {}),
    )


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _snapshot_tokens_windows(path: str) -> list[str]:
    """Перечислить SMB Previous Versions через ``NtFsControlFile``.

    ``DeviceIoControl`` не передаёт этот сетевой FSCTL во всех версиях
    Windows Redirector, поэтому используется документированный NT-вызов.
    """
    if os.name != "nt":
        raise SafetyViolation("SMB Previous Versions поддержаны только на Windows")

    from ctypes import wintypes

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("status", ctypes.c_void_p),
            ("information", ctypes.c_void_p),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    nt_fs_control = ntdll.NtFsControlFile
    nt_fs_control.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(IoStatusBlock),
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    ]
    nt_fs_control.restype = wintypes.ULONG
    rtl_error = ntdll.RtlNtStatusToDosError
    rtl_error.argtypes = [wintypes.ULONG]
    rtl_error.restype = wintypes.ULONG

    # ListDirectory | ReadAttributes | Synchronize
    access = 0x00000001 | 0x00000080 | 0x00100000
    handle = create_file(
        path,
        access,
        0x00000001 | 0x00000002,
        None,
        3,
        0x02000000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        raise SafetyViolation(
            f"не открыть SMB-каталог для VSS: Win32={ctypes.get_last_error()}"
        )
    try:
        def request(size: int) -> bytes:
            buffer = ctypes.create_string_buffer(size)
            iosb = IoStatusBlock()
            status = int(
                nt_fs_control(
                    handle,
                    None,
                    None,
                    None,
                    ctypes.byref(iosb),
                    0x00144064,
                    None,
                    0,
                    buffer,
                    size,
                )
            )
            if status != 0:
                raise SafetyViolation(
                    "не перечислить SMB snapshots: "
                    f"NT=0x{status:08X}, Win32={int(rtl_error(status))}"
                )
            return buffer.raw

        header = request(16)
        total = int.from_bytes(header[0:4], "little")
        byte_count = int.from_bytes(header[8:12], "little")
        if total == 0 or byte_count == 0:
            return []
        payload = request(12 + byte_count)
        returned = int.from_bytes(payload[4:8], "little")
        actual = int.from_bytes(payload[8:12], "little")
        if returned != total:
            raise SafetyViolation(
                f"VSS вернул не все точки: {returned} из {total}"
            )
        text = payload[12 : 12 + actual].decode("utf-16le", errors="strict")
        return [token for token in text.split("\0") if token]
    finally:
        close_handle(handle)


def _selected_vss_points(config: RecoveryConfig) -> list[tuple[str, datetime]]:
    vss = config.vss
    if not bool(vss.get("enabled", False)):
        return []
    tokens = _snapshot_tokens_windows(str(vss["enumeration_path"]))
    offset = timezone(timedelta(hours=int(vss.get("utc_offset_hours", 0))))
    earliest = datetime.fromisoformat(str(vss["from_local_date"])).date()
    parsed: list[tuple[str, datetime]] = []
    for token in tokens:
        try:
            utc = datetime.strptime(token, "@GMT-%Y.%m.%d-%H.%M.%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        local = utc.astimezone(offset)
        if local.date() >= earliest:
            parsed.append((token, local))
    parsed.sort(key=lambda item: item[1])
    if not bool(vss.get("latest_per_local_day", True)):
        return parsed
    by_day: dict[Any, tuple[str, datetime]] = {}
    for item in parsed:
        by_day[item[1].date()] = item
    return [by_day[day] for day in sorted(by_day)]


_STAMP_RE = re.compile(
    r"(\d{2})\.(\d{2})(?:\.(\d{2}|\d{4}))?\s+(\d{2})-(\d{2})"
)


def _backup_timestamp(path: Path, *, fallback: datetime) -> datetime:
    match = _STAMP_RE.search(path.stem)
    if not match:
        return fallback
    day, month, raw_year, hour, minute = match.groups()
    year = fallback.year
    if raw_year:
        year = int(raw_year)
        if year < 100:
            year += 2000
    try:
        return datetime(
            year,
            int(month),
            int(day),
            int(hour),
            int(minute),
            tzinfo=fallback.tzinfo,
        )
    except ValueError:
        return fallback


def _vss_path(config: RecoveryConfig, original: Path, token: str) -> Path:
    mapped = str(config.vss["mapped_root"]).rstrip("\\/")
    raw = str(original)
    if not raw.casefold().startswith(mapped.casefold()):
        raise SafetyViolation(f"путь не лежит под {mapped}: {original}")
    relative = raw[len(mapped) :].lstrip("\\/")
    share = str(config.vss["share_root"]).rstrip("\\/")
    return Path(share) / token / Path(relative)


def discover_recovery_sources(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
    config: RecoveryConfig,
    *,
    now: datetime | None = None,
) -> tuple[list[SourceDescriptor], list[Issue]]:
    now = now or datetime.now().astimezone()
    descriptors: list[SourceDescriptor] = []
    issues: list[Issue] = []
    excluded = re.compile(
        str(config.safety.get("exclude_filename_regex", r"(?!x)x")),
        flags=re.IGNORECASE,
    )
    vss_points = _selected_vss_points(config)
    countries = sorted({item.entry.country for item in snapshot.personals})
    archive_files: dict[tuple[str, str, str], Path] = {}
    for point in config.archive_points:
        for country in countries:
            directory = config.archive_root / str(point["folder"]) / country
            try:
                files = [
                    item
                    for item in directory.iterdir()
                    if item.is_file() and item.suffix.casefold() == ".xlsb"
                ]
            except OSError as exc:
                issues.append(
                    Issue(
                        "warning",
                        "recovery_archive_directory_unreadable",
                        f"не прочитана архивная папка: {directory}",
                        {"error": str(exc)},
                    )
                )
                continue
            for item in files:
                archive_files[
                    (
                        str(point["folder"]),
                        country.casefold(),
                        item.name.casefold(),
                    )
                ] = item

    for captured in snapshot.personals:
        entry = captured.entry
        if excluded.search(entry.path.name):
            issues.append(
                Issue(
                    "warning",
                    "recovery_target_excluded",
                    f"исключён неоднозначный целевой файл: {entry.path.name}",
                    {"entry_id": entry.entry_id, "path": str(entry.path)},
                )
            )
            continue
        for point in config.archive_points:
            source = archive_files.get(
                (
                    str(point["folder"]),
                    entry.country.casefold(),
                    entry.path.name.casefold(),
                )
            )
            if source is not None:
                descriptors.append(
                    SourceDescriptor(
                        entry_id=entry.entry_id,
                        label=str(point["label"]),
                        kind="daily_archive",
                        original_path=source,
                        captured_at=datetime.fromisoformat(str(point["at"])),
                    )
                )

        for token, local_time in vss_points:
            source = _vss_path(config, entry.path, token)
            # Проверка каждого VSS-пути через ``is_file`` даёт отдельный SMB
            # round-trip и на сотнях книг занимает минуты. Источник проверит
            # stable_copy; отсутствующий исторический файл станет warning.
            descriptors.append(
                SourceDescriptor(
                    entry_id=entry.entry_id,
                    label=f"VSS {local_time:%d.%m %H:%M}",
                    kind="vss",
                    original_path=source,
                    captured_at=local_time,
                )
            )

        extra_dirs: list[Path] = []
        if config.extra_sources.get("include_target_folder_backups", True):
            extra_dirs.append(entry.path.parent)
        if config.extra_sources.get("include_project_root_backups", True):
            extra_dirs.append(bundle.project_root)
        prefix = entry.path.stem + " (до "
        seen_dirs: set[str] = set()
        for directory in extra_dirs:
            folded = str(directory).casefold()
            if folded in seen_dirs:
                continue
            seen_dirs.add(folded)
            try:
                candidates = list(directory.glob(f"{entry.path.stem} (до *).xlsb"))
            except OSError as exc:
                issues.append(
                    Issue(
                        "warning",
                        "recovery_extra_source_unreadable",
                        f"не прочитана папка дополнительных копий: {directory}",
                        {"error": str(exc)},
                    )
                )
                continue
            for source in candidates:
                if not source.stem.startswith(prefix):
                    continue
                try:
                    fallback = datetime.fromtimestamp(
                        source.stat().st_mtime,
                        tz=now.tzinfo,
                    )
                except OSError:
                    continue
                descriptors.append(
                    SourceDescriptor(
                        entry_id=entry.entry_id,
                        label=source.stem[len(entry.path.stem) + 1 :],
                        kind="safety_copy",
                        original_path=source,
                        captured_at=_backup_timestamp(source, fallback=fallback),
                    )
                )

    unique: dict[tuple[str, str], SourceDescriptor] = {}
    for item in descriptors:
        key = (item.entry_id, str(item.original_path).casefold())
        unique.setdefault(key, item)
    ordered = sorted(
        unique.values(),
        key=lambda item: (
            item.entry_id,
            item.captured_at,
            str(item.original_path).casefold(),
        ),
    )
    covered = {item.entry_id for item in ordered}
    for captured in snapshot.personals:
        if excluded.search(captured.entry.path.name):
            continue
        if captured.entry.entry_id not in covered:
            issues.append(
                Issue(
                    "warning",
                    "recovery_history_missing",
                    f"нет исторических копий: {captured.entry.path.name}",
                    {"entry_id": captured.entry.entry_id},
                )
            )
    return ordered, issues


def capture_recovery_sources(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
    config: RecoveryConfig,
    *,
    progress: Callable[[int, int, SourceDescriptor], None] | None = None,
) -> tuple[Path, tuple[CapturedSource, ...], list[Issue]]:
    descriptors, issues = discover_recovery_sources(bundle, snapshot, config)
    recovery_dir = snapshot.run_dir / "recovery"
    source_dir = recovery_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    captured: list[CapturedSource] = []
    total = len(descriptors)
    per_entry = Counter()
    for index, item in enumerate(descriptors, 1):
        per_entry[item.entry_id] += 1
        destination = (
            source_dir
            / item.entry_id
            / f"{per_entry[item.entry_id]:03d}-{item.kind}.xlsb"
        )
        try:
            source_fingerprint = stable_copy(item.original_path, destination)
        except (OSError, SafetyViolation) as exc:
            issues.append(
                Issue(
                    "warning",
                    "recovery_source_copy_failed",
                    f"не скопирована историческая точка: {item.label}",
                    {
                        "entry_id": item.entry_id,
                        "path": str(item.original_path),
                        "error": str(exc),
                    },
                )
            )
            continue
        captured.append(
            CapturedSource(
                entry_id=item.entry_id,
                label=item.label,
                kind=item.kind,
                original_path=item.original_path,
                copy_path=destination,
                captured_at=item.captured_at,
                fingerprint=source_fingerprint,
            )
        )
        if progress is not None:
            progress(index, total, item)
    manifest_path = recovery_dir / "source_manifest.json"
    _atomic_json(
        manifest_path,
        {
            "run_id": snapshot.run_id,
            "snapshot_path": str(snapshot.run_dir / "snapshot.json"),
            "sources": [item.as_dict() for item in captured],
            "issues": [issue.as_dict() for issue in issues],
            "counts": {
                "discovered": total,
                "captured": len(captured),
                "entries": len({item.entry_id for item in captured}),
            },
        },
    )
    return manifest_path, tuple(captured), issues


def load_captured_sources(path: str | Path) -> tuple[CapturedSource, ...]:
    payload = _read_json(Path(path))
    out = []
    for item in payload.get("sources") or []:
        raw_fp = item["fingerprint"]
        captured = CapturedSource(
            entry_id=str(item["entry_id"]),
            label=str(item["label"]),
            kind=str(item["kind"]),
            original_path=Path(item["original_path"]),
            copy_path=Path(item["copy_path"]),
            captured_at=datetime.fromisoformat(str(item["captured_at"])),
            fingerprint=FileFingerprint(
                size=int(raw_fp["size"]),
                mtime_ns=int(raw_fp["mtime_ns"]),
                sha256=str(raw_fp["sha256"]),
            ),
        )
        if not captured.copy_path.is_file():
            raise SafetyViolation(
                f"в recovery manifest отсутствует копия: {captured.copy_path}"
            )
        out.append(captured)
    return tuple(out)


def _text_value(value: Any) -> str:
    normalized = json_value(value)
    if normalized is None:
        return ""
    return str(normalized).strip()


def _contains_all(latest: str, earlier: Iterable[str]) -> bool:
    haystack = re.sub(r"\s+", " ", latest).casefold()
    return all(
        re.sub(r"\s+", " ", value).casefold() in haystack
        for value in earlier
    )


def _row_value(snapshot: WorkbookSnapshot, row: tuple[Any, ...], column: int) -> Any:
    return snapshot.row_value(row, column)


def _rules_for_profile(
    config: RecoveryConfig,
    profile: str,
) -> tuple[RecoveryField, ...]:
    return tuple(field for field in config.fields if profile in field.profiles)


def _source_observations(
    source: WorkbookSnapshot,
    *,
    contract_id: str,
    title: str,
    current_responsible: str,
    bundle: ConfigBundle,
) -> tuple[list[str], str | None]:
    key_rows = source.rows_by_key(bundle.fields.key_title).get(contract_id, [])
    if not key_rows:
        return [], None
    column = source.column_index(title)
    responsible_column = source.column_index(bundle.fields.responsible_title)
    if column is None or responsible_column is None:
        return [], None
    values: list[str] = []
    for _row_index, row in key_rows:
        source_responsible = norm_name(_row_value(source, row, responsible_column))
        if source_responsible != current_responsible:
            continue
        value = _text_value(_row_value(source, row, column))
        if value:
            values.append(value)
    distinct = list(dict.fromkeys(values))
    if len(distinct) > 1:
        return [], "duplicate_source_values"
    return distinct, None


def build_recovery_plan(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
    config: RecoveryConfig,
    source_manifest_path: str | Path,
    *,
    snapshot_path: str | Path | None = None,
) -> RecoveryPlan:
    captured_sources = load_captured_sources(source_manifest_path)
    sources_by_entry: dict[str, list[CapturedSource]] = defaultdict(list)
    for source in captured_sources:
        sources_by_entry[source.entry_id].append(source)
    for values in sources_by_entry.values():
        values.sort(key=lambda item: (item.captured_at, item.label))

    issues: list[Issue] = []
    writes: list[RecoveryWrite] = []
    blocked_entries: set[str] = set()
    workbook_cache: dict[str, WorkbookSnapshot] = {}
    excluded = re.compile(
        str(config.safety.get("exclude_filename_regex", r"(?!x)x")),
        flags=re.IGNORECASE,
    )

    def read_cached(path: Path, sha256: str | None = None) -> WorkbookSnapshot:
        cache_key = sha256 or str(path.resolve()).casefold()
        if cache_key not in workbook_cache:
            workbook_cache[cache_key] = read_portfolio(path, bundle.fields)
        return workbook_cache[cache_key]

    for personal in snapshot.personals:
        entry = personal.entry
        if excluded.search(entry.path.name):
            blocked_entries.add(entry.entry_id)
            continue
        history = sources_by_entry.get(entry.entry_id, [])
        if not history:
            continue
        try:
            current = read_cached(personal.copy_path, personal.fingerprint.sha256)
        except Exception as exc:  # noqa: BLE001
            blocked_entries.add(entry.entry_id)
            issues.append(
                Issue(
                    "error",
                    "recovery_current_unreadable",
                    f"не прочитан текущий snapshot: {entry.path.name}",
                    {"entry_id": entry.entry_id, "error": str(exc)},
                )
            )
            continue
        rules = _rules_for_profile(config, entry.profile)
        missing = [
            rule.title
            for rule in rules
            if current.column_index(rule.title) is None
        ]
        if missing:
            blocked_entries.add(entry.entry_id)
            issues.append(
                Issue(
                    "error",
                    "recovery_columns_missing",
                    f"в текущем файле нет колонок восстановления: {entry.path.name}",
                    {"entry_id": entry.entry_id, "titles": missing},
                )
            )
            continue
        responsible_column = current.column_index(bundle.fields.responsible_title)
        if responsible_column is None:
            blocked_entries.add(entry.entry_id)
            continue
        history_books: list[tuple[CapturedSource, WorkbookSnapshot]] = []
        for source in history:
            try:
                history_books.append(
                    (
                        source,
                        read_cached(source.copy_path, source.fingerprint.sha256),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                issues.append(
                    Issue(
                        "warning",
                        "recovery_history_unreadable",
                        f"не прочитана историческая копия: {source.label}",
                        {
                            "entry_id": entry.entry_id,
                            "path": str(source.original_path),
                            "error": str(exc),
                        },
                    )
                )
        current_by_key = current.rows_by_key(bundle.fields.key_title)
        file_writes: list[RecoveryWrite] = []
        history_conflicts = 0
        duplicate_conflicts = 0
        foreign_rows = 0
        before_cutoff = 0

        for contract_id, current_rows in current_by_key.items():
            responsibles = {
                norm_name(_row_value(current, row, responsible_column))
                for _row_index, row in current_rows
                if norm_name(_row_value(current, row, responsible_column))
            }
            if len(responsibles) != 1:
                foreign_rows += 1
                continue
            current_responsible = next(iter(responsibles))
            responsible_display = _text_value(
                _row_value(current, current_rows[0][1], responsible_column)
            )
            for rule in rules:
                column = current.column_index(rule.title)
                assert column is not None
                current_values = {
                    _text_value(_row_value(current, row, column))
                    for _row_index, row in current_rows
                    if _text_value(_row_value(current, row, column))
                }
                if len(current_values) > 1:
                    duplicate_conflicts += 1
                    continue
                if current_values:
                    # Текущее значение имеет безусловный приоритет, даже если
                    # соседняя строка того же КД пуста.
                    continue

                observations: list[tuple[CapturedSource, str]] = []
                source_conflict = False
                for source_ref, source_book in history_books:
                    values, conflict = _source_observations(
                        source_book,
                        contract_id=contract_id,
                        title=rule.title,
                        current_responsible=current_responsible,
                        bundle=bundle,
                    )
                    if conflict:
                        source_conflict = True
                        break
                    if values:
                        if (
                            rule.not_before is not None
                            and source_ref.captured_at < rule.not_before
                        ):
                            before_cutoff += 1
                            continue
                        observations.append((source_ref, values[0]))
                if source_conflict:
                    duplicate_conflicts += 1
                    continue
                if not observations:
                    continue
                latest_source, latest_value = observations[-1]
                distinct = list(dict.fromkeys(value for _source, value in observations))
                if len(distinct) == 1:
                    confidence = (
                        "stable_history"
                        if len(observations) > 1
                        else "single_exact_backup"
                    )
                elif rule.append_only and _contains_all(latest_value, distinct[:-1]):
                    confidence = "monotonic_superset"
                else:
                    history_conflicts += 1
                    continue
                if len(latest_value) > rule.max_length:
                    issues.append(
                        Issue(
                            "warning",
                            "recovery_value_too_long",
                            f"историческое значение длиннее лимита: {entry.path.name}",
                            {
                                "entry_id": entry.entry_id,
                                "contract_id": contract_id,
                                "title": rule.title,
                                "length": len(latest_value),
                                "limit": rule.max_length,
                            },
                        )
                    )
                    continue
                for row_index, row in current_rows:
                    before = _row_value(current, row, column)
                    if not is_blank(before):
                        continue
                    file_writes.append(
                        RecoveryWrite(
                            entry_id=entry.entry_id,
                            original_path=entry.path,
                            source_copy=personal.copy_path,
                            sheet=current.sheet,
                            row=row_index,
                            column=column,
                            title=rule.title,
                            contract_id=contract_id,
                            responsible=responsible_display,
                            before=before,
                            after=latest_value,
                            provenance_path=latest_source.original_path,
                            provenance_label=latest_source.label,
                            provenance_at=latest_source.captured_at,
                            confidence=confidence,
                            observations=len(observations),
                        )
                    )

        if history_conflicts:
            issues.append(
                Issue(
                    "warning",
                    "recovery_history_conflicts",
                    f"история расходится, ячейки пропущены: {entry.path.name}",
                    {
                        "entry_id": entry.entry_id,
                        "count": history_conflicts,
                    },
                )
            )
        if duplicate_conflicts:
            issues.append(
                Issue(
                    "warning",
                    "recovery_duplicate_conflicts",
                    f"конфликты дублей КД, ячейки пропущены: {entry.path.name}",
                    {
                        "entry_id": entry.entry_id,
                        "count": duplicate_conflicts,
                    },
                )
            )
        if foreign_rows:
            issues.append(
                Issue(
                    "warning",
                    "recovery_responsible_conflicts",
                    f"неоднозначный ответственный, КД пропущены: {entry.path.name}",
                    {"entry_id": entry.entry_id, "count": foreign_rows},
                )
            )
        if before_cutoff:
            issues.append(
                Issue(
                    "info",
                    "recovery_before_field_cutoff",
                    f"досезонные значения не включены: {entry.path.name}",
                    {"entry_id": entry.entry_id, "observations": before_cutoff},
                )
            )

        max_writes = int(config.safety.get("max_writes_per_file", 300))
        max_share = float(config.safety.get("max_write_share", 0.25))
        data_rows = max(len(current.rows), 1)
        block_reasons = []
        if len(file_writes) > max_writes:
            block_reasons.append(f"{len(file_writes)} > {max_writes} ячеек")
        if len(file_writes) > data_rows * max_share:
            block_reasons.append(
                f"{len(file_writes)} > {max_share:.0%} от {data_rows} строк"
            )
        if block_reasons:
            blocked_entries.add(entry.entry_id)
            issues.append(
                Issue(
                    "error",
                    "recovery_file_limit_exceeded",
                    f"файл требует отдельного разбора: {entry.path.name}",
                    {
                        "entry_id": entry.entry_id,
                        "writes": len(file_writes),
                        "data_rows": data_rows,
                        "reasons": block_reasons,
                    },
                )
            )
            continue
        writes.extend(file_writes)

    plan = RecoveryPlan(
        run_id=snapshot.run_id,
        snapshot_path=Path(snapshot_path or snapshot.run_dir / "snapshot.json"),
        writes=writes,
        issues=issues,
        blocked_entries=blocked_entries,
        source_manifest_path=Path(source_manifest_path),
    )
    recovery_dir = snapshot.run_dir / "recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(recovery_dir / "plan.json", plan.as_dict())
    _write_plan_csv(recovery_dir / "plan.csv", plan.writes)
    _write_summary(recovery_dir / "summary.md", plan)
    return plan


def _write_plan_csv(path: Path, writes: list[RecoveryWrite]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "сотрудник",
                "файл",
                "строка",
                "колонка",
                "номер КД",
                "поле",
                "значение",
                "источник",
                "точка",
                "уверенность",
                "наблюдений",
            ]
        )
        for item in writes:
            writer.writerow(
                [
                    item.responsible,
                    str(item.original_path),
                    item.row + 1,
                    item.column + 1,
                    item.contract_id,
                    item.title,
                    item.after,
                    str(item.provenance_path),
                    item.provenance_label,
                    item.confidence,
                    item.observations,
                ]
            )


def _write_summary(path: Path, plan: RecoveryPlan) -> None:
    by_file = Counter(str(item.original_path) for item in plan.writes)
    by_field = Counter(item.title for item in plan.writes)
    by_confidence = Counter(item.confidence for item in plan.writes)
    lines = [
        "# План аддитивного восстановления",
        "",
        f"- Ячеек к восстановлению: **{len(plan.writes)}**",
        f"- Файлов: **{len(by_file)}**",
        f"- Заблокированных файлов: **{len(plan.blocked_entries)}**",
        f"- Digest: `{plan.digest}`",
        "",
        "## По полям",
        "",
        "| Поле | Ячеек |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {title} | {count} |"
        for title, count in by_field.most_common()
    )
    lines.extend(
        [
            "",
            "## По уверенности",
            "",
            "| Класс | Ячеек |",
            "|---|---:|",
        ]
    )
    lines.extend(
        f"| {label} | {count} |"
        for label, count in by_confidence.most_common()
    )
    lines.extend(
        [
            "",
            "Текущие непустые ячейки в план не входят. Расходящаяся история,",
            "чужой ответственный, конфликтующие дубли и превышение лимита",
            "автоматически пропущены.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _fingerprint_from_dict(payload: dict[str, Any]) -> FileFingerprint:
    return FileFingerprint(
        size=int(payload["size"]),
        mtime_ns=int(payload["mtime_ns"]),
        sha256=str(payload["sha256"]),
    )


def load_recovery_plan(path: str | Path) -> RecoveryPlan:
    plan_path = Path(path)
    payload = _read_json(plan_path)
    writes = []
    for item in payload.get("writes") or []:
        writes.append(
            RecoveryWrite(
                entry_id=str(item["entry_id"]),
                original_path=Path(item["original_path"]),
                source_copy=Path(item["source_copy"]),
                sheet=str(item["sheet"]),
                row=int(item["row_index"]),
                column=int(item["column_index"]),
                title=str(item["title"]),
                contract_id=str(item["contract_id"]),
                responsible=str(item["responsible"]),
                before=item.get("before"),
                after=str(item["after"]),
                provenance_path=Path(item["provenance_path"]),
                provenance_label=str(item["provenance_label"]),
                provenance_at=datetime.fromisoformat(str(item["provenance_at"])),
                confidence=str(item["confidence"]),
                observations=int(item["observations"]),
            )
        )
    issues = [
        Issue(
            severity=str(item["severity"]),
            code=str(item["code"]),
            message=str(item["message"]),
            context=dict(item.get("context") or {}),
        )
        for item in payload.get("issues") or []
    ]
    plan = RecoveryPlan(
        run_id=str(payload["run_id"]),
        snapshot_path=Path(payload["snapshot_path"]),
        writes=writes,
        issues=issues,
        blocked_entries=set(str(value) for value in payload.get("blocked_entries") or []),
        source_manifest_path=Path(payload["source_manifest_path"]),
    )
    expected = str(payload.get("digest") or "")
    if plan.digest != expected:
        raise SafetyViolation(
            f"digest recovery-плана не совпал: {plan.digest} != {expected}"
        )
    return plan


def audit_recovery_against_master(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
    plan: RecoveryPlan,
) -> RecoveryMasterAudit:
    """Запретить архивной записи противоречить ночному общему реестру.

    Пустота в мастере и дословное совпадение безопасны. Отсутствующий КД,
    другой ответственный, отсутствующая колонка или отличающееся непустое
    значение требуют ручного разбора и блокируют staging.
    """
    master = read_portfolio(snapshot.master_copy, bundle.fields)
    if master.fingerprint != snapshot.master_fingerprint:
        raise SafetyViolation("копия общего реестра изменилась после snapshot")

    responsible_column = master.column_index(bundle.fields.responsible_title)
    rows_by_key = master.rows_by_key(bundle.fields.key_title)
    counts: Counter[str] = Counter()
    conflicts: list[dict[str, Any]] = []

    for change in plan.writes:
        column = master.column_index(change.title)
        key_rows = rows_by_key.get(change.contract_id, [])
        state: str
        master_values: list[Any] = []
        if column is None:
            state = "missing_column"
        elif responsible_column is None:
            state = "missing_responsible_column"
        elif not key_rows:
            state = "missing_contract"
        else:
            same_responsible = [
                row
                for _row_index, row in key_rows
                if norm_name(_row_value(master, row, responsible_column))
                == norm_name(change.responsible)
            ]
            if not same_responsible:
                state = "responsible_mismatch"
            else:
                master_values = [
                    _row_value(master, row, column)
                    for row in same_responsible
                    if not is_blank(_row_value(master, row, column))
                ]
                if not master_values:
                    state = "blank"
                elif all(
                    comparable_value(value) == comparable_value(change.after)
                    for value in master_values
                ):
                    state = "equal"
                else:
                    state = "different_nonblank"
        counts[state] += 1
        if state not in {"blank", "equal"} and len(conflicts) < 100:
            conflicts.append(
                {
                    "entry_id": change.entry_id,
                    "file": str(change.original_path),
                    "contract_id": change.contract_id,
                    "responsible": change.responsible,
                    "title": change.title,
                    "recovery_value": change.after,
                    "master_values": [json_value(value) for value in master_values],
                    "state": state,
                }
            )

    audit = RecoveryMasterAudit(
        run_id=snapshot.run_id,
        plan_digest=plan.digest,
        master_path=snapshot.master_original,
        master_fingerprint=snapshot.master_fingerprint,
        checked_cells=len(plan.writes),
        counts=dict(sorted(counts.items())),
        conflicts=conflicts,
    )
    recovery_dir = snapshot.run_dir / "recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(recovery_dir / "master_audit.json", audit.as_dict())
    return audit


def _write_runs_to_book(
    host: ExcelHost,
    staged_path: Path,
    changes: list[RecoveryWrite],
    bundle: ConfigBundle,
) -> None:
    snapshot = read_portfolio(staged_path, bundle.fields)
    with host.open_book(staged_path, read_only=False) as book:
        sheet = sheet_by_name(book, bundle.fields.sheet_name)
        first_row = min(item.row + 1 for item in changes)
        last_row = max(item.row + 1 for item in changes)
        with _revealed_rows(
            sheet,
            first_row=first_row,
            last_row=last_row,
        ):
            ordered = sorted(changes, key=lambda item: (item.column, item.row))
            runs: list[list[RecoveryWrite]] = []
            for item in ordered:
                if (
                    runs
                    and runs[-1][-1].column == item.column
                    and runs[-1][-1].row + 1 == item.row
                ):
                    runs[-1].append(item)
                else:
                    runs.append([item])
            for run in runs:
                first, last = run[0], run[-1]
                target = sheet.range(
                    (first.row + 1, first.column + 1),
                    (last.row + 1, last.column + 1),
                )
                if range_has_formulas(target):
                    raise SafetyViolation(
                        "recovery staging: целевой диапазон содержит формулу: "
                        f"{staged_path.name}, {first.title}"
                    )
                current = target.value
                if len(run) == 1:
                    current_values = [current]
                elif isinstance(current, list):
                    current_values = current
                else:
                    current_values = [current]
                for change, value in zip(run, current_values, strict=True):
                    if isinstance(value, list) and len(value) == 1:
                        value = value[0]
                    if not is_blank(value):
                        raise SafetyViolation(
                            "recovery staging: ячейка перестала быть пустой: "
                            f"{staged_path.name}, {change.row + 1}:{change.column + 1}"
                        )
                target.value = [
                    [excel_safe_value(change.after, "text")]
                    for change in run
                ]
        book.api.Save()
    # Сохранение не должно менять размер таблицы/координаты данных.
    after = read_portfolio(staged_path, bundle.fields)
    if (
        snapshot.header_row,
        snapshot.type_row,
        snapshot.data_start,
        snapshot.headers,
        len(snapshot.rows),
    ) != (
        after.header_row,
        after.type_row,
        after.data_start,
        after.headers,
        len(after.rows),
    ):
        raise SafetyViolation(f"recovery staging изменил структуру: {staged_path.name}")


def build_recovery_staging(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
    plan: RecoveryPlan,
) -> RecoveryStage:
    unexpected_errors = [
        issue
        for issue in plan.issues
        if issue.severity == "error"
        and issue.code != "recovery_file_limit_exceeded"
    ]
    if unexpected_errors:
        raise SafetyViolation(
            "recovery-план содержит глобальные ошибки: "
            + ", ".join(issue.code for issue in unexpected_errors)
        )
    master_audit = audit_recovery_against_master(bundle, snapshot, plan)
    if master_audit.blocked:
        raise SafetyViolation(
            "recovery-план противоречит общему реестру: "
            + ", ".join(
                f"{state}={count}"
                for state, count in master_audit.counts.items()
                if state not in {"blank", "equal"}
            )
        )
    by_entry: dict[str, list[RecoveryWrite]] = defaultdict(list)
    for item in plan.writes:
        if item.entry_id in plan.blocked_entries:
            raise SafetyViolation(
                f"запись ссылается на заблокированный entry: {item.entry_id}"
            )
        by_entry[item.entry_id].append(item)
    if not by_entry:
        raise SafetyViolation("recovery-план не содержит разрешённых записей")

    personals = {item.entry.entry_id: item for item in snapshot.personals}
    stage_dir = snapshot.run_dir / "recovery" / f"staging-{plan.digest[:12]}"
    stage_dir.mkdir(parents=True, exist_ok=False)
    pending: list[tuple[Any, Path, list[RecoveryWrite]]] = []
    for entry_id, changes in sorted(by_entry.items()):
        source = personals.get(entry_id)
        if source is None:
            raise SafetyViolation(f"entry отсутствует в snapshot: {entry_id}")
        destination = stage_dir / f"{entry_id}{source.copy_path.suffix.casefold()}"
        stable_copy(source.copy_path, destination)
        pending.append((source, destination, changes))

    staged_files: list[RecoveryStagedFile] = []
    try:
        with ExcelHost() as host:
            for source, destination, changes in pending:
                _write_runs_to_book(host, destination, changes, bundle)
                from .files import fingerprint

                staged_files.append(
                    RecoveryStagedFile(
                        entry_id=source.entry.entry_id,
                        original_path=source.entry.path,
                        source_copy=source.copy_path,
                        staged_path=destination,
                        source_fingerprint=source.fingerprint,
                        staged_fingerprint=fingerprint(destination),
                        writes=len(changes),
                    )
                )
    except Exception:
        # Staging локален и остаётся как доказательство сбоя; он не может быть
        # загружен без полного stage.json.
        raise
    stage = RecoveryStage(
        run_id=snapshot.run_id,
        run_dir=snapshot.run_dir,
        plan_digest=plan.digest,
        files=tuple(staged_files),
    )
    _atomic_json(stage_dir / "stage.json", stage.as_dict())
    return stage


def load_recovery_stage(path: str | Path) -> RecoveryStage:
    stage_path = Path(path)
    payload = _read_json(stage_path)
    files = []
    for item in payload.get("files") or []:
        staged = RecoveryStagedFile(
            entry_id=str(item["entry_id"]),
            original_path=Path(item["original_path"]),
            source_copy=Path(item["source_copy"]),
            staged_path=Path(item["staged_path"]),
            source_fingerprint=_fingerprint_from_dict(item["source_fingerprint"]),
            staged_fingerprint=_fingerprint_from_dict(item["staged_fingerprint"]),
            writes=int(item["writes"]),
        )
        if not staged.source_copy.is_file() or not staged.staged_path.is_file():
            raise SafetyViolation(f"неполный recovery staging: {staged.entry_id}")
        files.append(staged)
    return RecoveryStage(
        run_id=str(payload["run_id"]),
        run_dir=Path(payload["run_dir"]),
        plan_digest=str(payload["plan_digest"]),
        files=tuple(files),
    )


def _snapshot_cell(
    snapshot: WorkbookSnapshot,
    absolute_row: int,
    column: int,
) -> Any:
    offset = absolute_row - snapshot.data_start
    if offset < 0 or offset >= len(snapshot.rows):
        return None
    return snapshot.row_value(snapshot.rows[offset], column)


def _validate_structure(
    source: WorkbookSnapshot,
    staged: WorkbookSnapshot,
) -> bool:
    return (
        source.sheet_names == staged.sheet_names
        and source.header_row == staged.header_row
        and source.type_row == staged.type_row
        and source.data_start == staged.data_start
        and source.headers == staged.headers
        and source.type_tags == staged.type_tags
        and len(source.rows) == len(staged.rows)
    )


def validate_recovery_staging(
    bundle: ConfigBundle,
    stage: RecoveryStage,
    plan: RecoveryPlan,
) -> RecoveryValidation:
    report = RecoveryValidation(
        run_id=stage.run_id,
        plan_digest=plan.digest,
        expected_changes=len(plan.writes),
    )
    assert report.issues is not None
    if stage.plan_digest != plan.digest:
        report.issues.append(
            Issue(
                "error",
                "recovery_stage_digest_mismatch",
                "digest staging не совпал с recovery-планом",
            )
        )
        return report
    by_entry: dict[str, list[RecoveryWrite]] = defaultdict(list)
    for item in plan.writes:
        by_entry[item.entry_id].append(item)
    if set(by_entry) != {item.entry_id for item in stage.files}:
        report.issues.append(
            Issue(
                "error",
                "recovery_stage_file_set_mismatch",
                "набор файлов staging не совпал с планом",
            )
        )
        return report

    for item in stage.files:
        from .files import fingerprint

        if fingerprint(item.staged_path) != item.staged_fingerprint:
            report.issues.append(
                Issue(
                    "error",
                    "recovery_stage_changed",
                    f"staging изменился после сборки: {item.staged_path.name}",
                )
            )
            continue
        source = read_portfolio(item.source_copy, bundle.fields)
        staged = read_portfolio(item.staged_path, bundle.fields)
        if not _validate_structure(source, staged):
            report.issues.append(
                Issue(
                    "error",
                    "recovery_stage_structure_changed",
                    f"изменилась структура книги: {item.original_path.name}",
                )
            )
            continue
        allowed = {
            (change.row, change.column): change
            for change in by_entry[item.entry_id]
        }
        actual_diffs: set[tuple[int, int]] = set()
        missing: list[dict[str, Any]] = []
        forbidden: list[dict[str, Any]] = []
        for offset in range(len(source.rows)):
            absolute_row = source.data_start + offset
            for column in range(len(source.headers)):
                before = _snapshot_cell(source, absolute_row, column)
                after = _snapshot_cell(staged, absolute_row, column)
                report.checked_cells += 1
                if comparable_value(before) == comparable_value(after):
                    continue
                coordinate = (absolute_row, column)
                actual_diffs.add(coordinate)
                change = allowed.get(coordinate)
                if change is None:
                    if len(forbidden) < 100:
                        forbidden.append(
                            {
                                "row": absolute_row + 1,
                                "column": column + 1,
                                "title": source.headers[column],
                                "before": json_value(before),
                                "after": json_value(after),
                            }
                        )
                    continue
                if _text_value(after) != change.after:
                    if len(missing) < 100:
                        missing.append(
                            {
                                "row": absolute_row + 1,
                                "column": column + 1,
                                "expected": change.after,
                                "actual": _text_value(after),
                            }
                        )
        expected = set(allowed)
        for coordinate in expected - actual_diffs:
            change = allowed[coordinate]
            after = _snapshot_cell(staged, change.row, change.column)
            if _text_value(after) != change.after and len(missing) < 100:
                missing.append(
                    {
                        "row": change.row + 1,
                        "column": change.column + 1,
                        "expected": change.after,
                        "actual": _text_value(after),
                    }
                )
        if forbidden:
            report.issues.append(
                Issue(
                    "error",
                    "recovery_forbidden_diff",
                    f"Excel изменил ячейки вне плана: {item.original_path.name}",
                    {"samples": forbidden},
                )
            )
        if missing:
            report.issues.append(
                Issue(
                    "error",
                    "recovery_missing_expected_diff",
                    f"не все значения записаны: {item.original_path.name}",
                    {"samples": missing},
                )
            )
        if actual_diffs != expected:
            report.issues.append(
                Issue(
                    "error",
                    "recovery_diff_set_mismatch",
                    f"фактический diff не равен плану: {item.original_path.name}",
                    {
                        "expected": len(expected),
                        "actual": len(actual_diffs),
                        "extra": len(actual_diffs - expected),
                        "missing": len(expected - actual_diffs),
                    },
                )
            )
        # Идемпотентность для утверждённых координат: второй проход обязан
        # увидеть их непустыми и не предложить ни одной повторной записи.
        repeated = [
            change
            for change in allowed.values()
            if is_blank(_snapshot_cell(staged, change.row, change.column))
        ]
        if repeated:
            report.issues.append(
                Issue(
                    "error",
                    "recovery_not_idempotent",
                    f"повторный план снова видит пустоты: {item.original_path.name}",
                    {"count": len(repeated)},
                )
            )
        report.checked_files += 1

    # Отдельная COM-проверка листов, формул/констант вне «Портфелей» и фильтров.
    try:
        with ExcelHost() as host:
            for item in stage.files:
                with host.open_book(item.source_copy, read_only=True) as source_book:
                    source_names = tuple(source_book.sheet_names)
                    source_signatures = workbook_untouched_signatures(
                        source_book,
                        mutable_sheet=bundle.fields.sheet_name,
                    )
                    source_filter = sheet_filter_signature(
                        sheet_by_name(source_book, bundle.fields.sheet_name)
                    )
                with host.open_book(item.staged_path, read_only=True) as staged_book:
                    staged_names = tuple(staged_book.sheet_names)
                    staged_signatures = workbook_untouched_signatures(
                        staged_book,
                        mutable_sheet=bundle.fields.sheet_name,
                    )
                    staged_filter = sheet_filter_signature(
                        sheet_by_name(staged_book, bundle.fields.sheet_name)
                    )
                if (
                    source_names != staged_names
                    or source_signatures != staged_signatures
                    or source_filter != staged_filter
                ):
                    report.issues.append(
                        Issue(
                            "error",
                            "recovery_workbook_semantics_changed",
                            f"изменились листы/фильтры вне плана: {item.original_path.name}",
                            {
                                "sheet_names_equal": source_names == staged_names,
                                "untouched_sheets_equal": (
                                    source_signatures == staged_signatures
                                ),
                                "filter_equal": source_filter == staged_filter,
                            },
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        report.issues.append(
            Issue(
                "error",
                "recovery_semantic_validation_failed",
                "не завершена COM-проверка recovery staging",
                {"error": str(exc)},
            )
        )

    _atomic_json(
        Path(stage.run_dir) / "recovery" / "validation.json",
        report.as_dict(),
    )
    return report


def _exclusive_file_check(path: Path) -> None:
    if os.name != "nt":
        return
    import pywintypes
    import win32con
    import win32file

    try:
        handle = win32file.CreateFile(
            str(path),
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            0,
            None,
            win32con.OPEN_EXISTING,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None,
        )
    except pywintypes.error as exc:
        raise SafetyViolation(f"файл занят или недоступен: {path} ({exc})") from exc
    else:
        handle.Close()


def _unique_backup_path(path: Path, stamp: datetime) -> Path:
    label = stamp.strftime("%d.%m.%y %H-%M")
    candidate = path.with_name(
        f"{path.stem} (до восстановления из архивов {label}){path.suffix}"
    )
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = path.with_name(
            f"{path.stem} (до восстановления из архивов {label}, {counter})"
            f"{path.suffix}"
        )
    return candidate


def publish_recovery(
    bundle: ConfigBundle,
    stage: RecoveryStage,
    plan: RecoveryPlan,
    *,
    confirmation_digest: str,
    notify_owner: bool = True,
) -> RecoveryPublishResult:
    if not (
        confirmation_digest
        == plan.digest
        == stage.plan_digest
    ):
        raise SafetyViolation("digest подтверждения recovery не совпал")
    validation = validate_recovery_staging(bundle, stage, plan)
    if validation.blocked:
        raise SafetyViolation("recovery staging не прошёл повторную validation")
    result = RecoveryPublishResult(
        run_id=stage.run_id,
        plan_digest=plan.digest,
        published=[],
        backups=[],
        rolled_back=[],
        issues=[],
    )
    from .files import fingerprint

    backup_by_entry: dict[str, Path] = {}
    temp_by_entry: dict[str, Path] = {}
    replaced: list[RecoveryStagedFile] = []
    stamp = datetime.now()
    try:
        for item in stage.files:
            lock = item.original_path.with_name("~$" + item.original_path.name)
            if lock.exists():
                raise SafetyViolation(
                    f"Excel lock-файл у цели: {item.original_path}"
                )
            _exclusive_file_check(item.original_path)
            if fingerprint(item.original_path) != item.source_fingerprint:
                raise SafetyViolation(
                    f"живой файл изменился после snapshot: {item.original_path}"
                )
            if fingerprint(item.staged_path) != item.staged_fingerprint:
                raise SafetyViolation(
                    f"staging изменился после validation: {item.staged_path}"
                )

        for item in stage.files:
            backup = _unique_backup_path(item.original_path, stamp)
            stable_copy(item.original_path, backup)
            backup_by_entry[item.entry_id] = backup
            result.backups.append(str(backup))
            temporary = item.original_path.parent / (
                f".registry-recovery-{stage.run_id}-{item.entry_id}-"
                f"{secrets.token_hex(3)}.next{item.original_path.suffix.casefold()}"
            )
            stable_copy(item.staged_path, temporary)
            if fingerprint(temporary) != item.staged_fingerprint:
                raise SafetyViolation(
                    f"сетевая temp-копия не совпала: {item.original_path}"
                )
            temp_by_entry[item.entry_id] = temporary

        # Закрываем окно между подготовкой temp и заменой.
        for item in stage.files:
            _exclusive_file_check(item.original_path)
            if fingerprint(item.original_path) != item.source_fingerprint:
                raise SafetyViolation(
                    f"живой файл изменился при подготовке: {item.original_path}"
                )

        for item in stage.files:
            temporary = temp_by_entry[item.entry_id]
            os.replace(temporary, item.original_path)
            replaced.append(item)
            if fingerprint(item.original_path) != item.staged_fingerprint:
                raise SafetyViolation(
                    f"проверка после публикации не прошла: {item.original_path}"
                )
            result.published.append(str(item.original_path))
    except Exception as exc:  # noqa: BLE001
        rollback_failures = []
        for item in reversed(replaced):
            backup = backup_by_entry[item.entry_id]
            rollback_temp = item.original_path.parent / (
                f".registry-recovery-{stage.run_id}-{item.entry_id}-"
                f"{secrets.token_hex(3)}.rollback{item.original_path.suffix.casefold()}"
            )
            try:
                stable_copy(backup, rollback_temp)
                os.replace(rollback_temp, item.original_path)
                if fingerprint(item.original_path) != item.source_fingerprint:
                    raise SafetyViolation("SHA rollback не совпал")
                result.rolled_back.append(str(item.original_path))
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_failures.append(
                    {"path": str(item.original_path), "error": str(rollback_exc)}
                )
        for temporary in temp_by_entry.values():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        result.issues.append(
            Issue(
                "error",
                "recovery_publish_failed",
                "публикация recovery отменена; выполнен rollback",
                {"error": str(exc), "rollback_failures": rollback_failures},
            )
        )
        _atomic_json(
            Path(stage.run_dir) / "recovery" / "publish.json",
            result.as_dict(),
        )
        if rollback_failures:
            raise SafetyViolation(
                "recovery rollback неполон: "
                + ", ".join(item["path"] for item in rollback_failures)
            ) from exc
        raise SafetyViolation(
            f"recovery публикация отменена и откачена: {exc}"
        ) from exc

    if notify_owner:
        from .notifications import OwnerNotifier

        notice = OwnerNotifier(bundle).send(
            "Восстановление из архивов завершено.\n"
            f"Ячеек: {len(plan.writes)}\n"
            f"Файлов: {len(stage.files)}\n"
            f"Пропущено по лимиту: "
            f"{sum(i.code == 'recovery_file_limit_exceeded' for i in plan.issues)}\n"
            f"Digest: {plan.digest[:16]}"
        )
        result.notification = {
            "sent": notice.sent,
            "skipped": notice.skipped,
            "detail": notice.detail,
            "audience": "owner_only",
        }
    _atomic_json(
        Path(stage.run_dir) / "recovery" / "publish.json",
        result.as_dict(),
    )
    return result


def _summary_payload(plan: RecoveryPlan) -> dict[str, Any]:
    return {
        "run_id": plan.run_id,
        "writes": len(plan.writes),
        "files": len({str(item.original_path) for item in plan.writes}),
        "blocked_entries": len(plan.blocked_entries),
        "issues": len(plan.issues),
        "by_field": dict(Counter(item.title for item in plan.writes)),
        "by_confidence": dict(Counter(item.confidence for item in plan.writes)),
        "digest": plan.digest,
        "plan": str(plan.snapshot_path.parent / "recovery" / "plan.json"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="registry-recovery",
        description="Аддитивное восстановление личных реестров из архивов",
    )
    parser.add_argument("--config-dir", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="скопировать исторические точки")
    capture.add_argument("--snapshot", type=Path, required=True)
    plan = commands.add_parser("plan", help="построить поклеточный план")
    plan.add_argument("--snapshot", type=Path, required=True)
    plan.add_argument("--sources", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="capture + plan")
    prepare.add_argument("--snapshot", type=Path, required=True)
    stage = commands.add_parser("stage", help="собрать локальный Excel-staging")
    stage.add_argument("--snapshot", type=Path, required=True)
    stage.add_argument("--plan", type=Path, required=True)
    validate = commands.add_parser("validate", help="проверить recovery staging")
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--stage", type=Path, required=True)
    publish = commands.add_parser("publish", help="опубликовать зелёный staging")
    publish.add_argument("--plan", type=Path, required=True)
    publish.add_argument("--stage", type=Path, required=True)
    publish.add_argument("--confirm-digest", required=True)
    publish.add_argument("--no-notify", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundle = load_config_bundle(args.config_dir)
    config = load_recovery_config(bundle)
    if args.command == "stage":
        snapshot = load_snapshot(args.snapshot)
        plan = load_recovery_plan(args.plan)
        staged = build_recovery_staging(bundle, snapshot, plan)
        print(json.dumps(staged.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "validate":
        plan = load_recovery_plan(args.plan)
        staged = load_recovery_stage(args.stage)
        report = validate_recovery_staging(bundle, staged, plan)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 2 if report.blocked else 0
    if args.command == "publish":
        plan = load_recovery_plan(args.plan)
        staged = load_recovery_stage(args.stage)
        result = publish_recovery(
            bundle,
            staged,
            plan,
            confirmation_digest=args.confirm_digest,
            notify_owner=not args.no_notify,
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0
    snapshot = load_snapshot(args.snapshot)
    if args.command in {"capture", "prepare"}:
        last_print = 0

        def progress(index: int, total: int, item: SourceDescriptor) -> None:
            nonlocal last_print
            if index == total or index - last_print >= 50:
                last_print = index
                sys.stderr.write(
                    f"[recovery] copied {index}/{total}: {item.label}\n"
                )
                sys.stderr.flush()

        manifest, _sources, issues = capture_recovery_sources(
            bundle,
            snapshot,
            config,
            progress=progress,
        )
        if args.command == "capture":
            print(
                json.dumps(
                    {
                        "run_id": snapshot.run_id,
                        "source_manifest": str(manifest),
                        "issues": [issue.as_dict() for issue in issues],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        source_manifest = manifest
    else:
        source_manifest = args.sources
    plan = build_recovery_plan(
        bundle,
        snapshot,
        config,
        source_manifest,
        snapshot_path=args.snapshot,
    )
    print(json.dumps(_summary_payload(plan), ensure_ascii=False, indent=2))
    return 2 if plan.blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
