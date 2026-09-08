"""JSON-ручки вкладки руководителя (/api/manager/*): гейт по роли, адресация
по ФИО+rep, серверные даты, единый гео-вердикт, аудит с составом.

Все перечисленные отказы — ровно то, куда бьёт адверсарий: чужой токен, чужой
сотрудник, чужой договор, клиентская дата, ADMIN-байпас, след в аудите.
"""
import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web
from config import ADMIN_ID

MGR = 556001
STRANGER = 556002
OWNER = ADMIN_ID or 556099
EMP_A, EMP_B, EMP_FOREIGN = "Апиев А.", "Бпиев Б.", "Чпужой Ч."
TODAY = db.date.today().isoformat()


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM managers WHERE telegram_id IN (?,?)",
                     (MGR, STRANGER))
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'MD-%'")
        conn.execute("DELETE FROM settings WHERE key LIKE 'acting_employee:%'")
        conn.commit()
    for i, emp in enumerate((EMP_A, EMP_A, EMP_B, EMP_FOREIGN)):
        row = {"contract_id": f"MD-{i}", "client": f"Клиент {i}",
               "responsible": emp, "address": f"адрес {i}", "partner": "ЗК",
               "debt_today": "500 000", "overall_result": "Оплачен"}
        with db.connect() as conn:
            keys = ", ".join(f'"{k}"' for k in row)
            conn.execute(f"INSERT OR REPLACE INTO clients ({keys}) VALUES "
                         f"({', '.join('?' * len(row))})", tuple(row.values()))
            conn.commit()
    # доказанные визиты за сегодня (снимок сильнее реестра)
    db.record_bot_visit("MD-0", {"call_status": "Нет контакта"}, TODAY)
    db.record_bot_visit("MD-2", {"call_status": "Рассрочка"}, TODAY)
    db.set_manager(MGR, "Рук", [], [EMP_A, EMP_B])


def _call(handler, uid, qs="", monkeypatch=None):
    assert monkeypatch is not None
    monkeypatch.setattr(web, "_uid", lambda _r: uid)
    req = make_mocked_request("GET", f"/api/manager/x{qs}")
    resp = asyncio.run(handler(req))
    return json.loads(resp.body.decode()), resp


def test_every_endpoint_needs_auth(monkeypatch):
    _seed()
    for h in (web._manager_summary, web._manager_employee_view,
              web._manager_person, web._manager_period,
              web._manager_promises, web._manager_app_map_data):
        with pytest.raises(web.web.HTTPUnauthorized):
            _call(h, None, monkeypatch=monkeypatch)


def test_stranger_is_refused_403(monkeypatch):
    _seed()
    for h in (web._manager_summary, web._manager_promises):
        with pytest.raises(web.web.HTTPForbidden):
            _call(h, STRANGER, monkeypatch=monkeypatch)


def test_gate_does_not_require_or_write_acting(monkeypatch):
    _seed()
    j, _ = _call(web._manager_summary, MGR, monkeypatch=monkeypatch)
    assert j["ok"] is True
    # открытие вкладки НЕ записало выбор портфеля
    assert db.get_setting(f"acting_employee:{MGR}") is None


def test_gate_admits_by_role_even_with_empty_team(monkeypatch):
    _seed()
    db.set_manager(556003, "Пустой направленец", [], [], direction="tj")
    j, _ = _call(web._manager_summary, 556003, monkeypatch=monkeypatch)
    assert j["ok"] is True and j["team"] == []    # пустой экран, не 403
    db.delete_manager(556003)


def test_employee_outside_scope_is_404(monkeypatch):
    _seed()
    with pytest.raises(web.web.HTTPNotFound):
        _call(web._manager_employee_view, MGR,
              qs=f"?employee={EMP_FOREIGN}", monkeypatch=monkeypatch)


def test_person_of_another_employee_is_404(monkeypatch):
    _seed()
    # rep чужого сотрудника — 404 даже для владельца (без ADMIN-байпаса)
    with pytest.raises(web.web.HTTPNotFound):
        _call(web._manager_person, MGR,
              qs=f"?employee={EMP_A}&rep=MD-3", monkeypatch=monkeypatch)


def test_owner_gets_a_synthetic_team(monkeypatch):
    _seed()
    j, _ = _call(web._manager_summary, OWNER, monkeypatch=monkeypatch)
    assert EMP_FOREIGN in j["team"]
    assert j["totals"]["persons"] >= 2            # реальные визиты, не нули
    assert j["manager"]["isOwner"] == (OWNER == ADMIN_ID)


def test_direction_manager_team_is_live(monkeypatch):
    _seed()
    with db.connect() as conn:
        conn.execute("UPDATE clients SET country='Узбекистан' "
                     "WHERE contract_id='MD-0'")
        conn.commit()
    db.set_manager(556004, "Направленец", [], [], direction="uz")
    db.reset_role_caches()
    j, _ = _call(web._manager_summary, 556004, monkeypatch=monkeypatch)
    assert EMP_A in j["team"]
    db.delete_manager(556004)


def test_addressing_is_by_name_and_rep(monkeypatch):
    _seed()
    j, _ = _call(web._manager_summary, MGR, monkeypatch=monkeypatch)
    for r in j["display"]:
        assert "ei" not in r and "ci" not in r
    j2, _ = _call(web._manager_person, MGR,
                  qs=f"?employee={EMP_A}&rep=MD-0", monkeypatch=monkeypatch)
    assert j2["rep"] == "MD-0" and j2["outcome"] == "Нет контакта"


def test_server_owns_the_date(monkeypatch):
    _seed()
    j, _ = _call(web._manager_summary, MGR, monkeypatch=monkeypatch)
    assert j["date"] == j["today"] and j["next_date"] is None
    j2, _ = _call(web._manager_summary, MGR, qs="?date=2026-08-01",
                  monkeypatch=monkeypatch)
    assert j2["date"] == "2026-08-01" and j2["next_date"] == "2026-08-02"
    with pytest.raises(web.web.HTTPBadRequest):
        _call(web._manager_summary, MGR, qs="?date=мусор", monkeypatch=monkeypatch)


def test_snapshot_beats_registry_in_api(monkeypatch):
    _seed()
    j, _ = _call(web._manager_employee_view, MGR, qs=f"?employee={EMP_A}",
                 monkeypatch=monkeypatch)
    case = next(c for c in j["cases"] if c["rep"] == "MD-0")
    assert case["outcome"] == "Нет контакта"      # не офисный «Оплачен»


def test_no_telegram_id_in_responses(monkeypatch):
    _seed()
    db.bind_agent(556077, EMP_A)
    j, _ = _call(web._manager_employee_view, MGR, qs=f"?employee={EMP_A}",
                 monkeypatch=monkeypatch)
    assert "556077" not in json.dumps(j)
    assert "telegram_id" not in json.dumps(j["activity"] or {})


def test_geo_verdict_uses_saved_geo_far(monkeypatch):
    _seed()
    db.log_visit("MD-0", 1, "p", lat=1.0, lon=2.0, dist_m=5000, geo_far=0)
    j, _ = _call(web._manager_person, MGR, qs=f"?employee={EMP_A}&rep=MD-0",
                 monkeypatch=monkeypatch)
    assert j["geo"]["far"] is False and j["geo"]["source"] == "stored"


def test_period_days_are_clamped(monkeypatch):
    _seed()
    j, _ = _call(web._manager_period, MGR, qs="?days=999", monkeypatch=monkeypatch)
    assert j["days"] == 7
    j2, _ = _call(web._manager_period, MGR, qs="?days=30", monkeypatch=monkeypatch)
    assert j2["days"] == 30


def test_period_note_appears_when_window_is_set(monkeypatch):
    _seed()
    db.set_setting("period_from", "2026-08-01")
    j, _ = _call(web._manager_summary, MGR, monkeypatch=monkeypatch)
    assert j["period_note"]
    db.set_setting("period_from", "")
    j2, _ = _call(web._manager_summary, MGR, monkeypatch=monkeypatch)
    assert j2["period_note"] == ""


def test_audit_is_written_for_every_screen(monkeypatch):
    _seed()
    _call(web._manager_summary, MGR, monkeypatch=monkeypatch)
    _call(web._manager_employee_view, MGR, qs=f"?employee={EMP_A}",
          monkeypatch=monkeypatch)
    _call(web._manager_person, MGR, qs=f"?employee={EMP_A}&rep=MD-0",
          monkeypatch=monkeypatch)
    _call(web._manager_promises, MGR, monkeypatch=monkeypatch)
    tail = [dict(r) for r in db.audit_tail(50)]
    actions = {r["action"] for r in tail}
    assert "digest_view" in actions and "digest_view_card" in actions
    emp_row = next(r for r in tail if (r["detail"] or "").startswith("employee "))
    assert "MD-0" in emp_row["detail"]            # состав показанных должников
    card = next(r for r in tail if r["action"] == "digest_view_card")
    assert card["target"] == "MD-0"


def test_app_map_needs_no_signature_and_returns_geo_far(monkeypatch):
    _seed()
    db.log_visit("MD-0", 1, "p", lat=1.0, lon=2.0, dist_m=100, geo_far=0)
    j, resp = _call(web._manager_app_map_data, MGR, qs=f"?employee={EMP_A}",
                    monkeypatch=monkeypatch)
    assert j["employee"] == EMP_A and "alert_m" in j
    assert all("geo_far" in v for v in j["visits"])
    assert resp.headers["Cache-Control"] == "no-store"


# --- личные рубильники руководителя ------------------------------------------
def test_personal_switches_default_to_on_and_survive_a_plain_save():
    """Включение роли не имеет права молча отобрать уже работающие пуши."""
    _seed()
    m = db.get_manager(MGR)
    assert (m["push_visits"], m["push_digest"], m["push_promises"]) == (True, True, True)
    db.set_manager_pushes(MGR, {"push_visits": False})
    db.set_manager(MGR, "Рук", [], [EMP_A, EMP_B])          # обычное «Сохранить»
    assert db.get_manager(MGR)["push_visits"] is False, "тихое сохранение вернуло пуши"
    db.set_manager_pushes(MGR, {"push_visits": True})


def test_visit_cards_stop_for_a_manager_who_switched_them_off():
    _seed()
    assert any(x["telegram_id"] == MGR for x in db.managers_for_employee(EMP_A))
    db.set_manager_pushes(MGR, {"push_visits": False})
    assert db.managers_for_employee(EMP_A) == [], "карточки продолжают идти"
    db.set_manager_pushes(MGR, {"push_visits": True})
    assert any(x["telegram_id"] == MGR for x in db.managers_for_employee(EMP_A))


def test_digest_and_promises_switches_filter_the_broadcast(monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "hardbot_app_switches", Path(web.__file__).with_name("app.py"))
    botapp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(botapp)
    _seed()
    ids = lambda kind: [m["telegram_id"] for m in botapp._digest_targets(kind)]  # noqa: E731
    assert MGR in ids(db.PUSH_DIGEST) and MGR in ids(db.PUSH_PROMISE)
    db.set_manager_pushes(MGR, {"push_digest": False})
    assert MGR not in ids(db.PUSH_DIGEST)
    assert MGR in ids(db.PUSH_PROMISE), "рубильники независимы"
    db.set_manager_pushes(MGR, {"push_digest": True})


def test_endpoint_touches_only_the_keys_it_was_given(monkeypatch):
    _seed()
    db.set_manager_pushes(MGR, {"push_promises": False})
    asyncio.run(_post_manager({"telegram_id": MGR, "name": "Рук", "cities": [],
                               "employees": [EMP_A], "direction": ""}, monkeypatch))
    assert db.get_manager(MGR)["push_promises"] is False, "старый пульт всё переключил"
    asyncio.run(_post_manager({"telegram_id": MGR, "name": "Рук", "cities": [],
                               "employees": [EMP_A], "direction": "",
                               "push_promises": True}, monkeypatch))
    assert db.get_manager(MGR)["push_promises"] is True


def _post_manager(body, monkeypatch):
    monkeypatch.setattr(web, "_admin", lambda _r: None)

    class _Req:
        async def json(self):
            return body

    return web._admin_manager_save(_Req())
