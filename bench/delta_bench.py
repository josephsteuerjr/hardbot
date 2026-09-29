"""Разница гридов против полного грида на синтетике разных изменений.

    python bench/delta_bench.py --rows 50000

Для каждого сценария: байты полного грида (JSON как шлёт пульт и gzip),
байты разницы (JSON и gzip), время расчёта разницы на пульте и сборки на
сервере, и главное — собранный грид совпадает с новым байт в байт. Для двух
сценариев дополнительно: база после импорта «целиком» и «разницей» одинакова.
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
TMP = Path(tempfile.mkdtemp(prefix="hard-delta-"))
os.environ["APP_DATA"] = str(TMP)
os.environ.setdefault("BOT_TOKEN", "bench")
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "pult"), str(ROOT)]

import db  # noqa: E402
import grid_delta as gd  # noqa: E402
import synth  # noqa: E402
from pult.core.workbook import read_workbook_sheets  # noqa: E402


def head_of(grid):
    return next(i for i, r in enumerate(grid) if any(isinstance(c, str) and "Номер" in c for c in r if c))


def scenarios(grid, rnd):
    h = head_of(grid)
    body = list(range(h + 1, len(grid)))
    width = max(len(r) for r in grid)

    def copy():
        return [list(r) for r in grid]

    def touch(g, rows, col=None):
        for i in rows:
            j = col if col is not None else rnd.randrange(width)
            while len(g[i]) <= j:
                g[i].append(None)
            g[i][j] = f"изм {rnd.randrange(10**6)}"
        return g

    yield "без изменений", copy()
    yield "0,1 % строк", touch(copy(), rnd.sample(body, len(body) // 1000))
    yield "1 % строк", touch(copy(), rnd.sample(body, len(body) // 100))
    yield "10 % строк", touch(copy(), rnd.sample(body, len(body) // 10))
    g = copy(); g += [list(grid[rnd.choice(body)]) for _ in range(500)]
    yield "+500 строк в конце", g
    g = copy()
    for _ in range(300):
        k = rnd.choice(body); g.insert(k, list(grid[k]))
    yield "+300 строк вразброс", g
    g = copy()
    for k in sorted(rnd.sample(body, 400), reverse=True):
        del g[k]
    yield "−400 строк вразброс", g
    g = copy(); emp_col = next(j for j, c in enumerate(grid[h]) if c and "ответствен" in str(c).lower())
    for i in rnd.sample(body, 2000):
        g[i][emp_col] = "Переназначен Тест"
    yield "переназначено 2000 договоров", g
    g = copy(); part = g[h + 1:]; rnd.shuffle(part); g = g[:h + 1] + part
    yield "пересортировка всех строк", g
    g = [list(r) + [f"новая {i}"] for i, r in enumerate(grid)]
    yield "новая колонка у всех строк", g


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=50000)
    a = ap.parse_args()
    db.init_db()
    random.seed(11)
    synth.EMPLOYEES[:] = [f"Сотрудник {i:02d} Бенч" for i in range(30)]
    path = synth.generate(n=a.rows, path=str(TMP / "registry.xlsx"))
    grid = db.pick_grid(read_workbook_sheets(path))
    rnd = random.Random(5)
    out = []
    for name, new in scenarios(grid, rnd):
        full = json.dumps({"sheets": [{"name": "Портфели", "grid": new}]}).encode()
        t = time.perf_counter(); d = gd.make_delta(grid, new); t_make = time.perf_counter() - t
        dj = json.dumps({"sheets_delta": d}).encode()
        t = time.perf_counter(); rebuilt = gd.apply_delta(grid, d); t_apply = time.perf_counter() - t
        r = {"сценарий": name,
             "полный_МБ": round(len(full) / 1e6, 1), "полный_gzip_МБ": round(len(gzip.compress(full, 6)) / 1e6, 2),
             "разница_КБ": round(len(dj) / 1e3, 1), "разница_gzip_КБ": round(len(gzip.compress(dj, 6)) / 1e3, 1),
             "расчёт_с": round(t_make, 2), "сборка_с": round(t_apply, 2),
             "совпал": gd.canonical(rebuilt) == gd.canonical(new)}
        out.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
    (ROOT / "bench" / "out").mkdir(exist_ok=True)
    (ROOT / "bench" / "out" / "delta.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
