"""
Обогащение реестра результатами выездов — движок пульта.

Проблема, которую решает: старый экспорт СОБИРАЛ ФАЙЛ ЗАНОВО из БД сервера —
то есть подменял живой реестр слепком последнего импорта (плюс терял чужие
листы, формулы и колонки, которых нет в схеме). Здесь наоборот: берём ЖИВОЙ
файл реестра и точечно дописываем в него ТОЛЬКО то, что заполнили выездники
через бота — только их кейсы, только заполненные столбцы визита. Всё
остальное (строки, столбцы, листы, форматирование, выпадающие списки) не
трогаем вообще: файл открывает и сохраняет живой Excel (xlwings/COM), а пишем
мы отдельными ячейками по точному плану.

Планировка отделена от записи и не требует Excel:
    build_plan(grid, fields, rows) -> план (какие ячейки чем заполнить)
    enrich_file(path, fields, rows) -> бэкап + запись плана через xlwings

Какие столбцы считаются «заполняет бот» — VISIT_KEYS, зеркало
db.VISIT_FILL_COLUMNS (колонки формы визита миниаппа) ∩ схема экспорта.
Кейс попадает в план, только если у него в БД непусто хотя бы одно из этих
полей; ячейка пишется, только если значение непустое и отличается от того,
что уже стоит в файле.
"""

from __future__ import annotations

import re
import shutil
from datetime import date, datetime
from pathlib import Path

SHEET_NAME = "Портфели"

# Поля формы визита (зеркало hardbot/db.VISIT_FILL_COLUMNS без служебных
# tips/reminder_date, которых нет в схеме экспорта). Порядок — как в реестре.
VISIT_KEYS = [
    "visit_date", "visit_comment", "overall_result", "meeting_date",
    "contact_with", "agreed_payment", "who_at_address", "client_location",
    "purpose_rf", "call_status", "call_comment",
]

CONTRACT_KEY = "contract_id"

# --- Нормализация заголовков (зеркало db._norm_title — держать в синхроне!) --
_ZERO_WIDTH = ("​", "‌", "‍", "﻿")
_HOMOGLYPHS = str.maketrans("acekmoptxy", "асекмортху")


def norm_title(s) -> str:
    """Регистр, юникод-пробелы, невидимые символы, латиница-двойники («Cтатус
    звонка» с латинской C), хвостовая пунктуация — как в db._norm_title."""
    if s in (None, ""):
        return ""
    t = str(s)
    for zw in _ZERO_WIDTH:
        t = t.replace(zw, "")
    t = re.sub(r"\s+", " ", t.strip().lower()).translate(_HOMOGLYPHS)
    return t.rstrip(":;*.")


def norm_cid(v) -> str:
    """Номер КД для матчинга: 12345.0 (float из Excel) → «12345», пробелы прочь."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def parse_date(v):
    """ISO / дд.мм.гггг / дд.мм.гг / datetime → date, иначе None."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    core = s.split(" ")[0].split("T")[0]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(core, fmt).date()
        except ValueError:
            continue
    return None


def cell_text(v) -> str:
    """Значение ячейки → сравнимый текст: даты в ISO (полночь режем), целые
    float без «.0», строки со схлопнутыми краями. None/'' → ''."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _values_equal(file_val, new_val, is_date: bool) -> bool:
    if is_date:
        fd, nd = parse_date(file_val), parse_date(new_val)
        if fd or nd:
            return fd == nd
    return cell_text(file_val) == cell_text(new_val)


def find_header(grid: list[list], fields: list[dict]) -> tuple[int, dict]:
    """(индекс строки заголовков, {key: индекс колонки}) — как в db.import_grid:
    лучшая из первых 10 строк, обязательное условие — колонка-ключ «Номер КД / ID»."""
    title_to_key = {norm_title(f["title"]): f["key"] for f in fields}
    best_h, best_map, best_n, best_has_id = -1, {}, -1, False
    for i, row in enumerate(grid[:10]):
        col_map = {}
        for j, cell in enumerate(row):
            key = title_to_key.get(norm_title(cell))
            if key and key not in col_map:
                col_map[key] = j
        has_id = CONTRACT_KEY in col_map
        if (has_id, len(col_map)) > (best_has_id, best_n):
            best_has_id, best_n, best_h, best_map = has_id, len(col_map), i, col_map
    if not best_has_id:
        raise ValueError("в листе нет колонки-ключа «Номер КД / ID» (первые 10 строк)")
    return best_h, best_map


def _is_source_row(row: list) -> bool:
    """Строка-пометка «сх/хард» под заголовками — не данные."""
    nonempty = [str(v).strip().lower() for v in row if v not in (None, "")]
    return bool(nonempty) and all(v in ("сх", "хард") for v in nonempty)


def build_plan(grid: list[list], fields: list[dict], rows: list[dict]) -> dict:
    """План обогащения. grid — лист файла как список строк (значения как есть),
    fields — схема сервера [{key,title,date,...}], rows — строки клиентов сервера.

    Возвращает {sheet_writes: [(r, c, value, is_date)], ...статистика}. Индексы
    0-based относительно grid. Пишутся ТОЛЬКО ячейки визита заполненных кейсов,
    и только когда значение отличается от файла."""
    header_row, col_map = find_header(grid, fields)
    is_date_key = {f["key"]: bool(f.get("date")) for f in fields}

    data_start = header_row + 1
    if data_start < len(grid) and _is_source_row(grid[data_start]):
        data_start += 1

    cid_col = col_map[CONTRACT_KEY]
    file_rows: dict[str, list[int]] = {}
    for r in range(data_start, len(grid)):
        row = grid[r]
        cid = norm_cid(row[cid_col] if cid_col < len(row) else None)
        if cid:
            file_rows.setdefault(cid, []).append(r)

    visit_cols_absent = sorted(k for k in VISIT_KEYS if k not in col_map)

    writes: list[tuple[int, int, object, bool]] = []
    filled_cases = matched_cases = changed_cases = 0
    cells_already = cells_over_nonempty = 0
    missing: list[str] = []

    for srv in rows:
        cid = norm_cid(srv.get(CONTRACT_KEY))
        values = {k: srv.get(k) for k in VISIT_KEYS
                  if cell_text(srv.get(k)) != "" and k in col_map}
        # «заполнен ботом» = есть хоть одно поле визита (даже если колонки нет в файле)
        if not cid or not any(cell_text(srv.get(k)) != "" for k in VISIT_KEYS):
            continue
        filled_cases += 1
        targets = file_rows.get(cid)
        if not targets:
            missing.append(cid)
            continue
        matched_cases += 1
        case_changed = False
        for key, val in values.items():
            c = col_map[key]
            is_date = is_date_key.get(key, False)
            out_val = parse_date(val) if is_date and parse_date(val) else cell_text(val)
            for r in targets:
                cur = grid[r][c] if c < len(grid[r]) else None
                if _values_equal(cur, val, is_date):
                    cells_already += 1
                    continue
                if cell_text(cur) != "":
                    cells_over_nonempty += 1
                writes.append((r, c, out_val, is_date))
                case_changed = True
        if case_changed:
            changed_cases += 1

    return {
        "header_row": header_row,
        "data_start": data_start,
        "writes": writes,
        "filled_cases": filled_cases,      # кейсов с данными бота в БД
        "matched_cases": matched_cases,    # из них нашлись в файле
        "changed_cases": changed_cases,    # из них реально было что дописать
        "cells_written": len(writes),
        "cells_already": cells_already,    # уже совпадало — не трогали
        "cells_over_nonempty": cells_over_nonempty,  # обновили поверх старого значения
        "missing": missing,                # заполнены ботом, но строки в файле нет
        "visit_cols_absent": visit_cols_absent,      # колонок визита нет в файле
    }


# --- Работа с живым файлом (xlwings/COM) -------------------------------------
def clear_gen_py():
    """Снести кэш COM-обёрток pywin32 (gen_py): он умеет протухать
    («…has no attribute 'CLSIDToPackageMap'») и валит любой запуск Excel."""
    import sys
    try:
        import win32com
        path = Path(win32com.__gen_path__)
    except Exception:  # noqa: BLE001
        import tempfile
        path = Path(tempfile.gettempdir()) / "gen_py" / \
            f"{sys.version_info.major}.{sys.version_info.minor}"
    shutil.rmtree(path, ignore_errors=True)
    for m in [m for m in sys.modules if "gen_py" in m]:
        sys.modules.pop(m, None)


def open_excel(xw, log=lambda s: None):
    """xw.App(visible=False) с самолечением битого кэша gen_py."""
    try:
        return xw.App(visible=False)
    except AttributeError as e:
        if "gen_py" not in repr(e) and "CLSIDToPackageMap" not in repr(e):
            raise
        log("Чиню кэш COM-обёрток Excel…")
        clear_gen_py()
        return xw.App(visible=False)


def _pick_sheet(wb, fields: list[dict]):
    """Лист «Портфели», иначе — где больше узнаваемых заголовков в первых 10 строках."""
    names = [s.name for s in wb.sheets]
    if SHEET_NAME in names:
        return wb.sheets[SHEET_NAME]
    title_keys = {norm_title(f["title"]) for f in fields}
    best, best_n = wb.sheets[0], -1
    for s in wb.sheets:
        head = s.range((1, 1), (10, 60)).value or []
        n = 0
        for row in head:
            row = row if isinstance(row, list) else [row]
            n = max(n, sum(1 for c in row if norm_title(c) in title_keys))
        if n > best_n:
            best_n, best = n, s
    return best


def backup_copy(path: Path) -> Path:
    """Копия рядом с файлом: «Имя (до обогащения 13.07 14-05).xlsb». Откат = переименовать."""
    stamp = datetime.now().strftime("%d.%m %H-%M")
    dst = path.with_name(f"{path.stem} (до обогащения {stamp}){path.suffix}")
    n = 2
    while dst.exists():
        dst = path.with_name(f"{path.stem} (до обогащения {stamp}, {n}){path.suffix}")
        n += 1
    shutil.copy2(path, dst)
    return dst


def enrich_file(path: str | Path, fields: list[dict], rows: list[dict],
                log=lambda s: None) -> dict:
    """Обогатить живой файл реестра: бэкап → читаем лист → план → точечная запись →
    сохранить тем же форматом. Возвращает план + backup/sheet. Требует Excel."""
    try:
        # пульт зовёт нас из фонового потока — COM в нём надо инициализировать явно
        import pythoncom
        pythoncom.CoInitialize()
    except ImportError:
        pass
    import xlwings as xw

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"файл не найден: {path}")

    log("Делаю резервную копию…")
    backup = backup_copy(path)

    log("Открываю файл в Excel…")
    with open_excel(xw, log) as app:
        app.display_alerts = False
        app.screen_updating = False
        wb = app.books.open(str(path))
        try:
            ws = _pick_sheet(wb, fields)
            log(f"Читаю лист «{ws.name}»…")
            # читаем строго от A1: used_range может начинаться не с A1,
            # а индексы плана — координаты ячеек листа
            last = ws.used_range.last_cell
            grid = ws.range((1, 1), (last.row, last.column)).value or []
            if grid and not isinstance(grid[0], list):   # один ряд/одна ячейка
                grid = [grid if isinstance(grid, list) else [grid]]

            plan = build_plan(grid, fields, rows)
            writes = plan["writes"]
            log(f"Пишу {len(writes)} ячеек…")
            for i, (r, c, val, _is_date) in enumerate(writes, 1):
                ws.range((r + 1, c + 1)).value = val
                if i % 200 == 0:
                    log(f"Пишу {i} / {len(writes)}…")
            if writes:
                wb.save()
            plan["backup"] = str(backup)
            plan["sheet"] = ws.name
            plan["file"] = str(path)
            return plan
        finally:
            wb.close()
