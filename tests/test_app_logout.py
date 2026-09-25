"""«Выйти с этого телефона»: POST /api/logout гасит ИМЕННО предъявленный токен.

Оболочка стирает телефон и сама, но у ярлыка iOS хранилище своё, а токен вшит
в его start_url: без серверного отзыва ярлык продолжал бы входить. Выход с
одного телефона — не отзыв доступа: второй телефон того же человека и его
ссылки живут дальше.
"""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

import db
import web


def run(scenario):
    async def main():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            return await scenario(client)
        finally:
            await client.close()
    return asyncio.run(main())


def test_logout_drops_only_the_presented_token():
    db.init_db()
    code = db.create_app_link(730101)
    phone = db.redeem_app_link(code)
    shortcut = db.redeem_app_link(code)          # второй вход того же человека
    assert db.uid_for_app_token(phone) == 730101

    async def scenario(c):
        r = await c.post("/api/logout", headers={"X-App-Token": phone})
        return r.status, await r.json()

    status, body = run(scenario)
    assert status == 200 and body == {"ok": True}
    assert db.uid_for_app_token(phone) is None, "токен этого телефона погашен"
    assert db.uid_for_app_token(shortcut) == 730101, "второй вход человека жив"
    with db.connect() as conn:
        rows = conn.execute("SELECT actor FROM audit WHERE action='app_logout'").fetchall()
    assert [r["actor"] for r in rows][-1] == 730101


def test_logout_answers_the_same_for_unknown_and_missing_tokens():
    """Ручка не служит проверкой «жив ли чужой токен»: ответ одинаковый."""
    db.init_db()

    async def scenario(c):
        a = await c.post("/api/logout", headers={"X-App-Token": "нет-такого"})
        b = await c.post("/api/logout")
        return (a.status, await a.json()), (b.status, await b.json())

    unknown, missing = run(scenario)
    assert unknown == missing == (200, {"ok": True})


def test_logout_with_a_stolen_hash_changes_nothing():
    """В базе лежит хэш токена; предъявленный хэш — не токен, гасить нечего."""
    db.init_db()
    tok = db.redeem_app_link(db.create_app_link(730102))

    async def scenario(c):
        r = await c.post("/api/logout", headers={"X-App-Token": db.app_token_key(tok)})
        return r.status

    assert run(scenario) == 200
    assert db.uid_for_app_token(tok) == 730102
