import asyncio
import sqlite3

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
import pytest

from crm.analyst_web import AnalystSite, COOKIE, PREFIX, create_app, password_hash


def test_owner_login_read_only_registry_and_immediate_revocation(tmp_path):
    asyncio.run(exercise_owner_login(tmp_path))


async def exercise_owner_login(tmp_path):
    primary = tmp_path / "primary.db"
    with sqlite3.connect(primary) as conn:
        conn.executescript("""
            CREATE TABLE accounts (id, username, role, status, must_change_password);
            INSERT INTO accounts VALUES (5,'analyst.qa','analyst','active',0);
            CREATE TABLE account_admin_grants (account_id, granted_by);
            INSERT INTO account_admin_grants VALUES (5,'ssh-root');
            CREATE TABLE clients (
                id INTEGER PRIMARY KEY, contract_id, client, partner, city,
                responsible_soft, responsible, debt_today, debt_total, phone
            );
            INSERT INTO clients VALUES
                (1,'A-101','Клиент <Проверка>','Кредитор','Город','Сотрудник','не закреплён','40','90','12345');
        """)
    origin = "https://analyst.example"
    original = "Synthetic pass one"
    changed = "Synthetic pass two"
    site = AnalystSite(primary, tmp_path / "state.db", origin, "analyst.qa", 5)
    site.initialize(password_hash(original))
    with site.authority() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM clients")

    async with TestServer(create_app(site, b"body{}")) as server:
        jars = lambda: CookieJar(unsafe=True, treat_as_secure_origin=server.make_url("/").origin())
        async with TestClient(server, cookie_jar=jars()) as client:
            denied = await client.get(PREFIX + "contract/1", allow_redirects=False)
            assert denied.status == 303
            assert "12345" not in await denied.text()
            fields = {"username": "analyst.qa", "password": original}
            assert (await client.post(PREFIX + "login", data=fields)).status == 403
            login = await client.post(PREFIX + "login", data=fields, headers={"Origin": origin}, allow_redirects=False)
            assert login.status == 303
            assert login.cookies[COOKIE]["secure"] and login.cookies[COOKIE]["httponly"]
            assert "A-101" in await (await client.get(PREFIX)).text()
            registry = await client.get(PREFIX, params={"q": "КЛИЕНТ"})
            assert registry.status == 200 and registry.headers["Cache-Control"] == "no-store"
            assert registry.headers["Referrer-Policy"] == "same-origin"
            assert "Клиент &lt;Проверка&gt;" in await registry.text()
            assert "12345" in await (await client.get(PREFIX + "contract/1")).text()
            assert "Назначено в Софт" in await (await client.get(PREFIX + "summary")).text()
            old_token = login.cookies[COOKIE].value
            changed_response = await client.post(
                PREFIX + "password", data={"current": original, "new": changed}, headers={"Origin": origin},
                allow_redirects=False,
            )
            assert changed_response.status == 303 and changed_response.cookies[COOKIE].value != old_token
            assert (await client.post(PREFIX + "login", data=fields, headers={"Origin": origin})).status == 401
            with sqlite3.connect(primary) as conn:
                conn.execute("DELETE FROM account_admin_grants")
            revoked = await client.get(PREFIX, allow_redirects=False)
            assert revoked.status == 303 and revoked.headers["Location"] == PREFIX + "login"
