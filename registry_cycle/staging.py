"""Построение изолированного staging-набора через Excel COM."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, replace
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
from .models import FileFingerprint, MonthRolloverPlan, PersonalProjection, SyncPlan
from .normalize import comparable_value, is_blank, json_value, norm_title
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
        if isinstance(value, datetime):
            return _excel_datetime_serial(value)
        if isinstance(value, date):
            return _excel_serial(value)
        if isinstance(value, str):
            return "'" + value
        return value
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


def _write_column_runs(
    sheet: Any,
    changes,
    rules_by_title,
    *,
    first_data_row: int,
) -> None:
    ordered = sorted(changes, key=lambda item: (item.column, item.row))
    date_format_cache: dict[int, Any] = {}
    general_format_cache: dict[str, Any] = {}
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
        before_values = _single_column_values(
            target.value,
            len(run),
        )
        for change, current in zip(run, before_values, strict=True):
            rule = rules_by_title[change.title]
            if not values_equal_for_rule(current, change.before, rule):
                raise SafetyViolation(
                    "staging: значение мастера не совпало с dry-run: "
                    f"{change.contract_id}, «{change.title}»"
                )
        rule = rules_by_title[first.title]
        for change in run:
            if (
                rule.value_type in {"date", "literal"}
                and isinstance(change.after, (int, float))
                and not isinstance(change.after, bool)
            ):
                sheet.range(
                    (change.row + 1, change.column + 1)
                ).api.NumberFormat = _general_format_for_sheet(
                    sheet,
                    row=change.row + 1,
                    first_data_row=first_data_row,
                    cache=general_format_cache,
                )
                continue
            if not isinstance(change.after, (date, datetime)):
                continue
            cell_row = change.row + 1
            cell_column = change.column + 1
            current_format = sheet.range(
                (cell_row, cell_column)
            ).api.NumberFormat
            if _is_general_number_format(current_format):
                sheet.range(
                    (cell_row, cell_column)
                ).api.NumberFormat = _date_format_for_changed_cell(
                    sheet,
                    row=cell_row,
                    column=cell_column,
                    first_data_row=first_data_row,
                    cache=date_format_cache,
                )
        target.value = [
            [excel_safe_value(change.after, rule.value_type)]
            for change in run
        ]


def _single_column_values(raw: Any, count: int) -> list[Any]:
    """Привести xlwings scalar/column matrix к списку скаляров."""
    if count == 1:
        if (
            isinstance(raw, list)
            and len(raw) == 1
            and isinstance(raw[0], list)
            and len(raw[0]) == 1
        ):
            return [raw[0][0]]
        return [raw]
    if not isinstance(raw, list):
        return [raw]
    values = []
    for row in raw:
        if isinstance(row, list):
            values.append(row[0] if row else None)
        else:
            values.append(row)
    return values


def _is_general_number_format(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().casefold() in {
        "@",
        "general",
        "общий",
        "основной",
    }


def _date_format_for_changed_cell(
    sheet: Any,
    *,
    row: int,
    column: int,
    first_data_row: int,
    cache: dict[int, Any],
) -> Any:
    current = sheet.range((row, column)).api.NumberFormat
    if not _is_general_number_format(current):
        return current
    if column in cache:
        return cache[column]

    candidates = [
        *range(max(first_data_row, row - 80), row),
        *range(first_data_row, first_data_row + 500),
    ]
    seen: set[int] = set()
    for candidate_row in candidates:
        if candidate_row in seen:
            continue
        seen.add(candidate_row)
        number_format = sheet.range(
            (candidate_row, column)
        ).api.NumberFormat
        if not _is_general_number_format(number_format):
            cache[column] = number_format
            return number_format
    # Fallback применяется только к новой typed-date ячейке, а не к столбцу.
    cache[column] = "dd.mm.yyyy"
    return cache[column]


def _general_format_for_sheet(
    sheet: Any,
    *,
    row: int,
    first_data_row: int,
    cache: dict[str, Any],
) -> Any:
    if "value" in cache:
        return cache["value"]
    candidates = [
        *((row, column) for column in range(1, 11)),
        *(
            (candidate_row, column)
            for candidate_row in range(first_data_row, first_data_row + 10)
            for column in range(1, 11)
        ),
    ]
    for candidate_row, candidate_column in candidates:
        number_format = sheet.range(
            (candidate_row, candidate_column)
        ).api.NumberFormat
        if _is_general_number_format(number_format):
            cache["value"] = number_format
            return number_format
    raise SafetyViolation(
        "staging: в книге не найден локализованный General-формат"
    )


def _contiguous_archive_runs(cells):
    runs: list[list[Any]] = []
    for cell in sorted(cells, key=lambda item: item.row):
        if runs and runs[-1][-1].row + 1 == cell.row:
            runs[-1].append(cell)
        else:
            runs.append([cell])
    return runs


def _write_month_rollover(
    host: ExcelHost,
    sheet: Any,
    source_snapshot,
    rollover: MonthRolloverPlan,
) -> None:
    first_data_row = source_snapshot.data_start + 1
    last_data_row = source_snapshot.data_start + len(source_snapshot.rows)
    source_current_column = rollover.current_column + 1
    current_header = sheet.range(
        (source_snapshot.header_row + 1, source_current_column)
    ).value
    if norm_title(current_header) != norm_title(rollover.current_title):
        raise SafetyViolation(
            "staging: колонка «Текущие платежи» сдвинулась после dry-run"
        )

    if last_data_row >= first_data_row:
        current_range = sheet.range(
            (first_data_row, source_current_column),
            (last_data_row, source_current_column),
        )
        if range_has_formulas(current_range):
            raise SafetyViolation(
                "staging: в «Текущих платежах» найдены формулы; "
                "архивация только вычисленного кэша запрещена"
            )
        current_values = _single_column_values(
            current_range.value,
            len(source_snapshot.rows),
        )
        for offset, actual in enumerate(current_values):
            expected = source_snapshot.row_value(
                source_snapshot.rows[offset],
                rollover.current_column,
            )
            if comparable_value(actual) != comparable_value(expected):
                raise SafetyViolation(
                    "staging: «Текущие платежи» изменились после dry-run: "
                    f"строка {source_snapshot.data_start + offset + 1}"
                )

    if rollover.insert_history_column:
        insertion = sheet.range((1, rollover.history_column + 1))
        insertion.api.EntireColumn.Insert()
        history_column = rollover.history_column + 1
        current_column = rollover.current_column + 2
        # Excel сам наследует формат соседней исторической колонки при
        # вставке. Форматы через PasteSpecial здесь намеренно не копируются:
        # на .xlsb с AutoFilter COM способен применить формат ко всему листу.
        sheet.range((1, history_column)).column_width = sheet.range(
            (1, current_column)
        ).column_width
    else:
        history_column = rollover.history_column + 1
        current_column = rollover.current_column + 1
        history_header = sheet.range(
            (source_snapshot.header_row + 1, history_column)
        ).value
        if norm_title(history_header) != norm_title(rollover.history_title):
            raise SafetyViolation(
                "staging: историческая колонка месяца сдвинулась "
                "после dry-run"
            )
        if last_data_row >= first_data_row:
            history_range = sheet.range(
                (first_data_row, history_column),
                (last_data_row, history_column),
            )
            if range_has_formulas(history_range):
                raise SafetyViolation(
                    "staging: в исторической колонке платежей найдены формулы"
                )

    sheet.range(
        (source_snapshot.header_row + 1, history_column)
    ).value = rollover.history_title
    if source_snapshot.type_row is not None:
        sheet.range(
            (source_snapshot.type_row + 1, history_column)
        ).clear_contents()

    for run in _contiguous_archive_runs(rollover.archive_cells):
        first = run[0].row + 1
        last = run[-1].row + 1
        target = sheet.range(
            (first, history_column),
            (last, history_column),
        )
        if not rollover.insert_history_column:
            existing = _single_column_values(target.value, len(run))
            for cell, before in zip(run, existing, strict=True):
                if (
                    not is_blank(before)
                    and comparable_value(before)
                    != comparable_value(cell.value)
                ):
                    raise SafetyViolation(
                        "staging: историческая оплата изменилась "
                        f"после dry-run: строка {cell.row + 1}"
                    )
        target.value = [
            [excel_safe_value(cell.value)]
            for cell in run
        ]
    if last_data_row >= first_data_row:
        sheet.range(
            (first_data_row, current_column),
            (last_data_row, current_column),
        ).clear_contents()


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
        if plan.month_rollover is not None:
            _write_month_rollover(
                host,
                sheet,
                source_snapshot,
                plan.month_rollover,
            )
        mapped_changes = plan.changes
        if (
            plan.month_rollover is not None
            and plan.month_rollover.insert_history_column
        ):
            insertion_column = plan.month_rollover.history_column
            mapped_changes = [
                (
                    replace(change, column=change.column + 1)
                    if change.column >= insertion_column
                    else change
                )
                for change in plan.changes
            ]
        if mapped_changes:
            first_row = min(change.row + 1 for change in mapped_changes)
            last_row = max(change.row + 1 for change in mapped_changes)
            with _revealed_rows(
                sheet,
                first_row=first_row,
                last_row=last_row,
            ):
                _write_column_runs(
                    sheet,
                    mapped_changes,
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
        general_format_cache: dict[str, Any] = {}
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
            chunk_size = 500
            for offset in range(0, len(projection.rows), chunk_size):
                chunk = projection.rows[offset : offset + chunk_size]
                start = first_data_row + offset
                finish = start + len(chunk) - 1
                for row_offset, row in enumerate(chunk):
                    for column, value in enumerate(row):
                        if (
                            value_types[column] in {"date", "literal"}
                            and isinstance(value, (int, float))
                            and not isinstance(value, bool)
                        ):
                            sheet.range(
                                (
                                    start + row_offset,
                                    column + 1,
                                )
                            ).api.NumberFormat = _general_format_for_sheet(
                                sheet,
                                row=start + row_offset,
                                first_data_row=first_data_row,
                                cache=general_format_cache,
                            )
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
