"""Нагрузка и норма по расчёту (workload.py).

Норма «10 на всех» не различает портфель в трёх кварталах и портфель по
кишлакам за 60 км. Расчёт: сколько выездов «дорога + время у адреса»
помещается в рабочий день; без дорожного графа — прямая с поправкой.
"""
import asyncio
import json
from datetime import date, datetime, timedelta

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import evidence
import web as webmod
import workload

EMP_CLOSE, EMP_FAR = "Близков Б.", "Дальнев Д."
MGR = 574001
TODAY = date.today()
T = TODAY.isoformat()
DUSHANBE = (38.5598, 68.7870)


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'WL-%'")
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'WL-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM settings WHERE key IN ('workload_cache', "
                     "'visit_plan_by_employee', 'work_minutes', 'visit_minutes')")
        conn.execute("DELETE FROM employee_city WHERE employee IN (?,?)", (EMP_CLOSE, EMP_FAR))
        conn.execute("INSERT INTO employee_city (employee, city, updated_at) VALUES (?,?,?)",
                     (EMP_CLOSE, "Душанбе", T))
        conn.execute("INSERT INTO employee_city (employee, city, updated_at) VALUES (?,?,?)",
                     (EMP_FAR, "Душанбе", T))
        # близкий портфель: адреса в 1–3 км от центра; дальний — по 40–70 км
        for i in range(10):
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, partner, "
                "debt_today, geo_lat, geo_lon, geo_src, geo_precision, country) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"WL-C{i}", f"Близкий {i}", EMP_CLOSE, "1990-01-01", "ЗК", "1 000",
                 DUSHANBE[0] + 0.01 * (i % 3), DUSHANBE[1] + 0.01 * (i % 4), "osm", "street",
                 "Таджикистан"))
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, partner, "
                "debt_today, geo_lat, geo_lon, geo_src, geo_precision, country) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"WL-F{i}", f"Дальний {i}", EMP_FAR, "1990-01-01", "ЗК", "1 000",
                 DUSHANBE[0] + 0.4 + 0.05 * (i % 3), DUSHANBE[1] + 0.3 * (i % 2), "osm", "village",
                 "Таджикистан"))
        conn.commit()
    db.set_manager(MGR, "Рук", [], [EMP_CLOSE, EMP_FAR])
    db.reset_role_caches()


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    _seed()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'WL-%'")
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'WL-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM settings WHERE key IN ('workload_cache', "
                     "'visit_plan_by_employee', 'work_minutes', 'visit_minutes')")
        conn.execute("DELETE FROM employee_city WHERE employee IN (?,?)", (EMP_CLOSE, EMP_FAR))
        conn.commit()
    db.reset_role_caches()


def test_suggested_norm_is_bounded_and_monotonic():
    assert workload.suggested_norm(5, 20, 480) == 16, "лёгкий портфель — потолок"
    assert workload.suggested_norm(90, 20, 480) == 4, "три часа дороги — пол"
    assert workload.suggested_norm(20, 20, 480) == 10
    assert workload.suggested_norm(None, 20) is None
    assert workload.suggested_norm(20, 20, 480) > workload.suggested_norm(40, 20, 480)


def test_far_portfolio_gets_a_smaller_norm_than_a_close_one():
    close = workload.employee_row(EMP_CLOSE, T, T, backends=[], vroom_url="")
    far = workload.employee_row(EMP_FAR, T, T, backends=[], vroom_url="")
    assert close["travel_src"] == "straight", "без графа — прямая с поправкой"
    assert close["radius_km"] < 5 and far["radius_km"] > 30
    assert close["norm_suggested"] > far["norm_suggested"]
    assert far["far_share"] > 0.5 and close["far_share"] == 0
    assert close["norm_now"] == db.visit_plan_per_day()


def test_travel_is_cached_per_day():
    workload.employee_row(EMP_CLOSE, T, T, backends=[], vroom_url="")
    cache = json.loads(db.get_setting("workload_cache"))
    assert cache[EMP_CLOSE]["day"] == T and cache[EMP_CLOSE]["src"] == "straight"


def test_measured_time_at_address_replaces_the_office_default():
    now = datetime.now()
    with db.connect() as conn:
        for i in range(6):
            ev = {"level": "confirmed", "near_min": 25 + i, "near_first": "10:00"}
            conn.execute(
                "INSERT INTO visits (contract_id, agent_id, created_at, payload, saved_at, "
                "received_at, visit_lat, visit_lon, fill_seconds, evidence_level, evidence) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"WL-C{i}", 1, T, "Выезд (миниапп). Статус: В работе; Договорённость: ; ",
                 (now - timedelta(minutes=40 * (6 - i))).isoformat(timespec="seconds"),
                 (now - timedelta(minutes=40 * (6 - i))).isoformat(timespec="seconds"),
                 DUSHANBE[0] + 0.01 * (i % 3), DUSHANBE[1] + 0.01 * (i % 4), 300,
                 "confirmed", evidence.dumps(ev)))
        conn.commit()
    row = workload.employee_row(EMP_CLOSE, T, T, backends=[], vroom_url="")
    assert row["visit_min_measured"] and row["visit_min"] == 27.5
    assert row["visits_per_day"] == 6 and row["field_days"] == 1


def test_day_rows_split_the_day_into_address_travel_and_idle():
    days = workload.day_rows(EMP_CLOSE, T, T, backends=[])
    assert len(days) == 1
    d = days[0]
    assert d["visits"] == 6 and d["first"] and d["last"]
    assert d["address_min"] == 25 + 26 + 27 + 28 + 29 + 30
    assert d["travel_min"] is not None and d["km"] is not None
    assert d["idle_min"] is not None and d["idle_min"] >= 0
    assert d["span_min"] >= d["address_min"] - 30


def test_workload_endpoint_serves_team_and_one_employee(monkeypatch):
    monkeypatch.setattr(webmod, "_uid", lambda _r: MGR)
    monkeypatch.setattr(webmod, "_routing_backends", lambda: [])
    req = make_mocked_request("GET", "/api/manager/workload?preset=today")
    data = json.loads(asyncio.run(webmod._manager_workload(req)).body.decode())
    assert data["screen"] == "workload" and len(data["rows"]) == 2
    assert data["totals"]["norm_suggested"] == sum(r["norm_suggested"] for r in data["rows"])
    req = make_mocked_request("GET", f"/api/manager/workload?preset=today&employee={EMP_CLOSE}")
    one = json.loads(asyncio.run(webmod._manager_workload(req)).body.decode())
    assert one["employee"] == EMP_CLOSE and one["days"][0]["visits"] == 6
    req = make_mocked_request("GET", "/api/manager/workload?preset=today&employee=Чужой")
    with pytest.raises(Exception):
        asyncio.run(webmod._manager_workload(req))


def test_plan_screen_carries_the_suggested_norm(monkeypatch):
    monkeypatch.setattr(webmod, "_uid", lambda _r: MGR)
    monkeypatch.setattr(webmod, "_routing_backends", lambda: [])
    req = make_mocked_request("GET", "/api/manager/plan")
    data = json.loads(asyncio.run(webmod._manager_plan(req)).body.decode())
    by = {e["employee"]: e for e in data["employees"]}
    assert by[EMP_CLOSE]["suggested"] > by[EMP_FAR]["suggested"]
    assert "дорога" in by[EMP_FAR]["why"]
