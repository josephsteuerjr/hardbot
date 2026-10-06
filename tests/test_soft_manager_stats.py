"""Статистика звонков и охват руководителя на отдельной синтетической БД."""
import asyncio
import json
from io import BytesIO

import pytest
from aiohttp import web as aio_web
from aiohttp.test_utils import make_mocked_request

import db
import digest_data as dd
import web

DAY = "2026-10-06"
RANGE = {"from": DAY, "to": DAY, "days": 1, "preset": "today", "shift": 0, "label": "Сегодня"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "stats.db")
    db.init_db()
    with db.connect() as conn:
        for cid, client, hard, soft, dob in [
            ("A", "Клиент один", "H1", "S1", "1980-01-01"),
            ("B", "Клиент один", "H2", "S1", "1980-01-01"),
            ("C", "Клиент другой", "H1", "S2", "1990-01-01"),
            ("D", "Без даты", "H1", "S1", ""),
            ("E", "Без даты", "H1", "S1", ""),
        ]:
            conn.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft,birth_date,partner,debt_today,country,city) VALUES(?,?,?,?,?,?,?,?,?)",
                         (cid, client, hard, soft, dob, "Тест", "1000", "Узбекистан", "Ташкент"))
        conn.commit()
    db.reset_role_caches()
    yield
    db.reset_role_caches()


def call(cid="A", emp="S1", op="one", status="Нет контакта", **values):
    return db.save_call_result(cid, 100, emp, {"soft_call_status": status, **values},
                               "Статус: устаревший текст;", op_id=op, on_date=DAY,
                               saved_at=DAY + "T10:00:00")


def detail(emp="S1", mode=""):
    return dd.stats_employee_data({}, RANGE, emp, employees=["S1", "H1"], mode=mode)


def test_soft_only_defaults_to_calls_and_actual_portfolio(world):
    call(soft_comm_date="2026-10-12", soft_comment="Уточнил номер")
    data = detail()
    assert data["profile"] == "soft" and data["profiles"] == ["soft"]
    assert (data["calls"], data["persons"], data["portfolio"], data["contracts"]) == (1, 1, 3, 4)
    assert data["by_day"][0]["calls"] == 1
    assert data["cases"][0]["outcome"] == "Нет контакта"
    assert data["cases"][0]["comment"] == "Уточнил номер"
    assert data["cases"][0]["n"] == 2
    assert "visits" not in data and "geo" not in data["cases"][0]


def test_idempotent_retry_and_repeat_call_count_people_once(world):
    assert not call()["dedup"]
    assert call()["dedup"]
    call(op="two", status="Обещает оплатить")
    data = detail()
    assert (data["calls"], data["persons"]) == (2, 1)
    assert len({c["id"] for c in data["cases"]}) == 2


def test_unknown_birth_date_does_not_merge_unidentified_namesakes(world):
    call("D", op="d")
    call("E", op="e")
    assert detail()["persons"] == 2


def test_soft_scope_uses_exact_team_even_for_all_direction(world):
    call()
    call("C", "S2", "foreign")
    data = dd.stats_data({"direction": "all"}, RANGE, employees=["S1"])
    assert [r["employee"] for r in data["soft"]["employees"]] == ["S1"]
    assert data["soft"]["calls"] == 1
    assert data["totals"]["plan"] == 0
    assert data["employees"][0]["profile"] == "soft"


def test_manual_team_without_direction_has_soft_metrics(world):
    assert dd.soft_block({}, RANGE, employees=["S1"])["contracts"] == 4


def test_empty_soft_stats_still_show_portfolio(world):
    data = detail()
    assert data["calls"] == 0 and data["portfolio"] == 3 and data["cases"] == []


def test_reassigned_contract_keeps_employee_call_history(world):
    call()
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='S2' WHERE contract_id IN ('A','B')")
        conn.commit()
    data = detail("S1", "soft")
    assert data["calls"] == 1 and data["contracts"] == 2
    assert data["cases"][0]["rep"] == "A" and data["cases"][0]["n"] == 2
    card = dd.person_data({}, DAY, "S1", "A", employees=["S1"], by_soft=True)
    assert card["profile"] == "soft"


def test_mixed_employee_can_select_calls_without_losing_hard(world):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible='S1' WHERE contract_id='D'")
        conn.commit()
    assert detail()["profile"] == "hard"
    assert detail()["profiles"] == ["hard", "soft"]
    assert detail(mode="soft")["portfolio"] == 3
    assert detail(mode="hard")["portfolio"] == 1


def test_forecast_counts_person_once_and_payments_are_separate_registry_values(world):
    call(forecast="Ждём", forecast_sum="9000")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET last_payment_date=?,last_payment_sum='500' WHERE contract_id IN ('A','B')", (DAY,))
        conn.commit()
    data = detail()
    assert data["wait"] == 9000
    assert data["paid_sum"] == 1000 and data["paid_persons"] == 1
    assert data["wait_text"] == "9 000 ₽" and data["max"] == 10000


def test_mixed_currencies_never_become_single_numeric_total(world, monkeypatch):
    monkeypatch.setattr(dd.debt, "unit", lambda partners: "UZS" if "Другой" in partners else "₽")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET forecast='Ждём',forecast_sum='10',partner='Другой' WHERE contract_id='D'")
        conn.execute("UPDATE clients SET forecast='Ждём',forecast_sum='20' WHERE contract_id='E'")
        conn.commit()
    data = detail()
    assert data["wait"] is None
    assert {u["unit"] for u in data["wait_money"]} == {"UZS", "₽"}
    assert dd.soft_block({}, RANGE, employees=["S1"])["wait"] is None


def test_legacy_bad_json_falls_back_to_journal_text(world):
    call()
    with db.connect() as conn:
        conn.execute("UPDATE calls SET values_json='bad',contracts_json='{}',payload='Статус: Нет контакта;' ")
        conn.commit()
    assert detail()["statuses"] == {"Нет контакта": 1}


def test_detail_rejects_foreign_employee_before_reading_calls(world):
    assert dd.stats_employee_data({}, RANGE, "S2", employees=["S1"], mode="soft") is None


def test_endpoint_selects_soft_and_preserves_scope(world, monkeypatch):
    monkeypatch.setattr(web, "_manager_gate", lambda request: (123, {}, ["S1"]))
    monkeypatch.setattr(web, "_stats_range_q", lambda request: RANGE)
    monkeypatch.setattr(web, "_range_team", lambda mgr, team, rng: team)
    monkeypatch.setattr(web, "_stats_envelope", lambda *a: {})
    req = make_mocked_request("GET", "/api/manager/stats_employee?employee=S1&mode=soft")
    assert json.loads(asyncio.run(web._manager_stats_employee(req)).text)["profile"] == "soft"
    req = make_mocked_request("GET", "/api/manager/stats_employee?employee=S2&mode=soft")
    with pytest.raises(aio_web.HTTPNotFound):
        asyncio.run(web._manager_stats_employee(req))


def test_endpoint_rejects_invalid_contour(world, monkeypatch):
    monkeypatch.setattr(web, "_manager_gate", lambda request: (123, {}, ["S1"]))
    monkeypatch.setattr(web, "_stats_range_q", lambda request: RANGE)
    monkeypatch.setattr(web, "_range_team", lambda mgr, team, rng: team)
    with pytest.raises(aio_web.HTTPBadRequest):
        asyncio.run(web._manager_stats_employee(make_mocked_request("GET", "/api/manager/stats_employee?employee=S1&mode=other")))


def test_excel_has_soft_metrics_without_zero_hard_employee_rows(world):
    from openpyxl import load_workbook
    call(forecast="Ждём", forecast_sum="9000")
    wb = load_workbook(BytesIO(dd.stats_xlsx({}, RANGE, employees=["S1"])))
    soft = list(wb["Софт"].values)
    row = next(r for r in soft if r[0] == "S1")
    assert row[1] == 4 and row[3] == 1 and row[4] == 9000
    assert not any(r[0] == "S1" for r in wb["Сотрудники"].values)


def test_excel_keeps_mixed_currency_forecast_as_text(world, monkeypatch):
    from openpyxl import load_workbook
    monkeypatch.setattr(dd.debt, "unit", lambda partners: "UZS" if "Другой" in partners else "₽")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET forecast='Ждём',forecast_sum='10',partner='Другой' WHERE contract_id='D'")
        conn.execute("UPDATE clients SET forecast='Ждём',forecast_sum='20' WHERE contract_id='E'")
        conn.commit()
    wb = load_workbook(BytesIO(dd.stats_xlsx({}, RANGE, employees=["S1"])))
    row = next(r for r in wb["Софт"].values if r[0] == "S1")
    assert isinstance(row[4], str) and "UZS" in row[4] and "₽" in row[4]


def test_employee_and_manager_comment_view_is_newest_first_without_rewriting_text(world):
    for i, note in enumerate(["Первая\nпродолжение", "Последняя\nвторая строка"]):
        db.save_call_result("A", 100, "S1", {"soft_call_status": "Нет контакта", "soft_comment": note},
                            "Тест", op_id=str(i), on_date=DAY, saved_at=DAY+f"T10:0{i}:00",
                            append_comment=True)
    raw = db.get_client("A")["soft_comment"]
    assert raw.index("Первая") < raw.index("Последняя")
    person = web._person_full("A", "ru", by_soft=True)
    assert person["soft"]["comment"] == raw
    assert "Последняя\nвторая строка" in person["soft"]["comment_entries"][0]
    data = dd.person_data({}, DAY, "S1", "A", employees=["S1"], by_soft=True)
    assert data["comment_display"].index("Последняя") < data["comment_display"].index("Первая")
    assert db.get_client("A")["soft_comment"] == raw
