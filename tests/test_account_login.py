"""Аккаунты «логин-пароль»: полный круг модели владельца.

Вход на телефоне (GPS обязателен) и ПК → «ввели и там, и там» → доступ
открывается только командой владельца (approve); повторное устройство —
тот же круг; подтверждение нового телефона гасит прежний; пароль, блокировка
перебора и журнал входов.
"""
import json
import pytest

import accounts
import db


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "accounts.db")
    db.init_db()
    return tmp_path


def make(world, username="kurbanov", employee="Примеров6 Сотрудник6", office="Душанбе"):
    result = accounts.create_account(employee, username, "parol1234",
                                     office=office, by="test")
    assert result.get("ok"), result
    return result


def phone_device(fp="phone-fp-1"):
    return {"kind": "phone", "fp": fp, "label": "iPhone · iOS · 390×844"}


def desktop_device(fp="pc-fp-1"):
    return {"kind": "desktop", "fp": fp, "label": "OFFICE-PC · user"}


GPS = {"lat": 38.5598, "lon": 68.7870, "accuracy": 12.5}


def login(username="kurbanov", password="parol1234", device=None, entrance="public", gps=GPS):
    return accounts.login(username, password, device=device or phone_device(),
                          entrance=entrance, ip="10.0.0.9", gps=gps)


def test_full_office_circle_before_and_after_owner_approval(world):
    make(world)
    # Оба ввода в офисе: телефон (публичный вход, GPS) и ПК (внутренний).
    phone = login(entrance="public", device=phone_device())
    desk = login(entrance="internal", device=desktop_device())
    assert phone["action"] == "ok_pending" and phone.get("token")
    assert desk["action"] == "ok_pending" and desk.get("token")

    rows = accounts.entered_list()
    assert len(rows) == 1 and rows[0]["ready"] is False
    assert rows[0]["phone"]["gps"] and rows[0]["status"] == "awaiting"

    # До команды владельца токен гейтится как pending.
    with db.connect() as conn:
        dev = conn.execute("SELECT id, account_id FROM account_devices "
                           "WHERE fingerprint='phone-fp-1'").fetchone()
        agent = conn.execute("SELECT id FROM agent_devices WHERE fingerprint='phone-fp-1' "
                             "AND revoked_at IS NULL").fetchone()
    assert accounts.device_gate_by_token("agent_devices", agent["id"]) == ("pending", "Примеров6 Сотрудник6")

    # Команда владельца: аккаунт + оба устройства.
    assert accounts.approve_account("kurbanov", by="owner-test")["ok"]
    assert accounts.approve_device(dev["id"], by="owner-test")["ok"]
    assert accounts.change_password("kurbanov", "parol1234", "novyiparol9")["ok"]
    assert accounts.device_gate_by_token("agent_devices", agent["id"]) == ("ok", "Примеров6 Сотрудник6")

    # Повторный вход тем же телефоном — сразу ok (устройство подтверждено).
    again = login(password="novyiparol9", entrance="public", device=phone_device())
    assert again["action"] == "ok" and again["employee"] == "Примеров6 Сотрудник6"


def test_phone_login_requires_gps(world):
    make(world)
    result = login(gps=None)
    assert result["action"] == "ok_pending" and not result["gps_exception_pending"]
    assert result.get("token")
    with db.connect() as c:
        row=c.execute("SELECT agent_dev_id,gps,status FROM account_devices WHERE kind='phone'").fetchone()
    assert row["status"]=="pending" and not row["gps"]
    assert accounts.device_gate_by_token("agent_devices",row["agent_dev_id"])[0]!="ok"
    with db.connect() as conn:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM account_logins")]
    assert "ok_pending" in kinds


def test_new_phone_requires_owner_circle_and_kills_previous(world):
    make(world)
    login(entrance="public", device=phone_device("phone-1"))
    login(entrance="internal", device=desktop_device())
    with db.connect() as conn:
        first = conn.execute("SELECT * FROM account_devices WHERE fingerprint='phone-1'").fetchone()
    accounts.approve_account("kurbanov", "owner-test")
    accounts.approve_device(first["id"], "owner-test")

    # Телефон сломался: новый ввод — снова pending, без доступа.
    fresh = login(entrance="public", device=phone_device("phone-2"))
    assert fresh["action"] == "ok_pending"

    # Подтверждение нового телефона гасит прежний и его токен.
    with db.connect() as conn:
        second = conn.execute("SELECT * FROM account_devices WHERE fingerprint='phone-2'").fetchone()
        first_agent = conn.execute("SELECT id FROM agent_devices WHERE fingerprint='phone-1' "
                                   "AND revoked_at IS NULL").fetchone()
    accounts.approve_device(second["id"], "owner-test")
    with db.connect() as conn:
        status = conn.execute("SELECT status FROM account_devices WHERE id=?",
                              (first["id"],)).fetchone()[0]
        gone = conn.execute("SELECT revoked_at FROM agent_devices WHERE fingerprint='phone-1' "
                            "AND revoked_at IS NULL").fetchone()
    assert status == "revoked" and gone is None and first_agent is not None


def test_bad_password_lockout_and_recovery(world):
    make(world)
    for _ in range(5):
        r = accounts.login("kurbanov", "wrongpass1", device=desktop_device(),
                           entrance="internal")
    assert r["action"] == "bad"
    locked = accounts.login("kurbanov", "parol1234", device=desktop_device(),
                            entrance="internal")
    assert locked["action"] == "locked" and not locked.get("token")


def test_disabled_account_blocks_and_revokes_tokens(world):
    make(world)
    login(entrance="internal", device=desktop_device())
    accounts.set_status("kurbanov", "disabled", by="owner-test")
    blocked = login(entrance="internal", device=desktop_device())
    assert blocked["action"] == "blocked"
    with db.connect() as conn:
        live = conn.execute("SELECT COUNT(*) FROM account_devices WHERE status='active'"
                            ).fetchone()[0]
    assert live == 0


def test_password_change_and_policy(world):
    make(world)
    assert not accounts.change_password("kurbanov", "parol1234", "short")["ok"]
    assert accounts.change_password("kurbanov", "parol1234", "novyiparol9")["ok"]
    assert accounts.login("kurbanov", "parol1234", device=desktop_device(),
                          entrance="internal")["action"] == "bad"
    assert accounts.login("kurbanov", "novyiparol9", device=desktop_device(),
                          entrance="internal")["action"] == "ok_pending"


def test_username_rules_and_duplicates(world):
    assert not accounts.create_account("ФИО", "Кириллица", "parol1234")["ok"]
    assert not accounts.create_account("ФИО", "ab", "parol1234")["ok"]
    make(world)
    assert not accounts.create_account("Другой", "kurbanov", "parol1234")["ok"]


def test_sessions_journal_and_token_link(world):
    make(world)
    login(entrance="public", device=phone_device())
    result = login(entrance="internal", device=desktop_device())
    with db.connect() as conn:
        agent_id = conn.execute("SELECT id FROM agent_devices WHERE fingerprint='pc-fp-1' "
                                "AND revoked_at IS NULL").fetchone()["id"]
    linked = accounts.account_of_token("agent_devices", agent_id)
    assert linked and linked[1] == "kurbanov"
    rows = accounts.sessions_for(agent_id, linked[0])
    assert len(rows) >= 2 and {r["kind"] for r in rows} == {"ok_pending"}


def test_entered_list_scopes_by_office_and_skips_empty(world):
    make(world, "kurbanov", office="Душанбе")
    make(world, "aknazarov", "Примеров1 Сотрудник1", office="Худжанд")
    login(username="kurbanov", entrance="internal", device=desktop_device())
    rows = accounts.entered_list("Худжанд")
    assert rows == []            # Примеров6 — другой офис; Примеров1 не вводил вовсе
    rows_all = accounts.entered_list()
    assert len(rows_all) == 1 and rows_all[0]["office"] == "Душанбе"


def _manager_world(world):
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO managers(telegram_id,name,cities,employees) "
                     "VALUES(501,'Учебный Профиль 2','','')")
        conn.commit()


def test_privileged_role_needs_one_real_admin_and_repeat_is_idempotent(world):
    _manager_world(world)
    made = accounts.create_account("Учебный Профиль 2", "mazanov", "parol1234",
                                   role="manager", by="test")
    assert made.get("ok"), made
    accounts.login("mazanov", "parol1234", device=desktop_device(), entrance="internal")
    first = accounts.approve_account("mazanov", by="admin-egor")
    assert first["ok"] and first["active"] and first["approvals"] == ['admin-egor']
    second = accounts.approve_account("mazanov", by="admin-egor")   # тот же админ — не считается
    assert second["active"]
    third = accounts.approve_account("mazanov", by="admin-mazanov")
    assert third["active"]


def test_manager_account_mints_manager_class_token(world):
    _manager_world(world)
    accounts.create_account("Учебный Профиль 2", "mazanov", "parol1234", role="manager")
    result = accounts.login("mazanov", "parol1234", device=desktop_device(),
                            entrance="internal")
    assert result["action"] == "ok_pending" and result.get("token")
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM manager_devices WHERE account_id IS NOT NULL "
                           "AND revoked_at IS NULL").fetchone()
        assert row is not None, "токен должен быть класса manager_devices"
        gate = accounts.device_gate_by_token("manager_devices", row["id"])
    assert gate[0] == "pending"      # аккаунт ещё не активирован двумя администраторами
    accounts.approve_account("mazanov", "admin-egor")
    accounts.approve_account("mazanov", "admin-mazanov")
    with db.connect() as conn:
        dev = conn.execute("SELECT id FROM account_devices WHERE kind='desktop'").fetchone()
    result2 = accounts.approve_device(dev["id"], "admin-egor")
    assert result2["active"]
    result3 = accounts.approve_device(dev["id"], "admin-mazanov")
    assert result3["active"]
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM manager_devices WHERE account_id IS NOT NULL "
                           "AND revoked_at IS NULL").fetchone()
    assert accounts.change_password("mazanov", "parol1234", "novyiparol9")["ok"]
    assert accounts.device_gate_by_token("manager_devices", row["id"])[0] == "ok"


def test_ungranted_analyst_has_internal_id_but_cannot_login(world):
    # Телеграм не нужен вовсе: человека нет в managers — запись заводится со
    # служебным id 90 000 000 000+, менеджерский токен работает как обычно.
    made = accounts.create_account("Учебный Профиль 1", "klinov", "parol1234", role="analyst")
    assert made.get("ok"), made
    with db.connect() as conn:
        row = conn.execute("SELECT telegram_id FROM managers WHERE name='Учебный Профиль 1'").fetchone()
    assert row and 90_000_000_000 <= row["telegram_id"] < 90_001_000_000
    from test_pc_first_accounts import identity,proof
    pc=identity()
    result = accounts.login("klinov", "parol1234", device={'kind':'desktop','fp':pc[2]}, entrance="internal",
        audience='analyst',pc_proof=proof(pc,'','klinov','parol1234'))
    assert result['action']=='blocked' and not result.get('token')


def test_agent_role_single_confirmation_still(world):
    make(world)
    result = accounts.approve_account("kurbanov", by="admin-egor")
    assert result["active"] is True


def test_logout_revokes_own_class_only(world):
    """Выход менеджерского аккаунта гасит ЕГО токен в manager_devices и не
    трогает agent_devices: id строк в таблицах независимы (ревью 15.10)."""
    _manager_world(world)
    accounts.create_account("Учебный Профиль 2", "mazanov", "parol1234", role="manager")
    accounts.login("mazanov", "parol1234", device=desktop_device(), entrance="internal")
    # Посторонний выездник со ссылочным токеном: agent_devices id совпадает
    # с менеджерским id в manager_devices.
    with db.connect() as conn:
        conn.execute("INSERT INTO agent_devices(employee,telegram_id,token_hash,device,"
                     "created_at,last_seen_at) VALUES('Примеров6 Сотрудник6',111,?,"
                     "'iPhone','2026-10-15T00:00:00','2026-10-15T00:00:00')",
                     (db.app_token_key("field-kurbanov"),))
        conn.execute("INSERT INTO agent_bind(telegram_id,employee,approved) "
                     "VALUES(111,'Примеров6 Сотрудник6',1)")
        conn.commit()
        mgr_dev = conn.execute("SELECT id FROM manager_devices WHERE account_id "
                               "IS NOT NULL AND revoked_at IS NULL").fetchone()
    assert accounts.revoke_token("manager_devices", int(mgr_dev["id"]))
    with db.connect() as conn:
        agent = conn.execute("SELECT revoked_at FROM agent_devices "
                             "WHERE token_hash=?", (db.app_token_key("field-kurbanov"),)).fetchone()
        mgr = conn.execute("SELECT revoked_at FROM manager_devices WHERE id=?",
                           (mgr_dev["id"],)).fetchone()
    assert agent["revoked_at"] is None, "выход менеджера не должен гасить чужое устройство"
    assert mgr["revoked_at"] is not None


def test_concurrent_approvals_of_two_admins_both_counted(world):
    """BEGIN IMMEDIATE: одновременные подтверждения двух РАЗНЫХ администраторов
    не теряют одно (раньше approvals оставался с одной записью — воспроизведено)."""
    _manager_world(world)
    accounts.create_account("Учебный Профиль 2", "mazanov", "parol1234", role="manager")
    import threading
    barrier = threading.Barrier(2)
    def approve(who):
        barrier.wait()
        accounts.approve_account("mazanov", by=who)
    threads = [threading.Thread(target=approve, args=(name,)) for name in ("admin-egor", "admin-mazanov")]
    for t in threads: t.start()
    for t in threads: t.join()
    with db.connect() as conn:
        row = conn.execute("SELECT approvals, status FROM accounts WHERE username='mazanov'").fetchone()
    assert sorted(json.loads(row["approvals"])) == ["admin-egor", "admin-mazanov"]
    assert row["status"] == "active"


def test_set_status_is_audited(world):
    make(world)
    accounts.set_status("kurbanov", "disabled", by="admin-test")
    with db.connect() as conn:
        row = conn.execute("SELECT action, target, detail FROM audit WHERE action='account_status' "
                           "ORDER BY rowid DESC").fetchone()
    assert row and row["target"] == "kurbanov" and "disabled" in (row["detail"] or "")


# --- Сквозной HTTP-круг (ревью 15.10: дверь сотрудника умирала на _uid) ---------

import asyncio

import web
from aiohttp.test_utils import TestClient, TestServer


def _call(method, path, *, headers=None, json_body=None):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers or {}, json=json_body)
            try:
                return resp.status, await resp.json()
            except Exception:  # noqa: BLE001
                return resp.status, {}
        finally:
            await client.close()
    return asyncio.run(scenario())


def _cleanup_http_rows(*usernames: str, fingerprints: str = "", mgr_uid: int = 0):
    """Убрать за собой в ОБЩЕЙ тестовой БД (conftest APP_DATA): HTTP-тесты
    живут не в world-изоляции, и их строки (менеджеры, ссылки, девайсы)
    засоряли последующие сьюты полного прогона."""
    with db.connect() as conn:
        if usernames:
            marks = ",".join("?" for _ in usernames)
            ids = [r["id"] for r in conn.execute(
                f"SELECT id FROM accounts WHERE username IN ({marks})", usernames)]
            if ids:
                id_marks = ",".join("?" for _ in ids)
                conn.execute(f"DELETE FROM account_devices WHERE account_id IN ({id_marks})", ids)
                conn.execute(f"DELETE FROM account_logins WHERE account_id IN ({id_marks})", ids)
                conn.execute(f"DELETE FROM agent_devices WHERE account_id IN ({id_marks})", ids)
            conn.execute(f"DELETE FROM accounts WHERE username IN ({marks})", usernames)
        for fp in filter(None, fingerprints.split(",")):
            conn.execute("DELETE FROM account_devices WHERE fingerprint=?", (fp,))
            conn.execute("DELETE FROM agent_devices WHERE fingerprint=?", (fp,))
        if mgr_uid:
            conn.execute("DELETE FROM manager_devices WHERE telegram_id=?", (mgr_uid,))
            conn.execute("DELETE FROM app_links WHERE telegram_id=?", (mgr_uid,))
            conn.execute("DELETE FROM managers WHERE telegram_id=?", (mgr_uid,))
        conn.commit()


def test_account_flow_http_end_to_end():
    """Токен аккаунта СОТРУДНИКА живёт до и после подтверждения: bootstrap
    отвечает pending с pending_kind=account, после approve — отдаёт портфель.
    До фикса 15.10 токен убивался bind-проверкой, а uid не существовало."""
    db.init_db()
    _cleanup_http_rows("e2ekurbanov", fingerprints="e2e-fp")
    try:
        accounts.create_account("Е2Э Курбанов", "e2ekurbanov", "parol1234",
                                office="Душанбе", by="test")
        login = accounts.login("e2ekurbanov", "parol1234",
                               device={"kind": "phone", "fp": "e2e-fp", "label": "iPhone"},
                               entrance="public", ip="10.0.0.9",
                               gps={"lat": 38.5, "lon": 68.7, "accuracy": 10})
        assert login["action"] == "ok_pending"
        assert isinstance(login.get("uid"), int), "токен сотрудника должен нести внутренний uid"

        auth = {"X-App-Token": login["token"]}
        st, body = _call("GET", "/api/bootstrap", headers=auth)
        assert st == 200 and body.get("pending") is True and body.get("pending_kind") == "account"
        assert body.get("employee") == "Е2Э Курбанов"

        with db.connect() as conn:
            dev = conn.execute("SELECT id FROM account_devices WHERE fingerprint='e2e-fp'").fetchone()
        accounts.approve_account("e2ekurbanov", by="admin-test")
        accounts.approve_device(dev["id"], by="admin-test")
        state, _row = db.agent_device_check(login["token"], "10.0.0.9")
        assert state == "ok", "аккаунт-токен не должен умирать bind-проверкой"
        assert accounts.change_password("e2ekurbanov", "parol1234", "novyiparol9")["ok"]

        st, body = _call("GET", "/api/bootstrap", headers=auth)
        assert st == 200 and not body.get("pending"), body
        assert body.get("employee") == "Е2Э Курбанов"
        assert isinstance(body.get("uid"), int)
        assert isinstance(body.get("persons"), list)
    finally:
        _cleanup_http_rows("e2ekurbanov", fingerprints="e2e-fp")


def test_agent_account_inherits_phone_binding_uid(world):
    """Переходный период: у человека уже есть телефонная привязка — аккаунт
    наследует её внутренний id (единая история), а не плодит новый."""
    with db.connect() as conn:
        conn.execute("INSERT INTO agent_bind(telegram_id,employee,approved) "
                     "VALUES(777,'Примеров6 Сотрудник6',1)")
        conn.commit()
    make(world)      # создание ПОСЛЕ привязки → наследует 777
    login()
    with db.connect() as conn:
        acc = conn.execute("SELECT telegram_id FROM accounts WHERE username='kurbanov'").fetchone()
        dev = conn.execute("SELECT telegram_id FROM agent_devices "
                           "WHERE account_id IS NOT NULL AND revoked_at IS NULL").fetchall()
    assert acc["telegram_id"] == 777
    assert dev and all(d["telegram_id"] == 777 for d in dev)


def test_manager_entered_hides_gps_from_managers():
    """«Кто ввёл» руководителю — без GPS-координат входа: факт «ввели с
    телефона» достаточен, точка — персональные данные (админам остаётся)."""
    db.init_db()
    MGR = 601601
    _cleanup_http_rows("e2egps", fingerprints="e2e-gps-fp", mgr_uid=MGR)
    try:
        db.set_manager(MGR, "Рук Е2Э", [], ["Е2Э GPS-Сотрудник"])
        accounts.create_account("Е2Э GPS-Сотрудник", "e2egps", "parol1234", by="test")
        accounts.login("e2egps", "parol1234",
                       device={"kind": "phone", "fp": "e2e-gps-fp", "label": "iPhone"},
                       entrance="public", ip="10.0.0.9",
                       gps={"lat": 55.75, "lon": 37.61, "accuracy": 8})
        code = db.create_app_link(MGR, max_uses=5)
        st, body = _call("POST", "/api/manager/device/enroll",
                         json_body={"code": f"https://x.example/i/{code}", "device": "ПК-Е2Э"})
        assert st == 200, body
        st, body = _call("GET", "/api/manager/entered",
                         headers={"X-Pult-Token": body["token"]})
        assert st == 200, body
        rows = [r for r in body.get("entered", []) if r["employee"] == "Е2Э GPS-Сотрудник"]
        assert rows and rows[0]["phone"] is not None
        assert "gps" not in rows[0]["phone"], "координаты входа не должны уходить руководителю"
    finally:
        _cleanup_http_rows("e2egps", fingerprints="e2e-gps-fp", mgr_uid=MGR)
