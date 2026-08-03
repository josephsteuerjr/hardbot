"""Граница регистратора разрешений на импорт.

Регистрация — отдельный акт от самой загрузки: одного admin-ключа не должно
хватать, чтобы залить в бота произвольный реестр. Владелец может открыть узкую
HTTP-ручку на СВОЁМ стенде, но она обязана оставаться выключенной по умолчанию и
никогда не принимать боевой `published_master` — для него регистратор живёт
только рядом с данными.
"""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

import db
import web

HEADERS = {"X-Admin-Key": "test-admin-key"}


def _post(body: dict) -> tuple[int, dict]:
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.post("/admin/import/authorize.json",
                                     json=body, headers=HEADERS)
            return resp.status, await resp.json()
        finally:
            await client.close()

    return asyncio.run(scenario())


def test_disabled_by_default(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(web, "IMPORT_REGISTRAR_HTTP", False)

    def forbidden(*_a, **_k):
        raise AssertionError("регистрация выполнилась при выключенной ручке")

    monkeypatch.setattr(db, "register_import_authorization", forbidden)
    status, body = _post({"source_kind": db.IMPORT_SOURCE_TEST_SUBSET,
                          "source_proof": {"x": 1}})
    assert status == 403
    assert body["blocked"] is True and body["enabled"] is False


def test_unknown_source_kind_is_refused(monkeypatch):
    """Вид источника — из закрытого списка, а не из тела запроса."""
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(web, "IMPORT_REGISTRAR_HTTP", True)

    def forbidden(*_a, **_k):
        raise AssertionError("зарегистрирован неизвестный source_kind")

    monkeypatch.setattr(db, "register_import_authorization", forbidden)
    for kind in ("personal_book", "", None, "PUBLISHED_MASTER"):
        status, body = _post({"source_kind": kind, "source_proof": {"x": 1}})
        assert status == 409, kind
        assert body["blocked"] is True


def test_both_kinds_register_when_enabled(monkeypatch):
    """Владелец открыл ручку — она работает для обоих видов источника.

    Это осознанный размен: с открытой ручкой admin-ключ становится достаточным
    условием загрузки. Закрытие флага возвращает двухшаговый порядок целиком.
    """
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(web, "IMPORT_REGISTRAR_HTTP", True)
    seen = {}

    def register(proof, *, source_kind, ttl_hours):
        seen.update(proof=proof, source_kind=source_kind, ttl_hours=ttl_hours)
        return {"proof_digest": "a" * 64, "status": "pending"}

    monkeypatch.setattr(db, "register_import_authorization", register)
    for kind in (db.IMPORT_SOURCE_TEST_SUBSET, db.IMPORT_SOURCE_PUBLISHED_MASTER):
        status, body = _post({"source_kind": kind, "source_proof": {"x": 1}})
        assert status == 200 and body["ok"] is True, kind
        assert seen["source_kind"] == kind
        assert seen["ttl_hours"] == 24


def test_bad_proof_is_a_refusal_not_a_crash(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(web, "IMPORT_REGISTRAR_HTTP", True)

    def boom(*_a, **_k):
        raise web.ExchangeContractError("proof schema unknown")

    monkeypatch.setattr(db, "register_import_authorization", boom)
    status, body = _post({"source_kind": db.IMPORT_SOURCE_TEST_SUBSET,
                          "source_proof": {"x": 1}})
    assert status == 409 and body["blocked"] is True


def test_key_is_still_required(monkeypatch):
    """Ручка не должна оказаться дырой в обход admin-ключа."""
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(web, "IMPORT_REGISTRAR_HTTP", True)

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.post("/admin/import/authorize.json",
                                     json={"source_kind": "test_subset"})
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) in (401, 403)
