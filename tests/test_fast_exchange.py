"""Опыт «быстрый обмен» (ветка exp/fast-exchange, 30.09.2026).

Без Excel: запись через COM, «лишние» цифры, способ «local» с заглушкой Excel
(книга — байты на диске, «сохранение» дописывает метку), гонки на замене,
сжатие тела запроса пульта. Сравнение с живым Excel — bench/enrich_ab.py.
"""
from __future__ import annotations

import gzip
import json
import sys
import types
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import enrich  # noqa: E402
from pult.core import api  # noqa: E402


# --- «лишние» цифры -------------------------------------------------------------
@pytest.mark.parametrize("v,lossy", [
    ("0012345", True), ("0", False), ("12345", False), ("8600123412341234", True),
    ("123456789012345", False), (" 007 ", True), ("12 345", False), ("٠١٢", False), (12, False)])
def test_digits_that_excel_would_mangle(v, lossy):
    assert enrich.excel_digits_lossy(v) is lossy
    if isinstance(v, str):
        assert enrich._excel_safe(v).startswith("'") is (lossy or enrich.excel_formula_like(v))


# --- запись через COM -------------------------------------------------------------
class _Cell:
    def __init__(self, log, r, c):
        self.log, self.r, self.c = log, r, c

    @property
    def Value(self):  # noqa: N802 — имя COM
        return None

    @Value.setter
    def Value(self, v):  # noqa: N802
        self.log.append((self.r, self.c, v))


def _ws():
    log = []
    ws = types.SimpleNamespace(api=types.SimpleNamespace(Cells=lambda r, c: _Cell(log, r, c)))
    return ws, log


def test_com_writer_applies_the_same_safety_and_last_write_wins(monkeypatch):
    fake = types.ModuleType("xlwings._xlwindows")
    fake._datetime_to_com_time = lambda d: ("utc", d)
    monkeypatch.setitem(sys.modules, "xlwings._xlwindows", fake)
    ws, log = _ws()
    n = enrich._write_cells(ws, [(0, 0, "=x", False), (1, 2, date(2026, 9, 30), True),
                                 (0, 0, "второе", False), (3, 1, "0012", False)])
    assert n == 3
    assert (1, 1, "второе") in log and all(v != "=x" for _, _, v in log)
    assert (2, 3, ("utc", datetime(2026, 9, 30))) in log
    assert (4, 2, "'0012") in log


# --- способ «local» на заглушке Excel ---------------------------------------------
FIELDS = [
    {"key": "contract_id", "title": "Номер КД / ID", "block": "Инфо", "source": "сх", "date": False},
    {"key": "visit_date", "title": "Дата выезда", "block": "Выезд", "source": "хард", "date": True},
    {"key": "visit_comment", "title": "Комментарий текущего месяца выезда", "block": "Выезд",
     "source": "хард", "date": False},
]


class _Book:
    """Книга-заглушка: сетка в JSON внутри файла, сохранение пишет её назад."""

    def __init__(self, path: Path, read_only=False):
        self.path, self.read_only = path, read_only
        self.grid = json.loads(path.read_text(encoding="utf-8"))
        sheet = types.SimpleNamespace(name=enrich.SHEET_NAME)
        writes = []
        sheet.api = types.SimpleNamespace(Cells=lambda r, c: _Cell(writes, r, c))
        self._writes = writes
        self.sheets = [sheet]
        self.api = types.SimpleNamespace(ReadOnly=read_only)

    def save(self):
        for r, c, v in self._writes:
            row = self.grid[r - 1]
            while len(row) < c:
                row.append(None)
            row[c - 1] = v.lstrip("'") if isinstance(v, str) else str(v)
        self.path.write_text(json.dumps(self.grid, ensure_ascii=False), encoding="utf-8")

    def close(self):
        pass


def _app():
    return types.SimpleNamespace(books=types.SimpleNamespace(
        open=lambda p, update_links=False, read_only=False: _Book(Path(p), read_only)))


@pytest.fixture()
def local_env(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich, "_workbook_grid", lambda wb, fields: (wb.sheets[0], wb.grid))
    fake = types.ModuleType("xlwings._xlwindows")
    fake._datetime_to_com_time = lambda d: d.date().isoformat()
    monkeypatch.setitem(sys.modules, "xlwings._xlwindows", fake)
    book = tmp_path / "share" / "Таджикистан" / "Душанбе" / "Тест" / "Тест.xlsb"
    book.parent.mkdir(parents=True)
    grid = [["Инфо", "Выезд", None], ["Номер КД / ID", "Дата выезда",
                                       "Комментарий текущего месяца выезда"],
            ["сх", "хард", "хард"], ["100", None, None], ["200", None, "офис"]]
    book.write_text(json.dumps(grid, ensure_ascii=False), encoding="utf-8")
    rows = [{"contract_id": "100", enrich.FILLED_MARKER: True, "visit_date": "2026-09-29",
             "visit_comment": "дома никого"}]
    return book, rows, tmp_path


def _run(book, rows, tmp_path, **kw):
    return enrich._enrich_local(_app(), book, FIELDS, rows, backup_root=tmp_path / "bk",
                                work_dir=tmp_path, **kw)


def test_local_engine_writes_through_a_verified_copy(local_env):
    book, rows, tmp = local_env
    before = book.read_bytes()
    plan = _run(book, rows, tmp)
    assert plan["cells_written"] == 2 and plan["engine"] == "local"
    grid = json.loads(book.read_text(encoding="utf-8"))
    assert grid[3][1:] == ["2026-09-29", "дома никого"]
    assert Path(plan["backup_path"]).read_bytes() == before
    assert plan["post_plan"]["cells_written"] == 0
    assert sorted(p.name for p in book.parent.iterdir()) == ["Тест.xlsb"]   # служебного не осталось
    again = _run(book, rows, tmp)                        # второй прогон — ноль и без копии
    assert again["cells_written"] == 0 and again["backup_path"] is None


def test_book_changed_during_run_is_left_to_the_office(local_env):
    book, rows, tmp = local_env
    other = json.dumps([["правка офиса"]], ensure_ascii=False)

    def edit(path):
        path.write_text(other, encoding="utf-8")
    with pytest.raises(enrich.BookChangedDuringRun):
        _run(book, rows, tmp, before_commit=edit)
    assert book.read_text(encoding="utf-8") == other
    assert sorted(p.name for p in book.parent.iterdir()) == ["Тест.xlsb"]


def test_replace_refused_by_windows_leaves_book_and_no_temp(local_env, monkeypatch):
    book, rows, tmp = local_env
    before = book.read_bytes()
    monkeypatch.setattr(enrich, "_REPLACE_RETRIES", (0.0,))

    def deny(src, dst):
        raise PermissionError("занято")
    monkeypatch.setattr(enrich.os, "replace", deny)
    with pytest.raises(enrich.BookChangedDuringRun):
        _run(book, rows, tmp)
    assert book.read_bytes() == before
    assert sorted(p.name for p in book.parent.iterdir()) == ["Тест.xlsb"]


def test_nonzero_second_plan_writes_nothing_to_the_share(local_env, monkeypatch):
    book, rows, tmp = local_env
    before = book.read_bytes()
    real = enrich._plan_workbook
    monkeypatch.setattr(enrich, "_plan_workbook",
                        lambda *a, **k: {**real(*a, **k), "cells_written": 1})
    with pytest.raises(RuntimeError, match="на шару ничего не записано"):
        _run(book, rows, tmp)
    assert book.read_bytes() == before
    assert not (tmp / "bk").exists() or not list((tmp / "bk").rglob("*.xlsb"))


# --- сжатие тела запроса пульта ----------------------------------------------------
class _Resp:
    def __init__(self, b):
        self.b, self.headers = b, {}

    def read(self):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_large_bodies_go_gzipped_small_ones_plain():
    seen = []

    def opener(req, **kw):
        seen.append(req)
        return _Resp(b'{"ok": true}')
    big = {"sheets": [{"name": "Портфели", "grid": [["Иванов"] * 40] * 400}]}
    api.request("https://h", "POST", "/admin/import/preview.json", big, opener=opener)
    api.request("https://h", "POST", "/admin/period", {"from": ""}, opener=opener)
    assert seen[0].get_header("Content-encoding") == "gzip"
    assert json.loads(gzip.decompress(seen[0].data)) == big
    assert seen[1].get_header("Content-encoding") is None
    assert json.loads(seen[1].data) == {"from": ""}
