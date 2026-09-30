"""Старый адрес приложения — только выгрузка того, что застряло на телефонах.

30.09.2026: после переезда часть выездников работала в старом приложении
(hardbot2.…nip.io и мини-приложение старого бота), выезды легли в очередь
телефона. Старый адрес снова ведёт на рабочий сервер с заголовком
X-Hard-Drain: 1 (Caddy на Атланте): досылка очереди и вход по ссылке —
пропускаются, работа — нет (410 drain_only). Подпись старого бота
проверяется только здесь.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse

from aiohttp.test_utils import TestClient, TestServer

import db
import web

DRAIN = {"X-Hard-Drain": "1"}


def _call(method, path, *, headers=None, json_body=None):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers or {}, json=json_body,
                                        allow_redirects=False)
            try:
                body = await resp.json()
            except Exception:  # noqa: BLE001
                body = await resp.text()
            return resp.status, body, dict(resp.headers)
        finally:
            await client.close()
    return asyncio.run(scenario())


def _init_data(token: str, uid: int) -> str:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "Т"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def _agent(uid=4242, emp="Выгрузкин Т."):
    db.init_db()
    db.bind_agent(uid, emp)
    return uid, emp


def test_work_is_closed_on_the_old_address():
    _agent()
    for path in ("/api/bootstrap", "/api/card?rep=1", "/api/manager/stats", "/admin/stats"):
        st, body, _h = _call("GET", path, headers=DRAIN)
        assert st == 410 and body["error"] == "drain_only", (path, st, body)
    st, _b, h = _call("GET", "/i/SOMECODE", headers=DRAIN)
    assert st == 302 and h["Location"] == "/"
    st, body, _h = _call("POST", "/api/route_day", headers=DRAIN, json_body={})
    assert st == 410


def test_shell_upload_and_login_pass_through():
    st, body, _h = _call("GET", "/version.json", headers=DRAIN)
    assert st == 200 and "drain" in body and "newUrl" in body["drain"]
    st, _b, _h = _call("GET", "/", headers=DRAIN)
    assert st == 200
    # Без входа — 401 от самой ручки (прослойка пропустила), а не 410.
    st, _b, _h = _call("POST", "/api/save", headers=DRAIN, json_body={})
    assert st == 401
    # Мёртвая ссылка — отказ самой ручки входа, а не прослойки.
    st, body, _h = _call("POST", "/api/redeem", headers=DRAIN, json_body={"code": "nope"})
    assert not (isinstance(body, dict) and body.get("error") == "drain_only"), body


def test_main_address_is_untouched():
    st, body, _h = _call("GET", "/version.json")
    assert st == 200 and "drain" not in body
    st, _b, _h = _call("GET", "/api/bootstrap")
    assert st in (401, 403)


def test_old_bot_signature_only_on_the_old_address(monkeypatch):
    uid, emp = _agent()
    monkeypatch.setenv("DRAIN_BOT_TOKEN", "123:old-bot-token")
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    init = _init_data("123:old-bot-token", uid)
    st, _b, _h = _call("POST", "/api/save", headers={**DRAIN, "X-Init-Data": init}, json_body={})
    assert st == 404, "подпись старого бота принята, дальше ручка ищет договор"
    st, _b, _h = _call("POST", "/api/save", headers={"X-Init-Data": init}, json_body={})
    assert st == 401, "на основном адресе подпись старого бота не пускает"


def test_accepted_upload_is_audited(monkeypatch):
    uid, emp = _agent()
    monkeypatch.setenv("DRAIN_BOT_TOKEN", "123:old-bot-token")
    init = _init_data("123:old-bot-token", uid)

    async def ok_save(request):
        return web.web.json_response({"ok": True})
    monkeypatch.setattr(web, "_save", ok_save)
    st, _b, _h = _call("POST", "/api/save", headers={**DRAIN, "X-Init-Data": init}, json_body={})
    assert st == 200
    with db.connect() as conn:
        n = conn.execute("SELECT count(*) FROM audit WHERE actor=? AND action='drain_upload'",
                         (uid,)).fetchone()[0]
    assert n >= 1
