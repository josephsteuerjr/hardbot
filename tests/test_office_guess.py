"""Офис новым людям при импорте — по географии их должников.

Офис из реестра напрямую не выводится, и новый человек неделями висел у
руководителя в «Без офиса». Правило: офис человека — это офис коллег, которые
работают там же. Проверено на живой базе 24.09.2026 (40 из 40 верно, спорные
не тронуты); здесь — те же границы: перевес обязателен, ручное решение
неприкосновенно, курьеры идут в общий офис курьеров, папка уточняет догадку.
"""
import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web

DUSH, KHUJ = (10.01, 10.01), (20.01, 20.01)      # ячейки, которых нет в других тестах
PEOPLE = ("Геодушанбинов Д", "Геохуджандов Х", "Геоновичков Н", "Геосмешанов С",
          "Геомалоточкин М", "Георучной Р", "К-Геокурьер Один", "К-Геокурьер Два")


def _clean():
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'OG-%'")
        conn.execute(f"DELETE FROM employee_city WHERE employee IN "
                     f"({','.join('?' * len(PEOPLE))})", PEOPLE)
        conn.commit()
    db.reset_role_caches()


def _clients(emp, point, n, start):
    with db.connect() as conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "geo_lat, geo_lon, country) VALUES (?,?,?,?,?,?,?)",
                (f"OG-{start + i}", f"Должник {start + i}", emp, "1990-01-01",
                 point[0] if point else None, point[1] if point else None,
                 "Таджикистан"))
        conn.commit()


@pytest.fixture(autouse=True)
def _seed():
    db.init_db()
    _clean()
    _clients("Геодушанбинов Д", DUSH, 12, 0)
    _clients("Геохуджандов Х", KHUJ, 12, 100)
    _clients("Геоновичков Н", DUSH, 12, 200)
    _clients("Геосмешанов С", DUSH, 6, 300)
    _clients("Геосмешанов С", KHUJ, 6, 310)
    _clients("Геомалоточкин М", DUSH, 3, 400)
    _clients("Георучной Р", DUSH, 12, 500)
    _clients("К-Геокурьер Один", None, 2, 600)
    _clients("К-Геокурьер Два", None, 2, 700)
    db.set_employee_city("Геодушанбинов Д", "Душанбе")
    db.set_employee_city("Геохуджандов Х", "Худжанд")
    db.set_employee_city("Георучной Р", "Самарканд")
    db.set_employee_city("К-Геокурьер Один", "Ташкент")
    db.reset_role_caches()
    yield
    _clean()


def test_newcomer_gets_the_office_of_colleagues_working_there():
    got = db.guess_offices(apply=True)
    assert got["filled"].get("Геоновичков Н") == "Душанбе"
    assert db.office_of("Геоновичков Н") == "Душанбе"
    assert db.office_sources()["Геоновичков Н"] == "auto"


def test_no_clear_majority_or_too_few_points_is_left_to_a_human():
    got = db.guess_offices(apply=True)
    assert "Геосмешанов С" in got["undecided"], "поровну в двух офисах — не угадываем"
    assert "Геомалоточкин М" in got["undecided"], "три точки — не основание"
    assert db.office_of("Геосмешанов С") == "" and db.office_of("Геомалоточкин М") == ""


def test_human_decision_is_never_overwritten():
    db.guess_offices(apply=True)
    assert db.office_of("Георучной Р") == "Самарканд"
    assert db.office_sources()["Георучной Р"] == "manual"


def test_courier_goes_to_the_couriers_office():
    got = db.guess_offices(apply=True)
    assert got["filled"].get("К-Геокурьер Два") == "Ташкент"


def test_dry_run_writes_nothing():
    got = db.guess_offices(apply=False)
    assert got["filled"].get("Геоновичков Н") == "Душанбе"
    assert db.office_of("Геоновичков Н") == ""


def _cities(method, body, monkeypatch):
    monkeypatch.setattr(web, "_admin", lambda _r: None)
    req = make_mocked_request(method, "/admin/employee_cities.json")
    if body is not None:
        async def _json():
            return body
        req.json = _json
    return json.loads(asyncio.run(web._admin_employee_cities(req)).body.decode())


def test_api_marks_guesses_and_folder_can_refine_them(monkeypatch):
    db.guess_offices(apply=True)
    state = _cities("GET", None, monkeypatch)
    assert "Геоновичков Н" in state["auto"]
    assert "Георучной Р" not in state["auto"], "ручной офис — не догадка"
    # Пульт по папке портфеля уточняет догадку — источник становится folder.
    _cities("POST", {"cities": {"Геоновичков Н": "Худжанд"}, "source": "folder"},
            monkeypatch)
    assert db.office_of("Геоновичков Н") == "Худжанд"
    assert db.office_sources()["Геоновичков Н"] == "folder"
    assert "Геоновичков Н" not in _cities("GET", None, monkeypatch)["auto"]


def test_import_toast_names_who_got_which_office():
    from pult.core import registry_import as ri
    text, err = ri.apply_toast({"inserted": 1, "updated": 2,
                                "offices_guessed": {"Геоновичков Н": "Душанбе"}})
    assert not err and "Геоновичков Н → Душанбе" in text
