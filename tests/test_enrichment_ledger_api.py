"""Ручки реестра доставленного: пульт забирает его перед обогащением и
записывает после. Сервер — единственный владелец реестра, потому что обогащать
могут с разных машин."""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

import db
import web

KEY = {"X-Admin-Key": "master"}
PATH = "/admin/enrichment/ledger.json"


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


def _fresh(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM enrichment_ledger")
        conn.commit()


def test_requires_admin(monkeypatch):
    _fresh(monkeypatch)
    status, _ = _call("GET", PATH, headers={})
    assert status == 401
    status, _ = _call("POST", PATH, headers={"X-Admin-Key": "wrong"},
                      json_body={"entries": []})
    assert status == 401


def test_round_trip_and_idempotent_upsert(monkeypatch):
    _fresh(monkeypatch)
    entries = [
        {"contract_id": "A", "key": "call_status", "value": "Отказ",
         "visit_date": "2026-08-20", "responsible": "Иванов И.", "file": "U:\\a.xlsb"},
        {"contract_id": "A", "key": "visit_date", "value": "2026-08-20",
         "responsible": "Иванов И."},
        {"contract_id": "B", "key": "call_status", "value": "Обещание",
         "responsible": "Петров П."},
    ]
    status, body = _call("POST", PATH, headers=KEY,
                         json_body={"entries": entries, "receipt_digest": "ab" * 32})
    assert status == 200 and body == {"ok": True, "recorded": 3}

    status, body = _call("GET", PATH, headers=KEY)
    assert status == 200 and body["count"] == 3
    assert body["ledger"]["A"]["call_status"]["value"] == "Отказ"
    assert body["ledger"]["A"]["call_status"]["visit_date"] == "2026-08-20"
    assert body["ledger"]["A"]["call_status"]["responsible"] == "Иванов И."
    assert body["ledger"]["A"]["call_status"]["at"]

    # повтор с новым значением — последнее побеждает, дублей нет
    status, body = _call("POST", PATH, headers=KEY, json_body={"entries": [
        {"contract_id": "A", "key": "call_status", "value": "Обещание"}]})
    assert body["recorded"] == 1
    _s, body = _call("GET", PATH, headers=KEY)
    assert body["count"] == 3
    assert body["ledger"]["A"]["call_status"]["value"] == "Обещание"


def test_filter_by_employees_both_spellings(monkeypatch):
    _fresh(monkeypatch)
    _call("POST", PATH, headers=KEY, json_body={"entries": [
        {"contract_id": "A", "key": "call_status", "value": "x", "responsible": "Иванов И."},
        {"contract_id": "B", "key": "call_status", "value": "y", "responsible": "Петров П."},
        {"contract_id": "C", "key": "call_status", "value": "z", "responsible": "Сидоров С."},
    ]})
    _s, body = _call("GET", PATH + "?employee=Иванов%20И.&employee=Петров%20П.",
                     headers=KEY)
    assert set(body["ledger"]) == {"A", "B"}
    _s, body = _call("GET", PATH + "?employees=иванов%20и.;Сидоров%20С.", headers=KEY)
    assert set(body["ledger"]) == {"A", "C"}, "регистр ФИО не должен мешать"


def test_garbage_is_rejected_before_any_write(monkeypatch):
    _fresh(monkeypatch)
    status, body = _call("POST", PATH, headers=KEY, json_body={"entries": [
        {"contract_id": "A", "key": "call_status", "value": "ok"},
        {"contract_id": "B", "key": "call_comment", "value": "запрещённое поле"},
    ]})
    assert status == 409 and "call_comment" in body["error"]
    _s, body = _call("GET", PATH, headers=KEY)
    assert body["count"] == 0, "половина реестра записалась"

    status, _ = _call("POST", PATH, headers=KEY, json_body={"entries": "нет"})
    assert status == 409
    status, _ = _call("POST", PATH, headers=KEY, json_body=["список"])
    assert status == 400
    status, body = _call("POST", PATH, headers=KEY, json_body={"entries": []})
    assert status == 200 and body["recorded"] == 0


def test_device_token_is_enough(monkeypatch):
    """Ежедневный путь пульта — токен устройства, не мастер-ключ."""
    _fresh(monkeypatch)
    with db.connect() as conn:
        conn.execute("DELETE FROM admin_devices")
        conn.commit()
    _s, made = _call("POST", "/admin/device/enroll.json", headers=KEY,
                     json_body={"device_id": "fp", "name": "Ноут"})
    token = {"X-Device-Token": made["token"], "X-Device-Id": "fp"}
    status, body = _call("POST", PATH, headers=token, json_body={"entries": [
        {"contract_id": "A", "key": "call_status", "value": "ok"}]})
    assert status == 200 and body["recorded"] == 1
    status, body = _call("GET", PATH, headers=token)
    assert status == 200 and body["count"] == 1
