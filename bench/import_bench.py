"""Замер импорта реестра на синтетике: где уходит время и сколько байт летит.

    python bench/import_bench.py --rows 50000

Ничего не трогает, кроме временной папки: своя база, свой синтетический
реестр (ФИО и номера выдуманы). Этапы:
  1. чтение книги calamine (как пульт);
  2. тело запроса: JSON как шлёт пульт (ensure_ascii) и то же в gzip;
  3. сервер: import_grid — первая загрузка, повтор того же файла, повтор с
     изменением 1 % строк (как ежедневное обновление реестра).
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=50000)
    ap.add_argument("--employees", type=int, default=30)
    ap.add_argument("--change", type=float, default=0.01)
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="hard-bench-"))
    os.environ["APP_DATA"] = str(tmp)
    os.environ.setdefault("BOT_TOKEN", "bench")
    sys.path[:0] = [str(ROOT / "server"), str(ROOT / "pult"), str(ROOT)]
    import db
    import synth
    from pult.core.workbook import read_workbook_sheets

    db.init_db()
    random.seed(7)
    synth.EMPLOYEES[:] = [f"Сотрудник {i:02d} Бенч" for i in range(a.employees)]
    res: dict = {"rows": a.rows}
    t = time.perf_counter()
    path = synth.generate(n=a.rows, path=str(tmp / "registry.xlsx"))
    res["synth_write_s"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    sheets = read_workbook_sheets(path)
    res["calamine_s"] = round(time.perf_counter() - t, 2)
    body = {"sheets": sheets}
    t = time.perf_counter()
    raw = json.dumps(body).encode()                 # как pult.core.api.request
    res["json_s"] = round(time.perf_counter() - t, 2)
    res["json_mb"] = round(len(raw) / 1e6, 1)
    t = time.perf_counter()
    gz = gzip.compress(raw, 6)
    res["gzip_s"] = round(time.perf_counter() - t, 2)
    res["gzip_mb"] = round(len(gz) / 1e6, 1)

    grid = db.pick_grid(sheets)

    def run(label, g, **kw):
        t = time.perf_counter()
        rep = db.import_grid(g, source_kind=db.IMPORT_SOURCE_TEST_SUBSET, **kw)
        res[label + "_s"] = round(time.perf_counter() - t, 2)
        res[label] = {k: rep.get(k) for k in ("added", "updated", "unchanged", "error", "blocked")
                      if k in rep}
        print(label, res[label + "_s"], res[label], flush=True)

    run("preview_first", grid, preview_only=True)
    run("import_first", grid)
    run("preview_same", grid, preview_only=True)
    run("import_same", grid)
    # Ежедневное обновление: у доли строк меняется сумма долга/статус.
    head = next(i for i, r in enumerate(grid) if any(isinstance(c, str) and "Номер" in c for c in r if c))
    g2 = [list(r) for r in grid]
    data_rows = list(range(head + 1, len(g2)))
    changed = random.sample(data_rows, max(1, int(len(data_rows) * a.change)))
    import re
    numlike = lambda c: isinstance(c, (int, float)) or (isinstance(c, str) and re.fullmatch(r"\d+([.,]\d+)?", c.strip() or "x"))
    width = max(len(r) for r in g2)
    num_cols = [j for j in range(width)
                if sum(1 for i in data_rows[:200] if j < len(g2[i]) and g2[i][j] not in (None, "") and numlike(g2[i][j])) > 150]
    for i in changed:
        j = random.choice(num_cols)
        v = g2[i][j]
        if v in (None, ""):
            continue
        g2[i][j] = (v + 1) if isinstance(v, (int, float)) else str(int(float(str(v).replace(",", "."))) + 1)
    res["changed_rows"] = len(changed)
    run("preview_1pct", g2, preview_only=True)
    run("import_1pct", g2)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
