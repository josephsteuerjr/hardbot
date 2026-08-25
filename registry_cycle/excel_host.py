"""Узкий адаптер Excel COM через xlwings."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from .errors import SafetyViolation
from .normalize import json_value

XL_CELL_TYPE_FORMULAS = -4123
XL_PASTE_FORMATS = -4122


class ExcelHost:
    """Один скрытый процесс Excel для конечной серии локальных книг."""

    def __init__(self) -> None:
        self.app = None

    def __enter__(self) -> "ExcelHost":
        import xlwings as xw

        self.app = xw.App(visible=False, add_book=False)
        self.app.display_alerts = False
        self.app.screen_updating = False
        try:
            self.app.calculation = "manual"
            self.app.api.CalculateBeforeSave = False
            self.app.api.EnableEvents = False
            self.app.api.AskToUpdateLinks = False
        except Exception:  # noqa: BLE001 — различия версий Excel
            pass
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.app is None:
            return
        try:
            self.app.api.CutCopyMode = False
        except Exception:  # noqa: BLE001
            pass
        try:
            self.app.quit()
        finally:
            self.app = None

    @contextmanager
    def open_book(
        self,
        path: str | Path,
        *,
        read_only: bool,
    ) -> Iterator[Any]:
        if self.app is None:
            raise RuntimeError("ExcelHost не запущен")
        book = self.app.books.open(
            str(Path(path).resolve()),
            update_links=False,
            read_only=read_only,
            ignore_read_only_recommended=True,
            notify=False,
            add_to_mru=False,
            local=True,
        )
        try:
            yield book
        finally:
            try:
                book.api.Close(SaveChanges=False)
            except Exception:  # noqa: BLE001 — Excel уже мог закрыть книгу
                pass


def sheet_by_name(book: Any, name: str) -> Any:
    """Доступ по индексу: COM иногда ломает кириллицу в `Sheets(name)`."""
    for index, sheet_name in enumerate(book.sheet_names):
        if sheet_name == name:
            return book.sheets[index]
    raise SafetyViolation(f"в {Path(book.fullname).name} нет листа «{name}»")


def range_has_formulas(cell_range: Any) -> bool:
    try:
        found = cell_range.api.SpecialCells(XL_CELL_TYPE_FORMULAS)
        return int(found.Count) > 0
    except Exception:  # noqa: BLE001 — COM кидает ошибку, когда ничего не найдено
        return False


def _as_matrix(value: Any, rows: int, columns: int) -> tuple[tuple[Any, ...], ...]:
    if rows == 1 and columns == 1:
        return ((value,),)
    if rows == 1:
        return (tuple(value),)
    if columns == 1:
        return tuple(
            (item[0] if isinstance(item, tuple) else item,)
            for item in value
        )
    return tuple(tuple(row) for row in value)


def sheet_formula_constant_signature(sheet: Any) -> dict[str, Any]:
    """Hash FormulaLocal: формулы и константы, но не вычисленный cache формул."""
    used = sheet.used_range
    rows = int(used.last_cell.row)
    columns = int(used.last_cell.column)
    matrix = _as_matrix(used.api.FormulaLocal, rows, columns)
    digest = hashlib.sha256()
    for row in matrix:
        for value in row:
            raw = json.dumps(
                json_value(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
    return {
        "rows": rows,
        "columns": columns,
        "sha256": digest.hexdigest(),
    }


def workbook_untouched_signatures(
    book: Any,
    *,
    mutable_sheet: str,
) -> dict[str, dict[str, Any]]:
    return {
        sheet.name: sheet_formula_constant_signature(sheet)
        for sheet in book.sheets
        if sheet.name != mutable_sheet
    }


def _signature_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_signature_value(item) for item in value]
    return json_value(value)


def _visible_row_numbers(
    sheet: Any,
    first_row: int,
    last_row: int,
) -> set[int]:
    if last_row < first_row:
        return set()
    visible_rows: set[int] = set()
    try:
        visible = sheet.range(
            (first_row, 1),
            (last_row, 1),
        ).api.SpecialCells(12)
        for area in visible.Areas:
            start = int(area.Row)
            count = int(area.Rows.Count)
            visible_rows.update(range(start, start + count))
    except Exception:  # noqa: BLE001 — нет видимых строк
        pass
    return visible_rows


def _column_values(raw: Any, count: int) -> list[Any]:
    if count == 1:
        if isinstance(raw, list) and len(raw) == 1:
            item = raw[0]
            return [item[0] if isinstance(item, list) else item]
        return [raw]
    if not isinstance(raw, list):
        return [raw]
    return [
        item[0] if isinstance(item, list) and item else item
        for item in raw
    ]


def _infer_date_group_criteria(
    observations: list[tuple[date, bool]],
) -> tuple[Any, ...]:
    """Infer the coarsest exact Excel date grouping from row visibility."""
    if not observations or not any(selected for _value, selected in observations):
        raise SafetyViolation(
            "Excel не раскрыл даты активного фильтра"
        )
    for level in (0, 1, 2):
        grouped: dict[tuple[int, ...], set[bool]] = {}
        representatives: dict[tuple[int, ...], date] = {}
        for value, selected in observations:
            key = (
                (value.year,)
                if level == 0
                else (value.year, value.month)
                if level == 1
                else (value.year, value.month, value.day)
            )
            grouped.setdefault(key, set()).add(selected)
            representatives.setdefault(key, value)
        if any(len(states) != 1 for states in grouped.values()):
            continue
        selected_keys = sorted(
            key
            for key, states in grouped.items()
            if True in states
        )
        if not selected_keys:
            continue
        payload: list[Any] = []
        for key in selected_keys:
            value = (
                date(key[0], 1, 1)
                if level == 0
                else date(key[0], key[1], 1)
                if level == 1
                else representatives[key]
            )
            payload.extend(
                (level, f"{value.month}/{value.day}/{value.year}")
            )
        return tuple(payload)
    raise SafetyViolation(
        "Excel date AutoFilter нельзя восстановить без изменения смысла"
    )


def infer_inaccessible_date_filter(
    sheet: Any,
    *,
    filter_range: Any,
    field: int,
) -> tuple[Any, ...]:
    """Recover a grouped date filter Excel keeps hidden from COM.

    Excel's object model can raise 1004 for Criteria1/Criteria2 of a grouped
    date filter. We infer its year/month/day groups from the current visible
    rows, then ShowAllData; callers restore the equivalent filter afterwards.
    """
    first_row = int(filter_range.Row) + 1
    last_row = int(filter_range.Row + filter_range.Rows.Count - 1)
    before_visible = _visible_row_numbers(sheet, first_row, last_row)
    if bool(sheet.api.FilterMode):
        sheet.api.ShowAllData()
    after_visible = _visible_row_numbers(sheet, first_row, last_row)
    column = int(filter_range.Column) + field - 1
    count = max(0, last_row - first_row + 1)
    if not count:
        raise SafetyViolation("активный фильтр Excel не содержит строк")
    values = _column_values(
        sheet.range(
            (first_row, column),
            (last_row, column),
        ).value,
        count,
    )
    observations: list[tuple[date, bool]] = []
    for row, value in zip(
        range(first_row, last_row + 1),
        values,
        strict=True,
    ):
        # Rows still hidden after ShowAllData were hidden manually and cannot
        # reveal anything about the filter criterion.
        if row not in after_visible:
            continue
        if isinstance(value, datetime):
            day = value.date()
        elif isinstance(value, date):
            day = value
        else:
            continue
        observations.append((day, row in before_visible))
    return _infer_date_group_criteria(observations)


def sheet_filter_signature(sheet: Any) -> dict[str, Any]:
    if not bool(sheet.api.AutoFilterMode):
        return {"enabled": False}
    auto_filter = sheet.api.AutoFilter
    cell_range = auto_filter.Range
    criteria = []
    active_fields = []
    for field in range(1, int(auto_filter.Filters.Count) + 1):
        item = auto_filter.Filters.Item(field)
        if not bool(item.On):
            continue
        active_fields.append((field, item, int(item.Operator)))
    for field, item, operator in active_fields:
        try:
            criteria1 = _signature_value(item.Criteria1)
        except Exception as exc:  # grouped date criteria are hidden by Excel
            if operator != 7 or len(active_fields) != 1:
                raise SafetyViolation(
                    "не удалось прочитать критерии AutoFilter"
                ) from exc
            inferred = infer_inaccessible_date_filter(
                sheet,
                filter_range=cell_range,
                field=field,
            )
            criteria.append(
                {
                    "field": field,
                    "operator": operator,
                    "grouped_date_criteria": list(inferred),
                }
            )
            continue
        if operator == 7 and len(active_fields) == 1:
            try:
                inferred = infer_inaccessible_date_filter(
                    sheet,
                    filter_range=cell_range,
                    field=field,
                )
            except SafetyViolation:
                pass
            else:
                criteria.append(
                    {
                        "field": field,
                        "operator": operator,
                        "grouped_date_criteria": list(inferred),
                    }
                )
                continue
        # Excel COM канонизирует xlFilterValues с единственным значением
        # в обычное равенство. Для видимых строк эти формы эквивалентны.
        if operator == 7 and not isinstance(criteria1, list):
            operator = 0
        record = {
            "field": field,
            "operator": operator,
            "criteria1": criteria1,
        }
        try:
            record["criteria2"] = _signature_value(item.Criteria2)
        except Exception:  # noqa: BLE001 — второй критерий необязателен
            pass
        criteria.append(record)
    return {
        "enabled": True,
        "first_row": int(cell_range.Row),
        "last_row": int(cell_range.Row + cell_range.Rows.Count - 1),
        "first_column": int(cell_range.Column),
        "columns": int(cell_range.Columns.Count),
        "criteria": criteria,
    }
