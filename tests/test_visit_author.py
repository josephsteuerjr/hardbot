"""Выезд принадлежит тому, кто выезжал, а не тому, за кем договор сейчас.

До 24.09.2026 журнал визитов не знал автора: выезд приписывался текущему
ответственному договора. В конце месяца портфели перезакрепляют, и прошлое
переписывалось молча — выезды переезжали к новому владельцу, а у сотрудника,
с которого сняли всё, пропадали из статистики вместе с ним самим. Здесь же —
состав команды за окно, офис при разном написании ФИО и починка старых строк.
"""
import asyncio
import json
from datetime import date, timedelta

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import digest_data as dd
import web

MGR = 561901
OWNER_A, OWNER_B = "Авторов Первый", "Новиков Владелец"
TODAY = date.today()
T = TODAY.isoformat()
D2 = (TODAY - timedelta(days=2)).isoformat()


def _payload(status):
    return f"Выезд (миниапп). Статус: {status}; Договорённость: ; "


def _clean():
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'VA-%'")
        conn.execute("DELETE FROM visit_presence WHERE contract_id LIKE 'VA-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'VA-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM agent_bind WHERE telegram_id IN (561911, 561912)")
        conn.execute("DELETE FROM employee_city WHERE employee LIKE 'Авторов%' "
                     "OR employee LIKE 'авторов%' OR employee LIKE 'Новиков%'")
        conn.commit()
    db.reset_role_caches()


def _seed():
    db.init_db()
    _clean()
    with db.connect() as conn:
        for cid, client, emp in (("VA-1", "Должник Один", OWNER_A),
                                 ("VA-2", "Должник Два", OWNER_A),
                                 ("VA-3", "Должник Три", OWNER_B)):
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "partner, debt_today, overall_result, country, city) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, client, emp, "1990-01-01", "ЗК", "50 000", "Не обработан",
                 "Таджикистан", "Душанбе"))
        conn.commit()
    # Выезд A два дня назад и сегодня — записаны так, как пишет их _save.
    db.log_visit("VA-1", 561911, _payload("Нет контакта"), on_date=D2,
                 employee=OWNER_A)
    db.log_visit("VA-1", 561911, _payload("Обещает оплатить"), on_date=T,
                 employee=OWNER_A)
    db.record_bot_visit("VA-1", {"call_status": "Обещает оплатить"}, T)
    db.reset_role_caches()


def _reassign_all_of_a_to_b():
    """Конец месяца: весь портфель A перезакреплён на B."""
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible=? WHERE responsible=?",
                     (OWNER_B, OWNER_A))
        conn.commit()
    db.reset_role_caches()


@pytest.fixture(autouse=True)
def _cleanup():
    _seed()
    yield
    _clean()


def _rng(d_from, d_to):
    return dd.stats_range(None, 0, d_from, d_to, today=TODAY)


# --- автор выезда ------------------------------------------------------------
def test_visits_stay_with_their_author_after_reassignment():
    _reassign_all_of_a_to_b()
    assert len(db.visits_for_employee(OWNER_A, D2, T)) == 2
    assert db.visits_for_employee(OWNER_B, D2, T) == [], \
        "новый владелец чужих выездов не получает"
    a = dd._employee_stats(OWNER_A, D2, T)
    b = dd._employee_stats(OWNER_B, D2, T)
    assert (a["visits"], a["persons"], a["portfolio"]) == (2, 1, 0)
    assert b["visits"] == 0 and b["portfolio"] == 3


def test_day_list_follows_the_author_too():
    _reassign_all_of_a_to_b()
    assert [p["rep"] for p in db.persons_processed(OWNER_A, T)] == ["VA-1"]
    assert db.persons_processed(OWNER_B, T) == [], \
        "снимок того дня — выезд A, новому владельцу его в сводку не кладём"


def test_row_without_author_still_falls_back_to_current_owner():
    """Строки, которым автора найти не удалось, считаются как раньше."""
    with db.connect() as conn:
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES ('VA-3', 1, ?, ?)", (T, _payload("Оплачен")))
        conn.commit()
    assert [v["contract_id"] for v in db.visits_for_employee(OWNER_B, T, T)] == ["VA-3"]


# --- починка старых строк ----------------------------------------------------
def test_backfill_prefers_presence_then_binding_then_owner():
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'VA-%'")
        # 1) сессия карточки знает ФИО точно
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload, "
                     "session_id) VALUES ('VA-3', 561912, ?, 'x', 'sess-va')", (T,))
        conn.execute("INSERT INTO visit_presence (session_id, contract_id, employee, "
                     "telegram_id, ts, kind) VALUES ('sess-va', 'VA-3', ?, 561912, ?, "
                     "'save')", (OWNER_A, T + "T10:00:00"))
        # 2) без сессии — по привязке телефона
        conn.execute("INSERT INTO agent_bind (telegram_id, employee, kind, approved) "
                     "VALUES (561911, ?, 'agent', 1)", (OWNER_A,))
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES ('VA-3', 561911, ?, 'y')", (T,))
        # 3) ни сессии, ни привязки — текущий ответственный
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES ('VA-3', 999, ?, 'z')", (T,))
        counts = db._backfill_visit_authors(conn)
        got = [r[0] for r in conn.execute(
            "SELECT employee FROM visits WHERE contract_id='VA-3' ORDER BY id")]
        conn.rollback()
    assert got == [OWNER_A, OWNER_A, OWNER_B]
    assert counts["presence"] >= 1 and counts["binding"] >= 1


def test_manager_phone_is_not_taken_as_the_author():
    """Руководитель закрывал визит ОТ ИМЕНИ сотрудника — его привязка не автор."""
    db.set_manager(MGR, "Рук", [], [OWNER_B])
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'VA-%'")
        conn.execute("INSERT INTO agent_bind (telegram_id, employee, kind, approved) "
                     "VALUES (?, ?, 'agent', 1)", (MGR, OWNER_A))
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES ('VA-3', ?, ?, 'x')", (MGR, T))
        db._backfill_visit_authors(conn)
        got = conn.execute("SELECT employee FROM visits WHERE contract_id='VA-3'"
                           ).fetchone()[0]
        conn.execute("DELETE FROM agent_bind WHERE telegram_id=?", (MGR,))
        conn.commit()
    assert got == OWNER_B


# --- состав команды ----------------------------------------------------------
def test_employee_without_contracts_leaves_the_team_but_not_his_window():
    db.set_manager(MGR, "Рук", [], [OWNER_A, OWNER_B])
    mgr = db.get_manager(MGR)
    far = (TODAY - timedelta(days=40)).isoformat()
    assert dd.window_team(mgr, dd.sorted_employees(mgr), far, far) == \
        sorted([OWNER_A, OWNER_B])
    _reassign_all_of_a_to_b()
    team = dd.sorted_employees(mgr)
    assert team == sorted([OWNER_A, OWNER_B]), \
        "состав ДОСТУПА не режем: по нему ищут фото откреплённых договоров"
    assert dd.window_team(mgr, team, far, far) == [OWNER_B], \
        "без договоров и без выездов в окне — не в сводке"
    assert dd.window_team(mgr, team, D2, T) == sorted([OWNER_A, OWNER_B]), \
        "в окне, где он выезжал, он остаётся со своими выездами"


def test_team_is_shown_in_registry_spelling():
    """Поимённый список набран с другим регистром — сводка всё равно видит
    портфель: выезды и договоры живут под написанием реестра."""
    db.set_manager(MGR, "Рук", [], ["авторов  первый"])
    mgr = db.get_manager(MGR)
    assert db.manager_employees(MGR) == ["авторов  первый"], \
        "доступ написание не меняет — иначе стёрлась бы оффлайн-очередь"
    assert dd.window_team(mgr, dd.sorted_employees(mgr), T, T) == [OWNER_A]


def test_window_team_respects_the_managers_scope():
    """Чужой бывший сотрудник в команду не попадает."""
    db.set_manager(MGR, "Рук", [], [OWNER_B])
    _reassign_all_of_a_to_b()
    mgr = db.get_manager(MGR)
    assert dd.window_team(mgr, dd.sorted_employees(mgr), D2, T) == [OWNER_B]


def test_direction_manager_keeps_his_former_employee_by_visit_country():
    db.set_manager(MGR, "Рук РТ", [], [], direction="tj")
    _reassign_all_of_a_to_b()
    mgr = db.get_manager(MGR)
    team = dd.window_team(mgr, dd.sorted_employees(mgr), D2, T)
    assert OWNER_A in team
    stats = dd.stats_data(mgr, _rng(D2, T), employees=team)
    row = next(r for r in stats["employees"] if r["employee"] == OWNER_A)
    assert row["visits"] == 2 and row["country"] == "tj"


def test_stats_api_shows_the_former_employee_and_his_card(monkeypatch):
    db.set_manager(MGR, "Рук", [], [OWNER_A, OWNER_B])
    _reassign_all_of_a_to_b()
    monkeypatch.setattr(web, "_uid", lambda _r: MGR)
    req = make_mocked_request("GET", f"/api/manager/stats?from={D2}&to={T}")
    j = json.loads(asyncio.run(web._manager_stats(req)).body.decode())
    names = {r["employee"]: r["visits"] for r in j["employees"]}
    assert names.get(OWNER_A) == 2
    req = make_mocked_request(
        "GET", f"/api/manager/person?date={T}&employee={OWNER_A}&rep=VA-1")
    j = json.loads(asyncio.run(web._manager_person(req)).body.decode())
    assert j["rep"] == "VA-1", "карточка по собственному выезду открывается"


# --- офис при разном написании ФИО --------------------------------------------
def test_office_matches_despite_spelling():
    with db.connect() as conn:
        conn.execute("INSERT INTO employee_city (employee, city) VALUES (?, ?)",
                     ("авторов  первый", "Худжанд"))
        conn.commit()
    assert db.office_of(OWNER_A) == "Худжанд"
    assert dd._employee_stats(OWNER_A, T, T)["office"] == "Худжанд"
    db.set_manager(MGR, "Рук", [], [], office_cities=["Худжанд"])
    mgr = db.get_manager(MGR)
    team = dd.sorted_employees(mgr)
    assert OWNER_A in team, "в команду офиса он попадает по ключу ФИО"
    stats = dd.stats_data(mgr, _rng(T, T), employees=[OWNER_A])
    assert [o["code"] for o in stats["offices"]] == ["Худжанд"], \
        "человек из команды офиса не должен висеть в «Без офиса»"


def test_set_office_writes_registry_spelling_and_drops_variants():
    with db.connect() as conn:
        conn.execute("INSERT INTO employee_city (employee, city) VALUES (?, ?)",
                     ("авторов первый", "Душанбе"))
        conn.commit()
    db.set_employee_city("АВТОРОВ ПЕРВЫЙ", "Худжанд")
    assert {e: c for e, c in db.employee_cities().items()
            if db._name_key(e) == db._name_key(OWNER_A)} == {OWNER_A: "Худжанд"}
