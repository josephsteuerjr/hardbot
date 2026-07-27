"""Узкий адаптер Excel COM через xlwings."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
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


def sheet_filter_signature(sheet: Any) -> dict[str, Any]:
    if not bool(sheet.api.AutoFilterMode):
        return {"enabled": False}
    auto_filter = sheet.api.AutoFilter
    cell_range = auto_filter.Range
    criteria = []
    for field in range(1, int(auto_filter.Filters.Count) + 1):
        item = auto_filter.Filters.Item(field)
        if not bool(item.On):
            continue
        criteria1 = _signature_value(item.Criteria1)
        operator = int(item.Operator)
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
