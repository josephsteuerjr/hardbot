"""Свод руководителя считается вне цикла событий.

Проход по портфелям 63 сотрудников занимает около шести секунд. Если считать
его в обработчике, на эти секунды встаёт весь сервер: выездник в поле в этот
момент не может ни открыть карточку, ни сохранить выезд. Ровно это уже было
с индексом приоритета 9 сентября, и лечится тем же — отдельным потоком
(соединение SQLite у него своё, см. db.connect).
"""
import asyncio
import json
import threading

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import digest_data as dd
import web


MGR = 560001
EMP = "Отсводов О."


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'OF-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute(
            "INSERT INTO clients (contract_id, client, responsible, birth_date, "
            "partner, debt_today, overall_result, country) VALUES (?,?,?,?,?,?,?,?)",
            ("OF-1", "Клиент Свод", EMP, "1990-01-01", "ЗК", "1 000",
             "Не обработан", "Таджикистан"))
        conn.commit()
    db.set_manager(MGR, "Рук", [], [EMP])
    db.reset_role_caches()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'OF-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    db.reset_role_caches()


def _where_did_it_run(monkeypatch, handler, path, attr, result):
    """Вернуть поток, в котором посчитался свод, и поток цикла событий."""
    _seed()
    monkeypatch.setattr(web, "_uid", lambda _r: MGR)
    seen = {}

    def fake(*args, **kwargs):
        seen["worker"] = threading.get_ident()
        return dict(result)

    monkeypatch.setattr(dd, attr, fake)

    async def go():
        seen["loop"] = threading.get_ident()
        resp = await handler(make_mocked_request("GET", path))
        return json.loads(resp.body.decode())

    payload = asyncio.run(go())
    return seen, payload


def test_team_summary_is_computed_off_the_event_loop(monkeypatch):
    seen, payload = _where_did_it_run(
        monkeypatch, web._manager_stats, "/api/manager/stats?preset=today",
        "stats_data",
        {"screen": "stats", "totals": {"visits": 0}, "employees": [],
         "by_day": [], "countries": [], "range": dd.stats_range("today")})
    assert payload["ok"]
    assert seen["worker"] != seen["loop"], "свод посчитан в цикле событий"


def test_employee_screen_is_computed_off_the_event_loop(monkeypatch):
    seen, _ = _where_did_it_run(
        monkeypatch, web._manager_stats_employee,
        f"/api/manager/stats_employee?preset=today&employee={EMP}",
        "stats_employee_data",
        {"screen": "emp", "cases": [], "by_day": [], "visits": 0,
         "range": dd.stats_range("today")})
    assert seen["worker"] != seen["loop"]


def test_cases_screen_is_computed_off_the_event_loop(monkeypatch):
    seen, _ = _where_did_it_run(
        monkeypatch, web._manager_cases,
        "/api/manager/cases?preset=today&metric=visits", "cases_data",
        {"screen": "cases", "metric": "visits", "cases": [], "total": 0,
         "range": dd.stats_range("today")})
    assert seen["worker"] != seen["loop"]
