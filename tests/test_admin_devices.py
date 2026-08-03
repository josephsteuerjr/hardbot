"""Устройства пульта: токен вместо мастер-ключа.

Смысл разделения: утёкший мастер-ключ перестаёт быть «загрузи что угодно откуда
угодно», а потерянный ноутбук отзывается по одному, не меняя ключ всем.
"""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

import db
import web

KEY = {"X-Admin-Key": "master"}


def _call(method, path, *, headers, json_body=None):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers, json=json_body)
            try:
                return resp.status, await resp.json()
            except Exception:  # noqa: BLE001 — 401 отдаёт текст, а не JSON
                return resp.status, {}
        finally:
            await client.close()

    return asyncio.run(scenario())


def _fresh(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM admin_devices")
        conn.commit()


def test_enroll_returns_token_that_then_works(monkeypatch):
    _fresh(monkeypatch)
    status, body = _call("POST", "/admin/device/enroll.json", headers=KEY,
                         json_body={"device_id": "fp-1", "name": "Ноут дома"})
    assert status == 200 and body["token"]
    token = body["token"]

    status, _ = _call("GET", "/admin/devices.json",
                      headers={"X-Device-Token": token})
    assert status == 200, "токен не пустили туда, куда пускает ключ"


def test_token_is_not_stored_in_clear(monkeypatch):
    """Дамп таблицы не должен давать вход."""
    _fresh(monkeypatch)
    _s, body = _call("POST", "/admin/device/enroll.json", headers=KEY,
                     json_body={"device_id": "fp-1", "name": "Ноут"})
    with db.connect() as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM admin_devices")]
    assert body["token"] not in stored
    assert all(len(h) == 64 for h in stored)


def test_revoked_token_stops_working(monkeypatch):
    _fresh(monkeypatch)
    _s, body = _call("POST", "/admin/device/enroll.json", headers=KEY,
                     json_body={"device_id": "fp-1", "name": "Потерянный"})
    token = body["token"]
    status, _ = _call("POST", "/admin/device/revoke.json", headers=KEY,
                      json_body={"id": body["id"]})
    assert status == 200
    status, _ = _call("GET", "/admin/devices.json",
                      headers={"X-Device-Token": token})
    assert status == 401


def test_device_token_cannot_enroll_or_revoke(monkeypatch):
    """Иначе укравший ноут запер бы владельца снаружи, а сам остался внутри."""
    _fresh(monkeypatch)
    _s, first = _call("POST", "/admin/device/enroll.json", headers=KEY,
                      json_body={"device_id": "fp-1", "name": "Первый"})
    tok = {"X-Device-Token": first["token"]}

    status, _ = _call("POST", "/admin/device/enroll.json", headers=tok,
                      json_body={"device_id": "fp-2", "name": "Чужой"})
    assert status == 401
    status, _ = _call("POST", "/admin/device/revoke.json", headers=tok,
                      json_body={"id": first["id"]})
    assert status == 401


def test_reenroll_replaces_old_token_of_the_same_machine(monkeypatch):
    """Переустановка пульта не должна плодить записи — иначе «отозвать это
    устройство» перестаёт что-либо значить."""
    _fresh(monkeypatch)
    _s, old = _call("POST", "/admin/device/enroll.json", headers=KEY,
                    json_body={"device_id": "fp-1", "name": "До"})
    _s, new = _call("POST", "/admin/device/enroll.json", headers=KEY,
                    json_body={"device_id": "fp-1", "name": "После"})
    assert new["token"] != old["token"]

    status, _ = _call("GET", "/admin/devices.json",
                      headers={"X-Device-Token": old["token"]})
    assert status == 401, "старый токен машины пережил перепривязку"
    status, body = _call("GET", "/admin/devices.json",
                         headers={"X-Device-Token": new["token"]})
    assert status == 200
    assert [d["name"] for d in body["devices"]] == ["После"]


def test_master_key_still_works_when_everything_is_revoked(monkeypatch):
    """Путь восстановления: отозвал все устройства — заходишь ключом и привязываешь
    заново. Без этого можно запереть себя окончательно."""
    _fresh(monkeypatch)
    _s, dev = _call("POST", "/admin/device/enroll.json", headers=KEY,
                    json_body={"device_id": "fp-1", "name": "Единственный"})
    _call("POST", "/admin/device/revoke.json", headers=KEY, json_body={"id": dev["id"]})
    status, body = _call("GET", "/admin/devices.json", headers=KEY)
    assert status == 200 and body["devices"] == []


def test_garbage_token_is_refused(monkeypatch):
    _fresh(monkeypatch)
    for bad in ("", "нет-такого", "a" * 64):
        status, _ = _call("GET", "/admin/devices.json",
                          headers={"X-Device-Token": bad})
        assert status == 401, bad
