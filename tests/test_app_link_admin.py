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
URL = "https://hard.example.org"


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
    assert body["url"] == f"{URL}/i/{body['code']}", "короткая ссылка, а не #link="
    assert body["days"] == db.APP_LINK_DAYS
    assert db.get_binding(tid) == "Иванов И."

    # ссылка обменивается на токен приложения, токен ведёт к этому сотруднику
    code = body["code"]
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


def test_manager_gets_login_link_without_binding(monkeypatch):
    """Руководитель раздаёт входы своей команде — но сам сначала входит по ссылке
    аналитика. Привязки к ФИО у него нет и быть не должно: это роль, а не
    выездник. Ссылка выдаётся по telegram_id, привязка не появляется."""
    _fresh(monkeypatch)
    mgr = 581000
    db.set_manager(mgr, "Рук Ссылкин", [], ["Кто-то К."])
    db.reset_role_caches()
    try:
        status, body = _call("POST", PATH, headers=KEY, json_body={"telegram_id": mgr})
        assert status == 200, body
        assert body["telegram_id"] == mgr and body["url"].startswith(URL + "/i/")
        assert db.get_binding(mgr) is None, "руководитель стал выездником"
        token = db.redeem_app_link(body["code"])
        assert db.uid_for_app_token(token) == mgr
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM managers WHERE telegram_id=?", (mgr,))
            conn.commit()
        db.reset_role_caches()


BULK = "/admin/app_links_bulk.json"


def test_bulk_issues_everyone_in_one_request(monkeypatch):
    """Старт пробы в один клик: список людей и руководителей — одним запросом.
    Правила те же, что у одиночной выдачи; ошибка одной строки не валит
    остальные."""
    _fresh(monkeypatch)
    mgr = 581100
    db.set_manager(mgr, "Рук Массов", [], ["Массов М."])
    db.reset_role_caches()
    try:
        status, body = _call("POST", BULK, headers=KEY, json_body={
            "uses": 2,
            "items": [{"employee": "Массов М."},
                      {"employee": "Второв В.", "kind": "courier"},
                      {"telegram_id": mgr},
                      {"telegram_id": 777},          # никому не привязан
                      "не объект"]})
        assert status == 200, body
        res = body["results"]
        assert body["issued"] == 3 and len(res) == 5
        assert [bool(r.get("url")) for r in res] == [True, True, True, False, False]
        assert res[2]["telegram_id"] == mgr and db.get_binding(mgr) is None
        assert "привязан" in res[3]["error"]
        assert all(r["url"].startswith(URL + "/i/") for r in res[:3])
        assert all(r["max_uses"] == 2 for r in res[:3])
        # каждая ссылка — рабочая
        for r in res[:3]:
            assert db.uid_for_app_token(db.redeem_app_link(r["code"])) == r["telegram_id"]
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM managers WHERE telegram_id=?", (mgr,))
            conn.commit()
        db.reset_role_caches()


def test_bulk_rejects_bad_bodies_and_missing_url(monkeypatch):
    _fresh(monkeypatch)
    status, _ = _call("POST", BULK, headers={}, json_body={"items": []})
    assert status == 401
    status, _ = _call("POST", BULK, headers=KEY, json_body={"items": "все"})
    assert status == 400
    status, body = _call("POST", BULK, headers=KEY,
                         json_body={"items": [{"employee": "Х"}] * 501})
    assert status == 400 and "500" in body["error"]
    _fresh(monkeypatch, url="")
    status, body = _call("POST", BULK, headers=KEY,
                         json_body={"items": [{"employee": "Иванов И."}]})
    assert status == 409 and "MINIAPP_URL" in body["error"]
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_bind").fetchone()[0] == 0


def test_bad_uses_creates_no_identity(monkeypatch):
    """Опечатка в числе открытий — отказ ДО того, как заводится личность."""
    _fresh(monkeypatch)
    status, _ = _call("POST", PATH, headers=KEY,
                      json_body={"employee": "Опечаткин О.", "uses": 999})
    assert status == 400
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM agent_bind WHERE employee=?",
                         ("Опечаткин О.",)).fetchone()[0]
    assert n == 0


def test_big_json_is_gzipped_only_when_asked(monkeypatch):
    """Крупный JSON сжимается на стороне приложения — до туннеля, а не после
    него. Без Accept-Encoding: gzip ответ прежний, мелочь не сжимается."""
    import gzip
    import json as _json

    from aiohttp import web as aio_web

    big = {"rows": [{"n": i, "text": "строка " * 20} for i in range(600)]}

    async def scenario():
        app = aio_web.Application(middlewares=[web._compress])
        app.router.add_get("/big", lambda r: aio_web.json_response(big))
        app.router.add_get("/small", lambda r: aio_web.json_response({"ok": 1}))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            out = {}
            for path, enc in (("/big", "gzip"), ("/big", "identity"), ("/small", "gzip")):
                resp = await client.get(path, headers={"Accept-Encoding": enc},
                                        auto_decompress=False)
                out[(path, enc)] = (resp.headers.get("Content-Encoding"), await resp.read())
            return out
        finally:
            await client.close()

    out = asyncio.run(scenario())
    enc, raw = out[("/big", "gzip")]
    assert enc == "gzip" and _json.loads(gzip.decompress(raw)) == big
    assert len(raw) < len(_json.dumps(big).encode()) / 5
    assert out[("/big", "identity")][0] is None
    assert out[("/small", "gzip")][0] is None
