"""Чтение книги Excel → грид листов (зеркало серверного parse_workbook).

Парсится локально (python-calamine: .xlsx/.xlsm/.xlsb/.xls), на сервер летит JSON.
"""

from __future__ import annotations

from datetime import date, datetime


def cell_json(v):
    """Значение calamine → JSON-значение как у серверного cell_to_json: пустые →
    None, целые float → int, даты → ISO (полночь режем до даты)."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, str):
        t = v.strip()
        return t or None
    if isinstance(v, float):
        return int(v) if v.is_integer() and abs(v) < 9e15 else v
    if isinstance(v, datetime):
        s = v.isoformat(sep=" ", timespec="seconds")
        return s[:-9] if s.endswith(" 00:00:00") else s
    if isinstance(v, date):
        return v.isoformat()
    return v if isinstance(v, (int, float, bool)) else str(v)


_cell_json = cell_json


def read_workbook_sheets(path: str) -> list[dict]:
    """Файл реестра → [{name, grid}] для POST /admin/import.json."""
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_path(path)
    sheets = []
    for name in wb.sheet_names:
        rows = wb.get_sheet_by_name(name).to_python()
        grid = [[cell_json(c) for c in row] for row in rows]
        if grid:
            sheets.append({"name": name, "grid": grid})
    if not sheets:
        raise ValueError("в файле нет читаемых листов")
    return sheets
