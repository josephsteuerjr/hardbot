"""CORS-мидлварь пульта: заголовки должны стоять на ЛЮБОМ ответе /admin/* —
успех, 401/404/400 (raise HTTPException) и 500 (непредвиденная ошибка).

Регресс на баг «Failed to fetch при очистке»: POST /admin/reset на старом сервере
возвращал 404, но без CORS-заголовков → webview пульта блокировал ответ и показывал
«нет связи» вместо настоящего статуса. Мидлварь раньше навешивала заголовки только
после успешного `await handler(...)`; на raise/500 они терялись.
"""
import asyncio

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import web as webmod

CORS = "Access-Control-Allow-Origin"


def _run(method, path, handler):
    req = make_mocked_request(method, path)
    return asyncio.run(webmod._cors(req, handler))


def test_options_preflight_has_cors():
    async def h(_req):  # до хендлера дело не доходит — OPTIONS замыкается в мидлвари
        raise AssertionError("handler must not be called for OPTIONS")
    resp = _run("OPTIONS", "/admin/reset", h)
    assert resp.status == 204
    assert resp.headers[CORS] == "*"
    assert "X-Admin-Key" in resp.headers["Access-Control-Allow-Headers"]


def test_success_has_cors():
    async def h(_req):
        return web.json_response({"ok": True})
    resp = _run("POST", "/admin/reset", h)
    assert resp.headers[CORS] == "*"


def test_http_error_has_cors():
    """404 (отсутствующая ручка) / 401 / 400 — raise HTTPException, но с CORS-заголовками."""
    for exc_cls in (web.HTTPNotFound, web.HTTPUnauthorized, web.HTTPBadRequest):
        async def h(_req, _e=exc_cls):
            raise _e(text="boom")
        try:
            _run("POST", "/admin/reset", h)
            assert False, f"{exc_cls.__name__} должен был подняться"
        except web.HTTPException as exc:
            assert exc.status == exc_cls().status
            assert exc.headers[CORS] == "*", f"нет CORS на {exc_cls.__name__}"


def test_internal_error_has_cors():
    """Непредвиденная ошибка → 500 с CORS-заголовками (а не голый краш без них)."""
    async def h(_req):
        raise RuntimeError("db upstream died")
    resp = _run("POST", "/admin/reset", h)
    assert resp.status == 500
    assert resp.headers[CORS] == "*"


def test_non_admin_default_no_cors():
    """Миниапп /api/* без CORS_ALLOW_ORIGIN — заголовок не ставим (same-origin за Caddy)."""
    webmod.CORS_ALLOW_ORIGIN = ""  # дефолт окружения тестов
    async def h(_req):
        return web.json_response({"ok": True})
    resp = _run("GET", "/api/bootstrap", h)
    assert CORS not in resp.headers
