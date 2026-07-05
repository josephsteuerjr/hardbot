"""
Разброс мастер-реестра по личным файлам сотрудников — параметризованный движок,
который вызывает ПУЛЬТ (Tauri). Замена/развитие `Скрипт СНГ/Разборка.py`:

  * корни задаются аргументом (диалог выбора папки в пульте), без хардкода U:\rbr;
  * `!Архив`, `_tech` и прочие служебные папки строго игнорируются на всех уровнях;
  * режим --dry-run: только JSON-отчёт «кто нашёлся / кто нет» (без Excel, без записи)
    — пульт показывает его ДО раскладки;
  * режим --apply: сама раскладка через живой Excel (xlwings/COM) — сохраняет формат
    .xlsb, лист «Статусы» (выпадающие списки) и «Статистику» с формулами (перенос из
    старого файла + починка ссылок), как в оригинале.

Тяжёлые импорты (pandas/calamine, xlwings) — ленивые: карту папок и отчёт можно
считать без Excel и без pandas (для превью в пульте и для тестов).

CLI:
  python razbros.py --dry-run --master "<master.xlsb>" --root "<...РТ>" --root "<...УЗБ>"
  python razbros.py --apply   --master "<master.xlsb>" --root "<...РТ>" --root "<...УЗБ>"
Вывод — JSON в stdout (отчёт), диагностика — в stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SHEET_NAME = "Портфели"
ALLOWED_SHEETS = {"Портфели", "Статусы", "Статистика"}
HEADER_ROW_NAMES = 2          # строка с названиями колонок (1-based)
HEADER_ROW_TYPES = 3          # строка с типами колонок: хард / сх / софт
DATA_START_ROW = 4
TARGET_COLUMN_NAME = "ответственный Сотрудник"

# Служебные папки, которые НИКОГДА не считаем ни городом, ни сотрудником.
# `!Архив` — уволенные/неактивные (в оригинале не исключался — источник скрытых багов).
SKIP_DIR_NAMES = {"_tech"}
def _is_skip_dir(name: str) -> bool:
    n = name.strip().lower()
    return n in SKIP_DIR_NAMES or n.startswith("!")   # !Архив, !что-угодно


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", str(name).strip())


def col_letter(col_idx: int) -> str:
    """Буквенное имя столбца Excel по 1-based индексу."""
    out = ""
    while col_idx > 0:
        col_idx, rem = divmod(col_idx - 1, 26)
        out = chr(65 + rem) + out
    return out


# --- Карта папок сотрудников (чистый Python, без Excel/pandas) --------------
def build_folder_map(roots: list[Path]) -> dict[str, list[Path]]:
    """{ФИО.strip().lower(): [папки]} по обходу root/<город|Soft>/<ФИО>, 2 уровня вниз.
    `!Архив`, `_tech` и прочие служебные — пропускаются на обоих уровнях. Имя может
    встретиться в нескольких местах (город + Soft + разные страны) — храним список."""
    folder_map: dict[str, list[Path]] = {}
    for root in roots:
        if not root.exists():
            log(f"[!] Корень недоступен, пропускаю: {root}")
            continue
        for sub in root.iterdir():                       # уровень 1: город / Soft / служебное
            if not sub.is_dir() or _is_skip_dir(sub.name):
                continue
            for emp in sub.iterdir():                     # уровень 2: папка сотрудника
                if not emp.is_dir() or _is_skip_dir(emp.name):
                    continue
                folder_map.setdefault(emp.name.strip().lower(), []).append(emp)
    return folder_map


def is_soft_folder(folder: Path) -> bool:
    """Сотрудник — «софт», если его папка лежит внутри папки с именем Soft."""
    return folder.parent.name.strip().lower() == "soft"


# --- Чтение мастер-реестра (ленивый pandas+calamine) ------------------------
def read_master(master: Path):
    """(employees, hard_cols, soft_cols, n_cols) из мастер-xlsb.
    hard = колонки типа 'хард'/'сх'; soft = 'софт'/'сх' (сх идут в оба)."""
    import pandas as pd  # ленивый импорт: превью/карта работают без pandas

    df = pd.read_excel(master, sheet_name=SHEET_NAME, header=None, engine="calamine")
    headers = [str(v).strip() if pd.notna(v) else "" for v in df.iloc[HEADER_ROW_NAMES - 1]]
    if TARGET_COLUMN_NAME not in headers:
        raise ValueError(f"В строке {HEADER_ROW_NAMES} нет колонки '{TARGET_COLUMN_NAME}'")
    tgt = headers.index(TARGET_COLUMN_NAME)
    types = [str(v).strip().lower() if pd.notna(v) else "" for v in df.iloc[HEADER_ROW_TYPES - 1]]
    hard_cols = [i + 1 for i, t in enumerate(types) if t in ("хард", "сх")]
    soft_cols = [i + 1 for i, t in enumerate(types) if "софт" in t or t == "сх"]
    employees = sorted({
        str(v).strip() for v in df.iloc[DATA_START_ROW - 1:, tgt]
        if str(v).strip() and str(v).strip().lower() not in ("nan", "none")
    })
    return employees, hard_cols, soft_cols, len(types)


# --- Отчёт-превью «кто нашёлся / кто нет» -----------------------------------
def make_report(folder_map: dict[str, list[Path]], employees: list[str]) -> dict:
    matched, unmatched = [], []
    for emp in employees:
        folders = folder_map.get(emp.strip().lower())
        if folders:
            matched.append({
                "employee": emp,
                "folders": [str(f) for f in folders],
                "kind": "soft" if is_soft_folder(folders[0]) else "hard",
            })
        else:
            unmatched.append(emp)                         # есть строки в мастере, но нет папки
    used = {emp.strip().lower() for emp in employees}
    extra = sorted({k for k in folder_map if k not in used})  # папка есть, в мастере нет строк
    return {
        "ok": True,
        "employees_total": len(employees),
        "matched": matched,
        "unmatched": unmatched,                           # спека: показывать в пульте, не молча
        "extra_folders": [folder_map[k][0].name for k in extra],
        "counts": {"matched": len(matched), "unmatched": len(unmatched), "extra": len(extra)},
    }


# --- Реальная раскладка через Excel (ленивый xlwings/COM) -------------------
def apply_scatter(master: Path, folder_map: dict[str, list[Path]],
                  employees, hard_cols, soft_cols, n_cols) -> dict:
    """Портирование Разборка.py: два мастер-шаблона (hard/soft) силами Excel →
    копии в папки сотрудников → фильтрация строк по 'ответственный Сотрудник' +
    перенос листа «Статистика» с формулами и починкой ссылок. Требует Excel."""
    import shutil
    import time
    import xlwings as xw

    all_cols = list(range(1, n_cols + 1))
    tmp_hard = master.parent / "~temp_hard.xlsb"
    tmp_soft = master.parent / "~temp_soft.xlsb"

    def build_template(dst: Path, keep_cols: list[int], app):
        shutil.copy2(master, dst)
        wb = app.books.open(str(dst))
        for sh in list(wb.sheets):
            if sh.name not in ALLOWED_SHEETS:
                sh.delete()
        ws = wb.sheets[SHEET_NAME]
        for c in sorted([c for c in all_cols if c not in keep_cols], reverse=True):
            ws.range(f"{col_letter(c)}:{col_letter(c)}").api.Delete()
        wb.save(); wb.close()

    written, skipped = [], []
    with xw.App(visible=False) as app:
        app.screen_updating = False
        app.calculation = "manual"
        app.display_alerts = False
        build_template(tmp_hard, hard_cols, app)
        build_template(tmp_soft, soft_cols, app)

        jobs = []
        for emp in employees:
            key = str(emp).strip().lower()
            if key not in folder_map:
                skipped.append(emp)                       # нет папки — в отчёт, не молча
                continue
            safe = sanitize_filename(emp)
            template = tmp_soft if is_soft_folder(folder_map[key][0]) else tmp_hard
            for folder in folder_map[key]:
                out = folder / f"{safe}.xlsb"
                old = None
                if out.exists():
                    old = folder / f"~old_{safe}.xlsb"
                    if old.exists():
                        os.remove(old)
                    os.rename(out, old)
                shutil.copy2(template, out)
                jobs.append((out, str(emp).strip(), old))

        for out, emp_name, old in jobs:
            _transfer_stats_and_filter(app, out, emp_name, old)
            written.append(str(out))
            time.sleep(0.05)

    for t in (tmp_hard, tmp_soft):
        if t.exists():
            os.remove(t)
    return {"ok": True, "written": written, "written_count": len(written), "skipped": skipped}


def _transfer_stats_and_filter(app, file_path: Path, emp_name: str, old_path):
    """Перенос «Статистики» из старого файла (формулы + починка ссылок) и фильтр строк
    портфеля по emp_name. Один-в-один с Разборка.py, но вынесено в функцию."""
    if old_path and Path(old_path).exists():
        try:
            wb_old = app.books.open(str(old_path))
            if "Статистика" in [s.name for s in wb_old.sheets]:
                wb_new = app.books.open(str(file_path))
                so, sn = wb_old.sheets["Статистика"], wb_new.sheets["Статистика"]
                rows = so.api.UsedRange.Rows.Count
                cols = so.api.UsedRange.Columns.Count
                if rows > 0 and cols > 0:
                    addr = f"A1:{col_letter(cols)}{rows}"
                    formulas = so.range(addr).api.FormulaLocal
                    sn.clear_contents()
                    sn.range(addr).api.FormulaLocal = formulas
                    name = Path(old_path).name
                    sn.api.Cells.Replace(What=f"[{name}]", Replacement="", LookAt=2, SearchOrder=1, MatchCase=False)
                    sn.api.Cells.Replace(What=f"{name}!", Replacement="", LookAt=2, SearchOrder=1, MatchCase=False)
                wb_new.save(); wb_new.close()
            wb_old.close()
        except Exception as e:  # noqa: BLE001
            log(f"[!] Статистика не перенесена для {emp_name}: {e}")
            try:
                wb_old.close()
            except Exception:
                pass
        finally:
            import time
            time.sleep(0.1)
            if Path(old_path).exists():
                try:
                    os.remove(old_path)
                except OSError as e:
                    log(f"[!] Не удалить временный {Path(old_path).name}: {e}")

    wb = app.books.open(str(file_path))
    ws = wb.sheets[SHEET_NAME]
    headers = ws.range(f"{HEADER_ROW_NAMES}:{HEADER_ROW_NAMES}").value
    if TARGET_COLUMN_NAME not in headers:
        wb.save(); wb.close(); return
    tgt = headers.index(TARGET_COLUMN_NAME) + 1
    last_row = ws.api.UsedRange.Rows.Count
    max_col = ws.api.UsedRange.Columns.Count
    if last_row >= DATA_START_ROW:
        max_letter = col_letter(max_col)
        vals = ws.range(f"A{DATA_START_ROW}:{max_letter}{last_row}").value
        if not isinstance(vals, list):
            vals = [[vals]]
        keep = [r for r in vals if len(r) >= tgt and str(r[tgt - 1]).strip() == emp_name]
        ws.range(f"A{DATA_START_ROW}:{max_letter}{last_row}").clear_contents()
        if keep:
            ws.range(f"A{DATA_START_ROW}").value = keep
            first_empty = DATA_START_ROW + len(keep)
            if last_row >= first_empty:
                ws.range(f"{first_empty}:{last_row}").api.Delete()
        else:
            ws.range(f"{DATA_START_ROW}:{last_row}").api.Delete()
    wb.save(); wb.close()


# --- CLI --------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Разброс мастер-реестра по сотрудникам")
    ap.add_argument("--root", action="append", default=[], dest="roots",
                    help="корневая папка страны (можно несколько)")
    ap.add_argument("--master", help="путь к мастер-реестру .xlsb")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только отчёт-превью, без записи")
    mode.add_argument("--apply", action="store_true", help="раскладка через Excel")
    args = ap.parse_args(argv)

    roots = [Path(r) for r in args.roots]
    if not roots:
        print(json.dumps({"ok": False, "error": "не заданы --root"}, ensure_ascii=False))
        return 2
    folder_map = build_folder_map(roots)

    if not args.master:                                   # без мастера — только карта папок
        report = {"ok": True, "folders_indexed": sum(len(v) for v in folder_map.values()),
                  "employees_with_folder": sorted(folder_map.keys())}
        print(json.dumps(report, ensure_ascii=False))
        return 0

    master = Path(args.master)
    if not master.exists():
        print(json.dumps({"ok": False, "error": f"мастер не найден: {master}"}, ensure_ascii=False))
        return 2
    employees, hard_cols, soft_cols, n_cols = read_master(master)

    if args.apply:
        result = apply_scatter(master, folder_map, employees, hard_cols, soft_cols, n_cols)
    else:
        result = make_report(folder_map, employees)       # dry-run по умолчанию
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
