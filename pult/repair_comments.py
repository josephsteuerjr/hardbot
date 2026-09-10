# -*- coding: utf-8 -*-
"""Починка личных реестров после бага «комментарий выезда — в колонке звонка».

До 18.07.2026 миниапп писал комментарий выездника в «комментарий звонка»
(call_comment), и обогащение разносило его в эту же колонку личных ФИО.xlsb —
затирая настоящие колл-комментарии. На сервере данные уже перенесены в
«Комментарий текущего месяца выезда» (visit_comment); этот скрипт чинит ФАЙЛЫ:

  * «Комментарий текущего месяца выезда» ← комментарий выездника с сервера;
  * если в «комментарий звонка» лежит РОВНО этот бот-текст — ячейка чистится,
    а оригинал восстанавливается из САМОЙ РАННЕЙ копии «Имя (до обогащения …).xlsb»
    (enrich делал её перед каждой записью — там колонка звонка до затирания);
  * настоящие колл-комментарии (текст не совпадает с серверным) не трогаются.

Запуск на компе с реестрами (окружение то же, что у пульта: python-calamine + Excel):
  python repair_comments.py                      # база из «настройки пульта.json», СУХОЙ прогон
  python repair_comments.py --go                 # применить (перед записью — копия «(до починки …)»)
  python repair_comments.py --base "D:\\Реестры" --go
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import enrich
import razbros

APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "настройки пульта.json"
# Пусто: адрес берётся из настроек пульта, как и папка с республиками.
DEFAULT_URL = ""
DEFAULT_KEY = ""

try:  # русские буквы в консоли Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def api_export(url: str, key: str) -> dict:
    req = urllib.request.Request(url.rstrip("/") + "/admin/export.json",
                                 headers={"X-Admin-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            ctx = ssl._create_unverified_context()  # noqa: S323 — как в пульте
            with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
                return json.load(r)
        raise


def pick_grid_calamine(path: Path, fields: list[dict]) -> list[list] | None:
    """Лист файла с узнаваемой шапкой (как enrich._pick_sheet, но без Excel)."""
    from python_calamine import CalamineWorkbook
    wb = CalamineWorkbook.from_path(str(path))
    names = wb.sheet_names
    order = ([enrich.SHEET_NAME] if enrich.SHEET_NAME in names else []) + \
        [n for n in names if n != enrich.SHEET_NAME]
    for name in order:
        grid = wb.get_sheet_by_name(name).to_python()
        try:
            enrich.find_header(grid, fields)
            return grid
        except ValueError:
            continue
    return None


def backup_folders(path: Path, roots=None):
    """Где искать копии книги: рядом с ней и в корнях для копий.

    Сорить в папках сотрудников запрещено, поэтому обогащение умеет складывать
    копии в отдельный корень по маске `<корень>/<город>/<ФИО>/`. Инструменты
    восстановления обязаны знать про оба места: копия, унесённая из папки
    сотрудника, раньше означала «доказательное восстановление невозможно».
    """
    yield path.parent
    for root in (roots or []):
        if not root:
            continue
        yield Path(root) / path.parent.parent.name / path.parent.name


def oldest_backup(path: Path, roots=None) -> Path | None:
    """Самая ранняя копия «(до обогащения …)» — в ней колонка звонка ДО затирания."""
    pref = path.stem + " (до обогащения"
    cands = []
    for folder in backup_folders(path, roots):
        try:
            cands += [p for p in folder.glob("*.xlsb") if p.stem.startswith(pref)]
        except OSError:
            continue
    return min(cands, key=lambda p: p.stat().st_mtime) if cands else None


def backup_call_map(bpath: Path, fields: list[dict]) -> dict[str, str]:
    """{КД: текст «комментария звонка»} из бэкапа. Пустой словарь — бэкап нечитаем."""
    grid = pick_grid_calamine(bpath, fields)
    if not grid:
        return {}
    h, cm = enrich.find_header(grid, fields)
    if "call_comment" not in cm:
        return {}
    start = h + 1
    if start < len(grid) and enrich._is_source_row(grid[start]):
        start += 1
    out: dict[str, str] = {}
    for r in range(start, len(grid)):
        row = grid[r]
        cid = enrich.norm_cid(row[cm["contract_id"]] if cm["contract_id"] < len(row) else None)
        if cid and cid not in out:
            v = row[cm["call_comment"]] if cm["call_comment"] < len(row) else None
            out[cid] = enrich.cell_text(v)
    return out


def build_repair_plan(grid: list[list], fields: list[dict], srv_by_cid: dict[str, dict],
                      backup_call: dict[str, str] | None = None) -> dict:
    """План починки одного листа. Чистая функция (тестируется без Excel).

    writes: [(строка, колонка, значение|None)] 0-based; None — очистить ячейку."""
    header_row, col_map = enrich.find_header(grid, fields)
    if "call_comment" not in col_map:
        raise ValueError("в листе нет колонки «комментарий звонка»")
    vis_col = col_map.get("visit_comment")   # может отсутствовать в старом шаблоне
    call_col, cid_col = col_map["call_comment"], col_map[enrich.CONTRACT_KEY]
    start = header_row + 1
    if start < len(grid) and enrich._is_source_row(grid[start]):
        start += 1

    writes: list[tuple[int, int, object]] = []
    st = {"rows_matched": 0, "visit_filled": 0, "poison_cleared": 0,
          "restored_from_backup": 0, "genuine_kept": 0, "visit_conflicts": [],
          "visit_col_missing": vis_col is None}
    for r in range(start, len(grid)):
        row = grid[r]
        cid = enrich.norm_cid(row[cid_col] if cid_col < len(row) else None)
        srv = srv_by_cid.get(cid) if cid else None
        if not srv:
            continue
        vc = enrich.cell_text(srv.get("visit_comment"))
        if not vc:
            continue
        st["rows_matched"] += 1
        cur_call = enrich.cell_text(row[call_col] if call_col < len(row) else None)
        if vis_col is not None:
            cur_vis = enrich.cell_text(row[vis_col] if vis_col < len(row) else None)
            if not cur_vis:
                writes.append((r, vis_col, vc))
                st["visit_filled"] += 1
            elif cur_vis != vc:
                # в файле НЕ пусто и НЕ серверный текст — возможно, писали руками
                # после прошлых обогащений. Не затираем: конфликт на глаза владельцу.
                st["visit_conflicts"].append((cid, cur_vis[:60]))
        if cur_call == vc:                      # яд: бот-текст в колонке звонка
            orig = (backup_call or {}).get(cid, "")
            if orig == vc:                      # бэкап сделан уже после затирания
                orig = ""
            writes.append((r, call_col, orig or None))
            st["poison_cleared"] += 1
            if orig:
                st["restored_from_backup"] += 1
        elif cur_call:
            st["genuine_kept"] += 1             # настоящий колл-комментарий — не трогаем
    st["writes"] = writes
    return st


def backup_copy_repair(path: Path) -> Path:
    stamp = datetime.now().strftime("%d.%m %H-%M")
    dst = path.with_name(f"{path.stem} (до починки {stamp}){path.suffix}")
    n = 2
    while dst.exists():
        dst = path.with_name(f"{path.stem} (до починки {stamp}, {n}){path.suffix}")
        n += 1
    import shutil
    shutil.copy2(path, dst)
    return dst


def personal_files(entries: list[dict], emp: str) -> list[Path]:
    """Личные .xlsb сотрудника (копия pult._personal_files: скрипт живёт без Tk)."""
    files: list[Path] = []
    for e in entries:
        folder = e["path"]
        cands = [folder / f"{razbros.sanitize_filename(emp)}.xlsb",
                 folder / f"{folder.name}.xlsb"]
        found = next((c for c in cands if c.exists()), None)
        if not found:
            # Страховочные копии «(до …)» сортируются раньше живого файла по алфавиту,
            # и без этого фильтра починка правила бы старую копию вместо реестра.
            xs = [p for p in sorted(folder.glob("*.xlsb"))
                  if not p.name.startswith("~") and not razbros.is_backup_copy(p.name)]
            found = xs[0] if xs else None
        if found and found not in files:
            files.append(found)
    return files


def collect_jobs(base: str, fields: list[dict], rows: list[dict]) -> tuple[list[dict], list[str]]:
    """[{employee, path, srv_by_cid}] по сотрудникам, у которых есть комментарии бота."""
    by_emp = enrich.split_by_employee(rows)
    fmap = razbros.build_folder_map(razbros.resolve_roots(base, razbros.load_config(None), []))
    jobs, warn = [], []
    for key, e in sorted(by_emp.items(), key=lambda kv: kv[1]["employee"].lower()):
        srv_by_cid = {enrich.norm_cid(r.get(enrich.CONTRACT_KEY)): r for r in e["rows"]
                      if enrich.cell_text(r.get("visit_comment")) != ""}
        if not srv_by_cid:
            continue
        entries = fmap.get(key)
        if not entries:
            warn.append(f"нет папки: {e['employee']} (комментариев: {len(srv_by_cid)})")
            continue
        files = personal_files(entries, e["employee"])
        if not files:
            warn.append(f"папка есть, но нет .xlsb: {e['employee']}")
            continue
        for p in files:
            jobs.append({"employee": e["employee"], "path": p, "srv_by_cid": srv_by_cid})
    return jobs, warn


def _report(job: dict, st: dict) -> None:
    print(f"  {job['employee']} — {Path(job['path']).name}: "
          f"строк с комментарием бота: {st['rows_matched']}, "
          f"дописать в «Комментарий выезда» (только пустые): {st['visit_filled']}, "
          f"очистить яд в «звонке»: {st['poison_cleared']} "
          f"(из бэкапа вернётся: {st['restored_from_backup']}), "
          f"настоящих колл-комментариев не тронуто: {st['genuine_kept']}"
          + ("  [!] НЕТ колонки «Комментарий текущего месяца выезда»"
             if st["visit_col_missing"] else ""))
    for cid, txt in st["visit_conflicts"]:
        print(f"    [конфликт, не тронуто] КД {cid}: в файле уже написано: {txt!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Починка колонок комментариев в личных реестрах")
    ap.add_argument("--base", help="папка с республиками (по умолчанию — из настроек пульта)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--key", default=DEFAULT_KEY)
    ap.add_argument("--go", action="store_true", help="применить (без флага — сухой прогон)")
    a = ap.parse_args(argv)

    base = a.base
    url = a.url
    if (not base or not url) and SETTINGS_PATH.exists():
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        base = base or saved.get("scatterBase", "")
        url = url or saved.get("url", "")
    if not url:
        print("не задан адрес сервера: --url или поле «Сервер» в настройках пульта")
        return 2
    if not base:
        print("Не задана папка с республиками: --base \"...\" (или выбери её один раз в пульте)")
        return 2

    print(f"Сервер: {url}\nПапка: {base}")
    d = api_export(url, a.key)
    fields, rows = d["fields"], d["rows"]
    jobs, warn = collect_jobs(base, fields, rows)
    for w in warn:
        print("[!]", w)
    if not jobs:
        print("Файлов для починки не найдено.")
        return 0

    print(f"Файлов к осмотру: {len(jobs)}{'' if a.go else '  (СУХОЙ ПРОГОН — без записи)'}")
    if not a.go:
        for job in jobs:
            grid = pick_grid_calamine(Path(job["path"]), fields)
            if grid is None:
                print(f"  [!] {job['path']}: не нашёл лист с шапкой реестра")
                continue
            b = oldest_backup(Path(job["path"]))
            bmap = backup_call_map(b, fields) if b else {}
            st = build_repair_plan(grid, fields, job["srv_by_cid"], bmap)
            _report(job, st)
        print("\nЭто был сухой прогон. Применить: python repair_comments.py --go")
        return 0

    enrich._co_init()
    import xlwings as xw
    changed = 0
    with enrich.open_excel(xw, lambda s: print(" ", s)) as app:
        app.display_alerts = False
        app.screen_updating = False
        for i, job in enumerate(jobs, 1):
            path = Path(job["path"])
            print(f"[{i}/{len(jobs)}] {job['employee']} — {path.name}")
            b = oldest_backup(path)
            bmap = backup_call_map(b, fields) if b else {}
            try:
                wb = app.books.open(str(path))
                try:
                    ws = enrich._pick_sheet(wb, fields)
                    last = ws.used_range.last_cell
                    grid = ws.range((1, 1), (last.row, last.column)).value or []
                    if grid and not isinstance(grid[0], list):
                        grid = [grid if isinstance(grid, list) else [grid]]
                    st = build_repair_plan(grid, fields, job["srv_by_cid"], bmap)
                    _report(job, st)
                    if st["writes"]:
                        bk = backup_copy_repair(path)
                        print(f"    копия: {bk.name}")
                        for r, c, val in st["writes"]:
                            ws.range((r + 1, c + 1)).value = val
                        wb.save()
                        changed += 1
                finally:
                    wb.close()
            except Exception as e:  # noqa: BLE001 — файл занят/битый: дальше по списку
                print(f"    [!] ошибка: {e}")
    print(f"\nГотово. Изменено файлов: {changed} из {len(jobs)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
