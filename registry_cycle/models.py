"""Неизменяемые модели чтения, планирования и публикации."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .normalize import json_value, norm_key, norm_name, norm_title


def _jsonify(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonify(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return json_value(value)


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    size: int
    mtime_ns: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Issue:
    severity: str
    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "context": _jsonify(self.context),
        }


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    entry_id: str
    path: Path
    country: str
    group: str
    profile: str
    employee_folder: str
    responsible_hint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "path": str(self.path),
            "country": self.country,
            "group": self.group,
            "profile": self.profile,
            "employee_folder": self.employee_folder,
            "responsible_hint": self.responsible_hint,
        }


@dataclass(slots=True)
class DiscoveryReport:
    entries: list[ManifestEntry] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    configured_groups: list[str] = field(default_factory=list)
    discovered_groups: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.as_dict() for entry in self.entries],
            "issues": [issue.as_dict() for issue in self.issues],
            "configured_groups": self.configured_groups,
            "discovered_groups": self.discovered_groups,
            "blocked": self.blocked,
        }


@dataclass(frozen=True, slots=True)
class WorkbookSnapshot:
    path: Path
    sheet: str
    sheet_names: tuple[str, ...]
    fingerprint: FileFingerprint
    header_row: int
    type_row: int | None
    data_start: int
    headers: tuple[Any, ...]
    type_tags: tuple[Any, ...]
    rows: tuple[tuple[Any, ...], ...]

    def column_index(self, title: str) -> int | None:
        wanted = norm_title(title)
        found = [index for index, value in enumerate(self.headers) if norm_title(value) == wanted]
        if len(found) == 1:
            return found[0]
        return None

    def title_map(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for index, value in enumerate(self.headers):
            key = norm_title(value)
            if key and key not in out:
                out[key] = index
        return out

    def row_value(self, row: tuple[Any, ...], column: int) -> Any:
        return row[column] if column < len(row) else None

    def rows_by_key(self, key_title: str) -> dict[str, list[tuple[int, tuple[Any, ...]]]]:
        column = self.column_index(key_title)
        if column is None:
            return {}
        out: dict[str, list[tuple[int, tuple[Any, ...]]]] = {}
        for offset, row in enumerate(self.rows):
            key = norm_key(self.row_value(row, column))
            if key:
                out.setdefault(key, []).append((self.data_start + offset, row))
        return out

    def unique_responsibles(self, responsible_title: str) -> tuple[str, ...]:
        column = self.column_index(responsible_title)
        if column is None:
            return ()
        values: dict[str, str] = {}
        for row in self.rows:
            raw = self.row_value(row, column)
            key = norm_name(raw)
            if key:
                values.setdefault(key, str(raw).strip())
        return tuple(values.values())


@dataclass(frozen=True, slots=True)
class CellChange:
    target: str
    path: Path
    sheet: str
    row: int
    column: int
    title: str
    contract_id: str
    before: Any
    after: Any
    source_path: Path
    source_row: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "path": str(self.path),
            "sheet": self.sheet,
            "row": self.row + 1,
            "column": self.column + 1,
            "title": self.title,
            "contract_id": self.contract_id,
            "before": json_value(self.before),
            "after": json_value(self.after),
            "source_path": str(self.source_path),
            "source_row": self.source_row + 1,
        }


@dataclass(frozen=True, slots=True)
class PaymentArchiveCell:
    row: int
    value: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "row": self.row + 1,
            "value": json_value(self.value),
        }


@dataclass(frozen=True, slots=True)
class MonthRolloverPlan:
    closing_month: str
    history_title: str
    current_title: str
    history_column: int
    current_column: int
    insert_history_column: bool
    mode: str
    archive_cells: tuple[PaymentArchiveCell, ...]
    employee_clear_titles: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "closing_month": self.closing_month,
            "history_title": self.history_title,
            "current_title": self.current_title,
            "history_column": self.history_column + 1,
            "current_column": self.current_column + 1,
            "insert_history_column": self.insert_history_column,
            "mode": self.mode,
            "archive_cells": [
                cell.as_dict()
                for cell in self.archive_cells
            ],
            "archive_cell_count": len(self.archive_cells),
            "employee_clear_titles": list(self.employee_clear_titles),
        }


@dataclass(frozen=True, slots=True)
class PersonalProjection:
    entry: ManifestEntry
    responsible: str
    headers: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    source_fingerprint: FileFingerprint
    type_tags: tuple[Any, ...] = ()
    added_columns: tuple[str, ...] = ()
    preserved_cells: int = 0
    employee_owned_preserved_cells: int = 0
    handoff_cells: int = 0
    rollover_cleared_cells: int = 0
    retained_rows: int = 0
    removed_rows: int = 0

    def as_summary(self) -> dict[str, Any]:
        return {
            "entry": self.entry.as_dict(),
            "responsible": self.responsible,
            "columns": len(self.headers),
            "headers": list(self.headers),
            "type_tags": _jsonify(self.type_tags),
            "added_columns": list(self.added_columns),
            "rows": len(self.rows),
            "preserved_cells": self.preserved_cells,
            "employee_owned_preserved_cells": (
                self.employee_owned_preserved_cells
            ),
            "handoff_cells": self.handoff_cells,
            "rollover_cleared_cells": self.rollover_cleared_cells,
            "retained_rows": self.retained_rows,
            "removed_rows": self.removed_rows,
            "source_fingerprint": self.source_fingerprint.as_dict(),
        }


@dataclass(slots=True)
class SyncPlan:
    master_path: Path
    master_fingerprint: FileFingerprint
    changes: list[CellChange] = field(default_factory=list)
    projections: list[PersonalProjection] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    month_rollover: MonthRolloverPlan | None = None
    config_identity: str = ""

    @property
    def blocked(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "master_path": str(self.master_path),
            "master_fingerprint": self.master_fingerprint.as_dict(),
            "config_identity": self.config_identity,
            "changes": [change.as_dict() for change in self.changes],
            "projections": [projection.as_summary() for projection in self.projections],
            "issues": [issue.as_dict() for issue in self.issues],
            "month_rollover": (
                self.month_rollover.as_dict()
                if self.month_rollover is not None
                else None
            ),
            "blocked": self.blocked,
        }

    @property
    def digest(self) -> str:
        raw = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        payload = self.canonical_payload()
        payload["digest"] = self.digest
        return payload
