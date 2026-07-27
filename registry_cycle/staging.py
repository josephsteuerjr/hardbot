"""Построение изолированного staging-набора через Excel COM."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from .config import ConfigBundle
from .errors import SafetyViolation
from .excel_host import (
    ExcelHost,
    XL_PASTE_FORMATS,
    range_has_formulas,
    sheet_by_name,
)
from .files import fingerprint, stable_copy
from .models import FileFingerprint, PersonalProjection, SyncPlan
from .normalize import is_blank, json_value, norm_title, parse_date
from .reader import read_portfolio
from .semantics import values_equal_for_rule
from .snapshot import CapturedPersonal, SnapshotSet

ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True, slots=True)
class StagedFile:
    kind: str
    original_path: Path
    source_copy: Path
    staged_path: Path
    source_fingerprint: FileFingerprint
    staged_fingerprint: FileFingerprint
    entry_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "entry_id": self.entry_id,
            "original_path": str(self.original_path),
            "source_copy": str(self.source_copy),
            "staged_path": str(self.staged_path),
            "source_fingerprint": self.source_fingerprint.as_dict(),
            "staged_fingerprint": self.staged_fingerprint.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class StageSet:
    run_id: str
    run_dir: Path
    staging_dir: Path
    plan_digest: str
    master: StagedFile
    personals: tuple[StagedFile, ...]

    @property
    def files(self) -> tuple[StagedFile, ...]:
        return (self.master, *self.personals)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "staging_dir": str(self.staging_dir),
            "plan_digest": self.plan_digest,
            "master": self.master.as_dict(),
            "personals": [item.as_dict() for item in self.personals],
        }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _fingerprint_from_dict(payload: dict[str, Any]) -> FileFingerprint:
    return FileFingerprint(
        size=int(payload["size"]),
        mtime_ns=int(payload["mtime_ns"]),
        sha256=str(payload["sha256"]),
    )


def _staged_file_from_dict(payload: dict[str, Any]) -> StagedFile:
    return StagedFile(
        kind=str(payload["kind"]),
        entry_id=(
            str(payload["entry_id"])
            if payload.get("entry_id") is not None
            else None
        ),
        original_path=Path(payload["original_path"]),
        source_copy=Path(payload["source_copy"]),
        staged_path=Path(payload["staged_path"]),
        source_fingerprint=_fingerprint_from_dict(payload["source_fingerprint"]),
        staged_fingerprint=_fingerprint_from_dict(payload["staged_fingerprint"]),
    )


def load_stage(path: str | Path) -> StageSet:
    stage_path = Path(path)
    payload = json.loads(stage_path.read_text(encoding="utf-8"))
    stage = StageSet(
        run_id=str(payload["run_id"]),
        run_dir=Path(payload["run_dir"]),
        staging_dir=Path(payload["staging_dir"]),
        plan_digest=str(payload["plan_digest"]),
        master=_staged_file_from_dict(payload["master"]),
        personals=tuple(
            _staged_file_from_dict(item)
            for item in payload["personals"]
        ),
    )
    missing = [
        str(item.staged_path)
        for item in stage.files
        if not item.staged_path.is_file()
    ]
    if missing:
        raise SafetyViolation(
            "staging неполон; отсутствуют файлы: " + ", ".join(missing[:10])
        )
    return stage


def _copy_to_staging(source: Path, destination: Path) -> None:
    stable_copy(source, destination)


def _excel_serial(value: date) -> int:
    if value < date(1900, 3, 1):
        return (value - date(1899, 12, 31)).days
    return (value - date(1899, 12, 30)).days


def _excel_datetime_serial(value: datetime) -> int | float:
    days = _excel_serial(value.date())
    seconds = (
        value.hour * 3600
        + value.minute * 60
        + value.second
        + value.microsecond / 1_000_000
    )
    return days if seconds == 0 else days + seconds / 86400


def excel_safe_value(value: Any, value_type: str | None = None) -> Any:
    """Не позволить Excel самовольно превратить текст в число/дату.

    Апостроф — штатный Excel-маркер текстового ввода и не становится частью
    значения ячейки. Для января–февраля 1900 нужен исходный Excel serial:
    OLE Automation иначе сдвигает дату на сутки из-за фиктивного 29 февраля.
    """
    if is_blank(value):
        return None
    if value_type == "text" and isinstance(value, (date, datetime)):
        return "'" + str(json_value(value))
    if value_type == "date":
        parsed = parse_date(value)
        if parsed is not None:
            return _excel_serial(parsed)
        if value is not None:
            return "'" + str(json_value(value))
    if isinstance(value, datetime):
        return _excel_datetime_serial(value)
    if isinstance(value, date):
        return _excel_serial(value)
    if isinstance(value, str):
        return "'" + value
    return value


@dataclass(frozen=True, slots=True)
class _FilterRule:
    field: int
    criteria1: Any
    operator: int
    criteria2: Any = None
    has_criteria2: bool = False


@dataclass(frozen=True, slots=True)
class _SheetView:
    had_autofilter: bool
    filter_first_row: int = 0
    filter_first_column: int = 0
    filter_columns: int = 0
    filter_last_row: int = 0
    filters: tuple[_FilterRule, ...] = ()
    manually_hidden_runs: tuple[tuple[int, int], ...] = ()


def _hidden_runs(sheet: Any, first_row: int, last_row: int) -> tuple[tuple[int, int], ...]:
    if last_row < first_row:
        return ()
    visible_rows: set[int] = set()
    cell_range = sheet.range((first_row, 1), (last_row, 1))
    try:
        visible = cell_range.api.SpecialCells(12)
        for area in visible.Areas:
            start = int(area.Row)
            count = int(area.Rows.Count)
            visible_rows.update(range(start, start + count))
    except Exception:  # noqa: BLE001 — нет видимых ячеек
        pass
    hidden = [
        row
        for row in range(first_row, last_row + 1)
        if row not in visible_rows
    ]
    runs: list[tuple[int, int]] = []
    for row in hidden:
        if runs and runs[-1][1] + 1 == row:
            runs[-1] = (runs[-1][0], row)
        else:
            runs.append((row, row))
    return tuple(runs)


def _capture_filters(sheet: Any) -> tuple[
    bool,
    int,
    int,
    int,
    int,
    tuple[_FilterRule, ...],
]:
    if not bool(sheet.api.AutoFilterMode):
        return False, 0, 0, 0, 0, ()
    auto_filter = sheet.api.AutoFilter
    filter_range = auto_filter.Range
    rules: list[_FilterRule] = []
    for field in range(1, int(auto_filter.Filters.Count) + 1):
        item = auto_filter.Filters.Item(field)
        if not bool(item.On):
            continue
        criteria1 = item.Criteria1
        operator = int(item.Operator)
        try:
            criteria2 = item.Criteria2
            has_criteria2 = True
        except Exception:  # noqa: BLE001 — у фильтра нет второго критерия
            criteria2 = None
            has_criteria2 = False
        rules.append(
            _FilterRule(
                field=field,
                criteria1=criteria1,
                operator=operator,
                criteria2=criteria2,
                has_criteria2=has_criteria2,
            )
        )
    return (
        True,
        int(filter_range.Row),
        int(filter_range.Column),
        int(filter_range.Columns.Count),
        int(filter_range.Row + filter_range.Rows.Count - 1),
        tuple(rules),
    )


def _restore_sheet_view(
    sheet: Any,
    state: _SheetView,
    *,
    filter_last_row: int | None,
) -> None:
    if state.had_autofilter:
        sheet.api.AutoFilterMode = False
        last_row = filter_last_row or state.filter_last_row
        target = sheet.range(
            (state.filter_first_row, state.filter_first_column),
            (
                max(state.filter_first_row, last_row),
                state.filter_first_column + state.filter_columns - 1,
            ),
        )
        target.api.AutoFilter()
        for rule in state.filters:
            criteria1 = rule.criteria1
            # xlFilterValues (7) даже с одним выбранным значением требует
            # SAFEARRAY; строку Excel молча превращает в обычный Operator=0.
            if rule.operator == 7 and not isinstance(criteria1, tuple):
                import pythoncom
                from win32com.client import VARIANT

                criteria1 = VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_VARIANT,
                    [criteria1],
                )
            kwargs = {
                "Field": rule.field,
                "Criteria1": criteria1,
            }
            # Excel отклоняет Operator=0 у обычного одиночного критерия:
            # ноль означает не оператор, а отсутствие аргумента.
            if rule.operator:
                kwargs["Operator"] = rule.operator
            if rule.has_criteria2:
                kwargs["Criteria2"] = rule.criteria2
            target.api.AutoFilter(**kwargs)
    for first, last in state.manually_hidden_runs:
        sheet.range((first, 1), (last, 1)).api.EntireRow.Hidden = True


@contextmanager
def _revealed_rows(
    sheet: Any,
    *,
    first_row: int,
    last_row: int,
    filter_last_row: int | None = None,
):
    (
        had_autofilter,
        filter_first_row,
        filter_first_column,
        filter_columns,
        original_filter_last_row,
        filters,
    ) = _capture_filters(sheet)
    if bool(sheet.api.FilterMode):
        sheet.api.ShowAllData()
    manual_runs = _hidden_runs(sheet, first_row, last_row)
    if last_row >= first_row:
        sheet.range((first_row, 1), (last_row, 1)).api.EntireRow.Hidden = False
    state = _SheetView(
        had_autofilter=had_autofilter,
        filter_first_row=filter_first_row,
        filter_first_column=filter_first_column,
        filter_columns=filter_columns,
        filter_last_row=original_filter_last_row,
        filters=filters,
        manually_hidden_runs=manual_runs,
    )
    try:
        yield
    finally:
        _restore_sheet_view(
            sheet,
            state,
            filter_last_row=filter_last_row,
        )


def _canonical_date_format(
    sheet: Any,
    *,
    column: int,
    first_data_row: int,
    last_search_row: int,
) -> Any:
    fallback = None
    for row in range(first_data_row, min(last_search_row, first_data_row + 500) + 1):
        number_format = sheet.range((row, column)).api.NumberFormat
        fallback = fallback or number_format
        if number_format not in (None, "@", "General", "Общий"):
            return number_format
    if fallback not in (None, "@", "General", "Общий"):
        return fallback
    raise SafetyViolation(
        f"staging: не найден шаблон формата даты в колонке {column}"
    )


def _write_column_runs(
    sheet: Any,
    changes,
    rules_by_title,
    *,
    first_data_row: int,
) -> None:
    ordered = sorted(changes, key=lambda item: (item.column, item.row))
    runs: list[list[Any]] = []
    for change in ordered:
        if (
            runs
            and runs[-1][-1].column == change.column
            and runs[-1][-1].row + 1 == change.row
        ):
            runs[-1].append(change)
        else:
            runs.append([change])

    for run in runs:
        first = run[0]
        last = run[-1]
        target = sheet.range(
            (first.row + 1, first.column + 1),
            (last.row + 1, last.column + 1),
        )
        before_values = target.value
        if len(run) == 1:
            before_values = [before_values]
        elif not isinstance(before_values, list):
            before_values = [before_values]
        for change, current in zip(run, before_values, strict=True):
            rule = rules_by_title[change.title]
            if not values_equal_for_rule(current, change.before, rule):
                raise SafetyViolation(
                    "staging: значение мастера не совпало с dry-run: "
                    f"{change.contract_id}, «{change.title}»"
                )
        rule = rules_by_title[first.title]
        if rule.value_type == "date":
            target.api.NumberFormat = _canonical_date_format(
                sheet,
                column=first.column + 1,
                first_data_row=first_data_row,
                last_search_row=max(last.row + 1, first_data_row),
            )
        target.value = [
            [excel_safe_value(change.after, rule.value_type)]
            for change in run
        ]


def _write_master(
    host: ExcelHost,
    path: Path,
    plan: SyncPlan,
    bundle: ConfigBundle,
) -> None:
    rules = {rule.title: rule for rule in bundle.fields.collect}
    source_snapshot = read_portfolio(path, bundle.fields)
    with host.open_book(path, read_only=False) as book:
        sheet = sheet_by_name(book, bundle.fields.sheet_name)
        if plan.changes:
            first_row = min(change.row + 1 for change in plan.changes)
            last_row = max(change.row + 1 for change in plan.changes)
            with _revealed_rows(
                sheet,
                first_row=first_row,
                last_row=last_row,
            ):
                _write_column_runs(
                    sheet,
                    plan.changes,
                    rules,
                    first_data_row=source_snapshot.data_start + 1,
                )
        book.api.Save()


def _format_new_rows(
    host: ExcelHost,
    sheet: Any,
    *,
    first_data_row: int,
    old_last_row: int,
    new_last_row: int,
    columns: int,
) -> None:
    if new_last_row <= old_last_row or old_last_row < first_data_row:
        return
    source = sheet.range(
        (max(first_data_row, old_last_row), 1),
        (max(first_data_row, old_last_row), columns),
    )
    target = sheet.range(
        (old_last_row + 1, 1),
        (new_last_row, columns),
    )
    source.api.Copy()
    target.api.PasteSpecial(Paste=XL_PASTE_FORMATS)
    if host.app is not None:
        host.app.api.CutCopyMode = False


def _write_projection(
    host: ExcelHost,
    path: Path,
    source: CapturedPersonal,
    projection: PersonalProjection,
    bundle: ConfigBundle,
) -> None:
    snapshot = read_portfolio(source.copy_path, bundle.fields)
    if tuple(projection.headers) != tuple(
        str(value or "").strip()
        for value in snapshot.headers
        if str(value or "").strip()
    ):
        raise SafetyViolation(
            f"staging: заголовки изменились после dry-run: {source.entry.path.name}"
        )
    width = len(projection.headers)
    first_data_row = snapshot.data_start + 1
    old_last_row = snapshot.data_start + len(snapshot.rows)
    new_last_row = snapshot.data_start + len(projection.rows)
    clear_last_row = max(old_last_row, new_last_row)

    with host.open_book(path, read_only=False) as book:
        sheet = sheet_by_name(book, bundle.fields.sheet_name)
        rules_by_title = {
            norm_title(rule.title): rule
            for rule in bundle.fields.collect
        }
        value_types = [
            (
                rules_by_title[norm_title(title)].value_type
                if norm_title(title) in rules_by_title
                else None
            )
            for title in projection.headers
        ]
        with _revealed_rows(
            sheet,
            first_row=first_data_row,
            last_row=clear_last_row,
            filter_last_row=max(
                snapshot.type_row + 1 if snapshot.type_row is not None else 1,
                new_last_row,
            ),
        ):
            if clear_last_row >= first_data_row:
                mutable = sheet.range(
                    (first_data_row, 1),
                    (clear_last_row, width),
                )
                if range_has_formulas(mutable):
                    raise SafetyViolation(
                        "staging: в перезаписываемом диапазоне найдены формулы: "
                        f"{source.entry.path.name}"
                    )
                _format_new_rows(
                    host,
                    sheet,
                    first_data_row=first_data_row,
                    old_last_row=old_last_row,
                    new_last_row=new_last_row,
                    columns=width,
                )
                mutable.clear_contents()
                for column, value_type in enumerate(value_types, start=1):
                    if value_type != "date" or new_last_row < first_data_row:
                        continue
                    number_format = _canonical_date_format(
                        sheet,
                        column=column,
                        first_data_row=first_data_row,
                        last_search_row=max(old_last_row, first_data_row),
                    )
                    sheet.range(
                        (first_data_row, column),
                        (new_last_row, column),
                    ).api.NumberFormat = number_format
            chunk_size = 500
            for offset in range(0, len(projection.rows), chunk_size):
                chunk = projection.rows[offset : offset + chunk_size]
                start = first_data_row + offset
                finish = start + len(chunk) - 1
                sheet.range((start, 1), (finish, width)).value = [
                    [
                        excel_safe_value(value, value_types[column])
                        for column, value in enumerate(row)
                    ]
                    for row in chunk
                ]
        book.api.Save()


def build_staging(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
    plan: SyncPlan,
    *,
    output_dir: str | Path | None = None,
    progress: ProgressCallback | None = None,
) -> StageSet:
    if plan.blocked:
        raise SafetyViolation("заблокированный план нельзя переносить в staging")
    if plan.digest == "":
        raise SafetyViolation("у плана нет digest")
    staging_dir = (
        Path(output_dir)
        if output_dir is not None
        else snapshot.run_dir / "staging"
    )
    staging_dir.mkdir(parents=True, exist_ok=False)
    personal_dir = staging_dir / "personals"
    personal_dir.mkdir()

    projection_by_id = {
        projection.entry.entry_id: projection
        for projection in plan.projections
    }
    snapshot_by_id = {
        item.entry.entry_id: item
        for item in snapshot.personals
    }
    if set(projection_by_id) != set(snapshot_by_id):
        raise SafetyViolation(
            "staging: manifest снимка и набор проекций не совпадают"
        )

    master_path = staging_dir / f"master{snapshot.master_copy.suffix.casefold()}"
    _copy_to_staging(snapshot.master_copy, master_path)
    staged_personal_paths: dict[str, Path] = {}
    for entry_id, source in snapshot_by_id.items():
        target = personal_dir / f"{entry_id}{source.copy_path.suffix.casefold()}"
        _copy_to_staging(source.copy_path, target)
        staged_personal_paths[entry_id] = target

    total = 1 + len(snapshot.personals)
    done = 0
    with ExcelHost() as host:
        _write_master(host, master_path, plan, bundle)
        done += 1
        if progress is not None:
            progress("Общий реестр", done, total)
        for entry_id in sorted(snapshot_by_id):
            source = snapshot_by_id[entry_id]
            _write_projection(
                host,
                staged_personal_paths[entry_id],
                source,
                projection_by_id[entry_id],
                bundle,
            )
            done += 1
            if progress is not None:
                progress(source.entry.path.name, done, total)

    master_file = StagedFile(
        kind="master",
        original_path=snapshot.master_original,
        source_copy=snapshot.master_copy,
        staged_path=master_path,
        source_fingerprint=snapshot.master_fingerprint,
        staged_fingerprint=fingerprint(master_path),
    )
    personals = tuple(
        StagedFile(
            kind="personal",
            entry_id=entry_id,
            original_path=snapshot_by_id[entry_id].entry.path,
            source_copy=snapshot_by_id[entry_id].copy_path,
            staged_path=staged_personal_paths[entry_id],
            source_fingerprint=snapshot_by_id[entry_id].fingerprint,
            staged_fingerprint=fingerprint(staged_personal_paths[entry_id]),
        )
        for entry_id in sorted(snapshot_by_id)
    )
    stage_set = StageSet(
        run_id=snapshot.run_id,
        run_dir=snapshot.run_dir,
        staging_dir=staging_dir,
        plan_digest=plan.digest,
        master=master_file,
        personals=personals,
    )
    _atomic_json(staging_dir / "stage.json", stage_set.as_dict())
    return stage_set
