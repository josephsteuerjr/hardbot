"""A/B обогащения на синтетике: старый способ против нового, ячейка в ячейку.

    python bench/enrich_ab.py [--rows 330] [--engines share,share+batch,local]

Всё — во временной папке, «шара» — локальная папка той же формы
(<республика>/<город>/<ФИО>/<ФИО>.xlsb), ФИО и номера выдуманы. Для каждого
сценария книга копируется для каждого способа, прогон идёт тем же
enrich_many, затем сравниваются:
  • отчёты (записано, конфликты, правки офиса, решения, реестр доставленного);
  • книги: Excel пересохраняет каждую в .xlsx, openpyxl сверяет каждую
    ячейку — значение, тип, формула, числовой формат, шрифт и заливка;
  • копия «до обогащения» совпадает с исходником байт в байт.
Отдельно — гонки только для способа «local»: книгу держат открытой с самого
начала, меняют её посреди прогона, открывают перед заменой.
"""
from __future__ import annotations

import argparse
import copy
import ctypes
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="hard-ab-"))
os.environ["APP_DATA"] = str(TMP / "appdata")
os.environ.setdefault("BOT_TOKEN", "bench")
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "pult"), str(ROOT)]

import db  # noqa: E402
import enrich  # noqa: E402

WINDOW = (date.today() - timedelta(days=6), date.today())
NASTY = ["=перезвонить", "+992 90 123 45 67", "-", "@дома", "5.08", "01.09.2026",
         "клиент сказал 'завтра' и \"точно\"", "строка1\nстрока2", "   пробелы  ",
         "Naqd pul berdi 400 ming", "Пулро ба ман дод, 300 сомонӣ", "x" * 2000, "0012345", "8600123412341234567"]
STATUSES = ["Обещает оплатить", "Нет контакта", "В работе", "Отказ от оплаты", "Оплачен"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --- синтетическая книга ---------------------------------------------------------
def book_grid(fields, n, rnd):
    titles = [f["title"] for f in fields] + ["Служебная формула"]
    blocks, prev = [], None
    for f in fields:
        blocks.append(f["block"] if f["block"] != prev else None)
        prev = f["block"]
    blocks.append(None)
    sources = [f.get("source") or "" for f in fields] + [""]
    rows = []
    keys = [f["key"] for f in fields]
    for i in range(n):
        r = []
        for f in fields:
            k = f["key"]
            if k == "contract_id":
                r.append(str(47_000_000 + i))
            elif k in enrich.VISIT_KEYS or k in enrich.WRITE_TARGETS.values():
                r.append(None)
            elif f.get("date"):
                r.append(date(2025, 1 + i % 12, 1 + i % 27))
            elif f.get("num"):
                r.append(round(rnd.uniform(100, 99999), 2))
            else:
                r.append(f"{f['title'][:12]} {i}")
        r.append(None)            # формула — ставится Excel после заполнения
        rows.append(r)
    # Правки офиса: часть визитных ячеек уже заполнена.
    for i in rnd.sample(range(n), n // 5):
        k = rnd.choice(["overall_result", "contact_with", "visit_comment", "extra_address"])
        if k in keys:
            rows[i][keys.index(k)] = rnd.choice(["Оплачен", "офис: звонил", "Родственник", "тел 111"])
    return [blocks, titles, sources, *rows]


def make_book(app, path: Path, grid, fields):
    wb = app.books.add()
    ws = wb.sheets[0]
    ws.name = enrich.SHEET_NAME
    ws.range("A1").value = grid
    n = len(grid)
    last = len(grid[1])
    for j, f in enumerate(fields, 1):
        if f.get("date"):
            # NumberFormat «dd.mm.yyyy» в русском Excel даёт xlsb, который тот же
            # Excel потом не открывает (проверено 30.09.2026) — формат локальный.
            ws.range((4, j), (n, j)).api.NumberFormatLocal = "ДД.ММ.ГГГГ"
    ws.range((4, last), (n, last)).formula = "=ROW()*2"
    ws.range((2, 1), (2, last)).api.Font.Bold = True
    ws.range((2, 1), (2, last)).color = (244, 228, 207)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.api.SaveAs(str(path), 50)          # xlsb
    wb.api.Close(False)                   # после SaveAs имя книги другое — закрываем через COM


def server_rows(ids, rnd, heavy=False):
    rows = []
    for cid in ids:
        vd = WINDOW[0] + timedelta(days=rnd.randint(0, (WINDOW[1] - WINDOW[0]).days))
        r = {"contract_id": cid, enrich.FILLED_MARKER: True,
             "visit_date": vd.isoformat(),
             "call_status": rnd.choice(STATUSES),
             "contact_with": rnd.choice(["Клиент", "Родственник", "Соседи"]),
             "agreed_payment": rnd.choice(["Да", "Нет", ""]),
             "who_at_address": rnd.choice(["Клиент", "Мать", "Никого"]),
             "client_location": rnd.choice(["Дома", "В РФ", ""]),
             "purpose_rf": rnd.choice(["Работа", ""]),
             "tips": rnd.choice(["", "доп номер +992 91 000 11 22", "доп адрес: ул. Мира 5"]),
             "visit_comment": rnd.choice(NASTY) if heavy or rnd.random() < .5 else "Дома никого",
             "promise_date": (vd + timedelta(days=5)).isoformat() if rnd.random() < .4 else None,
             "forecast_sum": rnd.choice(["1500", "1 500,50", "", "0"]),
             "forecast": rnd.choice(["Оплатит", ""])}
        rows.append(r)
    return rows


# --- сравнение книг ----------------------------------------------------------------
def to_xlsx(app, src: Path, dst: Path):
    wb = app.books.open(str(src), update_links=False, read_only=True)
    api = wb.api
    try:
        api.SaveAs(str(dst), 51)
    finally:
        api.Close(False)


def cells_of(path: Path) -> dict:
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb[enrich.SHEET_NAME]
    out = {}
    for row in ws.iter_rows():
        for c in row:
            if c.value is None and not c.has_style:
                continue
            out[c.coordinate] = (repr(c.value), c.data_type, c.number_format,
                                 bool(c.font.b), str(c.fill.fgColor.rgb), c.quotePrefix)
    return out


def diff_cells(a: dict, b: dict, limit=8) -> list:
    keys = sorted(set(a) | set(b))
    bad = [(k, a.get(k), b.get(k)) for k in keys if a.get(k) != b.get(k)]
    return bad[:limit] + ([f"… всего {len(bad)}"] if len(bad) > limit else [])


def report_view(r: dict) -> dict:
    keep = ("cells_written", "cells_already", "cells_over_nonempty", "changed_cases",
            "filled_cases", "matched_cases", "missing", "conflicts", "office_edits",
            "resolved", "displaced", "ledger_entries", "ledger_seeded", "error",
            "not_attempted")
    v = {k: r.get(k) for k in keep if k in r}
    if v.get("error"):
        v["error"] = str(v["error"]).split(":")[0][:40]
    return json.loads(json.dumps(v, ensure_ascii=False, sort_keys=True, default=str))


# --- «держат открытой» -------------------------------------------------------------
def hold_open(path: Path):
    """Хэндл с правом только на чтение для других — как у открытой книги Excel."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    h = k32.CreateFileW(str(path), 0x80000000 | 0x40000000, 0x1, None, 3, 0x80, None)
    if h in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_last_error())
    return lambda: k32.CloseHandle(ctypes.c_void_p(h))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=330)
    ap.add_argument("--engines", default="share,share+batch,local")
    a = ap.parse_args()
    engines = a.engines.split(",")
    import xlwings as xw
    db.init_db()
    fields = db.export_schema()
    rnd = random.Random(30)
    enrich._co_init()
    app = enrich.open_excel(xw)
    app.display_alerts = False
    app.screen_updating = False
    summary = {"rows": a.rows, "scenarios": [], "races": []}
    try:
        base = TMP / "base.xlsb"
        make_book(app, base, book_grid(fields, a.rows, rnd), fields)
        ids = [str(47_000_000 + i) for i in range(a.rows)]
        pick = rnd.sample(ids, a.rows // 5)
        scen = {
            "fresh+ledger": dict(rows=server_rows(pick, rnd), ledger={}),
            "no-ledger": dict(rows=server_rows(pick, rnd), ledger=None),
            "server-auth": dict(rows=server_rows(pick, rnd), ledger={}, server_authoritative=True),
            "heavy-all": dict(rows=server_rows(ids, rnd, heavy=True), ledger=None, window=False),
        }
        # решения по конфликтам: половина «server», половина «file»
        pre = enrich.preview_many([{"employee": "Тест", "path": base, "rows": scen["fresh+ledger"]["rows"]}],
                                  fields, since=WINDOW[0].isoformat(), until=WINDOW[1].isoformat(), ledger={})
        conf = pre[0].get("conflicts") or []
        scen["decisions"] = dict(rows=scen["fresh+ledger"]["rows"], ledger={},
                                 decisions={(c["contract_id"], c["key"]): ("server" if i % 2 else "file")
                                            for i, c in enumerate(conf)})
        for name, sc in scen.items():
            since, until = (WINDOW[0].isoformat(), WINDOW[1].isoformat()) if sc.get("window", True) else (None, None)
            out = {"scenario": name, "engines": {}}
            books, reps = {}, {}
            for eng in engines:
                share = TMP / "share" / eng.replace("+", "_") / name
                book = share / "Таджикистан" / "Душанбе" / "Тестов Тест" / "Тестов Тест.xlsb"
                book.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(base, book)
                bk = share / "_backup"
                res_all = []
                for rnd_i in (1, 2):           # второй прогон обязан быть нулевым
                    t = time.perf_counter()
                    res = enrich.enrich_many([{"employee": "Тестов Тест", "path": str(book), "rows": sc["rows"]}],
                                             fields, since=since, until=until, backup_root=bk,
                                             ledger=copy.deepcopy(sc.get("ledger")),
                                             server_authoritative=sc.get("server_authoritative", False),
                                             decisions=sc.get("decisions"), engine=eng)
                    dt = round(time.perf_counter() - t, 2)
                    res_all.append((dt, res[0]))
                r1, r2 = res_all[0][1], res_all[1][1]
                backups = sorted(bk.rglob("*.xlsb"))
                out["engines"][eng] = {
                    "run1_s": res_all[0][0], "run2_s": res_all[1][0],
                    "written": r1.get("cells_written"), "error1": r1.get("error"),
                    "second_run_writes": r2.get("cells_written"), "error2": r2.get("error"),
                    "backups": len(backups),
                    "backup_is_original": bool(backups) and sha(backups[0]) == sha(base),
                    "leftovers": sorted(p.name for p in book.parent.iterdir() if p.name != book.name),
                }
                books[eng] = book
                reps[eng] = report_view(r1)
            ref = engines[0]
            xl = {}
            for eng, book in books.items():
                x = TMP / "xlsx" / name / (eng.replace("+", "_") + ".xlsx")
                x.parent.mkdir(parents=True, exist_ok=True)
                to_xlsx(app, book, x)
                xl[eng] = cells_of(x)
            for eng in engines[1:]:
                out["engines"][eng]["same_report_as_" + ref] = reps[eng] == reps[ref]
                if reps[eng] != reps[ref]:
                    out["engines"][eng]["report_diff"] = [k for k in reps[ref] if reps[ref].get(k) != reps[eng].get(k)]
                d = diff_cells(xl[ref], xl[eng])
                out["engines"][eng]["same_cells_as_" + ref] = not d
                if d:
                    out["engines"][eng]["cell_diff"] = d
            out["cells_compared"] = len(xl[ref])
            summary["scenarios"].append(out)
            print(json.dumps(out, ensure_ascii=False, default=str), flush=True)

        if "local" in engines:
            rows = scen["fresh+ledger"]["rows"]

            def race(label, prepare=None, hook=None):
                share = TMP / "race" / label
                book = share / "Таджикистан" / "Худжанд" / "Гонка Тест" / "Гонка Тест.xlsb"
                book.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(base, book)
                release = prepare(book) if prepare else None
                try:
                    res = enrich.enrich_many([{"employee": "Гонка Тест", "path": str(book), "rows": rows}],
                                             fields, since=WINDOW[0].isoformat(), until=WINDOW[1].isoformat(),
                                             backup_root=share / "_backup", ledger={}, engine="local",
                                             before_commit=hook)[0]
                finally:
                    if release:
                        release()
                    for rel in getattr(hook, "releases", []):
                        rel()
                r = {"race": label, "error": (res.get("error") or "")[:110],
                     "book_is_original": sha(book) == sha(base),
                     "leftovers": sorted(p.name for p in book.parent.iterdir() if p.name != book.name)}
                if hook and getattr(hook, "other", None):
                    r["book_is_other_edit"] = sha(book) == hook.other
                summary["races"].append(r)
                print(json.dumps(r, ensure_ascii=False), flush=True)

            race("held-open-from-start", prepare=hold_open)

            other = TMP / "other.xlsb"
            make_book(app, other, book_grid(fields, 12, random.Random(1)), fields)

            def edited(book):
                shutil.copyfile(other, book)
            edited.other = sha(other)
            race("edited-during-run", hook=edited)

            def opened(book):
                opened.releases.append(hold_open(book))
            opened.releases = []
            race("opened-before-commit", hook=opened)
    finally:
        try:
            app.quit()
        except Exception:  # noqa: BLE001
            pass
    (ROOT / "bench" / "out").mkdir(exist_ok=True)
    (ROOT / "bench" / "out" / "enrich_ab.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
