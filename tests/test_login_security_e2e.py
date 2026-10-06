"""Real HTTP admission: synthetic identities, isolated DB, no production tokens."""
import asyncio
import json
import pytest
from aiohttp.test_utils import TestClient, TestServer
import db
import web

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "entry-e2e.db")
    monkeypatch.setattr(web, "DEV", False)
    monkeypatch.setenv("ADMIN_API_KEY", "synthetic-key")
    monkeypatch.setenv("MINIAPP_URL", "https://synthetic.invalid")
    db.init_db()
    db.reset_role_caches()
    with db.connect() as c:
        c.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft,country) VALUES (?,?,?,?,?)",
                  ("SYN-SOFT", "Synthetic client", "", "Synthetic Soft", "Узбекистан"))
        c.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft,country) VALUES (?,?,?,?,?)",
                  ("SYN-ALIEN", "Other client", "Other employee", "Other employee", "Узбекистан"))

def test_new_soft_employee_does_not_inherit_reserved_manager_role():
    async def scenario():
        manager_uid = 910_000_000_003
        db.set_manager(manager_uid, "Different leader", [], ["Synthetic Soft"])
        async with TestClient(TestServer(web.build_app(None))) as client:
            issued = await client.post("/admin/app_link.json", headers={"X-Admin-Key": "synthetic-key"},
                                       json={"employee": "Synthetic Soft"})
            link = await issued.json()
            assert issued.status == 200 and link["telegram_id"] > manager_uid
            wrong = await client.post("/api/manager/device/enroll", json={"code": link["code"]})
            assert wrong.status == 403
            enrolled = await client.post("/api/agent/device/enroll", json={"code": link["code"], "device": "Synthetic PC"})
            entry = await enrolled.json()
            assert enrolled.status == 200
            headers = {"X-App-Token": entry["token"]}
            boot = await client.get("/api/bootstrap?mode=soft", headers=headers)
            body = await boot.json()
            assert boot.status == 200 and body["employee"] == "Synthetic Soft"
            assert body["roles"]["soft"] and not body["roles"].get("manager")
            assert [p["rep"] for p in body["persons"]] == ["SYN-SOFT"]
            for path in ["/api/manager/device/me", "/api/manager/app_links", "/admin/app_links.json"]:
                response = await client.get(path, headers=headers)
                assert response.status in (401, 403), path
            foreign = await client.get("/api/person?mode=soft&rep=SYN-ALIEN", headers=headers)
            assert foreign.status in (403, 404)
            with db.connect() as c:
                assert c.execute("SELECT uses FROM app_links WHERE code=?", (link["code"],)).fetchone()[0] == 1
    asyncio.run(scenario())

def test_employee_pc_cannot_gain_manager_capabilities_from_later_role_change():
    uid = 554433
    db.set_bind(uid, "Synthetic Soft")
    code = db.create_app_link(uid)
    pc = db.enroll_agent_device(code, "Synthetic PC")
    db.set_manager(uid, "Same real person", [], ["Synthetic Soft", "Other employee"])
    async def scenario():
        async with TestClient(TestServer(web.build_app(None))) as client:
            headers = {"X-App-Token": pc[3]}
            response = await client.get("/api/bootstrap?mode=soft", headers=headers)
            data = await response.json()
            assert not data["roles"].get("manager")
            assert data["employee"] == "Synthetic Soft"
            assert (await client.get("/api/manager/app_links", headers=headers)).status == 403
    asyncio.run(scenario())

def test_exhausted_new_link_does_not_hide_older_pc_activation():
    uid = 554433
    db.set_bind(uid, "Synthetic Soft")
    old = db.create_app_link(uid)
    assert db.redeem_app_link(old, "android")
    new = db.create_app_link(uid)
    assert db.redeem_app_link(new, "android")
    assert db.redeem_app_link(new, "android")
    assert db.enroll_agent_device(new, "PC")
    state = next(r for r in db.app_links_state() if r["telegram_id"] == uid)
    assert state["code"] == old and state["desktop_available"]
