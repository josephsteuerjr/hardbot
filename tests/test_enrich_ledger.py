"""Реестр доставленного: писатель помнит, что сам положил в книгу.

Сентябрь 2026. Два прогона потеряли правки офиса (04.08 без окна — 28
договоров; 28.08 с окном — 107 статусов через шлюз). Корень один: писатель
сравнивал сервер с файлом и не знал, что в файле стоит его собственная прошлая
доставка, а что — рука офиса. Окно дат было единственным предохранителем и
зависело от человека.

Теперь план строится трёхсторонне: сервер S, файл F, доставлено D.
"""

import json
import sys
import types
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import enrich  # noqa: E402


FIELDS = [
    {"key": "contract_id", "title": "Номер КД / ID"},
    {"key": "visit_date", "title": "Дата выезда", "date": True},
    {"key": "call_status", "title": "Статус звонка"},
    {"key": "overall_result", "title": "Общий результат"},
    {"key": "visit_comment", "title": "Комментарий текущего месяца выезда"},
    {"key": "extra_address", "title": "Доп. Адрес/Доп.Номер"},
    {"key": "contact_with", "title": "С кем был контакт?"},
]
TITLES = [f["title"] for f in FIELDS]


def _grid(rows):
    out = [TITLES]
    for r in rows:
        out.append([r.get(t, None) for t in TITLES])
    return out


def _srv(cid, visit_date, **vals):
    row = {"contract_id": cid, "visit_date": visit_date,
           enrich.FILLED_MARKER: True}
    row.update(vals)
    return row


def _ledger(**cells):
    """{КД: {поле: значение}} → формат ответа сервера."""
    out = {}
    for cid, fields in cells.items():
        out[cid] = {k: {"value": v, "at": "2026-08-20T10:00:00+00:00"}
                    for k, v in fields.items()}
    return out


def _writes_by_key(plan):
    return {(r, c): v for r, c, v, _d in plan["writes"]}


# --- Без реестра поведение прежнее ------------------------------------------

def test_ledger_none_keeps_legacy_behaviour():
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса",
                   "Дата выезда": date(2026, 8, 20)}])
    plan = enrich.build_plan(grid, FIELDS,
                             [_srv("A", "2026-08-20", call_status="Отказ")])
    assert plan["ledger_mode"] is False
    assert plan["cells_written"] == 1, "без реестра пишем, как раньше"
    assert plan["displaced"] and plan["displaced"][0]["was"] == "Правка офиса"
    assert plan["conflicts"] == [] and plan["office_edits"] == []


# --- Реестр включён ----------------------------------------------------------

def test_empty_cell_without_history_is_written_and_recorded():
    grid = _grid([{"Номер КД / ID": "A"}])
    plan = enrich.build_plan(grid, FIELDS,
                             [_srv("A", "2026-08-20", call_status="Отказ")],
                             ledger={})
    assert plan["ledger_mode"] is True
    keys = {(e["contract_id"], e["key"], e["kind"]) for e in plan["ledger_entries"]}
    assert ("A", "call_status", "written") in keys
    assert ("A", "visit_date", "written") in keys
    assert plan["conflicts"] == []


def test_technical_empty_without_history_is_still_written():
    """«Не обработан» — довизитная пустота, а не чьё-то решение."""
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Не обработан"}])
    plan = enrich.build_plan(grid, FIELDS,
                             [_srv("A", "2026-08-20", call_status="Отказ")],
                             ledger={})
    assert plan["cells_written"] == 2
    assert plan["technical_overwrites"] == 1
    assert plan["conflicts"] == []


def test_nonempty_cell_without_history_is_a_conflict_not_a_write():
    """Истории нет, в файле что-то стоит — решает человек, не писатель."""
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса"}])
    plan = enrich.build_plan(grid, FIELDS,
                             [_srv("A", "2026-08-20", call_status="Отказ")],
                             ledger={})
    assert plan["displaced"] == [], "вытеснения быть не должно"
    written_keys = {e["key"] for e in plan["ledger_entries"] if e["kind"] == "written"}
    assert "call_status" not in written_keys
    assert "visit_date" in written_keys, "пустая дата пишется, конфликт точечный"
    assert len(plan["conflicts"]) == 1
    c = plan["conflicts"][0]
    assert c["kind"] == enrich.CONFLICT_NO_HISTORY
    assert c["file"] == "Правка офиса" and c["server"] == "Отказ"
    assert c["column"] == "Общий результат" and c["row"] == 2


def test_server_authoritative_overrides_no_history_conflict_with_journal():
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса"}])
    plan = enrich.build_plan(grid, FIELDS,
                             [_srv("A", "2026-08-20", call_status="Отказ")],
                             ledger={}, server_authoritative=True)
    assert plan["conflicts"] == []
    assert plan["displaced"] and plan["displaced"][0]["was"] == "Правка офиса"
    assert plan["server_authoritative"] is True


def test_office_edit_after_delivery_is_protected():
    """S == D, F != D: офис правил после прогона. Не трогаем и показываем."""
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Обещал 15.09"}])
    plan = enrich.build_plan(grid, FIELDS,
                             [_srv("A", "2026-08-20", call_status="Отказ")],
                             ledger=_ledger(A={"call_status": "Отказ"}))
    assert all(e["key"] != "call_status" for e in plan["ledger_entries"]
               if e["kind"] == "written")
    assert plan["displaced"] == []
    assert len(plan["office_edits"]) == 1
    o = plan["office_edits"][0]
    assert o["kind"] == enrich.OFFICE_EDIT_AFTER_DELIVERY
    assert o["file"] == "Обещал 15.09" and o["delivered"] == "Отказ"


def test_server_update_over_own_delivery_is_written_without_displacement():
    """S != D, F == D: сервер обновился, файл нетронут. Пишем, журнал чист."""
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Отказ",
                   "Дата выезда": "2026-08-20"}])
    plan = enrich.build_plan(
        grid, FIELDS,
        [_srv("A", "2026-08-20", call_status="Обещание")],
        ledger=_ledger(A={"call_status": "Отказ", "visit_date": "2026-08-20"}),
    )
    assert plan["cells_written"] == 1
    assert plan["cells_updated_delivered"] == 1
    assert plan["displaced"] == [], "замена своей доставки — не вытеснение"
    assert plan["conflicts"] == [] and plan["office_edits"] == []
    written = [e for e in plan["ledger_entries"] if e["kind"] == "written"]
    assert written == [{"contract_id": "A", "key": "call_status",
                        "value": "Обещание", "visit_date": "2026-08-20",
                        "kind": "written"}]


def test_both_sides_changed_is_a_conflict():
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса"}])
    plan = enrich.build_plan(
        grid, FIELDS,
        [_srv("A", "2026-08-20", call_status="Обещание")],
        ledger=_ledger(A={"call_status": "Отказ"}),
    )
    assert plan["displaced"] == []
    assert len(plan["conflicts"]) == 1
    c = plan["conflicts"][0]
    assert c["kind"] == enrich.CONFLICT_BOTH_CHANGED
    assert (c["file"], c["server"], c["delivered"]) == \
        ("Правка офиса", "Обещание", "Отказ")


def test_file_cleared_after_delivery_is_refilled():
    """D есть, файл опустел: пустота — не решение, пишем свежее значение."""
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": ""}])
    plan = enrich.build_plan(
        grid, FIELDS,
        [_srv("A", "2026-08-20", call_status="Обещание")],
        ledger=_ledger(A={"call_status": "Отказ"}),
    )
    assert plan["conflicts"] == [] and plan["office_edits"] == []
    assert any(e["key"] == "call_status" and e["kind"] == "written"
               for e in plan["ledger_entries"])


def test_matching_file_seeds_the_ledger_without_writing():
    """F == S без истории: ничего не пишем, но реестр досеиваем."""
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Отказ",
                   "Дата выезда": date(2026, 8, 20)}])
    plan = enrich.build_plan(grid, FIELDS,
                             [_srv("A", "2026-08-20", call_status="Отказ")],
                             ledger={})
    assert plan["cells_written"] == 0
    assert plan["ledger_seeded"] == 2
    seeds = {(e["key"], e["value"]) for e in plan["ledger_entries"]
             if e["kind"] == "seed"}
    assert seeds == {("call_status", "Отказ"), ("visit_date", "2026-08-20")}


def test_seed_is_not_repeated_when_ledger_already_matches():
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Отказ",
                   "Дата выезда": date(2026, 8, 20)}])
    plan = enrich.build_plan(grid, FIELDS,
                             [_srv("A", "2026-08-20", call_status="Отказ")],
                             ledger=_ledger(A={"call_status": "Отказ"}))
    assert plan["ledger_seeded"] == 1, "дата не в реестре — досеять; статус уже есть"
    assert {e["key"] for e in plan["ledger_entries"]} == {"visit_date"}


def test_merge_modes_are_not_governed_by_the_ledger():
    """Дописывающие режимы ничего не уничтожают — реестр им не указ."""
    grid = _grid([{"Номер КД / ID": "A",
                   "Комментарий текущего месяца выезда": "офис писал",
                   "Доп. Адрес/Доп.Номер": "+998 90 111 22 33"}])
    plan = enrich.build_plan(
        grid, FIELDS,
        [_srv("A", "2026-08-20", visit_comment="бот писал",
              tips="+998 90 111 22 33\nул. Новая 5")],
        ledger=_ledger(A={"visit_comment": "что-то другое"}),
    )
    assert plan["conflicts"] == [] and plan["office_edits"] == []
    values = list(_writes_by_key(plan).values())
    assert any("офис писал" in str(v) and "бот писал" in str(v) for v in values)
    assert any("ул. Новая 5" in str(v) for v in values)


def test_post_plan_with_same_ledger_is_zero():
    """Инвариант evidence-gate: после записи повторный план обязан быть нулевым
    даже при конфликтах — они не пишутся ни в первый раз, ни во второй."""
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Правка офиса"},
                  {"Номер КД / ID": "B"}])
    rows = [_srv("A", "2026-08-20", call_status="Отказ"),
            _srv("B", "2026-08-21", call_status="Обещание")]
    plan = enrich.build_plan(grid, FIELDS, rows, ledger={})
    # применяем записи к сетке
    for r, c, v, _d in plan["writes"]:
        grid[r][c] = v
    post = enrich.build_plan(grid, FIELDS, rows, ledger={})
    assert post["cells_written"] == 0 and post["changed_cases"] == 0
    assert len(post["conflicts"]) == 1


# --- Пачка: квитанция, отправка реестра, повтор из квитанции ----------------

class _FakeApp:
    display_alerts = True
    screen_updating = True


@contextmanager
def _fake_excel(*_a, **_k):
    yield _FakeApp()


def _run_batch(monkeypatch, tmp_path, *, plans, on_delivered=None, ledger=None):
    monkeypatch.setitem(sys.modules, "xlwings", types.SimpleNamespace())
    monkeypatch.setattr(enrich, "_co_init", lambda: None)
    monkeypatch.setattr(enrich, "open_excel", _fake_excel)
    calls = iter(plans)

    def fake_evidence(app, path, fields, rows, log, since, until, backup_root,
                      ledger=None, server_authoritative=False, decisions=None):
        plan = next(calls)
        if isinstance(plan, Exception):
            raise plan
        return {**plan, "path": str(path), "file": str(path)}

    monkeypatch.setattr(enrich, "_enrich_with_evidence", fake_evidence)
    jobs = [{"employee": f"Сотрудник {i}", "path": tmp_path / f"{i}.xlsb",
             "rows": []} for i in range(len(plans))]
    return enrich.enrich_many(jobs, FIELDS, receipt_dir=tmp_path / "кв",
                              ledger=ledger, on_delivered=on_delivered)


def _plan(**over):
    base = {"cells_written": 1, "changed_cases": 1, "displaced": [],
            "conflicts": [], "office_edits": [], "ledger_entries": [],
            "filled_contract_ids": [], "matched_contract_ids": [], "missing": [],
            "errors": []}
    base.update(over)
    return base


def test_batch_sends_ledger_after_receipt_and_reports_count(monkeypatch, tmp_path):
    sent = []

    def on_delivered(payload):
        sent.append(payload)
        return {"ok": True, "recorded": len(payload["entries"])}

    entry = {"contract_id": "A", "key": "call_status", "value": "Отказ",
             "visit_date": "2026-08-20", "kind": "written"}
    results = _run_batch(monkeypatch, tmp_path, ledger={}, on_delivered=on_delivered,
                         plans=[_plan(ledger_entries=[entry])])
    assert len(sent) == 1
    payload = sent[0]
    assert payload["entries"][0]["responsible"] == "Сотрудник 0"
    assert payload["entries"][0]["file"].endswith("0.xlsb")
    assert payload["receipt"] and Path(payload["receipt"]).is_file()
    assert len(payload["receipt_digest"]) == 64
    assert results[0]["ledger_recorded"] == 1
    receipt = json.loads(Path(results[0]["receipt"]).read_text(encoding="utf-8"))
    assert receipt["ledger_entries"][0]["contract_id"] == "A"
    assert receipt["conflicts"] == [] and receipt["office_edits"] == []


def test_ledger_send_failure_is_visible_in_results_not_only_in_log(monkeypatch, tmp_path):
    def on_delivered(payload):
        raise RuntimeError("Нет связи с сервером")

    entry = {"contract_id": "A", "key": "call_status", "value": "Отказ",
             "visit_date": "", "kind": "written"}
    results = _run_batch(monkeypatch, tmp_path, ledger={}, on_delivered=on_delivered,
                         plans=[_plan(ledger_entries=[entry])])
    assert results[0]["ledger_error"] == "Нет связи с сервером"
    assert "ledger_recorded" not in results[0]


def test_failed_book_contributes_nothing_but_unverified_one_does(monkeypatch, tmp_path):
    sent = []
    entry = {"contract_id": "B", "key": "call_status", "value": "Отказ",
             "visit_date": "", "kind": "written"}
    unverified = enrich.WrittenButUnverified(
        path="x", pre_sha256="1", post_sha256="2", backup_path=None,
        backup_sha256=None, cells_written=1, displaced=[],
        ledger_entries=[entry], conflicts=[], office_edits=[], reason="Excel умер")
    results = _run_batch(
        monkeypatch, tmp_path, ledger={},
        on_delivered=lambda p: sent.append(p) or {"recorded": len(p["entries"])},
        plans=[RuntimeError("занят"), unverified],
    )
    assert "error" in results[0] and "error" in results[1]
    assert results[1]["written_unverified"] is True
    assert [e["contract_id"] for e in sent[0]["entries"]] == ["B"]


def test_legacy_batch_without_ledger_never_calls_on_delivered(monkeypatch, tmp_path):
    called = []
    results = _run_batch(monkeypatch, tmp_path, ledger=None,
                         on_delivered=lambda p: called.append(p),
                         plans=[_plan(ledger_entries=[{"contract_id": "A",
                                                       "key": "call_status",
                                                       "value": "x",
                                                       "visit_date": "",
                                                       "kind": "written"}])])
    assert called == []
    assert "ledger_recorded" not in results[0] and "ledger_error" not in results[0]


def test_replay_from_receipt_resends_the_same_entries(monkeypatch, tmp_path):
    sent = []
    entry = {"contract_id": "A", "key": "call_status", "value": "Отказ",
             "visit_date": "", "kind": "written"}
    results = _run_batch(monkeypatch, tmp_path, ledger={},
                         on_delivered=lambda p: (_ for _ in ()).throw(OSError("сеть")),
                         plans=[_plan(ledger_entries=[entry])])
    assert results[0]["ledger_error"]
    outcome = enrich.replay_ledger_receipt(
        results[0]["receipt"],
        lambda p: sent.append(p) or {"recorded": len(p["entries"])})
    assert outcome == {"recorded": 1, "entries": 1}
    assert sent[0]["entries"][0]["responsible"] == "Сотрудник 0"


# --- Предложенное окно -------------------------------------------------------

def test_suggest_window_is_empty_without_receipts(tmp_path):
    assert enrich.suggest_window(tmp_path) == ("", "")
    assert enrich.suggest_window(tmp_path / "нет") == ("", "")
    assert enrich.suggest_window(None) == ("", "")


def test_suggest_window_uses_the_latest_receipt_minus_margin(tmp_path):
    folder = tmp_path / "кв"
    folder.mkdir()
    (folder / "обогащение-20260810T120000.json").write_text(
        json.dumps({"at": "2026-08-10T12:00:00"}), encoding="utf-8")
    (folder / "обогащение-20260825T090000.json").write_text(
        json.dumps({"at": "2026-08-25T09:00:00"}), encoding="utf-8")
    since, until = enrich.suggest_window(folder, today=date(2026, 9, 2))
    assert since == "18.08.2026", "неделя запаса от последнего прогона"
    assert until == "02.09.2026"


def test_suggest_window_ignores_a_broken_receipt(tmp_path):
    folder = tmp_path / "кв"
    folder.mkdir()
    (folder / "обогащение-20260825T090000.json").write_text("{мусор", encoding="utf-8")
    assert enrich.suggest_window(folder) == ("", "")
