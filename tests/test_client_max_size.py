"""Предел тела запроса — 64 МБ, а не 256 (ревью 25.09, #13).

Один запрос в четверть гигабайта занимал память сервера целиком. Самое
тяжёлое законное — загрузка реестра из пульта и фото (до PHOTO_MAX_BYTES),
и то и другое помещается с запасом.
"""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

import db
import web

LIMIT = 64 * 1024 * 1024


def test_limit_is_64_megabytes():
    assert web.CLIENT_MAX_SIZE == LIMIT
    assert web.build_app(None)._client_max_size == LIMIT
    assert web.PHOTO_MAX_BYTES < LIMIT, "фото перестало бы помещаться в запрос"


def test_body_over_the_limit_is_refused_with_413():
    """Ручка без авторизации, читающая тело: обмен кода привязки."""
    db.init_db()

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.post("/admin/device/redeem.json",
                                     data=b" " * (LIMIT + 1),
                                     headers={"Content-Type": "application/json"})
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 413
