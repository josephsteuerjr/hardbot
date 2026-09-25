"""Защитные заголовки на каждом ответе (ревью 25.09, #14).

Без них вредная страница прятала наш адрес в невидимый фрейм и наводила
руководителя на «одобрить»/«снять»/«отозвать вход» — вкладка уже авторизована
токеном. Caddy на публикаторе заголовков не ставит, их ставит приложение, и
ставит на ВСЁ: на ошибки (401/404/500), на потоковую отдачу карты, на
preflight. Запрет фреймов зависит от Телеграма: мини-приложение в
web.telegram.org живёт во фрейме.
"""
import asyncio
import os

import pytest
from aiohttp.test_utils import TestClient, TestServer

import db
import maplink
import web

DENY_ALL = "frame-ancestors 'none'"
TELEGRAM = "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"


def _fetch(requests, *, bot=None, extra_routes=()):
    """Прогнать запросы одним сервером: [(метод, путь, заголовки)] → ответы."""
    async def scenario():
        app = web.build_app(bot)
        for method, path, handler in extra_routes:
            app.router.add_route(method, path, handler)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            out = []
            for method, path, headers in requests:
                resp = await client.request(method, path, headers=headers or {})
                await resp.read()
                out.append((resp.status, resp.headers))
            return out
        finally:
            await client.close()

    return asyncio.run(scenario())


@pytest.fixture()
def telegram_off(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "")
    db.init_db()


def _assert_locked(status, headers, where):
    assert headers.get("X-Content-Type-Options") == "nosniff", where
    assert headers.get("Referrer-Policy") == "no-referrer", where
    assert headers.get("X-Frame-Options") == "DENY", where
    assert headers.get("Content-Security-Policy") == DENY_ALL, where


def test_every_kind_of_response_carries_the_headers(telegram_off):
    (index, boot, manifest, missing, admin, preflight) = _fetch([
        ("GET", "/", None),
        ("GET", "/api/bootstrap", None),                   # без токена
        ("GET", "/manifest.webmanifest", None),
        ("GET", "/нет-такой-страницы", None),
        ("GET", "/admin/stats", None),
        ("OPTIONS", "/admin/stats", None),
    ])
    assert index[0] == 200 and boot[0] == 401 and manifest[0] == 200
    assert missing[0] == 404 and admin[0] == 401 and preflight[0] == 204
    for name, (status, headers) in zip(
            ("/", "401 bootstrap", "manifest", "404", "401 admin", "preflight"),
            (index, boot, manifest, missing, admin, preflight)):
        _assert_locked(status, headers, name)


def test_headers_set_by_a_handler_are_kept(telegram_off):
    """Кэш оболочки и манифеста задаёт хендлер — общий слой его не трогает."""
    (index, manifest) = _fetch([("GET", "/", None),
                                ("GET", "/manifest.webmanifest", None)])
    assert index[1]["Cache-Control"] == "no-store, max-age=0"
    assert manifest[1]["Cache-Control"] == "no-store"
    admin = _fetch([("GET", "/admin/stats", None)])[0]
    assert admin[1]["Access-Control-Allow-Origin"], "CORS пульта пропал"


def test_unexpected_error_still_carries_the_headers(telegram_off):
    async def boom(_request):
        raise RuntimeError("упало внутри")

    (status, headers), = _fetch([("GET", "/__boom", None)],
                                extra_routes=[("GET", "/__boom", boom)])
    assert status == 500
    _assert_locked(status, headers, "500")


def test_streamed_map_file_carries_the_headers(telegram_off, monkeypatch, tmp_path):
    """Карта отдаётся FileResponse по Range — middleware видел бы её уже
    отправленной, поэтому заголовки ставятся перед отправкой."""
    tiles = tmp_path / "basemap.pmtiles"
    tiles.write_bytes(os.urandom(4096))
    monkeypatch.setattr(web, "TILES_FILE", tiles)
    (status, headers), = _fetch([("GET", "/tiles/basemap.pmtiles",
                                  {"Range": "bytes=0-99"})])
    assert status == 206
    _assert_locked(status, headers, "pmtiles")


def test_day_map_keeps_the_referrer_osm_needs(telegram_off):
    """Растровый фолбэк страницы карты ходит прямо на tile.openstreetmap.org,
    а без Referer там отказывают. Уходит только адрес сайта."""
    qs = maplink.sign("Картограф Заголовков", "2026-09-25")
    (status, headers), = _fetch([("GET", f"/manager/map?{qs}", None)])
    assert status == 200
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert headers["X-Frame-Options"] == "DENY"


def test_telegram_frame_is_allowed_only_when_telegram_is_on(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456789:" + "T" * 35)
    db.init_db()
    for status, headers in _fetch([("GET", "/", None), ("GET", "/api/bootstrap", None)]):
        assert "X-Frame-Options" not in headers, "фрейм web.telegram.org запрещён"
        assert headers["Content-Security-Policy"] == TELEGRAM
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"


def test_running_bot_counts_as_telegram_even_with_empty_env(monkeypatch):
    """Токен бывает только в settings (сменён из пульта): тогда бот запущен,
    хотя окружение пусто, — и мини-приложению нужен фрейм."""
    monkeypatch.setenv("BOT_TOKEN", "")
    db.init_db()
    (status, headers), = _fetch([("GET", "/", None)], bot=object())
    assert "X-Frame-Options" not in headers
    assert headers["Content-Security-Policy"] == TELEGRAM


def test_only_frame_ancestors_in_the_policy(telegram_off):
    """Полный CSP сломал бы страницу карты (maplibre с CDN) и оболочку
    (встроенные скрипты) — политика ограничена фреймами."""
    (status, headers), = _fetch([("GET", "/", None)])
    assert headers["Content-Security-Policy"].split()[0] == "frame-ancestors"
    assert "script-src" not in headers["Content-Security-Policy"]
