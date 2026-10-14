"""Аккаунты «логин-пароль»: полный круг модели владельца.

Вход на телефоне (GPS обязателен) и ПК → «ввели и там, и там» → доступ
открывается только командой владельца (approve); повторное устройство —
тот же круг; подтверждение нового телефона гасит прежний; пароль, блокировка
перебора и журнал входов.
"""
import pytest

import accounts
import db


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "accounts.db")
    db.init_db()
    return tmp_path


def make(world, username="kurbanov", employee="Курбонов Шохрух", office="Душанбе"):
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
    assert len(rows) == 1 and rows[0]["ready"] is True
    assert rows[0]["phone"]["gps"] and rows[0]["status"] == "awaiting"

    # До команды владельца токен гейтится как pending.
    with db.connect() as conn:
        dev = conn.execute("SELECT id, account_id FROM account_devices "
                           "WHERE fingerprint='phone-fp-1'").fetchone()
        agent = conn.execute("SELECT id FROM agent_devices WHERE fingerprint='phone-fp-1' "
                             "AND revoked_at IS NULL").fetchone()
    assert accounts.device_gate_by_token("agent_devices", agent["id"]) == ("pending", "Курбонов Шохрух")

    # Команда владельца: аккаунт + оба устройства.
    assert accounts.approve_account("kurbanov", by="owner-test")["ok"]
    assert accounts.approve_device(dev["id"], by="owner-test")["ok"]
    assert accounts.device_gate_by_token("agent_devices", agent["id"]) == ("ok", "Курбонов Шохрух")

    # Повторный вход тем же телефоном — сразу ok (устройство подтверждено).
    again = login(entrance="public", device=phone_device())
    assert again["action"] == "ok" and again["employee"] == "Курбонов Шохрух"


def test_phone_login_requires_gps(world):
    make(world)
    result = login(gps=None)
    assert result["action"] == "no_gps" and "геолокаци" in result["error"]
    assert not result.get("token")
    with db.connect() as conn:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM account_logins")]
    assert "no_gps" in kinds


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
    make(world, "aknazarov", "Акназаров Карим", office="Худжанд")
    login(username="kurbanov", entrance="internal", device=desktop_device())
    rows = accounts.entered_list("Худжанд")
    assert rows == []            # Курбонов — другой офис; Акназаров не вводил вовсе
    rows_all = accounts.entered_list()
    assert len(rows_all) == 1 and rows_all[0]["office"] == "Душанбе"


def _manager_world(world):
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO managers(telegram_id,name,cities,employees) "
                     "VALUES(501,'Мазанов Сергей','','')")
        conn.commit()


def test_privileged_role_needs_two_admin_confirmations(world):
    _manager_world(world)
    made = accounts.create_account("Мазанов Сергей", "mazanov", "parol1234",
                                   role="manager", by="test")
    assert made.get("ok"), made
    accounts.login("mazanov", "parol1234", device=desktop_device(), entrance="internal")
    first = accounts.approve_account("mazanov", by="admin-egor")
    assert first["ok"] and not first["active"] and first["need"] == 2
    second = accounts.approve_account("mazanov", by="admin-egor")   # тот же админ — не считается
    assert not second["active"]
    third = accounts.approve_account("mazanov", by="admin-mazanov")
    assert third["active"]


def test_manager_account_mints_manager_class_token(world):
    _manager_world(world)
    accounts.create_account("Мазанов Сергей", "mazanov", "parol1234", role="manager")
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
    assert not result2["active"]
    result3 = accounts.approve_device(dev["id"], "admin-mazanov")
    assert result3["active"]
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM manager_devices WHERE account_id IS NOT NULL "
                           "AND revoked_at IS NULL").fetchone()
    assert accounts.device_gate_by_token("manager_devices", row["id"])[0] == "ok"


def test_privileged_without_binding_gets_synthetic_internal_id(world):
    # Телеграм не нужен вовсе: человека нет в managers — запись заводится со
    # служебным id 90 000 000 000+, менеджерский токен работает как обычно.
    made = accounts.create_account("Клинов Дмитрий", "klinov", "parol1234", role="analyst")
    assert made.get("ok"), made
    with db.connect() as conn:
        row = conn.execute("SELECT telegram_id FROM managers WHERE name='Клинов Дмитрий'").fetchone()
    assert row and 90_000_000_000 <= row["telegram_id"] < 90_001_000_000
    result = accounts.login("klinov", "parol1234", device=desktop_device(), entrance="internal")
    assert result["action"] == "ok_pending" and result.get("token")


def test_agent_role_single_confirmation_still(world):
    make(world)
    result = accounts.approve_account("kurbanov", by="admin-egor")
    assert result["active"] is True
