"""Режим без Telegram: пустой BOT_TOKEN — сервер поднимает только приложение.

Визиты сохраняются, пульт работает, пуши и сводки не отправляются — каждый
пропуск оставляет строку «skipped_no_bot» в push_log, а в журнале процесса
предупреждение звучит один раз, не на каждый визит.
"""

import asyncio
import importlib.util
import logging
import sys
from datetime import date
from pathlib import Path

from aiohttp import web as aiohttp_web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

import db
import notify
import web as webmod

ROOT = Path(__file__).resolve().parent.parent
EMP = "Безбота Сотрудник"
MGR = 770901
KEY = {"X-Admin-Key": "master"}


def _load_entrypoint():
    """hardbot/app.py под своим именем: пакет engine/app перекрывает `import app`."""
    spec = importlib.util.spec_from_file_location("hardbot_entrypoint", ROOT / "hardbot" / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'NB-%'")
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'NB-%'")
        conn.execute(
            "INSERT OR REPLACE INTO clients (contract_id, client, responsible, partner) "
            "VALUES (?,?,?,?)", ("NB-1", "Первый Б.", EMP, "ЗК"))
        conn.commit()
    db.set_manager(MGR, "Безбота", [], [EMP])
    db.set_setting("visit_push", "1")
    db.reset_role_caches()


def _push_rows(kind, status):
    with db.connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM push_log WHERE kind=? AND status=?", (kind, status)
        ).fetchone()[0]


def _reset_warning():
    notify._NO_BOT_WARNED = False


# --- старт ------------------------------------------------------------------------

def test_main_without_token_starts_only_the_site(monkeypatch, caplog):
    entry = _load_entrypoint()
    db.init_db()
    monkeypatch.setattr(entry, "bot_token", lambda: "")
    monkeypatch.setattr(entry, "_geocode_on_startup", lambda: None)
    monkeypatch.setenv("MINIAPP_PORT", "18099")
    started = {}

    async def fake_start_site(bot, host="0.0.0.0", port=8080):
        started["bot"] = bot
        started["port"] = port
        return object()

    async def no_wait():
        return None

    monkeypatch.setattr(entry.web, "start_site", fake_start_site)
    monkeypatch.setattr(entry, "_wait_forever", no_wait)
    with caplog.at_level(logging.INFO):
        asyncio.run(entry.main())
    assert started == {"bot": None, "port": 18099}
    assert entry.NO_TELEGRAM_NOTE in caplog.text


def test_bot_token_prefers_durable_setting_then_env(monkeypatch):
    entry = _load_entrypoint()
    db.init_db()
    db.set_setting("bot_token", "")
    monkeypatch.setenv("BOT_TOKEN", "   ")
    assert entry.bot_token() == ""
    monkeypatch.setenv("BOT_TOKEN", "123:env")
    assert entry.bot_token() == "123:env"
    db.set_setting("bot_token", "456:settings")
    try:
        assert entry.bot_token() == "456:settings"
    finally:
        db.set_setting("bot_token", "")


# --- визит без бота -------------------------------------------------------------------

def _save(rep, op_id, monkeypatch):
    monkeypatch.setattr(webmod, "_auth", lambda _r: (5901, EMP))
    app = aiohttp_web.Application()
    app["bot"] = None
    request = make_mocked_request("POST", "/api/save", app=app)

    async def _json():
        submitted = {"call_status": "В работе"}
        return {"rep": rep, "finish": True, "op_id": op_id, "values": submitted,
                "form_version": webmod.FORM_PROTOCOL_VERSION, "input_keys": list(submitted)}

    request.json = _json
    return asyncio.run(webmod._save(request))


def test_save_visit_without_bot_succeeds_and_logs_skip_once(monkeypatch, caplog):
    _seed()
    _reset_warning()
    before = _push_rows(db.PUSH_VISIT, db.PUSH_SKIP_NO_BOT)
    with caplog.at_level(logging.WARNING):
        for op in ("nb-1", "nb-2", "nb-3"):
            response = _save("NB-1", op, monkeypatch)
            assert response.status == 200
    assert _push_rows(db.PUSH_VISIT, db.PUSH_SKIP_NO_BOT) >= before + 1
    assert caplog.text.count("Telegram отключён") <= 1
    db.delete_manager(MGR)


# --- admin-ручки, которые шлют пуш --------------------------------------------------------

def _client():
    return TestClient(TestServer(webmod.build_app(None)))


def test_admin_push_test_without_bot_reports_delivery_off(monkeypatch):
    db.init_db()
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    _reset_warning()

    async def scenario():
        client = _client()
        await client.start_server()
        try:
            response = await client.post("/admin/push_test", headers=KEY,
                                         json={"kind": db.PUSH_VISIT, "telegram_id": 1})
            assert response.status == 200
            body = await response.json()
            assert body["ok"] is False
            assert body["status"] == db.PUSH_SKIP_NO_BOT
            assert body["delivery"] == "off"
            assert "BOT_TOKEN" in body["error"]
        finally:
            await client.close()

    asyncio.run(scenario())
    assert _push_rows(db.PUSH_TEST, db.PUSH_SKIP_NO_BOT) >= 1


def test_admin_notify_without_bot_returns_ok_with_delivery_off(monkeypatch):
    db.init_db()
    monkeypatch.setenv("ADMIN_API_KEY", "master")

    async def scenario():
        client = _client()
        await client.start_server()
        try:
            response = await client.post("/admin/notify", headers=KEY,
                                         json={"text": "суточный цикл завершён"})
            assert response.status == 200
            assert await response.json() == {"ok": True, "delivery": "off"}
        finally:
            await client.close()

    asyncio.run(scenario())


# --- рассылки и помощники notify ---------------------------------------------------------

def test_daily_send_without_bot_settles_with_one_telemetry_row():
    entry = _load_entrypoint()
    _seed()
    today = date.today().isoformat()
    before = _push_rows(db.PUSH_DIGEST, db.PUSH_SKIP_NO_BOT)
    pending = asyncio.run(entry._send_daily(
        db.PUSH_DIGEST, None, today, 1, lambda _mgr: ("текст", None)))
    assert pending == 0                      # улажено — ретраев не будет
    assert _push_rows(db.PUSH_DIGEST, db.PUSH_SKIP_NO_BOT) == before + 1
    db.delete_manager(MGR)


def test_notify_helpers_tolerate_missing_bot(caplog):
    db.init_db()
    db.set_setting("group_chat_id", "-100500")
    _reset_warning()
    seen = []
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(notify.post_to_group(None, text="x", strict=True)) is False
        assert asyncio.run(notify.send_album(None, 1, [], "подпись"))["ok"] is False
        asyncio.run(notify.post_visit_report(
            None, [11, 22], "отчёт", [], on_result=lambda cid, status, err=None:
            seen.append((cid, status))))
    assert seen == [(11, db.PUSH_SKIP_NO_BOT), (22, db.PUSH_SKIP_NO_BOT)]
    assert caplog.text.count("Telegram отключён") == 1
    db.set_setting("group_chat_id", "")
