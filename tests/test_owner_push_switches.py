"""Личные рубильники владельца: он сам решает, что ему приходит.

Владелец — не руководитель: записи в `managers` у него может не быть вовсе,
роль даёт `ADMIN_ID` в `.env`. Из-за этого его рассылки долго жили по чужим
правилам: дайджест выключался только общим рубильником — вместе со всеми
руководителями, а карточки по закрытым визитам не приходили никогда и включить
их было нечем (адресатов берут из `managers`).

Теперь у него три собственных ключа с прежними умолчаниями: сводка включена,
обещания и карточки — нет. Ничьё поведение это не меняет, но выбор появляется.
"""
import asyncio
import importlib.util
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import db
import web

# server/app.py нельзя импортировать как «app»: это имя занято пакетом
# engine/app (см. conftest) — грузим по пути под своим именем.
_spec = importlib.util.spec_from_file_location(
    "hardbot_app_owner_switches", Path(web.__file__).with_name("app.py"))
hard_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hard_app)

KEY = {"X-Admin-Key": "master"}
OWNER_KEYS = ("owner_digest", "owner_promise", "owner_visits")


def _call(method, path, *, json_body=None):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=KEY, json=json_body)
            return resp.status, await resp.json()
        finally:
            await client.close()

    return asyncio.run(scenario())


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key IN (?,?,?)", OWNER_KEYS)
        conn.commit()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key IN (?,?,?)", OWNER_KEYS)
        conn.commit()


def test_defaults_keep_previous_behaviour():
    """Сводка идёт, обещания и карточки — нет. Ровно как было до появления ключей."""
    status, payload = _call("GET", "/admin/digest_settings")
    assert status == 200
    assert payload["owner_digest"] is True
    assert payload["owner_promise"] is False
    assert payload["owner_visits"] is False


def test_switches_round_trip():
    _call("POST", "/admin/digest_settings",
          json_body={"owner_digest": False, "owner_promise": True,
                     "owner_visits": True})
    _status, payload = _call("GET", "/admin/digest_settings")
    assert payload["owner_digest"] is False
    assert payload["owner_promise"] is True
    assert payload["owner_visits"] is True


def test_old_pult_body_does_not_touch_owner_switches():
    """Старый пульт этих ключей не шлёт — обычное «Сохранить» не должно молча
    переключать владельцу рассылки."""
    _call("POST", "/admin/digest_settings",
          json_body={"owner_digest": False, "owner_visits": True})
    _call("POST", "/admin/digest_settings",
          json_body={"time": "16:30", "enabled": True})     # тело старого пульта
    _status, payload = _call("GET", "/admin/digest_settings")
    assert payload["owner_digest"] is False
    assert payload["owner_visits"] is True


def test_owner_is_a_visit_target_only_when_he_asked(monkeypatch):
    """Проверяем сам отбор адресатов карточки визита, а не только настройку."""
    monkeypatch.setattr(web, "ADMIN_ID", 809306689)
    monkeypatch.setattr(db, "managers_for_employee",
                        lambda emp: [{"telegram_id": 111, "matched_as": emp}])

    def targets():
        base = db.managers_for_employee("Сотрудник")
        if (web.ADMIN_ID and db.push_enabled("owner_visits", "0")
                and all(m["telegram_id"] != web.ADMIN_ID for m in base)):
            base = [*base, {"telegram_id": web.ADMIN_ID, "matched_as": "Сотрудник"}]
        return [m["telegram_id"] for m in base]

    assert targets() == [111]                       # по умолчанию владельца нет
    db.set_setting("owner_visits", "1")
    assert targets() == [111, 809306689]            # включил — появился


def test_owner_is_not_duplicated_when_he_is_also_a_manager(monkeypatch):
    """Владельца завели руководителем руками — карточка не должна уйти дважды."""
    monkeypatch.setattr(web, "ADMIN_ID", 809306689)
    monkeypatch.setattr(
        db, "managers_for_employee",
        lambda emp: [{"telegram_id": 809306689, "matched_as": emp}])
    db.set_setting("owner_visits", "1")
    base = db.managers_for_employee("Сотрудник")
    if (web.ADMIN_ID and db.push_enabled("owner_visits", "0")
            and all(m["telegram_id"] != web.ADMIN_ID for m in base)):
        base = [*base, {"telegram_id": web.ADMIN_ID, "matched_as": "Сотрудник"}]
    assert [m["telegram_id"] for m in base] == [809306689]


def test_daily_targets_respect_owner_switches(monkeypatch):
    """Дневные рассылки: владелец пропадает из адресатов, когда выключил себе."""
    monkeypatch.setattr(hard_app, "ADMIN_ID", 809306689)
    monkeypatch.setattr(db, "list_managers", lambda: [{"telegram_id": 111,
                                                       "name": "Руководитель"}])
    monkeypatch.setattr(hard_app.digest, "owner_scope",
                        lambda uid: {"telegram_id": uid, "name": "Владелец",
                                     "employees": [], "cities": [], "direction": ""})

    def ids(kind):
        return [m["telegram_id"] for m in hard_app._digest_targets(kind)]

    assert 809306689 in ids(db.PUSH_DIGEST)          # умолчание: сводка идёт
    assert 809306689 not in ids(db.PUSH_PROMISE)     # умолчание: обещания нет

    db.set_setting("owner_digest", "0")
    assert 809306689 not in ids(db.PUSH_DIGEST)
    db.set_setting("owner_promise", "1")
    assert 809306689 in ids(db.PUSH_PROMISE)


def test_pult_sends_the_key_only_to_a_server_that_knows_it():
    """Пульт не шлёт ключ старому серверу: «Сохранить» там ничего не значит,
    но и не притворяется, будто сработало."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for p in (str(root / "pult"), str(root)):
        if p not in sys.path:
            sys.path.insert(0, p)
    from pult.core import managers as mg

    common = dict(time="16:30", enabled=True, promise_time="09:30",
                  promise_enabled=True, visit_push=True, owner_known=True,
                  owner_digest=True, owner_promise=False)
    assert "owner_visits" not in mg.digest_body(**common)
    body = mg.digest_body(**common, owner_visits_known=True, owner_visits=True)
    assert body["owner_visits"] is True
