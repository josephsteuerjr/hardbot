"""Замер Excel: книга с шары против локальной копии. Шару ТОЛЬКО читаем.

    python bench/excel_bench.py --books 4 --cells 400

По каждой книге:
  share_open_ro — открыть с шары только на чтение + прочитать лист + закрыть;
  local_open    — открыть локальную копию на запись + прочитать лист;
  write_cell    — записать N ячеек по одной (как сейчас _enrich_workbook);
  write_batch   — те же N ячеек столбцами-отрезками одним вызовом на отрезок;
  save_local    — сохранить локальную копию.
Записи идут только в локальные копии во временной папке.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT.parent / "Бот для выездников"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", type=int, default=4)
    ap.add_argument("--cells", type=int, default=400)
    a = ap.parse_args()
    sys.path[:0] = [str(ROOT / "pult"), str(ROOT)]
    from pult.core import bootstrap
    bootstrap.ensure_paths()
    from pult.core import enrichment as en
    import enrich
    import xlwings as xw

    settings = json.load(open(MAIN / "pult" / "настройки пульта.json", encoding="utf-8"))
    fmap = en.folder_map(settings["scatterBase"])
    books = []
    for entries in fmap.values():
        books += en.personal_files(entries, entries[0]["path"].name)
    random.seed(3)
    books = random.sample(books, min(a.books, len(books)))
    tmp = Path(tempfile.mkdtemp(prefix="hard-xl-"))
    out = []
    enrich._co_init()
    app = enrich.open_excel(xw)
    app.display_alerts = False
    app.screen_updating = False
    try:
        for f in books:
            r = {"name": f.name}
            t = time.perf_counter()
            wb = app.books.open(str(f), update_links=False, read_only=True)
            ws = wb.sheets[enrich.SHEET_NAME] if enrich.SHEET_NAME in [s.name for s in wb.sheets] else wb.sheets[0]
            grid = ws.used_range.value
            wb.close()
            r["share_open_ro_s"] = round(time.perf_counter() - t, 3)
            loc = tmp / f.name
            t = time.perf_counter(); shutil.copy2(f, loc); r["copy_s"] = round(time.perf_counter() - t, 3)
            t = time.perf_counter()
            wb = app.books.open(str(loc))
            ws = wb.sheets[enrich.SHEET_NAME] if enrich.SHEET_NAME in [s.name for s in wb.sheets] else wb.sheets[0]
            last = ws.used_range.last_cell
            grid = ws.range((1, 1), (last.row, last.column)).value
            r["local_open_s"] = round(time.perf_counter() - t, 3)
            rows, cols = last.row, last.column
            # N ячеек в «хвостовых» колонках, как у обогащения: по строкам подряд.
            cells = [(4 + i % max(1, rows - 4), cols + 1 + (i // max(1, rows - 4)))
                     for i in range(a.cells)]
            t = time.perf_counter()
            for rr, cc in cells:
                ws.range((rr, cc)).value = f"тест {rr}"
            r["write_cell_s"] = round(time.perf_counter() - t, 3)
            # Пакетно: по колонке — непрерывные отрезки строк одним вызовом.
            runs: dict[int, list[int]] = {}
            for rr, cc in cells:
                runs.setdefault(cc + 3, []).append(rr)
            t = time.perf_counter()
            calls = 0
            for cc, rs in runs.items():
                rs.sort(); start = prev = rs[0]
                for x in rs[1:] + [None]:
                    if x is not None and x == prev + 1:
                        prev = x; continue
                    ws.range((start, cc), (prev, cc)).value = [[f"тест {k}"] for k in range(start, prev + 1)]
                    calls += 1
                    if x is not None:
                        start = prev = x
            r["write_batch_s"] = round(time.perf_counter() - t, 3)
            r["batch_calls"] = calls
            t = time.perf_counter(); wb.save(); r["save_local_s"] = round(time.perf_counter() - t, 3)
            t = time.perf_counter(); wb.close(); r["close_s"] = round(time.perf_counter() - t, 3)
            t = time.perf_counter()
            wb = app.books.open(str(loc), update_links=False, read_only=True); wb.close()
            r["reopen_local_ro_s"] = round(time.perf_counter() - t, 3)
            out.append(r)
            print(json.dumps(r, ensure_ascii=False), flush=True)
    finally:
        try:
            app.quit()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
