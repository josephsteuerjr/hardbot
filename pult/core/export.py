"""Экспорт: слепок реестра и метки курьеров в xlsx, состав запроса выгрузки."""

from __future__ import annotations

import urllib.parse
from datetime import datetime

from .text import fmt_n, parse_num, to_iso


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
    """Слепок БД в новый xlsx: 3 строки шапки (блоки объединены), даты и суммы —
    типизированные, автофильтр, закреплённая шапка."""
    import enrich
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Портфели"

    bold = Font(bold=True)
    italic = Font(italic=True, color="8A8578")
    center = Alignment(horizontal="center")

    # строка 1 — шапки блоков (merge по колонкам блока)
    start = 0
    for i in range(1, len(fields) + 1):
        if i == len(fields) or fields[i]["block"] != fields[start]["block"]:
            c = ws.cell(row=1, column=start + 1, value=fields[start]["block"])
            c.font, c.alignment = bold, center
            if i - 1 > start:
                ws.merge_cells(start_row=1, start_column=start + 1,
                               end_row=1, end_column=i)
            start = i
    # строка 2 — заголовки, строка 3 — сх/хард, ширины
    for j, fld in enumerate(fields, start=1):
        ws.cell(row=2, column=j, value=fld["title"]).font = bold
        ws.cell(row=3, column=j, value=fld["source"]).font = italic
        ws.column_dimensions[get_column_letter(j)].width = col_width(fld["key"], fld["title"])
    # данные со строки 4
    for r, row in enumerate(rows, start=4):
        for j, fld in enumerate(fields, start=1):
            v = row.get(fld["key"])
            if v in (None, ""):
                continue
            if fld.get("date"):
                d = enrich.parse_date(v)
                if d:
                    c = ws.cell(row=r, column=j, value=d)
                    c.number_format = "DD.MM.YYYY"
                    continue
            if fld.get("num"):
                n = parse_num(v)
                if n is not None:
                    c = ws.cell(row=r, column=j, value=n)
                    c.number_format = "#,##0"
                    continue
            ws.cell(row=r, column=j, value=str(v))
    last = max(len(rows) + 3, 4)
    ws.auto_filter.ref = f"A2:{get_column_letter(len(fields))}{last}"
    ws.freeze_panes = "A4"
    wb.save(path)


MARKER_COLS = [
    ("created_at", "Дата", 19), ("kind", "Тип", 12), ("telegram_id", "telegram_id", 14),
    ("rep", "Кейс (rep)", 16), ("client", "Клиент", 28), ("address", "Адрес", 42),
    ("label", "Метка/подпись", 30), ("lat", "Широта", 12), ("lon", "Долгота", 12),
]


def write_markers_xlsx(path: str, rows: list[dict]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Метки"
    for j, (_k, title, w) in enumerate(MARKER_COLS, start=1):
        ws.cell(row=1, column=j, value=title).font = Font(bold=True)
        ws.column_dimensions[get_column_letter(j)].width = w
    for r, row in enumerate(rows, start=2):
        for j, (k, _t, _w) in enumerate(MARKER_COLS, start=1):
            v = row.get(k)
            if v in (None, ""):
                continue
            if k == "created_at":
                try:
                    c = ws.cell(row=r, column=j,
                                value=datetime.fromisoformat(str(v).replace("T", " ")))
                    c.number_format = "DD.MM.YYYY HH:MM"
                    continue
                except ValueError:
                    pass
            elif k in ("lat", "lon") and isinstance(v, (int, float)):
                c = ws.cell(row=r, column=j, value=float(v))
                c.number_format = "0.000000"
                continue
            ws.cell(row=r, column=j, value=v if isinstance(v, (int, float)) else str(v))
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(MARKER_COLS))}{len(rows) + 1}"
    ws.freeze_panes = "A2"
    wb.save(path)


def export_request(employees: list[dict], selected: set[str],
                   since_text: str, until_text: str) -> dict:
    """Точный состав выгрузки: путь `/admin/export.json?mode=full…`, сотрудники
    и окно доказанных выездов. `employees` — объединённый список из БД
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
        else "без ограничения по дате выезда"
    )
    return (f"Строк в выгрузке: {fmt_n(rows_count)}\n\n"
            f"Сотрудники: {employee_text}\nПериод: {window}\n\n"
            "Сохранить новый xlsx?")
