"""Ссылка входа из пульта — без Telegram.

Сентябрь 2026: Telegram стал необязательным каналом, но персональную ссылку
входа выдавал только бот на слово «ссылка». Новый телефон подключить без бота
было нельзя. Теперь пульт просит ссылку у сервера; сотруднику без привязки
сервер выдаёт служебную личность из диапазона выше любого Telegram-id.
"""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

import db
import web

KEY = {"X-Admin-Key": "master"}
PATH = "/admin/app_link.json"
URL = "https://vyezdy.example.org"


def _call(method, path, *, headers, json_body=None):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers, json=json_body)
            try:
                return resp.status, await resp.json()
            except Exception:  # noqa: BLE001 — 401 отдаёт текст
                return resp.status, {}
        finally:
            await client.close()

    return asyncio.run(scenario())


def _fresh(monkeypatch, url=URL):
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    if url:
        monkeypatch.setenv("MINIAPP_URL", url)
    else:
        monkeypatch.delenv("MINIAPP_URL", raising=False)
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM app_links")
        conn.execute("DELETE FROM app_tokens")
        conn.execute("DELETE FROM agent_bind")
        conn.execute("DELETE FROM users WHERE telegram_id >= ?", (db.LOCAL_UID_BASE,))
        conn.commit()


def test_requires_admin(monkeypatch):
    _fresh(monkeypatch)
    status, _ = _call("POST", PATH, headers={}, json_body={"employee": "Иванов И."})
    assert status == 401


def test_employee_without_telegram_gets_identity_and_working_link(monkeypatch):
    _fresh(monkeypatch)
    status, body = _call("POST", PATH, headers=KEY,
                         json_body={"employee": "Иванов И.", "kind": "agent"})
    assert status == 200 and body["ok"] is True
    assert body["created"] is True and body["local_identity"] is True
    tid = body["telegram_id"]
    assert tid >= db.LOCAL_UID_BASE
    assert body["url"].startswith(URL + "/#link=")
    assert body["days"] == db.APP_LINK_DAYS
    assert db.get_binding(tid) == "Иванов И."

    # ссылка обменивается на токен приложения, токен ведёт к этому сотруднику
    code = body["url"].split("#link=", 1)[1]
    status, redeemed = _call("POST", "/api/redeem", headers={}, json_body={"code": code})
    assert status == 200 and redeemed["token"]
    assert db.uid_for_app_token(redeemed["token"]) == tid
    # гость виден в пульте под ФИО, а не голым числом
    names = {g["telegram_id"]: g["name"] for g in db.guests()}
    assert names.get(tid) == "Иванов И."


def test_second_request_reuses_identity_and_reissues_link(monkeypatch):
    _fresh(monkeypatch)
    _s, first = _call("POST", PATH, headers=KEY, json_body={"employee": "Иванов И."})
    _s, second = _call("POST", PATH, headers=KEY, json_body={"employee": "Иванов И."})
    assert second["telegram_id"] == first["telegram_id"]
    assert second["created"] is False
    assert second["url"] != first["url"], "новая ссылка гасит прежнюю неоткрытую"


def test_real_telegram_binding_is_reused(monkeypatch):
    _fresh(monkeypatch)
    db.set_bind(123456789, "Петров П.", "courier")
    _s, body = _call("POST", PATH, headers=KEY, json_body={"employee": "Петров П."})
    assert body["telegram_id"] == 123456789
    assert body["local_identity"] is False and body["created"] is False
    # и по самому id тоже
    _s, by_id = _call("POST", PATH, headers=KEY, json_body={"telegram_id": 123456789})
    assert by_id["employee"] == "Петров П."


def test_unbound_id_needs_employee(monkeypatch):
    _fresh(monkeypatch)
    status, body = _call("POST", PATH, headers=KEY, json_body={"telegram_id": 555})
    assert status == 409 and "привязан" in body["error"]
    status, body = _call("POST", PATH, headers=KEY,
                         json_body={"telegram_id": 555, "employee": "Сидоров С."})
    assert status == 200 and db.get_binding(555) == "Сидоров С."


def test_bad_bodies(monkeypatch):
    _fresh(monkeypatch)
    status, _ = _call("POST", PATH, headers=KEY, json_body={})
    assert status == 400
    status, _ = _call("POST", PATH, headers=KEY, json_body={"telegram_id": "abc"})
    assert status == 400
    status, _ = _call("POST", PATH, headers=KEY, json_body=["список"])
    assert status == 400


def test_without_public_url_the_link_cannot_be_built(monkeypatch):
    _fresh(monkeypatch, url="")
    status, body = _call("POST", PATH, headers=KEY, json_body={"employee": "Иванов И."})
    assert status == 409 and "MINIAPP_URL" in body["error"]
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM agent_bind").fetchone()[0]
    assert n == 0, "без ссылки личность создавать незачем"


def test_local_ids_are_allocated_sequentially(monkeypatch):
    _fresh(monkeypatch)
    a, created_a = db.local_identity_for("А")
    b, created_b = db.local_identity_for("Б")
    assert created_a and created_b and b == a + 1
    assert db.is_local_uid(a) and not db.is_local_uid(123456789)
