"""Аддитивный ночной разброс новых закрепов без сборки и удаления строк.

Этот режим намеренно уже полного цикла:

* общий реестр используется только как read-only источник новых строк;
* уже существующие строки личных файлов не переписываются;
* строки, исчезнувшие из общего реестра, остаются в личном файле;
* каждая добавляемая строка должна иметь пустого ответственного в указанном
  историческом baseline и нового ответственного в текущем мастере;
* Excel работает только с локальным staging, публикация выполняется отдельно
  с optimistic-lock, соседней копией и rollback.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ConfigBundle, load_config_bundle
from .cycle_lock import CycleLock
from .discovery import discover
from .errors import SafetyViolation
from .excel_host import (
    ExcelHost,
    range_has_formulas,
    sheet_by_name,
    sheet_filter_signature,
    workbook_untouched_signatures,
)
from .files import fingerprint, stable_copy
from .models import FileFingerprint, Issue, WorkbookSnapshot
from .normalize import (
    comparable_value,
    is_blank,
    json_value,
    norm_key,
    norm_name,
    norm_title,
)
from .ownership import manifest_owner_matches
from .reader import read_portfolio
from .semantics import values_equal_for_rule
from .snapshot import SnapshotSet, capture_sources, load_snapshot
from .staging import (
    _canonical_date_format,
    _format_new_rows,
    _revealed_rows,
    excel_safe_value,
)


@dataclass(frozen=True, slots=True)
class DistributionConfig:
    responsible_aliases: dict[str, str]
    safety: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DistributionRow:
    entry_id: str
    original_path: Path
    source_copy: Path
    profile: str
    responsible: str
    master_responsible: str
    contract_id: str
    master_row: int
    headers: tuple[str, ...]
    values: tuple[Any, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "original_path": str(self.original_path),
            "source_copy": str(self.source_copy),
            "profile": self.profile,
            "responsible": self.responsible,
            "master_responsible": self.master_responsible,
            "contract_id": self.contract_id,
            "master_row_index": self.master_row,
            "master_row": self.master_row + 1,
            "headers": list(self.headers),
            "values": [json_value(value) for value in self.values],
        }


@dataclass(slots=True)
class DistributionPlan:
    run_id: str
    snapshot_path: Path
    assignment_baseline_path: Path
    assignment_baseline_fingerprint: FileFingerprint
    master_fingerprint: FileFingerprint
    rows: list[DistributionRow]
    issues: list[Issue]

    @property
    def blocked(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def digest(self) -> str:
        payload = {
            "run_id": self.run_id,
            "snapshot_path": str(self.snapshot_path),
            "assignment_baseline_path": str(self.assignment_baseline_path),
            "assignment_baseline_fingerprint": (
                self.assignment_baseline_fingerprint.as_dict()
            ),
            "master_fingerprint": self.master_fingerprint.as_dict(),
            "rows": [row.as_dict() for row in self.rows],
            "issues": [issue.as_dict() for issue in self.issues],
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "snapshot_path": str(self.snapshot_path),
            "assignment_baseline_path": str(self.assignment_baseline_path),
            "assignment_baseline_fingerprint": (
                self.assignment_baseline_fingerprint.as_dict()
            ),
            "master_fingerprint": self.master_fingerprint.as_dict(),
            "rows": [row.as_dict() for row in self.rows],
            "issues": [issue.as_dict() for issue in self.issues],
            "blocked": self.blocked,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class DistributionStagedFile:
    entry_id: str
    original_path: Path
    source_copy: Path
    staged_path: Path
    source_fingerprint: FileFingerprint
    staged_fingerprint: FileFingerprint
    appended_rows: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "original_path": str(self.original_path),
            "source_copy": str(self.source_copy),
            "staged_path": str(self.staged_path),
            "source_fingerprint": self.source_fingerprint.as_dict(),
            "staged_fingerprint": self.staged_fingerprint.as_dict(),
            "appended_rows": self.appended_rows,
        }


@dataclass(frozen=True, slots=True)
class DistributionStage:
    run_id: str
    run_dir: Path
    staging_dir: Path
    plan_digest: str
    files: tuple[DistributionStagedFile, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "staging_dir": str(self.staging_dir),
            "plan_digest": self.plan_digest,
            "files": [item.as_dict() for item in self.files],
        }


@dataclass(slots=True)
class DistributionValidation:
    run_id: str
    plan_digest: str
    checked_files: int = 0
    checked_existing_cells: int = 0
    checked_appended_cells: int = 0
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
            "checked_existing_cells": self.checked_existing_cells,
            "checked_appended_cells": self.checked_appended_cells,
            "issues": [issue.as_dict() for issue in self.issues or []],
            "blocked": self.blocked,
        }


@dataclass(slots=True)
class DistributionPreflight:
    run_id: str
    plan_digest: str
    validation: DistributionValidation
    checked_master: bool = False
    checked_files: int = 0
    sidecar_files: list[str] | None = None
    issues: list[Issue] | None = None

    def __post_init__(self) -> None:
        if self.sidecar_files is None:
            self.sidecar_files = []
        if self.issues is None:
            self.issues = []

    @property
    def blocked(self) -> bool:
        return self.validation.blocked or any(
            issue.severity == "error" for issue in self.issues or []
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "checked_master": self.checked_master,
            "checked_files": self.checked_files,
            "sidecar_files": self.sidecar_files,
            "validation": self.validation.as_dict(),
            "issues": [issue.as_dict() for issue in self.issues or []],
            "blocked": self.blocked,
        }


@dataclass(slots=True)
class DistributionPublishResult:
    run_id: str
    plan_digest: str
    published: list[str]
    backups: list[str]
    rolled_back: list[str]
    issues: list[Issue]
    sidecars: list[str] | None = None
    notification: dict[str, Any] | None = None
    provenance_path: str | None = None

    def __post_init__(self) -> None:
        if self.sidecars is None:
            self.sidecars = []

    @property
    def ok(self) -> bool:
        return bool(self.published or self.sidecars) and not any(
            issue.severity == "error" for issue in self.issues
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "published": self.published,
            "sidecars": self.sidecars,
            "backups": self.backups,
            "rolled_back": self.rolled_back,
            "issues": [issue.as_dict() for issue in self.issues],
            "notification": self.notification,
            "provenance_path": self.provenance_path,
            "ok": self.ok,
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SafetyViolation(f"файл не найден: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SafetyViolation(
            f"битый JSON {path}: строка {exc.lineno}, {exc.msg}"
        ) from exc


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_distribution_config(bundle: ConfigBundle) -> DistributionConfig:
    payload = _read_json(bundle.config_dir / "distribution.json")
    if int(payload.get("schema_version", 0)) != 1:
        raise SafetyViolation(
            "неподдерживаемая schema_version в distribution.json"
        )
    aliases = {
        str(source).strip(): str(target).strip()
        for source, target in (payload.get("responsible_aliases") or {}).items()
    }
    if any(not source or not target for source, target in aliases.items()):
        raise SafetyViolation("пустой alias ответственного в distribution.json")
    normalized = [norm_name(source) for source in aliases]
    if len(normalized) != len(set(normalized)):
        raise SafetyViolation("дубли aliases ответственных в distribution.json")
    return DistributionConfig(
        responsible_aliases=aliases,
        safety=dict(payload.get("safety") or {}),
    )


def _fingerprint_from_dict(payload: dict[str, Any]) -> FileFingerprint:
    return FileFingerprint(
        size=int(payload["size"]),
        mtime_ns=int(payload["mtime_ns"]),
        sha256=str(payload["sha256"]),
    )


def _profile_columns(
    master: WorkbookSnapshot,
    bundle: ConfigBundle,
    profile: str,
) -> tuple[tuple[str, int], ...]:
    tags = set(bundle.fields.profile_tags[profile])
    return tuple(
        (str(title or "").strip(), index)
        for index, title in enumerate(master.headers)
        if norm_title(title)
        and str(
            master.type_tags[index] if index < len(master.type_tags) else ""
        ).strip().casefold()
        in tags
    )


def _canonical_title(bundle: ConfigBundle, value: Any) -> str:
    normalized = norm_title(value)
    for canonical, aliases in bundle.fields.aliases.items():
        if normalized in {norm_title(alias) for alias in aliases}:
            return canonical
    return str(value or "").strip()


def _schema_titles(
    snapshot: WorkbookSnapshot,
    bundle: ConfigBundle,
) -> tuple[str, ...]:
    return tuple(
        _canonical_title(bundle, value)
        for value in snapshot.headers
        if norm_title(value)
    )


def _single_rows_by_key(
    snapshot: WorkbookSnapshot,
    key_title: str,
) -> tuple[dict[str, tuple[int, tuple[Any, ...]]], set[str]]:
    rows: dict[str, tuple[int, tuple[Any, ...]]] = {}
    duplicates: set[str] = set()
    for key, values in snapshot.rows_by_key(key_title).items():
        if len(values) != 1:
            duplicates.add(key)
            continue
        rows[key] = values[0]
    return rows, duplicates


def build_distribution_plan(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
    config: DistributionConfig,
    assignment_baseline_path: str | Path,
    *,
    snapshot_path: str | Path | None = None,
) -> DistributionPlan:
    """Построить план только на отсутствующие у сотрудника новые КД."""
    master = read_portfolio(snapshot.master_copy, bundle.fields)
    if master.fingerprint != snapshot.master_fingerprint:
        raise SafetyViolation("копия общего реестра изменилась после snapshot")
    baseline_path = Path(assignment_baseline_path)
    baseline = read_portfolio(baseline_path, bundle.fields)

    key_column = master.column_index(bundle.fields.key_title)
    responsible_column = master.column_index(bundle.fields.responsible_title)
    baseline_responsible_column = baseline.column_index(
        bundle.fields.responsible_title
    )
    if (
        key_column is None
        or responsible_column is None
        or baseline_responsible_column is None
    ):
        raise SafetyViolation(
            "в текущем мастере или assignment-baseline нет КД/ответственного"
        )
    baseline_rows_by_key = baseline.rows_by_key(bundle.fields.key_title)
    master_rows_by_key = master.rows_by_key(bundle.fields.key_title)
    baseline_duplicates = {
        key
        for key, values in baseline_rows_by_key.items()
        if len(values) > 1
    }
    master_duplicates = {
        key
        for key, values in master_rows_by_key.items()
        if len(values) > 1
    }

    issues: list[Issue] = []
    if baseline_duplicates:
        issues.append(
            Issue(
                "info",
                "distribution_baseline_duplicate_contracts",
                "в assignment-baseline есть повторяющиеся КД; "
                "они сверяются по строкам",
                {
                    "count": len(baseline_duplicates),
                    "samples": sorted(baseline_duplicates)[:50],
                },
            )
        )
    if master_duplicates:
        issues.append(
            Issue(
                "info",
                "distribution_master_duplicate_contracts",
                "в текущем мастере есть повторяющиеся КД; "
                "они сохраняются отдельными строками",
                {
                    "count": len(master_duplicates),
                    "samples": sorted(master_duplicates)[:50],
                },
            )
        )

    aliases = {
        norm_name(source): norm_name(target)
        for source, target in config.responsible_aliases.items()
    }
    alias_display = {
        norm_name(source): (source, target)
        for source, target in config.responsible_aliases.items()
    }
    excluded = re.compile(
        str(config.safety.get("exclude_filename_regex", r"(?!x)x")),
        flags=re.IGNORECASE,
    )

    personal_targets: dict[
        tuple[str, str],
        tuple[Any, WorkbookSnapshot, str, tuple[tuple[str, int], ...]],
    ] = {}
    excluded_files = 0
    for captured in snapshot.personals:
        entry = captured.entry
        if excluded.search(entry.path.name):
            excluded_files += 1
            continue
        try:
            book = read_portfolio(captured.copy_path, bundle.fields)
        except Exception as exc:  # noqa: BLE001
            issues.append(
                Issue(
                    "error",
                    "distribution_personal_unreadable",
                    f"не прочитан личный snapshot: {entry.path.name}",
                    {"entry_id": entry.entry_id, "error": str(exc)},
                )
            )
            continue
        if book.fingerprint != captured.fingerprint:
            issues.append(
                Issue(
                    "error",
                    "distribution_personal_snapshot_changed",
                    f"личный snapshot изменился: {entry.path.name}",
                    {"entry_id": entry.entry_id},
                )
            )
            continue
        responsibles = book.unique_responsibles(bundle.fields.responsible_title)
        if len(responsibles) != 1:
            issues.append(
                Issue(
                    "error",
                    "distribution_personal_responsible_not_unique",
                    f"неоднозначный ответственный: {entry.path.name}",
                    {"responsibles": list(responsibles)},
                )
            )
            continue
        responsible = responsibles[0]
        if not manifest_owner_matches(bundle, entry, responsible):
            issues.append(
                Issue(
                    "error",
                    "distribution_manifest_owner_mismatch",
                    "ответственный внутри книги не совпал с папкой или именем файла",
                    {
                        "entry_id": entry.entry_id,
                        "path": str(entry.path),
                        "employee_folder": entry.employee_folder,
                        "responsible_hint": entry.responsible_hint,
                        "book_responsible": responsible,
                    },
                )
            )
            continue
        target_key = (entry.profile, norm_name(responsible))
        if target_key in personal_targets:
            issues.append(
                Issue(
                    "error",
                    "distribution_duplicate_target",
                    f"два файла одного профиля для {responsible}",
                    {
                        "first": str(personal_targets[target_key][0].entry.path),
                        "second": str(entry.path),
                    },
                )
            )
            continue
        expected_columns = _profile_columns(master, bundle, entry.profile)
        expected_titles = tuple(title for title, _column in expected_columns)
        actual_titles = _schema_titles(book, bundle)
        if tuple(map(norm_title, actual_titles)) != tuple(
            map(norm_title, expected_titles)
        ):
            issues.append(
                Issue(
                    "error",
                    "distribution_personal_schema_drift",
                    f"схема личного файла не совпала: {entry.path.name}",
                    {
                        "profile": entry.profile,
                        "expected": list(expected_titles),
                        "actual": list(actual_titles),
                    },
                )
            )
            continue
        _rows, duplicates = _single_rows_by_key(
            book,
            bundle.fields.key_title,
        )
        if duplicates:
            issues.append(
                Issue(
                    "error",
                    "distribution_personal_duplicate_contracts",
                    f"в личном файле повторяются КД: {entry.path.name}",
                    {
                        "count": len(duplicates),
                        "samples": sorted(duplicates)[:50],
                    },
                )
            )
            continue
        personal_targets[target_key] = (
            captured,
            book,
            responsible,
            expected_columns,
        )

    if excluded_files:
        issues.append(
            Issue(
                "info",
                "distribution_excluded_files",
                "неоднозначные копии исключены из разброса",
                {"count": excluded_files},
            )
        )

    target_by_responsible: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for profile, responsible_norm in personal_targets:
        target_by_responsible[responsible_norm].append((profile, responsible_norm))

    master_assignments: dict[
        str,
        list[tuple[str, int, tuple[Any, ...], str]],
    ] = defaultdict(list)
    unmatched = Counter()
    alias_counts = Counter()
    for offset, row in enumerate(master.rows):
        master_row = master.data_start + offset
        contract_id = norm_key(master.row_value(row, key_column))
        if not contract_id:
            continue
        raw_responsible = master.row_value(row, responsible_column)
        original_norm = norm_name(raw_responsible)
        if not original_norm:
            continue
        target_norm = aliases.get(original_norm, original_norm)
        targets = target_by_responsible.get(target_norm, [])
        if not targets:
            unmatched[str(raw_responsible).strip()] += 1
            continue
        if original_norm in aliases:
            alias_counts[alias_display[original_norm]] += 1
        for profile, responsible_norm in targets:
            master_assignments[
                f"{profile}\0{responsible_norm}"
            ].append(
                (
                    contract_id,
                    master_row,
                    row,
                    str(raw_responsible).strip(),
                )
            )

    rows: list[DistributionRow] = []
    unproven: list[dict[str, Any]] = []
    missing_baseline: list[dict[str, Any]] = []
    unproven_count = 0
    missing_baseline_count = 0
    per_file = Counter()
    require_blank = bool(
        config.safety.get("require_baseline_owner_blank", True)
    )

    for (profile, responsible_norm), (
        captured,
        book,
        responsible,
        expected_columns,
    ) in sorted(
        personal_targets.items(),
        key=lambda item: str(item[1][0].entry.path).casefold(),
    ):
        current_rows_by_key = book.rows_by_key(bundle.fields.key_title)
        assignments = master_assignments.get(
            f"{profile}\0{responsible_norm}",
            [],
        )
        responsible_output = next(
            (
                index
                for index, (title, _master_column) in enumerate(expected_columns)
                if norm_title(title)
                == norm_title(bundle.fields.responsible_title)
            ),
            None,
        )
        if responsible_output is None:
            issues.append(
                Issue(
                    "error",
                    "distribution_responsible_column_missing",
                    f"нет ответственного в профиле: {captured.entry.path.name}",
                )
            )
            continue
        assignments_by_key: dict[
            str,
            list[tuple[str, int, tuple[Any, ...], str]],
        ] = defaultdict(list)
        for assignment in assignments:
            assignments_by_key[assignment[0]].append(assignment)
        for contract_id, contract_assignments in assignments_by_key.items():
            existing_count = len(current_rows_by_key.get(contract_id, []))
            if existing_count >= len(contract_assignments):
                continue
            if existing_count:
                unproven_count += 1
                if len(unproven) < 100:
                    unproven.append(
                        {
                            "contract_id": contract_id,
                            "kind": "partial_duplicate_in_personal",
                            "personal_rows": existing_count,
                            "master_rows": len(contract_assignments),
                            "target": responsible,
                            "file": str(captured.entry.path),
                        }
                    )
                continue
            for (
                _contract_id,
                master_row,
                master_values,
                raw_master_responsible,
            ) in contract_assignments:
                baseline_offset = master_row - master.data_start
                baseline_item = None
                if 0 <= baseline_offset < len(baseline.rows):
                    candidate = baseline.rows[baseline_offset]
                    baseline_key_column = baseline.column_index(
                        bundle.fields.key_title
                    )
                    assert baseline_key_column is not None
                    if (
                        norm_key(
                            baseline.row_value(
                                candidate,
                                baseline_key_column,
                            )
                        )
                        == contract_id
                    ):
                        baseline_item = (
                            baseline.data_start + baseline_offset,
                            candidate,
                        )
                if baseline_item is None:
                    candidates = baseline_rows_by_key.get(contract_id, [])
                    if len(candidates) == 1:
                        baseline_item = candidates[0]
                if baseline_item is None:
                    missing_baseline_count += 1
                    if len(missing_baseline) < 100:
                        missing_baseline.append(
                            {
                                "contract_id": contract_id,
                                "target": responsible,
                                "file": str(captured.entry.path),
                            }
                        )
                    continue
                _baseline_row, baseline_values = baseline_item
                baseline_responsible = baseline.row_value(
                    baseline_values,
                    baseline_responsible_column,
                )
                if require_blank and not is_blank(baseline_responsible):
                    unproven_count += 1
                    if len(unproven) < 100:
                        unproven.append(
                            {
                                "contract_id": contract_id,
                                "baseline_responsible": json_value(
                                    baseline_responsible
                                ),
                                "current_responsible": raw_master_responsible,
                                "target": responsible,
                                "file": str(captured.entry.path),
                            }
                        )
                    continue
                values = [
                    master.row_value(master_values, master_column)
                    for _title, master_column in expected_columns
                ]
                values[responsible_output] = responsible
                distribution_row = DistributionRow(
                    entry_id=captured.entry.entry_id,
                    original_path=captured.entry.path,
                    source_copy=captured.copy_path,
                    profile=profile,
                    responsible=responsible,
                    master_responsible=raw_master_responsible,
                    contract_id=contract_id,
                    master_row=master_row,
                    headers=tuple(
                        title for title, _column in expected_columns
                    ),
                    values=tuple(values),
                )
                rows.append(distribution_row)
                per_file[captured.entry.entry_id] += 1

    if missing_baseline_count:
        issues.append(
            Issue(
                "error",
                "distribution_contract_missing_in_baseline",
                "новые строки отсутствуют в assignment-baseline",
                {
                    "count": missing_baseline_count,
                    "samples": missing_baseline,
                },
            )
        )
    if unproven_count:
        issues.append(
            Issue(
                "error",
                "distribution_assignment_not_new",
                "часть строк не имеет доказанного перехода от пустого закрепа",
                {"count": unproven_count, "samples": unproven},
            )
        )
    if unmatched:
        issues.append(
            Issue(
                "warning",
                "distribution_responsibles_without_file",
                "в мастере есть ответственные без активного личного файла",
                {
                    "responsibles": len(unmatched),
                    "rows": sum(unmatched.values()),
                    "counts": dict(unmatched.most_common()),
                },
            )
        )
    if alias_counts:
        issues.append(
            Issue(
                "info",
                "distribution_responsible_aliases_used",
                "применены настроенные aliases ответственных",
                {
                    "counts": {
                        f"{source} -> {target}": count
                        for (source, target), count in alias_counts.items()
                    }
                },
            )
        )

    max_per_file = int(config.safety.get("max_rows_per_file", 300))
    too_large = {
        entry_id: count
        for entry_id, count in per_file.items()
        if count > max_per_file
    }
    if too_large:
        issues.append(
            Issue(
                "error",
                "distribution_file_limit_exceeded",
                "число новых строк в одном файле выше лимита",
                {"limit": max_per_file, "entries": too_large},
            )
        )
    max_total = int(config.safety.get("max_total_rows", 2500))
    if len(rows) > max_total:
        issues.append(
            Issue(
                "error",
                "distribution_total_limit_exceeded",
                "общий объём разброса выше лимита",
                {"rows": len(rows), "limit": max_total},
            )
        )

    rows.sort(
        key=lambda row: (
            str(row.original_path).casefold(),
            row.master_row,
            row.contract_id,
        )
    )
    plan = DistributionPlan(
        run_id=snapshot.run_id,
        snapshot_path=Path(snapshot_path or snapshot.run_dir / "snapshot.json"),
        assignment_baseline_path=baseline_path,
        assignment_baseline_fingerprint=baseline.fingerprint,
        master_fingerprint=snapshot.master_fingerprint,
        rows=rows,
        issues=issues,
    )
    output = snapshot.run_dir / "distribution"
    _atomic_json(output / "plan.json", plan.as_dict())
    _write_plan_csv(output / "plan.csv", plan.rows)
    _write_plan_summary(output / "summary.md", plan)
    return plan


def _write_plan_csv(path: Path, rows: list[DistributionRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "сотрудник",
                "профиль",
                "файл",
                "номер КД",
                "строка мастера",
                "ответственный в мастере",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.responsible,
                    row.profile,
                    str(row.original_path),
                    row.contract_id,
                    row.master_row + 1,
                    row.master_responsible,
                ]
            )


def _write_plan_summary(path: Path, plan: DistributionPlan) -> None:
    by_employee = Counter(row.responsible for row in plan.rows)
    by_profile = Counter(row.profile for row in plan.rows)
    lines = [
        "# Аддитивный план ночного разброса",
        "",
        f"- Новых строк: **{len(plan.rows)}**",
        f"- Личных файлов: **{len({row.entry_id for row in plan.rows})}**",
        f"- Статус: **{'ЗАБЛОКИРОВАН' if plan.blocked else 'ГОТОВ К STAGING'}**",
        f"- Digest: `{plan.digest}`",
        f"- Assignment baseline: `{plan.assignment_baseline_fingerprint.sha256}`",
        f"- Текущий мастер: `{plan.master_fingerprint.sha256}`",
        "",
        "Существующие строки личных файлов и общий реестр в план не входят.",
        "Строки, отсутствующие в мастере, не удаляются.",
        "",
        "## По профилям",
        "",
        "| Профиль | Строк |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {profile.upper()} | {count} |"
        for profile, count in by_profile.most_common()
    )
    lines.extend(
        [
            "",
            "## По сотрудникам",
            "",
            "| Сотрудник | Новых строк |",
            "|---|---:|",
        ]
    )
    lines.extend(
        f"| {name} | {count} |"
        for name, count in by_employee.most_common()
    )
    if plan.issues:
        lines.extend(["", "## Сигналы", ""])
        lines.extend(
            f"- [{issue.severity.upper()}] `{issue.code}` — {issue.message}"
            for issue in plan.issues
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_distribution_plan(path: str | Path) -> DistributionPlan:
    payload = _read_json(Path(path))
    rows = [
        DistributionRow(
            entry_id=str(item["entry_id"]),
            original_path=Path(item["original_path"]),
            source_copy=Path(item["source_copy"]),
            profile=str(item["profile"]),
            responsible=str(item["responsible"]),
            master_responsible=str(item["master_responsible"]),
            contract_id=str(item["contract_id"]),
            master_row=int(item["master_row_index"]),
            headers=tuple(str(value) for value in item["headers"]),
            values=tuple(item["values"]),
        )
        for item in payload.get("rows") or []
    ]
    issues = [
        Issue(
            severity=str(item["severity"]),
            code=str(item["code"]),
            message=str(item["message"]),
            context=dict(item.get("context") or {}),
        )
        for item in payload.get("issues") or []
    ]
    plan = DistributionPlan(
        run_id=str(payload["run_id"]),
        snapshot_path=Path(payload["snapshot_path"]),
        assignment_baseline_path=Path(
            payload["assignment_baseline_path"]
        ),
        assignment_baseline_fingerprint=_fingerprint_from_dict(
            payload["assignment_baseline_fingerprint"]
        ),
        master_fingerprint=_fingerprint_from_dict(
            payload["master_fingerprint"]
        ),
        rows=rows,
        issues=issues,
    )
    expected = str(payload.get("digest") or "")
    if plan.digest != expected:
        raise SafetyViolation(
            f"digest distribution-плана не совпал: {plan.digest} != {expected}"
        )
    return plan


def _value_types(
    bundle: ConfigBundle,
    headers: tuple[str, ...],
) -> tuple[str | None, ...]:
    rules = {
        norm_title(rule.title): rule
        for rule in bundle.fields.collect
    }
    return tuple(
        (
            rules[norm_title(title)].value_type
            if norm_title(title) in rules
            else None
        )
        for title in headers
    )


def _append_rows_to_book(
    host: ExcelHost,
    staged_path: Path,
    rows: list[DistributionRow],
    bundle: ConfigBundle,
) -> None:
    before = read_portfolio(staged_path, bundle.fields)
    headers = rows[0].headers
    if any(row.headers != headers for row in rows):
        raise SafetyViolation(
            f"разные схемы строк distribution: {staged_path.name}"
        )
    actual_headers = _schema_titles(before, bundle)
    if tuple(map(norm_title, actual_headers)) != tuple(
        map(norm_title, headers)
    ):
        raise SafetyViolation(
            f"distribution staging: схема изменилась: {staged_path.name}"
        )

    width = len(headers)
    first_data_row = before.data_start + 1
    old_last_row = before.data_start + len(before.rows)
    first_new_row = old_last_row + 1
    new_last_row = old_last_row + len(rows)
    value_types = _value_types(bundle, headers)

    with host.open_book(staged_path, read_only=False) as book:
        sheet = sheet_by_name(book, bundle.fields.sheet_name)
        with _revealed_rows(
            sheet,
            first_row=first_new_row,
            last_row=new_last_row,
            filter_last_row=max(
                before.type_row + 1 if before.type_row is not None else 1,
                new_last_row,
            ),
        ):
            target = sheet.range(
                (first_new_row, 1),
                (new_last_row, width),
            )
            if range_has_formulas(target):
                raise SafetyViolation(
                    "distribution staging: диапазон добавления содержит формулы: "
                    f"{staged_path.name}"
                )
            current = target.value
            current_rows = (
                current
                if isinstance(current, list)
                else [[current]]
            )
            flattened = [
                value
                for row in current_rows
                for value in (row if isinstance(row, list) else [row])
            ]
            if any(not is_blank(value) for value in flattened):
                raise SafetyViolation(
                    "distribution staging: диапазон добавления не пуст: "
                    f"{staged_path.name}"
                )
            _format_new_rows(
                host,
                sheet,
                first_data_row=first_data_row,
                old_last_row=old_last_row,
                new_last_row=new_last_row,
                columns=width,
            )
            for column, value_type in enumerate(value_types, start=1):
                if value_type != "date":
                    continue
                number_format = _canonical_date_format(
                    sheet,
                    column=column,
                    first_data_row=first_data_row,
                    last_search_row=max(old_last_row, first_data_row),
                )
                sheet.range(
                    (first_new_row, column),
                    (new_last_row, column),
                ).api.NumberFormat = number_format
            target.value = [
                [
                    excel_safe_value(value, value_types[column])
                    for column, value in enumerate(row.values)
                ]
                for row in rows
            ]
        book.api.Save()

    after = read_portfolio(staged_path, bundle.fields)
    if (
        before.sheet_names,
        before.header_row,
        before.type_row,
        before.data_start,
        before.headers,
        before.type_tags,
    ) != (
        after.sheet_names,
        after.header_row,
        after.type_row,
        after.data_start,
        after.headers,
        after.type_tags,
    ):
        raise SafetyViolation(
            f"distribution staging изменил структуру: {staged_path.name}"
        )


def build_distribution_staging(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
    plan: DistributionPlan,
) -> DistributionStage:
    if plan.blocked:
        raise SafetyViolation(
            "заблокированный distribution-план нельзя переносить в staging"
        )
    if not plan.rows:
        raise SafetyViolation("distribution-план не содержит новых строк")
    if snapshot.master_fingerprint != plan.master_fingerprint:
        raise SafetyViolation(
            "distribution-план относится к другой копии общего реестра"
        )
    if fingerprint(plan.assignment_baseline_path) != (
        plan.assignment_baseline_fingerprint
    ):
        raise SafetyViolation("assignment-baseline изменился после плана")

    by_entry: dict[str, list[DistributionRow]] = defaultdict(list)
    for row in plan.rows:
        by_entry[row.entry_id].append(row)
    personals = {
        personal.entry.entry_id: personal
        for personal in snapshot.personals
    }
    staging_dir = (
        snapshot.run_dir
        / "distribution"
        / f"staging-{plan.digest[:12]}"
    )
    staging_dir.mkdir(parents=True, exist_ok=False)

    pending = []
    for entry_id, rows in sorted(by_entry.items()):
        source = personals.get(entry_id)
        if source is None:
            raise SafetyViolation(
                f"entry distribution отсутствует в snapshot: {entry_id}"
            )
        if fingerprint(source.copy_path) != source.fingerprint:
            raise SafetyViolation(
                "личный snapshot изменился после построения плана: "
                f"{source.copy_path}"
            )
        destination = (
            staging_dir
            / f"{entry_id}{source.copy_path.suffix.casefold()}"
        )
        stable_copy(source.copy_path, destination)
        pending.append((source, destination, rows))

    staged_files: list[DistributionStagedFile] = []
    with ExcelHost() as host:
        for source, destination, rows in pending:
            _append_rows_to_book(
                host,
                destination,
                rows,
                bundle,
            )
            staged_files.append(
                DistributionStagedFile(
                    entry_id=source.entry.entry_id,
                    original_path=source.entry.path,
                    source_copy=source.copy_path,
                    staged_path=destination,
                    source_fingerprint=source.fingerprint,
                    staged_fingerprint=fingerprint(destination),
                    appended_rows=len(rows),
                )
            )

    stage = DistributionStage(
        run_id=snapshot.run_id,
        run_dir=snapshot.run_dir,
        staging_dir=staging_dir,
        plan_digest=plan.digest,
        files=tuple(staged_files),
    )
    _atomic_json(staging_dir / "stage.json", stage.as_dict())
    return stage


def load_distribution_stage(path: str | Path) -> DistributionStage:
    payload = _read_json(Path(path))
    files = []
    for item in payload.get("files") or []:
        staged = DistributionStagedFile(
            entry_id=str(item["entry_id"]),
            original_path=Path(item["original_path"]),
            source_copy=Path(item["source_copy"]),
            staged_path=Path(item["staged_path"]),
            source_fingerprint=_fingerprint_from_dict(
                item["source_fingerprint"]
            ),
            staged_fingerprint=_fingerprint_from_dict(
                item["staged_fingerprint"]
            ),
            appended_rows=int(item["appended_rows"]),
        )
        if not staged.source_copy.is_file() or not staged.staged_path.is_file():
            raise SafetyViolation(
                f"неполный distribution staging: {staged.entry_id}"
            )
        files.append(staged)
    return DistributionStage(
        run_id=str(payload["run_id"]),
        run_dir=Path(payload["run_dir"]),
        staging_dir=Path(payload["staging_dir"]),
        plan_digest=str(payload["plan_digest"]),
        files=tuple(files),
    )


def _structure_equal(
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
    )


def _row_cell(
    snapshot: WorkbookSnapshot,
    row: tuple[Any, ...],
    column: int,
) -> Any:
    return snapshot.row_value(row, column)


def validate_distribution_staging(
    bundle: ConfigBundle,
    stage: DistributionStage,
    plan: DistributionPlan,
) -> DistributionValidation:
    report = DistributionValidation(
        run_id=stage.run_id,
        plan_digest=plan.digest,
    )
    assert report.issues is not None
    if stage.plan_digest != plan.digest:
        report.issues.append(
            Issue(
                "error",
                "distribution_stage_digest_mismatch",
                "digest staging не совпал с distribution-планом",
            )
        )
        return report

    by_entry: dict[str, list[DistributionRow]] = defaultdict(list)
    for row in plan.rows:
        by_entry[row.entry_id].append(row)
    if set(by_entry) != {item.entry_id for item in stage.files}:
        report.issues.append(
            Issue(
                "error",
                "distribution_stage_file_set_mismatch",
                "набор файлов staging не совпал с distribution-планом",
            )
        )
        return report

    rules = {
        norm_title(rule.title): rule
        for rule in bundle.fields.collect
    }
    for item in stage.files:
        if fingerprint(item.staged_path) != item.staged_fingerprint:
            report.issues.append(
                Issue(
                    "error",
                    "distribution_stage_changed",
                    f"staging изменился после сборки: {item.staged_path.name}",
                )
            )
            continue
        source = read_portfolio(item.source_copy, bundle.fields)
        staged = read_portfolio(item.staged_path, bundle.fields)
        if not _structure_equal(source, staged):
            report.issues.append(
                Issue(
                    "error",
                    "distribution_structure_changed",
                    f"изменилась структура книги: {item.original_path.name}",
                )
            )
            continue
        expected = by_entry[item.entry_id]
        expected_count = len(source.rows) + len(expected)
        if len(staged.rows) != expected_count:
            report.issues.append(
                Issue(
                    "error",
                    "distribution_row_count_mismatch",
                    f"неверное число строк: {item.original_path.name}",
                    {
                        "before": len(source.rows),
                        "appended": len(expected),
                        "expected": expected_count,
                        "actual": len(staged.rows),
                    },
                )
            )
            continue

        forbidden: list[dict[str, Any]] = []
        width = len(source.headers)
        for offset, source_row in enumerate(source.rows):
            staged_row = staged.rows[offset]
            for column in range(width):
                before = _row_cell(source, source_row, column)
                after = _row_cell(staged, staged_row, column)
                report.checked_existing_cells += 1
                if comparable_value(before) != comparable_value(after):
                    if len(forbidden) < 100:
                        forbidden.append(
                            {
                                "row": source.data_start + offset + 1,
                                "column": column + 1,
                                "title": source.headers[column],
                                "before": json_value(before),
                                "after": json_value(after),
                            }
                        )
        if forbidden:
            report.issues.append(
                Issue(
                    "error",
                    "distribution_existing_cell_changed",
                    "изменена существующая ячейка личного файла",
                    {
                        "file": str(item.original_path),
                        "samples": forbidden,
                    },
                )
            )

        mismatches: list[dict[str, Any]] = []
        for index, expected_row in enumerate(expected):
            staged_row = staged.rows[len(source.rows) + index]
            for column, wanted in enumerate(expected_row.values):
                actual = _row_cell(staged, staged_row, column)
                rule = rules.get(norm_title(expected_row.headers[column]))
                equal = (
                    values_equal_for_rule(actual, wanted, rule)
                    if rule is not None
                    else comparable_value(actual) == comparable_value(wanted)
                )
                report.checked_appended_cells += 1
                if not equal and len(mismatches) < 100:
                    mismatches.append(
                        {
                            "row": (
                                staged.data_start
                                + len(source.rows)
                                + index
                                + 1
                            ),
                            "column": column + 1,
                            "title": expected_row.headers[column],
                            "contract_id": expected_row.contract_id,
                            "expected": json_value(wanted),
                            "actual": json_value(actual),
                        }
                    )
        if mismatches:
            report.issues.append(
                Issue(
                    "error",
                    "distribution_appended_value_mismatch",
                    f"добавленные строки не совпали: {item.original_path.name}",
                    {"samples": mismatches},
                )
            )
        report.checked_files += 1

    try:
        with ExcelHost() as host:
            for item in stage.files:
                source_snapshot = read_portfolio(
                    item.source_copy,
                    bundle.fields,
                )
                with host.open_book(
                    item.source_copy,
                    read_only=True,
                ) as source_book:
                    source_names = tuple(source_book.sheet_names)
                    source_signatures = workbook_untouched_signatures(
                        source_book,
                        mutable_sheet=bundle.fields.sheet_name,
                    )
                    source_filter = sheet_filter_signature(
                        sheet_by_name(
                            source_book,
                            bundle.fields.sheet_name,
                        )
                    )
                with host.open_book(
                    item.staged_path,
                    read_only=True,
                ) as staged_book:
                    staged_names = tuple(staged_book.sheet_names)
                    staged_signatures = workbook_untouched_signatures(
                        staged_book,
                        mutable_sheet=bundle.fields.sheet_name,
                    )
                    staged_filter = sheet_filter_signature(
                        sheet_by_name(
                            staged_book,
                            bundle.fields.sheet_name,
                        )
                    )
                source_filter_without_last = {
                    key: value
                    for key, value in source_filter.items()
                    if key != "last_row"
                }
                staged_filter_without_last = {
                    key: value
                    for key, value in staged_filter.items()
                    if key != "last_row"
                }
                expected_filter_last = source_filter.get("last_row")
                if source_filter.get("enabled"):
                    expected_filter_last = max(
                        int(source_filter["first_row"]),
                        (
                            source_snapshot.data_start
                            + len(source_snapshot.rows)
                            + item.appended_rows
                        ),
                    )
                if (
                    source_names != staged_names
                    or source_signatures != staged_signatures
                    or source_filter_without_last
                    != staged_filter_without_last
                    or staged_filter.get("last_row")
                    != expected_filter_last
                ):
                    report.issues.append(
                        Issue(
                            "error",
                            "distribution_workbook_semantics_changed",
                            "изменились листы или фильтр вне плана: "
                            f"{item.original_path.name}",
                            {
                                "sheet_names_equal": (
                                    source_names == staged_names
                                ),
                                "untouched_sheets_equal": (
                                    source_signatures == staged_signatures
                                ),
                                "filter_before": source_filter,
                                "filter_after": staged_filter,
                                "expected_filter_last": (
                                    expected_filter_last
                                ),
                            },
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        report.issues.append(
            Issue(
                "error",
                "distribution_semantic_validation_failed",
                "не завершена COM-проверка distribution staging",
                {"error": str(exc)},
            )
        )

    _atomic_json(stage.staging_dir / "validation.json", report.as_dict())
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
        raise SafetyViolation(
            f"файл занят или недоступен: {path} ({exc})"
        ) from exc
    else:
        handle.Close()


def _excel_lock_path(path: Path) -> Path:
    return path.with_name("~$" + path.name)


def _quick_stat_matches(
    path: Path,
    expected: FileFingerprint,
) -> bool:
    stat = path.stat()
    return (
        stat.st_size == expected.size
        and stat.st_mtime_ns == expected.mtime_ns
    )


def preflight_distribution(
    bundle: ConfigBundle,
    stage: DistributionStage,
    plan: DistributionPlan,
    *,
    allow_locked_sidecars: bool = False,
) -> DistributionPreflight:
    """Повторно проверить staging и optimistic-lock всех live-источников."""
    validation = validate_distribution_staging(bundle, stage, plan)
    report = DistributionPreflight(
        run_id=stage.run_id,
        plan_digest=plan.digest,
        validation=validation,
    )
    assert report.issues is not None
    if validation.blocked:
        report.issues.append(
            Issue(
                "error",
                "distribution_validation_blocked",
                "distribution staging не прошёл validation",
                {
                    "codes": [
                        issue.code
                        for issue in validation.issues or []
                        if issue.severity == "error"
                    ]
                },
            )
        )
        _atomic_json(stage.staging_dir / "preflight.json", report.as_dict())
        return report

    try:
        snapshot = load_snapshot(plan.snapshot_path)
    except Exception as exc:  # noqa: BLE001
        report.issues.append(
            Issue(
                "error",
                "distribution_snapshot_unavailable",
                "не загружен snapshot distribution-плана",
                {"path": str(plan.snapshot_path), "error": str(exc)},
            )
        )
        _atomic_json(stage.staging_dir / "preflight.json", report.as_dict())
        return report

    if (
        snapshot.run_id != plan.run_id
        or stage.run_id != plan.run_id
        or snapshot.master_fingerprint != plan.master_fingerprint
    ):
        report.issues.append(
            Issue(
                "error",
                "distribution_snapshot_identity_mismatch",
                "snapshot, план и staging относятся к разным запускам",
            )
        )

    try:
        if fingerprint(plan.assignment_baseline_path) != (
            plan.assignment_baseline_fingerprint
        ):
            report.issues.append(
                Issue(
                    "error",
                    "distribution_assignment_baseline_changed",
                    "assignment-baseline изменился после построения плана",
                    {"path": str(plan.assignment_baseline_path)},
                )
            )
    except Exception as exc:  # noqa: BLE001
        report.issues.append(
            Issue(
                "error",
                "distribution_assignment_baseline_unavailable",
                "assignment-baseline недоступен",
                {
                    "path": str(plan.assignment_baseline_path),
                    "error": str(exc),
                },
            )
        )

    master_path = snapshot.master_original
    master_lock = _excel_lock_path(master_path)
    if master_lock.exists():
        report.issues.append(
            Issue(
                "error",
                "distribution_live_master_locked",
                "общий реестр открыт в Excel",
                {"path": str(master_path), "lock": str(master_lock)},
            )
        )
    else:
        try:
            _exclusive_file_check(master_path)
            if fingerprint(master_path) != plan.master_fingerprint:
                report.issues.append(
                    Issue(
                        "error",
                        "distribution_live_master_changed",
                        "общий реестр изменился после snapshot",
                        {"path": str(master_path)},
                    )
                )
            else:
                report.checked_master = True
        except Exception as exc:  # noqa: BLE001
            report.issues.append(
                Issue(
                    "error",
                    "distribution_live_master_unavailable",
                    "общий реестр занят или недоступен для preflight",
                    {"path": str(master_path), "error": str(exc)},
                )
            )

    captured_by_entry = {
        item.entry.entry_id: item
        for item in snapshot.personals
    }
    for item in stage.files:
        captured = captured_by_entry.get(item.entry_id)
        if captured is None:
            report.issues.append(
                Issue(
                    "error",
                    "distribution_live_entry_missing",
                    "файл staging отсутствует в snapshot",
                    {"entry_id": item.entry_id},
                )
            )
            continue
        if (
            captured.fingerprint != item.source_fingerprint
            or str(captured.entry.path).casefold()
            != str(item.original_path).casefold()
        ):
            report.issues.append(
                Issue(
                    "error",
                    "distribution_live_entry_identity_mismatch",
                    "цель staging не совпала со snapshot",
                    {
                        "entry_id": item.entry_id,
                        "path": str(item.original_path),
                    },
                )
            )
            continue
        requires_sidecar = False
        lock = _excel_lock_path(item.original_path)
        if lock.exists():
            if not allow_locked_sidecars:
                report.issues.append(
                    Issue(
                        "error",
                        "distribution_live_personal_locked",
                        "личный реестр открыт в Excel",
                        {
                            "entry_id": item.entry_id,
                            "path": str(item.original_path),
                            "lock": str(lock),
                        },
                    )
                )
                continue
            requires_sidecar = True
        try:
            if not requires_sidecar:
                _exclusive_file_check(item.original_path)
        except Exception as exc:  # noqa: BLE001
            if not allow_locked_sidecars:
                report.issues.append(
                    Issue(
                        "error",
                        "distribution_live_personal_unavailable",
                        "личный реестр или staging занят либо недоступен",
                        {
                            "entry_id": item.entry_id,
                            "path": str(item.original_path),
                            "error": str(exc),
                        },
                    )
                )
                continue
            requires_sidecar = True
        try:
            live_fingerprint = fingerprint(item.original_path)
            staged_fingerprint = fingerprint(item.staged_path)
        except Exception as exc:  # noqa: BLE001
            report.issues.append(
                Issue(
                    "error",
                    "distribution_live_personal_unavailable",
                    "личный реестр или staging занят либо недоступен",
                    {
                        "entry_id": item.entry_id,
                        "path": str(item.original_path),
                        "error": str(exc),
                    },
                )
            )
            continue
        if live_fingerprint != item.source_fingerprint:
            report.issues.append(
                Issue(
                    "error",
                    "distribution_live_personal_changed",
                    "личный реестр изменился после snapshot",
                    {
                        "entry_id": item.entry_id,
                        "path": str(item.original_path),
                    },
                )
            )
            continue
        if staged_fingerprint != item.staged_fingerprint:
            report.issues.append(
                Issue(
                    "error",
                    "distribution_staged_personal_changed",
                    "локальный staging изменился после сборки",
                    {
                        "entry_id": item.entry_id,
                        "path": str(item.staged_path),
                    },
                )
            )
            continue
        if requires_sidecar:
            assert report.sidecar_files is not None
            report.sidecar_files.append(str(item.original_path))
            report.issues.append(
                Issue(
                    "warning",
                    "distribution_live_personal_sidecar_required",
                    "открытый личный реестр будет опубликован соседней копией",
                    {
                        "entry_id": item.entry_id,
                        "path": str(item.original_path),
                        "lock": str(lock) if lock.exists() else None,
                    },
                )
            )
        report.checked_files += 1

    _atomic_json(stage.staging_dir / "preflight.json", report.as_dict())
    return report


def _unique_distribution_backup(path: Path, stamp: datetime) -> Path:
    label = stamp.strftime("%d.%m.%y %H-%M")
    candidate = path.with_name(
        f"{path.stem} (до ночного разброса {label}){path.suffix}"
    )
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = path.with_name(
            f"{path.stem} (до ночного разброса {label}, {counter})"
            f"{path.suffix}"
        )
    return candidate


def _unique_distribution_sidecar(path: Path, stamp: datetime) -> Path:
    label = stamp.strftime("%d.%m.%y %H-%M")
    candidate = path.with_name(
        f"Копия {path.stem} (после распределения {label}){path.suffix}"
    )
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = path.with_name(
            f"Копия {path.stem} (после распределения {label}, {counter})"
            f"{path.suffix}"
        )
    return candidate


def publish_distribution(
    bundle: ConfigBundle,
    stage: DistributionStage,
    plan: DistributionPlan,
    *,
    confirmation_digest: str,
    notify_owner: bool = True,
    sidecar_locked: bool = False,
) -> DistributionPublishResult:
    if not (
        confirmation_digest == plan.digest == stage.plan_digest
    ):
        raise SafetyViolation(
            "digest подтверждения distribution не совпал"
        )
    preflight = preflight_distribution(
        bundle,
        stage,
        plan,
        allow_locked_sidecars=sidecar_locked,
    )
    if preflight.blocked:
        raise SafetyViolation(
            "distribution не прошёл финальный preflight"
        )
    result = DistributionPublishResult(
        run_id=stage.run_id,
        plan_digest=plan.digest,
        published=[],
        backups=[],
        rolled_back=[],
        issues=[],
    )
    backup_by_entry: dict[str, Path] = {}
    temp_by_entry: dict[str, Path] = {}
    replaced: list[DistributionStagedFile] = []
    created_sidecars: list[DistributionStagedFile] = []
    stamp = datetime.now()
    snapshot = load_snapshot(plan.snapshot_path)
    sidecar_paths = {
        path.casefold()
        for path in (preflight.sidecar_files or [])
    }
    sidecar_entry_ids = {
        item.entry_id
        for item in stage.files
        if str(item.original_path).casefold() in sidecar_paths
    }
    if sidecar_locked:
        for item in stage.files:
            if item.entry_id in sidecar_entry_ids:
                continue
            if _excel_lock_path(item.original_path).exists():
                sidecar_entry_ids.add(item.entry_id)
                continue
            try:
                _exclusive_file_check(item.original_path)
            except Exception:  # noqa: BLE001
                sidecar_entry_ids.add(item.entry_id)
    sidecar_by_entry = {
        item.entry_id: _unique_distribution_sidecar(
            item.original_path,
            stamp,
        )
        for item in stage.files
        if item.entry_id in sidecar_entry_ids
    }
    intent_path = stage.staging_dir / "publication_intent.json"
    _atomic_json(
        intent_path,
        {
            "created_at": datetime.now().astimezone().isoformat(),
            "run_id": stage.run_id,
            "plan_digest": plan.digest,
            "master": {
                "path": str(snapshot.master_original),
                "fingerprint": plan.master_fingerprint.as_dict(),
                "mode": "read_only",
            },
            "files": [
                {
                    "entry_id": item.entry_id,
                    "path": str(item.original_path),
                    "before": item.source_fingerprint.as_dict(),
                    "after": item.staged_fingerprint.as_dict(),
                    "appended_rows": item.appended_rows,
                    "mode": (
                        "sidecar"
                        if item.entry_id in sidecar_entry_ids
                        else "replace"
                    ),
                    "published_path": str(
                        sidecar_by_entry.get(
                            item.entry_id,
                            item.original_path,
                        )
                    ),
                }
                for item in stage.files
            ],
        },
    )

    try:
        master_lock = _excel_lock_path(snapshot.master_original)
        if master_lock.exists():
            raise SafetyViolation(
                f"Excel lock-файл у общего: {snapshot.master_original}"
            )
        _exclusive_file_check(snapshot.master_original)
        if fingerprint(snapshot.master_original) != plan.master_fingerprint:
            raise SafetyViolation(
                "общий реестр изменился после distribution snapshot"
            )

        for item in stage.files:
            if item.entry_id not in sidecar_entry_ids:
                lock = _excel_lock_path(item.original_path)
                if lock.exists():
                    raise SafetyViolation(
                        f"Excel lock-файл у цели: {item.original_path}"
                    )
                _exclusive_file_check(item.original_path)
            if fingerprint(item.original_path) != item.source_fingerprint:
                raise SafetyViolation(
                    "живой файл изменился после snapshot: "
                    f"{item.original_path}"
                )
            if fingerprint(item.staged_path) != item.staged_fingerprint:
                raise SafetyViolation(
                    f"staging изменился: {item.staged_path}"
                )

        for item in stage.files:
            if item.entry_id not in sidecar_entry_ids:
                backup = _unique_distribution_backup(
                    item.original_path,
                    stamp,
                )
                stable_copy(item.original_path, backup)
                backup_by_entry[item.entry_id] = backup
                result.backups.append(str(backup))
            temporary = item.original_path.parent / (
                f".rcd-{item.entry_id[:8]}-{secrets.token_hex(3)}.next"
                f"{item.original_path.suffix.casefold()}"
            )
            stable_copy(item.staged_path, temporary)
            if fingerprint(temporary) != item.staged_fingerprint:
                raise SafetyViolation(
                    f"сетевая temp-копия не совпала: {item.original_path}"
                )
            temp_by_entry[item.entry_id] = temporary

        for item in stage.files:
            if item.entry_id not in sidecar_entry_ids:
                _exclusive_file_check(item.original_path)
            if fingerprint(item.original_path) != item.source_fingerprint:
                raise SafetyViolation(
                    "живой файл изменился при подготовке: "
                    f"{item.original_path}"
                )
        if _excel_lock_path(snapshot.master_original).exists():
            raise SafetyViolation(
                f"Excel lock-файл у общего: {snapshot.master_original}"
            )
        _exclusive_file_check(snapshot.master_original)
        if fingerprint(snapshot.master_original) != plan.master_fingerprint:
            raise SafetyViolation(
                "общий реестр изменился при подготовке distribution"
            )

        for item in stage.files:
            if _excel_lock_path(snapshot.master_original).exists():
                raise SafetyViolation(
                    f"Excel lock-файл у общего: {snapshot.master_original}"
                )
            if not _quick_stat_matches(
                snapshot.master_original,
                plan.master_fingerprint,
            ):
                raise SafetyViolation(
                    "общий реестр изменился во время distribution"
                )
            if fingerprint(item.original_path) != item.source_fingerprint:
                raise SafetyViolation(
                    "живой файл изменился непосредственно перед публикацией: "
                    f"{item.original_path}"
                )
            temporary = temp_by_entry[item.entry_id]
            if item.entry_id in sidecar_entry_ids:
                sidecar = sidecar_by_entry[item.entry_id]
                if sidecar.exists():
                    raise SafetyViolation(
                        f"sidecar уже существует: {sidecar}"
                    )
                os.replace(temporary, sidecar)
                created_sidecars.append(item)
                if fingerprint(sidecar) != item.staged_fingerprint:
                    raise SafetyViolation(
                        f"проверка sidecar после публикации не прошла: "
                        f"{sidecar}"
                    )
                assert result.sidecars is not None
                result.sidecars.append(str(sidecar))
            else:
                lock = _excel_lock_path(item.original_path)
                if lock.exists():
                    raise SafetyViolation(
                        f"Excel lock-файл у цели: {item.original_path}"
                    )
                _exclusive_file_check(item.original_path)
                os.replace(temporary, item.original_path)
                replaced.append(item)
                if fingerprint(item.original_path) != (
                    item.staged_fingerprint
                ):
                    raise SafetyViolation(
                        f"проверка после публикации не прошла: "
                        f"{item.original_path}"
                    )
                result.published.append(str(item.original_path))
    except Exception as exc:  # noqa: BLE001
        rollback_failures = []
        for item in reversed(created_sidecars):
            sidecar = sidecar_by_entry[item.entry_id]
            try:
                if fingerprint(sidecar) != item.staged_fingerprint:
                    raise SafetyViolation(
                        "SHA sidecar перед rollback не совпал"
                    )
                sidecar.unlink()
                result.rolled_back.append(str(sidecar))
                if result.sidecars is not None:
                    result.sidecars.remove(str(sidecar))
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_failures.append(
                    {
                        "path": str(sidecar),
                        "error": str(rollback_exc),
                    }
                )
        for item in reversed(replaced):
            backup = backup_by_entry[item.entry_id]
            rollback_temp = item.original_path.parent / (
                f".rcd-{item.entry_id[:8]}-{secrets.token_hex(3)}.rollback"
                f"{item.original_path.suffix.casefold()}"
            )
            try:
                stable_copy(backup, rollback_temp)
                os.replace(rollback_temp, item.original_path)
                if fingerprint(item.original_path) != (
                    item.source_fingerprint
                ):
                    raise SafetyViolation(
                        "SHA distribution rollback не совпал"
                    )
                result.rolled_back.append(str(item.original_path))
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_failures.append(
                    {
                        "path": str(item.original_path),
                        "error": str(rollback_exc),
                    }
                )
        for temporary in temp_by_entry.values():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        result.issues.append(
            Issue(
                "error",
                "distribution_publish_failed",
                "ночной разброс отменён; выполнен rollback",
                {
                    "error": str(exc),
                    "rollback_failures": rollback_failures,
                },
            )
        )
        _atomic_json(
            stage.staging_dir / "publish.json",
            result.as_dict(),
        )
        if rollback_failures:
            raise SafetyViolation(
                "distribution rollback неполон: "
                + ", ".join(
                    item["path"] for item in rollback_failures
                )
            ) from exc
        raise SafetyViolation(
            f"distribution публикация отменена и откачена: {exc}"
        ) from exc

    provenance_path = stage.staging_dir / "post_publish_provenance.json"
    try:
        _atomic_json(
            provenance_path,
            {
                "published_at": datetime.now().astimezone().isoformat(),
                "run_id": stage.run_id,
                "plan_digest": plan.digest,
                "plan_path": str(
                    plan.snapshot_path.parent / "distribution" / "plan.json"
                ),
                "master": {
                    "path": str(snapshot.master_original),
                    "fingerprint": plan.master_fingerprint.as_dict(),
                    "mode": "read_only",
                },
                "files": [
                    {
                        "entry_id": item.entry_id,
                        "path": str(item.original_path),
                        "before": item.source_fingerprint.as_dict(),
                        "after": item.staged_fingerprint.as_dict(),
                        "mode": (
                            "sidecar"
                            if item.entry_id in sidecar_entry_ids
                            else "replace"
                        ),
                        "published_path": str(
                            sidecar_by_entry.get(
                                item.entry_id,
                                item.original_path,
                            )
                        ),
                        "backup": (
                            str(backup_by_entry[item.entry_id])
                            if item.entry_id in backup_by_entry
                            else None
                        ),
                        "appended_rows": item.appended_rows,
                    }
                    for item in stage.files
                ],
            },
        )
        result.provenance_path = str(provenance_path)
    except Exception as exc:  # noqa: BLE001
        result.issues.append(
            Issue(
                "warning",
                "distribution_provenance_write_failed",
                "публикация завершена, но не записан отдельный post-manifest",
                {"intent": str(intent_path), "error": str(exc)},
            )
        )

    if notify_owner:
        from .notifications import OwnerNotifier

        notice = OwnerNotifier(bundle).send(
            "Ночной аддитивный разброс завершён.\n"
            f"Новых КД: {len(plan.rows)}\n"
            f"Файлов: {len(stage.files)}\n"
            f"Соседних копий для открытых файлов: "
            f"{len(result.sidecars or [])}\n"
            "Общий реестр: не изменялся\n"
            f"Digest: {plan.digest[:16]}"
        )
        result.notification = {
            "sent": notice.sent,
            "skipped": notice.skipped,
            "detail": notice.detail,
            "audience": "owner_only",
        }
    _atomic_json(
        stage.staging_dir / "publish.json",
        result.as_dict(),
    )
    return result


def _summary_payload(plan: DistributionPlan) -> dict[str, Any]:
    return {
        "run_id": plan.run_id,
        "rows": len(plan.rows),
        "files": len({row.entry_id for row in plan.rows}),
        "blocked": plan.blocked,
        "issues": len(plan.issues),
        "by_employee": dict(
            Counter(row.responsible for row in plan.rows).most_common()
        ),
        "by_profile": dict(
            Counter(row.profile for row in plan.rows).most_common()
        ),
        "digest": plan.digest,
        "plan": str(
            plan.snapshot_path.parent / "distribution" / "plan.json"
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="registry-distribution",
        description=(
            "Аддитивный разброс новых закрепов без изменения общего реестра"
        ),
    )
    parser.add_argument("--config-dir", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare",
        help="снять свежий snapshot, собрать и проверить staging",
    )
    prepare.add_argument(
        "--assignment-baseline",
        type=Path,
        required=True,
    )
    prepare.add_argument("--run-id")
    plan = commands.add_parser(
        "plan",
        help="построить доказательный план новых строк",
    )
    plan.add_argument("--snapshot", type=Path, required=True)
    plan.add_argument(
        "--assignment-baseline",
        type=Path,
        required=True,
    )
    stage = commands.add_parser(
        "stage",
        help="добавить строки только в локальный Excel staging",
    )
    stage.add_argument("--snapshot", type=Path, required=True)
    stage.add_argument("--plan", type=Path, required=True)
    validate = commands.add_parser(
        "validate",
        help="проверить неизменность старых ячеек и новые строки",
    )
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--stage", type=Path, required=True)
    preflight = commands.add_parser(
        "preflight",
        help="проверить staging и неизменность всех live-источников",
    )
    preflight.add_argument("--plan", type=Path, required=True)
    preflight.add_argument("--stage", type=Path, required=True)
    publish = commands.add_parser(
        "publish",
        help="опубликовать проверенные личные файлы",
    )
    publish.add_argument("--plan", type=Path, required=True)
    publish.add_argument("--stage", type=Path, required=True)
    publish.add_argument("--confirm-digest", required=True)
    publish.add_argument(
        "--sidecar-locked",
        action="store_true",
        help=(
            "для открытых целей создать соседнюю копию "
            "'Копия ... (после распределения ...)'"
        ),
    )
    publish.add_argument("--no-notify", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundle = load_config_bundle(args.config_dir)
    if args.command == "prepare":
        config = load_distribution_config(bundle)
        stale = int(
            bundle.app.safety.get("lock_stale_seconds", 21600)
        )
        with CycleLock(
            bundle.workspace_path() / "cycle.lock",
            stale_seconds=stale,
        ):
            discovery = discover(bundle)
            if discovery.blocked:
                print(
                    json.dumps(
                        discovery.as_dict(),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 2
            snapshot = capture_sources(
                bundle,
                discovery,
                run_id=args.run_id,
            )
            distribution_dir = snapshot.run_dir / "distribution"
            _atomic_json(
                distribution_dir / "discovery.json",
                discovery.as_dict(),
            )
            plan = build_distribution_plan(
                bundle,
                snapshot,
                config,
                args.assignment_baseline,
                snapshot_path=snapshot.run_dir / "snapshot.json",
            )
            payload = _summary_payload(plan)
            payload["snapshot"] = str(
                snapshot.run_dir / "snapshot.json"
            )
            if plan.blocked:
                print(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 2
            staged = build_distribution_staging(
                bundle,
                snapshot,
                plan,
            )
            preflight = preflight_distribution(
                bundle,
                staged,
                plan,
            )
            payload["stage"] = str(
                staged.staging_dir / "stage.json"
            )
            payload["preflight"] = str(
                staged.staging_dir / "preflight.json"
            )
            payload["checked_master"] = preflight.checked_master
            payload["checked_files"] = preflight.checked_files
            payload["checked_existing_cells"] = (
                preflight.validation.checked_existing_cells
            )
            payload["checked_appended_cells"] = (
                preflight.validation.checked_appended_cells
            )
            payload["preflight_blocked"] = preflight.blocked
            print(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2 if preflight.blocked else 0
    if args.command == "plan":
        snapshot = load_snapshot(args.snapshot)
        config = load_distribution_config(bundle)
        plan = build_distribution_plan(
            bundle,
            snapshot,
            config,
            args.assignment_baseline,
            snapshot_path=args.snapshot,
        )
        print(
            json.dumps(
                _summary_payload(plan),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2 if plan.blocked else 0
    if args.command == "stage":
        snapshot = load_snapshot(args.snapshot)
        plan = load_distribution_plan(args.plan)
        staged = build_distribution_staging(bundle, snapshot, plan)
        print(json.dumps(staged.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "validate":
        plan = load_distribution_plan(args.plan)
        staged = load_distribution_stage(args.stage)
        report = validate_distribution_staging(
            bundle,
            staged,
            plan,
        )
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 2 if report.blocked else 0
    if args.command == "preflight":
        plan = load_distribution_plan(args.plan)
        staged = load_distribution_stage(args.stage)
        report = preflight_distribution(
            bundle,
            staged,
            plan,
        )
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 2 if report.blocked else 0
    if args.command == "publish":
        plan = load_distribution_plan(args.plan)
        staged = load_distribution_stage(args.stage)
        result = publish_distribution(
            bundle,
            staged,
            plan,
            confirmation_digest=args.confirm_digest,
            notify_owner=not args.no_notify,
            sidecar_locked=args.sidecar_locked,
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
