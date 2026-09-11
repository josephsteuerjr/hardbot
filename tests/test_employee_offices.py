"""Офис сотрудника: живой состав руководителя по городу.

Эта серверная часть однажды уже пропала: код был выложен на прод 10.09.2026, а
в репозиторий не попал — пульт звал `/admin/employee_cities.json`, тесты пульта
её мокали, и потеря всплыла только когда очередная выкладка увезла прод без
неё. Тест закрепляет и ручку, и оба места, где охват обязан работать: портфели
(manager_employees) И адресация карточек по визитам (managers_by_employee_index) —
второе легко забыть, и тогда руководитель видит людей, но не получает отбивок.
"""
import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web

MGR = 564001
CITY = "Худжанд"
EMP_HERE, EMP_THERE = "Офисов О.", "Чужгородов Ч."


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'OF-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM employee_city WHERE employee IN (?,?)",
                     (EMP_HERE, EMP_THERE))
        for cid, emp in (("OF-1", EMP_HERE), ("OF-2", EMP_THERE)):
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "partner, debt_today, overall_result, country) VALUES (?,?,?,?,?,?,?,?)",
                (cid, f"Клиент {cid}", emp, "1990-01-01", "ЗК", "1 000",
                 "Не обработан", "Таджикистан"))
        conn.commit()
    db.reset_role_caches()


@pytest.fixture(autouse=True)
def _cleanup():
    _seed()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'OF-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM employee_city WHERE employee IN (?,?)",
                     (EMP_HERE, EMP_THERE))
        conn.commit()
    db.reset_role_caches()


def _call(method, body, monkeypatch):
    monkeypatch.setattr(web, "_admin", lambda _r: None)
    req = make_mocked_request(method, "/admin/employee_cities.json")
    if body is not None:
        async def _json():
            return body
        req.json = _json
    resp = asyncio.run(web._admin_employee_cities(req))
    return json.loads(resp.body.decode())


# --- хранение ----------------------------------------------------------------
def test_office_is_stored_and_cleared():
    db.set_employee_city(EMP_HERE, CITY)
    assert db.employee_cities().get(EMP_HERE) == CITY
    db.set_employee_city(EMP_HERE, "")
    assert EMP_HERE not in db.employee_cities(), "пустой город снимает закрепление"


def test_employees_by_cities_is_case_insensitive():
    db.set_employee_city(EMP_HERE, CITY)
    assert db.employees_by_cities(["худжанд"]) == [EMP_HERE]
    assert db.employees_by_cities([]) == []
    assert db.employees_by_cities(["Душанбе"]) == []


# --- охват -------------------------------------------------------------------
def test_manager_of_an_office_sees_its_staff():
    db.set_employee_city(EMP_HERE, CITY)
    db.set_manager(MGR, "Старший", [], [], office_cities=[CITY])
    assert db.manager_employees(MGR) == [EMP_HERE]


def test_office_staff_also_receives_visit_cards():
    """Дыра, уже случавшаяся с направленцами: портфели видны, отбивки не идут."""
    db.set_employee_city(EMP_HERE, CITY)
    db.set_manager(MGR, "Старший", [], [], office_cities=[CITY])
    index = db.managers_by_employee_index()
    assert any(MGR == m["telegram_id"]
               for m in index.get(db._name_key(EMP_HERE), []))


def test_saving_a_manager_without_offices_keeps_them():
    """Старый пульт поля не шлёт — сохранение имени не должно снимать охват."""
    db.set_employee_city(EMP_HERE, CITY)
    db.set_manager(MGR, "Старший", [], [], office_cities=[CITY])
    db.set_manager(MGR, "Старший переименованный", [], [])
    assert db.get_manager(MGR)["office_cities"] == [CITY]
    db.set_manager(MGR, "Старший", [], [], office_cities=[])
    assert db.get_manager(MGR)["office_cities"] == [], "пустой список снимает охват"


# --- ручка -------------------------------------------------------------------
def test_handler_reads_and_writes(monkeypatch):
    out = _call("POST", {"cities": {EMP_HERE: CITY}}, monkeypatch)
    assert out["ok"] and out["cities"].get(EMP_HERE) == CITY
    assert CITY in out["known"], "подсказка городов для пульта"
    again = _call("GET", None, monkeypatch)
    assert again["cities"].get(EMP_HERE) == CITY


def test_handler_rejects_garbage(monkeypatch):
    with pytest.raises(web.web.HTTPBadRequest):
        _call("POST", {"cities": "Худжанд"}, monkeypatch)


def test_route_is_registered():
    """Ручка уже терялась один раз — маршрут закрепляем явно."""
    body = (web.__file__ and open(web.__file__, encoding="utf-8").read())
    assert 'app.router.add_get("/admin/employee_cities.json"' in body
    assert 'app.router.add_post("/admin/employee_cities.json"' in body
