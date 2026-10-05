"""Импорт, два личных реестра и доступ СБ: синтетические рабочие сценарии."""
from datetime import date, timedelta
import asyncio
import json
import pytest
from aiohttp import web as aio_web
from aiohttp.test_utils import make_mocked_request

import db
import control
import digest_data
import web
from pult.core import bootstrap
bootstrap.ensure_paths()
from pult import enrich
from pult.core import enrichment as en, registry_import as ri
from pult.core.settings import Settings
from test_review_soft_integrity import world, call, grid  # общая изолированная API-среда


def test_import_choices_survive_reload_empty_selection_and_profile_switch(tmp_path):
    settings = Settings({"url": "https://test.invalid", "device_token": "synthetic"}, tmp_path / "settings.json")
    employees = [{"key": "one", "name": "One", "file_count": 3, "db_count": 2},
                 {"key": "two", "name": "Two", "file_count": 4, "db_count": 0}]
    book = {"import_profile": "hard", "employees": employees}
    ri.remember_import_selection(book, {"one"}, settings)
    book["import_profile"] = "soft"
    ri.remember_import_selection(book, set(), settings)
    settings.save()
    loaded = Settings.load(settings.path)
    assert ri.restore_import_selection(book, loaded) == set()
    book["import_profile"] = "hard"
    book["employees"] = employees + [{"key": "new", "name": "New", "file_count": 100, "db_count": 0}]
    assert ri.restore_import_selection(book, loaded) == {"one"}
    loaded["profiles"][loaded["active"]]["device_token"] = "another operator"
    assert ri.import_preferences(loaded) == {}


def test_filter_and_sort_do_not_remove_hidden_checks():
    rows = [{"key": "a", "name": "Альфа", "file_count": 1, "db_count": 1},
            {"key": "b", "name": "Бета", "file_count": 10, "db_count": 0},
            {"key": "v", "name": "Вега", "file_count": 0, "db_count": 12}]
    selected = {"a", "v"}
    assert [e["key"] for e in ri.filter_import_employees(rows, selected, query="бЕТ")] == ["b"]
    assert selected == {"a", "v"}
    assert [e["key"] for e in ri.filter_import_employees(rows, selected, sort="file")] == ["b", "a", "v"]
    assert [e["key"] for e in ri.filter_import_employees(rows, selected, group="selected")] == ["a", "v"]
    assert [e["key"] for e in ri.filter_import_employees(rows, selected, group="db_only")] == ["v"]


def test_book_delivery_history_is_independent_and_idempotent(world):
    hard, soft = "U:/Синтетика/Город/Hard.xlsb", "U:/Синтетика/Soft/Soft.xlsb"
    db.record_enrichment_deliveries([
        {"contract_id": "A", "key": "forecast_sum", "value": "100", "file": hard, "responsible": "H1"},
        {"contract_id": "A", "key": "forecast_sum", "value": "200", "file": soft, "responsible": "S1"}])
    db.record_enrichment_deliveries([
        {"contract_id": "A", "key": "forecast_sum", "value": "300", "file": hard.upper().replace("/", "\\"), "responsible": "H1"}])
    db.init_db()
    books = db.enrichment_book_ledger()
    assert len(books) == 2
    assert books[enrich.book_key(hard)]["A"]["forecast_sum"]["value"] == "300"
    assert books[enrich.book_key(soft)]["A"]["forecast_sum"]["value"] == "200"
    assert set(db.enrichment_book_ledger(["S1"])) == {enrich.book_key(soft)}
    assert enrich.ledger_for_book({"_books": books}, "U:/missing.xlsb") == {}


def test_legacy_delivery_migration_seeds_only_known_book(world):
    db.record_enrichment_deliveries([{"contract_id": "A", "key": "forecast_sum", "value": "100", "file": "U:/Known.xlsb"}])
    with db.connect() as conn:
        conn.execute("DROP TABLE enrichment_book_ledger")
        conn.commit()
    db.init_db()
    assert set(db.enrichment_book_ledger()) == {"u:/known.xlsb"}


def test_conflict_decisions_belong_to_one_book():
    results = [{"employee": "Same", "file": path, "conflicts": [{"contract_id": "A", "key": "forecast_sum"}]} for path in ("U:/Hard.xlsb", "U:/Soft.xlsb")]
    conflicts, _ = en.collect_conflicts(results)
    decisions = {en.conflict_key(conflicts[0]): "server", en.conflict_key(conflicts[1]): "file"}
    assert enrich.decisions_for_book(decisions, "u:/hard.xlsb") == {("A", "forecast_sum"): "server"}
    assert enrich.decisions_for_book(decisions, "u:/soft.xlsb") == {("A", "forecast_sum"): "file"}


def test_cleared_callback_date_reaches_soft_book_but_office_edit_is_protected(world, monkeypatch):
    future = (date.today() + timedelta(days=3)).isoformat()
    call(monkeypatch, "initial-date", values={"soft_comm_date": future})
    call(monkeypatch, "clear-date", values={"soft_comm_date": ""}, cleared_keys=["soft_comm_date"])
    exported = next(r for r in db.enrichment_rows() if r["contract_id"] == "A")
    assert "soft_comm_date" in exported["_enrichment_clear_keys"]
    schema = db.export_schema()
    soft_keys = {"contract_id", "extra_address", "soft_call_status", "soft_comm_date", "soft_comment", "promise_date", "forecast_sum", "forecast"}
    cols = [f for f in schema if f["key"] in soft_keys]
    book = [[f["title"] for f in cols], ["софт"] * len(cols),
            ["A" if f["key"] == "contract_id" else future if f["key"] == "soft_comm_date" else None for f in cols]]
    ledger = {"A": {"soft_comm_date": {"value": future}}}
    plan = enrich.build_plan(book, schema, [{**exported, "_profile": "soft"}], ledger=ledger)
    index = next(i for i,f in enumerate(cols) if f["key"] == "soft_comm_date")
    assert (2, index, "", True) in plan["writes"]
    book[2][index] = (date.today() + timedelta(days=5)).isoformat()
    plan = enrich.build_plan(book, schema, [{**exported, "_profile": "soft"}], ledger=ledger)
    assert any(c["key"] == "soft_comm_date" and c["kind"] == "both_changed" for c in plan["conflicts"])
    assert not any(w[1] == index for w in plan["writes"])


def test_security_scope_and_historical_calls_keep_soft_employee(world, monkeypatch):
    for cid in ("A", "B"):
        db.update_fields(cid, {"country": "Узбекистан"})
    db.update_fields("C", {"country": "Таджикистан"})
    db.set_manager(123, "Security", ["Узбекистан"], [], direction="uz", role="security")
    mgr = db.get_manager(123)
    assert "S1" in db.manager_employees(123) and "S2" not in db.manager_employees(123)
    assert {"S1", "S2"} <= set(db.manager_employees(999, is_owner=True))
    call(monkeypatch, "historical-call", values={"soft_comment": "синтетический результат"})
    for cid in ("A", "B"):
        db.update_fields(cid, {"responsible_soft": "S9"})
    today = date.today().isoformat()
    team = digest_data.window_team(mgr, db.manager_employees(123), today, today)
    assert "S1" in team and "S2" not in team
    activity = control.activity(mgr, team, {"from": today, "to": today})
    row = next(r for r in activity["employees"] if r["employee"] == "S1")
    assert row["calls"] == 1 and row["visits"] == 0
    event = next(e for e in activity["events"] if e["kind"] == "call")
    assert "Нет контакта" in event["title"] and "синтетический результат" in event["detail"]
    person = digest_data.person_data(mgr, today, "S1", "B", employees=team, by_soft=True)
    assert person["profile"] == "soft" and person["calls"]
    assert digest_data.person_data(mgr, today, "S2", "B", employees=team, by_soft=True) is None


def test_manager_call_card_api_checks_current_and_historical_owner(world, monkeypatch):
    call(monkeypatch, "card-history")
    today = date.today().isoformat()
    mgr = {"telegram_id": 999, "employees": ["S1"], "direction": ""}
    monkeypatch.setattr(web, "_manager_gate", lambda request: (999, mgr, ["S1"]))
    monkeypatch.setattr(web, "_manager_date", lambda request: (today, today, None, None))
    def read(rep, employee="S1"):
        req = make_mocked_request("GET", f"/api/manager/person?rep={rep}&employee={employee}&mode=soft")
        return json.loads(asyncio.run(web._manager_person(req)).body)
    assert read("B")["profile"] == "soft"
    db.update_fields("A", {"responsible_soft": "S9"})
    db.update_fields("B", {"responsible_soft": "S9"})
    assert read("B")["profile"] == "soft"
    with pytest.raises(aio_web.HTTPNotFound):
        read("C")


def test_profile_comes_from_columns_and_only_fio_books_are_selected(tmp_path, monkeypatch):
    folders = {}
    for name in ("Converted", "WithCopies", "Missing", "Regular"):
        folder = tmp_path / "City" / name
        folder.mkdir(parents=True)
        (folder / (name + ".xlsb")).write_bytes(b"test placeholder")
        folders[name.lower()] = [{"path": folder, "kind": "hard"}]
    (folders["withcopies"][0]["path"] / "WithCopies 02.10.2026.xlsb").write_bytes(b"old copy")
    (folders['missing'][0]['path'] / 'Missing.xlsb').rename(folders['missing'][0]['path'] / 'copy.xlsb')
    def header(path):
        return [["Номер КД / ID", "статус звонка софт", "Дата коммуникации"]] if "Converted" in path else [["Номер КД / ID", "Дата выезда"]]
    monkeypatch.setattr(en.header_map, "read_header_grid", header)
    by_emp = {name.lower(): {"employee": name, "profile": profile, "filled": 1, "rows": []} for name, profile in (("Converted", "soft"), ("WithCopies", "hard"), ("Missing", "hard"), ("Regular", "hard"))}
    matched, nofolder, errors = en.match_employees(by_emp, folders)
    assert {m["employee"] for m in matched} == {"Converted", "WithCopies", "Regular"}
    assert not nofolder and 'Missing' in errors[0] and 'нет .xlsb' in errors[0]
