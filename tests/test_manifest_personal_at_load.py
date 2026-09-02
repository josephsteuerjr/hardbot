"""Ярлык PWA уносит вход с собой: манифест персональный с момента загрузки.

02.09.2026: переустановленный ярлык на iPhone открывался без токена и получал
401. Safari скачивает манифест при загрузке страницы, до входа, и при «На экран
Домой» его не перечитывает, поэтому подмена <link rel=manifest> после входа
ничего не давала: стартовый адрес ярлыка оставался «/» без #t=<токен>.
"""

import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

import web

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "server" / "miniapp" / "index.html").read_text("utf-8")


def test_manifest_link_is_built_by_script_with_token_before_anything_else():
    head = INDEX.split("</head>", 1)[0]
    assert '<link rel="manifest" href="/manifest.webmanifest">' not in head, \
        "статичный манифест без токена — ярлык родится без входа"
    assert 'l.rel="manifest"' in head and '"/manifest.webmanifest"+(t?"?t="+' in head
    assert 'localStorage.getItem("app_token")' in head
    assert "window.MANIFEST_PERSONAL=!!t" in head
    # скрипт манифеста стоит раньше подключения Telegram SDK и любых стилей
    assert head.index('l.rel="manifest"') < head.index("telegram-web-app.js")


def test_first_login_reloads_once_so_the_manifest_becomes_personal():
    assert 'if(r===true && !(TG&&TG.initData) && !window.MANIFEST_PERSONAL){' in INDEX
    assert 'stampTokenUrl(); location.reload(); return "reload";' in INDEX
    assert 'if((await redeemFromUrl())==="reload") return;' in INDEX


def test_ios_home_screen_icon_and_meta():
    head = INDEX.split("</head>", 1)[0]
    assert '<link rel="apple-touch-icon" href="/vendor/icon-192.png">' in head
    assert '<meta name="apple-mobile-web-app-title" content="Хард">' in head
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in head


def test_apple_touch_icon_routes_serve_the_png():
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            out = {}
            for path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
                         "/favicon.ico"):
                resp = await client.get(path)
                body = await resp.read()
                out[path] = (resp.status, body[:8])
            return out
        finally:
            await client.close()

    out = asyncio.run(scenario())
    for path, (status, magic) in out.items():
        assert status == 200 and magic == b"\x89PNG\r\n\x1a\n", path
