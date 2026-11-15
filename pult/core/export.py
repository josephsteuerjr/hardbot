"""Экспорт: слепок реестра и метки курьеров в xlsx, состав запроса выгрузки."""

from __future__ import annotations

import urllib.parse
from datetime import datetime

import enrich

from .text import fmt_n, parse_num, to_iso


def _text_cell(ws, row: int, column: int, value):
    """Ячейка openpyxl; строка ложится ТЕКСТОМ, числа и даты — как были.

    openpyxl сам делает из строки с ведущим «=» формулу (а из «#N/A» —
    ошибку), и комментарий выездника «=HYPERLINK(…)» исполнялся бы при
    открытии выгрузки у аналитика. Апостроф, которым `enrich._excel_safe`
    защищает запись через Excel, здесь не годится: openpyxl ввод не разбирает,
    и апостроф остался бы в самой строке. Поэтому тип ячейки ставится явно, а
    строку, похожую на формулу, помечаем quotePrefix — тем же флагом, каким
    Excel хранит набранный апостроф: формула не оживёт и после F2 + Enter.
    """
    cell = ws.cell(row=row, column=column, value=value)
    if isinstance(value, str):
        cell.data_type = "s"
        if enrich.excel_formula_like(value):
            cell.quotePrefix = True
    return cell


def col_width(key: str, title: str) -> float:
    if key in ("address", "visit_comment", "historical_visit_comment"):
        return 42
    if key in ("client", "address_customer", "extra_address", "point_address"):
        return 28
    if key in ("responsible", "point_name", "call_comment"):
        return 22
    if key.endswith("_date"):
        return 12
    return min(max(len(title) * 1.1, 9), 20)


_col_width = col_width


def write_registry_xlsx(path: str, fields: list[dict], rows: list[dict]) -> None:
    from .native_exports import registry
    registry(path,fields,rows)


MARKER_COLS = [
    ("created_at", "Дата", 19), ("kind", "Тип", 12), ("telegram_id", "telegram_id", 14),
    ("rep", "Кейс (rep)", 16), ("client", "Клиент", 28), ("address", "Адрес", 42),
    ("label", "Метка/подпись", 30), ("lat", "Широта", 12), ("lon", "Долгота", 12),
]


def write_markers_xlsx(path: str, rows: list[dict]) -> None:
    from .native_exports import markers
    markers(path,rows)


def export_request(employees: list[dict], selected: set[str],
                   since_text: str, until_text: str) -> dict:
    """Точный состав выгрузки: путь `/admin/export.json?mode=full…`, сотрудники
    и окно доказанных выездов/звонков. `employees` — объединённый список из БД
    (`registry_import.merge_import_employees([], db_employees)`)."""
    all_keys = {employee["key"] for employee in employees}
    picked = set(selected).intersection(all_keys)
    if all_keys and not picked:
        raise ValueError("отметь хотя бы одного сотрудника для экспорта")
    all_employees = not all_keys or picked == all_keys
    names = [] if all_employees else [
        employee["name"] for employee in employees if employee["key"] in picked
    ]
    visit_from = to_iso(since_text)
    visit_to = to_iso(until_text)
    if visit_from and visit_to and visit_from > visit_to:
        raise ValueError("начало окна выгрузки позже окончания")
    params = [("mode", "full")]
    params.extend(("employee", name) for name in names)
    if visit_from:
        params.append(("visit_from", visit_from))
    if visit_to:
        params.append(("visit_to", visit_to))
    return {
        "path": "/admin/export.json?" + urllib.parse.urlencode(params),
        "employees": names,
        "all_employees": all_employees,
        "visit_from": visit_from,
        "visit_to": visit_to,
    }


def export_confirm_text(request: dict, rows_count: int) -> str:
    employee_text = (
        "весь реестр, включая строки без ответственного"
        if request["all_employees"]
        else ", ".join(request["employees"])
    )
    window = (
        f"с {request['visit_from'] or '…'} по {request['visit_to'] or '…'}"
        if request["visit_from"] or request["visit_to"]
        else "без ограничения по дате выезда или звонка"
    )
    return (f"Строк в выгрузке: {fmt_n(rows_count)}\n\n"
            f"Сотрудники: {employee_text}\nПериод: {window}\n\n"
            "Сохранить новый xlsx?")
