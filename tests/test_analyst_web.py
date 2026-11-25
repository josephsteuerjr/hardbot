import asyncio
import sqlite3

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
import pytest

from crm.analyst_web import AnalystSite, COOKIE, PREFIX, create_app, password_hash
from crm.analyst_registry_write import Catalog, card


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


def test_analyst_changes_are_versioned_without_extending_the_server_owner(tmp_path):
    asyncio.run(exercise_analyst_write(tmp_path))


async def exercise_analyst_write(tmp_path):
    primary = tmp_path / "primary.db"
    cols = set(Catalog().labels) | {
        "id", "contract_id", "client", "partner", "city", "responsible_soft", "responsible",
        "debt_today", "debt_total", "phone", "updated_at",
    }
    columns = ",".join(f'"{key}" TEXT' for key in sorted(cols - {"id"}))
    with sqlite3.connect(primary) as conn:
        conn.executescript(f"""
            CREATE TABLE accounts (id, username, role, status, must_change_password);
            INSERT INTO accounts VALUES (5,'owner.qa','analyst','active',0);
            CREATE TABLE account_admin_grants (account_id, granted_by);
            INSERT INTO account_admin_grants VALUES (5,'ssh-root');
            CREATE TABLE managers (telegram_id INTEGER, name, role, direction);
            INSERT INTO managers VALUES (77,'QA analyst','full','all');
            CREATE TABLE clients (id INTEGER PRIMARY KEY, {columns});
            INSERT INTO clients(id,contract_id,client,partner,phone) VALUES (1,'A-101','Test','QA','111');
            CREATE TABLE audit(ts,actor,action,target,detail,event_id UNIQUE);
        """)
    operator = dict(username="analyst.qa", uid=77, name="QA analyst", directory_role="full",
                    verifier=password_hash("Synthetic analyst password"))
    site = AnalystSite(primary, tmp_path / "web.db", "https://example.test", "owner.qa", 5, operator)
    site.initialize(password_hash("Synthetic owner password"))
    headers = {"Origin": site.origin}
    route = PREFIX + "contract/1/edit"
    with site.authority() as conn:
        _, revision = card(conn, site.catalog, 1)
    async with TestServer(create_app(site, b"body{}")) as server:
        def jar():
            return CookieJar(unsafe=True, treat_as_secure_origin=server.make_url("/").origin())
        async with TestClient(server, cookie_jar=jar()) as owner, TestClient(server, cookie_jar=jar()) as analyst:
            for client, username, password in ((owner, "owner.qa", "Synthetic owner password"),
                                               (analyst, "analyst.qa", "Synthetic analyst password")):
                assert (await client.post(PREFIX + "login", headers=headers, allow_redirects=False,
                                          data=dict(username=username, password=password))).status == 303
            edit = dict(revision=revision, operation="a"*32, field="phone", value="222")
            assert (await owner.post(route, headers=headers, data=edit)).status == 403
            assert (await analyst.get(route)).status == 200
            for _ in range(2):
                assert (await analyst.post(route, headers=headers, data=edit, allow_redirects=False)).status == 303
            assert (await analyst.post(route, headers=headers, data={**edit, "operation": "b"*32})).status == 409
            assert (await analyst.post(route, headers=headers, data={**edit, "field": "responsible"})).status == 400
            with sqlite3.connect(primary) as conn:
                assert conn.execute("SELECT phone FROM clients WHERE id=1").fetchone()[0] == "222"
                assert conn.execute("SELECT actor,count(*) FROM audit").fetchone() == (77, 1)
                conn.execute("UPDATE managers SET direction='tj'")
            assert (await analyst.post(route, headers=headers, data=edit, allow_redirects=False)).status == 303
            assert (await owner.get(PREFIX)).status == 200
