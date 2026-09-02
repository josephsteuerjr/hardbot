"""Решения оператора по конфликтам обогащения и проверка без Excel.

Реестр доставленного научил писателя не затирать чужое, но конфликты он
только показывал: решать их приходилось руками в книге. Теперь оператор
решает в пульте — по одной ячейке или всем сразу — и одна запись применяет
решения: «записать сервер» пишет с журналом вытесненного, «оставить как в
книге» ничего не пишет, но сервер запоминает своё значение как доставленное,
и дальше ячейка считается правкой офиса, а не конфликтом.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import enrich  # noqa: E402


FIELDS = [
    {"key": "contract_id", "title": "Номер КД / ID"},
    {"key": "visit_date", "title": "Дата выезда", "date": True},
    {"key": "call_status", "title": "Статус звонка"},
    {"key": "overall_result", "title": "Общий результат"},
    {"key": "visit_comment", "title": "Комментарий текущего месяца выезда"},
]
TITLES = [f["title"] for f in FIELDS]


def _grid(rows):
    out = [TITLES]
    for r in rows:
        out.append([r.get(t, None) for t in TITLES])
    return out


def _srv(cid, visit_date, **vals):
    row = {"contract_id": cid, "visit_date": visit_date, enrich.FILLED_MARKER: True}
    row.update(vals)
    return row


def _ledger(**cells):
    return {cid: {k: {"value": v, "at": "2026-08-20T10:00:00+00:00"}
                  for k, v in fields.items()} for cid, fields in cells.items()}


STATUS_COL = TITLES.index("Общий результат")


def test_decision_file_keeps_book_and_teaches_the_ledger():
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса"}])
    rows = [_srv("A", "2026-08-20", call_status="Отказ")]
    plan = enrich.build_plan(grid, FIELDS, rows, ledger={},
                             decisions={("A", "call_status"): "file"})
    assert plan["conflicts"] == []
    assert plan["resolved_file"] == 1 and plan["resolved_server"] == 0
    assert plan["resolved"][0]["decision"] == "file"
    assert plan["resolved"][0]["kind"] == enrich.CONFLICT_NO_HISTORY
    kept = [e for e in plan["ledger_entries"] if e["kind"] == "kept_file"]
    assert kept == [{"contract_id": "A", "key": "call_status", "value": "Отказ",
                     "visit_date": "2026-08-20", "kind": "kept_file"}]
    assert all(c != STATUS_COL for _r, c, _v, _d in plan["writes"]), "статус не записан"
    # следующий прогон с обученным реестром: это уже правка офиса, не конфликт
    again = enrich.build_plan(grid, FIELDS, rows, ledger=_ledger(A={"call_status": "Отказ"}))
    assert again["conflicts"] == [] and len(again["office_edits"]) == 1


def test_decision_server_writes_with_journal():
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса"}])
    plan = enrich.build_plan(grid, FIELDS, [_srv("A", "2026-08-20", call_status="Отказ")],
                             ledger={}, decisions={("A", "call_status"): "server"})
    assert plan["conflicts"] == []
    assert plan["resolved_server"] == 1 and plan["resolved"][0]["decision"] == "server"
    assert plan["displaced"] and plan["displaced"][0]["was"] == "Правка офиса"
    assert any(c == STATUS_COL for _r, c, _v, _d in plan["writes"])
    assert any(e["key"] == "call_status" and e["kind"] == "written"
               for e in plan["ledger_entries"])


def test_decisions_cover_both_changed_too():
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса"},
                  {"Номер КД / ID": "B", "Общий результат": "Ещё правка"}])
    rows = [_srv("A", "2026-08-20", call_status="Обещание"),
            _srv("B", "2026-08-21", call_status="Обещание")]
    ledger = _ledger(A={"call_status": "Отказ"}, B={"call_status": "Отказ"})
    plan = enrich.build_plan(grid, FIELDS, rows, ledger=ledger,
                             decisions={("A", "call_status"): "server",
                                        ("B", "call_status"): "file"})
    assert plan["conflicts"] == []
    assert plan["resolved_server"] == 1 and plan["resolved_file"] == 1
    kinds = {c["contract_id"]: c["kind"] for c in plan["resolved"]}
    assert kinds == {"A": enrich.CONFLICT_BOTH_CHANGED, "B": enrich.CONFLICT_BOTH_CHANGED}
    assert [d["contract_id"] for d in plan["displaced"]] == ["A"]


def test_undecided_conflicts_stay_conflicts():
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса"},
                  {"Номер КД / ID": "B", "Общий результат": "Ещё правка"}])
    rows = [_srv("A", "2026-08-20", call_status="Отказ"),
            _srv("B", "2026-08-21", call_status="Отказ")]
    plan = enrich.build_plan(grid, FIELDS, rows, ledger={},
                             decisions={("A", "call_status"): "file"})
    assert [c["contract_id"] for c in plan["conflicts"]] == ["B"]


def test_post_plan_after_decisions_is_zero():
    """Инвариант evidence-gate: после записи повторный план нулевой и при
    решениях. «server» — ячейка стала равна серверу; «file» — реестр в памяти
    ещё старый, но решение то же, записи нет."""
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса"},
                  {"Номер КД / ID": "B", "Общий результат": "Ещё правка"}])
    rows = [_srv("A", "2026-08-20", call_status="Отказ"),
            _srv("B", "2026-08-21", call_status="Отказ")]
    decisions = {("A", "call_status"): "server", ("B", "call_status"): "file"}
    plan = enrich.build_plan(grid, FIELDS, rows, ledger={}, decisions=decisions)
    for r, c, v, _d in plan["writes"]:
        grid[r][c] = v
    post = enrich.build_plan(grid, FIELDS, rows, ledger={}, decisions=decisions)
    assert post["cells_written"] == 0 and post["changed_cases"] == 0
    assert post["conflicts"] == []


def test_preview_many_plans_without_excel(monkeypatch, tmp_path):
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса"},
                  {"Номер КД / ID": "B"}])
    monkeypatch.setattr(enrich, "read_stable_workbook",
                        lambda p: {"sheets": [{"name": enrich.SHEET_NAME, "grid": grid},
                                              {"name": "Статусы", "grid": [["x"]]}],
                                   "file_sha256": "f" * 64})

    def no_excel(*_a, **_k):
        raise AssertionError("Excel открыт при проверке")

    monkeypatch.setattr(enrich, "open_excel", no_excel)
    rows = [_srv("A", "2026-08-20", call_status="Отказ"),
            _srv("B", "2026-08-21", call_status="Обещание")]
    book = tmp_path / "Иванов.xlsb"
    book.write_bytes(b"x")
    results = enrich.preview_many([{"employee": "Иванов", "path": book, "rows": rows}],
                                  FIELDS, ledger={})
    r = results[0]
    assert r["preview"] is True and r["employee"] == "Иванов" and "writes" not in r
    assert r["cells_written"] == 3, "B: статус и дата; A: только дата"
    assert [c["contract_id"] for c in r["conflicts"]] == ["A"]
    assert r["file_sha256"] == "f" * 64
    assert not list(tmp_path.glob("*до обогащения*")), "копий и записи быть не должно"

    again = enrich.preview_many([{"employee": "Иванов", "path": book, "rows": rows}],
                                FIELDS, ledger={}, decisions={("A", "call_status"): "file"})
    assert again[0]["conflicts"] == [] and again[0]["resolved_file"] == 1


def test_preview_many_reports_missing_sheet_per_file(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich, "read_stable_workbook",
                        lambda p: {"sheets": [{"name": "Другой", "grid": [["a"]]}],
                                   "file_sha256": "0" * 64})
    book = tmp_path / "x.xlsb"
    book.write_bytes(b"x")
    results = enrich.preview_many([{"employee": "И", "path": book, "rows": []}], FIELDS)
    assert "error" in results[0] and enrich.SHEET_NAME in results[0]["error"]
    assert results[0]["employee"] == "И"
