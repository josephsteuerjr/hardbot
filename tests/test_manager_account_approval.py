"""Self registration and delegated approvals use disposable data and real HTTP auth."""
import asyncio
import json
import secrets
import hashlib

import pytest
from aiohttp.test_utils import TestClient, TestServer

import accounts
import db
import web
from test_pc_first_accounts import identity, proof
PCS = {}

MGR = 781001


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "delegated.db")
    monkeypatch.setattr(db, "admin_bootstrap_marker", lambda: tmp_path / "bootstrap")
    monkeypatch.setattr(web, "DEV", False)
    db.init_db()
    PCS.clear()
    with db.connect() as c:
        for cid, employee in (("SELF-1", "Own Employee"), ("SELF-2", "Foreign Employee")):
            c.execute("INSERT INTO clients(contract_id,client,responsible) VALUES(?,?,?)", (cid, "Synthetic Client", employee))
    db.set_manager(MGR, "Synthetic Leader", [], ["Own Employee"])
    token = secrets.token_urlsafe(32)
    with db.connect() as c:
        c.execute("INSERT INTO manager_devices(telegram_id,token_hash,device,created_at) VALUES(?,?,?,?)",
                  (MGR, hashlib.sha256(token.encode()).hexdigest(), "Synthetic PC", accounts._now()))
    web._ADMIN_FAILS.clear()
    return {"X-Pult-Token": token}


def call(method, path, headers=None, body=None):
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as client:
            r = await client.request(method, path, headers={"X-Client-Version":"3.2.0", **(headers or {})}, json=body)
            return r.status, json.loads(await r.text()) if r.content_type == "application/json" else {}
    return asyncio.run(run())


def register(employee="Own Employee", username="self-own", **extra):
    pc=PCS.setdefault(username,identity())
    return call("POST","/api/account/register",body={"employee":employee,"username":username,
        "password":"chosen123","pc_proof":proof(pc,employee,username,"chosen123"),**extra})


def login(username="self-own", fp="pc", kind="desktop"):
    pc=PCS.setdefault(username if fp=="pc" else fp,identity())
    return accounts.login(username,"chosen123",device={"kind":kind,"fp":pc[2] if kind=="desktop" else fp,"label":fp},
        entrance="public",audience="employee",gps={"lat":1,"lon":2},
        pc_proof=proof(pc,"",username,"chosen123") if kind=="desktop" else None)


def test_registration_does_not_accept_role_scope_or_uid(world):
    status, body = register(role="full", telegram_id=MGR, office="Foreign office")
    assert status == 201 and body["pending"]
    with db.connect() as c:
        a = c.execute("SELECT * FROM accounts").fetchone()
    assert a["role"] == "agent" and a["status"] == "awaiting" and a["telegram_id"] != MGR
    assert not a["must_change_password"] and a["office"] != "Foreign office"
    assert login()["action"] == "ok_pending"


def test_unknown_and_privileged_people_cannot_self_register(world):
    assert register("Unknown Employee")[0] == 400
    with db.connect() as c:
        c.execute("UPDATE clients SET responsible='Synthetic Leader' WHERE contract_id='SELF-1'")
    assert register("Synthetic Leader")[0] == 400
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_pending_registration_is_visible_and_manager_approves_real_device(world):
    assert register()[0]==201
    row=call("GET","/api/manager/entered",world)[1]["entered"][0]
    assert row["can_approve"]
    token=login()["token"]; dev=row["devices"][0]
    assert call("POST","/api/manager/account_approve",world,{"device_id":dev["id"],"by":"forged-admin"})[0]==404
    assert call("POST","/api/manager/account_approve",world,{"device_id":dev["id"],"observed_pc":True,"by":"forged-admin"})[1]["active"]
    status,bootstrap=call("GET","/api/bootstrap",{"X-App-Token":token})
    assert status==200 and not bootstrap.get("pending") and bootstrap["employee"]=="Own Employee"
    with db.connect() as c: events=c.execute("SELECT * FROM account_approval_events ORDER BY id").fetchall()
    assert len(events)==3 and all(e["actor_uid"]==MGR for e in events)
    assert {e['action'] for e in events}=={'account','pc_observation','device'}
    assert all(e["actor"].startswith(f"manager:{MGR}:") and "forged" not in e["actor"] for e in events)


def test_foreign_role_change_and_self_approval_fail_closed(world):
    assert register("Foreign Employee", "self-foreign")[0] == 201
    assert call("POST", "/api/manager/account_approve", world, {"username": "self-foreign"})[0] == 404
    assert register()[0] == 201
    db.set_manager(MGR, "Synthetic Leader", [], [])
    assert call("POST", "/api/manager/account_approve", world, {"username": "self-own"})[0] == 404
    db.set_manager(MGR, "Synthetic Leader", [], ["Own Employee"], role="security")
    assert call("POST", "/api/manager/account_approve", world, {"username": "self-own"})[0] == 403
    db.set_manager(MGR, "Own Employee", [], ["Own Employee"], role="manager")
    assert call("POST", "/api/manager/account_approve", world, {"username": "self-own"})[0] == 404


def test_approving_phone_revokes_old_and_revoked_phone_cannot_return(world):
    assert register()[0]==201
    pc=call("GET","/api/manager/entered",world)[1]["entered"][0]["devices"][0]["id"]
    assert call("POST","/api/manager/account_approve",world,{"device_id":pc,"observed_pc":True})[1]['active']
    first=login(fp="old",kind="phone");assert first['action']=='ok_pending'
    with db.connect() as c: old_id=c.execute("SELECT id FROM account_devices WHERE fingerprint='old'").fetchone()[0]
    assert call('POST','/api/manager/account_approve',world,{'device_id':old_id})[1]['active']
    old=call("GET","/api/manager/entered",world)[1]["entered"][0]["devices"][0]["id"]
    assert login(fp="new",kind="phone")['action']=='ok_pending'
    row=call("GET","/api/manager/entered",world)[1]["entered"][0]
    assert "gps" not in row["phone"] and all("gps" not in d for d in row["devices"])
    new=row["devices"][0]["id"]
    assert call("POST","/api/manager/account_approve",world,{"device_id":new})[1]['active']
    assert call("POST","/api/manager/account_approve",world,{"device_id":old})[0]==404
    assert db.agent_device_check(first['token'])[0]!='ok'
    with db.connect() as c: assert c.execute("SELECT status FROM account_devices WHERE id=?",(old,)).fetchone()[0]=="revoked"


@pytest.mark.parametrize("body", [[], {}, {"device_id": True}, {"device_id": "1"}, {"username": "self-own", "device_id": 1}])
def test_malformed_approval_does_not_write(world, body):
    assert call("POST", "/api/manager/account_approve", world, body)[0] == 400


def test_unauthenticated_approval_and_private_role_are_rejected(world):
    assert call("POST", "/api/manager/account_approve", body={"username": "self-own"})[0] in (401, 403)
    accounts.create_account("Own Employee", "privileged", "chosen123", role="manager")
    assert call("POST", "/api/manager/account_approve", world, {"username": "privileged"})[0] == 404


def test_manager_cannot_approve_own_known_employee_alias_with_another_uid(world):
    # Legacy manager records may contain a short name while the roster uses a full name.
    accounts.create_account("Own Employee", "alias-own", "chosen123", telegram_id=999123)
    db.set_bind(MGR, "Own Employee")
    assert call("POST", "/api/manager/account_approve", world, {"username": "alias-own"})[0] == 404
    rows = call("GET", "/api/manager/entered", world)[1]["entered"]
    assert rows[0]["can_approve"] is False
