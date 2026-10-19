"""Account lifecycle and HTTP authority regressions, using disposable data only."""
import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

import accounts
import db
import web


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "review.db")
    monkeypatch.setattr(db, "admin_bootstrap_marker", lambda: tmp_path / "bootstrap")
    monkeypatch.setattr(web, "DEV", False)
    db.init_db()
    assert accounts.create_account("Synthetic Employee", "review-user", "initial123")["ok"]
    web._ADMIN_FAILS.clear()


def enter(fp="pc", kind="desktop", **kwargs):
    return accounts.login("review-user", "initial123",
                          device={"kind": kind, "fp": fp, "label": fp},
                          entrance="internal", **kwargs)


def device(fp="pc"):
    with db.connect() as conn:
        return dict(conn.execute("SELECT * FROM account_devices WHERE fingerprint=? "
                                 "ORDER BY id DESC", (fp,)).fetchone())


def call(method, path, headers=None, body=None):
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as client:
            response = await client.request(method, path, headers=headers, json=body)
            return response.status, await response.json()
    return asyncio.run(run())


def test_disable_revokes_pending_tokens_and_enable_requires_approval(world):
    token = enter()["token"]
    accounts.set_status("review-user", "disabled", "test")
    assert db.agent_device_check(token)[0] != "ok"
    accounts.set_status("review-user", "active", "test")
    with db.connect() as conn:
        row = conn.execute("SELECT status,approvals FROM accounts").fetchone()
    assert row["status"] == "awaiting"
    assert json.loads(row["approvals"]) == []
    assert device()["status"] == "revoked"


def test_identity_distinguishes_pending_from_revocation(world):
    token=enter()['token']
    status, body=call('GET','/api/identity',{'X-App-Token':token})
    assert status==200 and body['pending'] and body['uid']==0
    assert accounts.set_status('review-user','disabled','test')['ok']
    async def revoked():
        async with TestClient(TestServer(web.build_app(None))) as client:
            r=await client.get('/api/identity',headers={'X-App-Token':token})
            return r.status
    assert asyncio.run(revoked())==401


def test_manager_without_team_has_valid_own_identity(world):
    assert accounts.create_account('Synthetic Empty Manager','empty-manager','initial123',role='manager')['ok']
    result=accounts.login('empty-manager','initial123',device={'kind':'desktop','fp':'empty-manager-pc'},entrance='internal',audience='manager')
    assert result.get('token'),result
    with db.connect() as conn:
        acc=conn.execute("SELECT * FROM accounts WHERE username='empty-manager'").fetchone()
        dev=conn.execute('SELECT id FROM account_devices WHERE account_id=?',(acc['id'],)).fetchone()
    assert accounts.approve_account('empty-manager','test')['ok']
    assert accounts.approve_device(dev['id'],'test')['ok']
    assert accounts.change_password('empty-manager','initial123','changed123')['ok']
    status,body=call('GET','/api/identity',{'X-Pult-Token':result['token']})
    assert status==200 and body['uid']==acc['telegram_id'] and not body.get('pending')


def test_revoked_phone_cannot_be_approved_again(world):
    enter("old", "phone", gps={"lat": 1, "lon": 2, "accuracy": 3})
    old = device("old")
    accounts.approve_device(old["id"], "test")
    enter("new", "phone", gps={"lat": 1, "lon": 2, "accuracy": 3})
    fresh = device("new")
    accounts.approve_device(fresh["id"], "test")
    assert not accounts.approve_device(old["id"], "test")["ok"]
    assert device("new")["status"] == "active"


def test_fingerprint_does_not_merge_phone_and_desktop(world):
    enter("same", "phone", gps={"lat": 1, "lon": 2, "accuracy": 3})
    enter("same")
    assert not accounts.entered_list()[0]["ready"]
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM account_devices").fetchone()[0] == 2


@pytest.mark.parametrize("gps", [
    {"lat": float("nan"), "lon": 2}, {"lat": 91, "lon": 2},
    {"lat": 1, "lon": 181}, {"lat": 1, "lon": 2, "accuracy": -1},
])
def test_gps_requires_finite_geographic_coordinates(world, gps):
    result=enter("phone","phone",gps=gps)
    # Login GPS is optional under the accepted mobile registration policy.
    # Invalid telemetry still cannot become trusted coordinates or activation.
    assert result["action"]=="ok_pending" and not result["gps_exception_pending"]
    assert not device("phone")["gps"] and device("phone")["status"]=="pending"


def test_password_endpoint_cannot_bypass_account_lock(world):
    for _ in range(accounts.LOCK_AFTER_FAILS):
        assert not accounts.change_password("review-user", "wrong1234", "changed123")["ok"]
    assert enter()["action"] == "locked"
    assert not accounts.change_password("review-user", "initial123", "changed123")["ok"]


def test_admin_reset_revokes_sessions_and_clears_lock(world):
    token = enter()["token"]
    for _ in range(accounts.LOCK_AFTER_FAILS):
        accounts.login("review-user", "wrong1234", device={"fp": "pc"}, entrance="internal")
    assert accounts.set_password("review-user", "reset1234")["ok"]
    assert db.agent_device_check(token)[0] != "ok"
    assert accounts.login("review-user", "reset1234", device={"fp": "pc"},
                          entrance="internal")["action"] == "ok_pending"


def test_password_change_is_enforced_after_restart(world):
    token = enter()["token"]
    accounts.approve_account("review-user", "test")
    accounts.approve_device(device()["id"], "test")
    headers = {"X-App-Token": token}
    status, body = call("GET", "/api/bootstrap", headers)
    assert status == 200 and body.get("must_change_password") is True
    assert body.get("username") == "review-user" and not body.get("persons")
    assert accounts.change_password("review-user", "initial123", "changed123")["ok"]
    status, body = call("GET", "/api/bootstrap", headers)
    assert status == 200 and not body.get("pending")


def test_bad_password_json_returns_400(world):
    assert call("POST", "/api/account/password", body=[])[0] == 400


def test_failed_privileged_creation_does_not_add_manager(world):
    with db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM managers").fetchone()[0]
    assert not accounts.create_account("Other Person", "review-user", "initial123", role="manager")["ok"]
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM managers").fetchone()[0] == before


def test_expected_uid_refusal_does_not_rotate_existing_token(world):
    token = enter()["token"]
    result = accounts.login("review-user", "initial123", device={"kind": "desktop", "fp": "pc"},
                            entrance="internal", expected_uid=123)
    assert result["action"] == "conflict" and not result.get("token")
    assert db.agent_device_check(token)[0] == "ok"


def test_http_approvals_use_registered_admin_identity(world, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "synthetic-master")
    # A bound operator device can approve only as the principal registered via SSH.
    enrolled = db.enroll_admin_device("admin-fp", "Synthetic Admin")
    assert enrolled.get("token")
    headers = {"X-Device-Token": enrolled["token"], "X-Device-Id": "admin-fp"}
    assert accounts.create_account("Synthetic Manager", "review-manager", "initial123",
                                   role="manager")["ok"]
    for by in ("first-admin", "second-admin"):
        status, _ = call("POST", "/admin/account_approve.json", headers,
                         {"username": "review-manager", "by": by})
        assert status == 403
    assert accounts.register_approver(enrolled["id"], "first-admin")["ok"]
    for by in ("first-admin", "second-admin"):
        status, body = call("POST", "/admin/account_approve.json", headers,
                            {"username": "review-manager", "by": by})
        assert status == 200 and body["active"] is True
        assert len(body["approvals"]) == 1
    second = db.enroll_admin_device("another-fp", "Another Admin")
    assert accounts.register_approver(second["id"], " FIRST-ADMIN ")["ok"]
    other_headers = {"X-Device-Token": second["token"], "X-Device-Id": "another-fp"}
    assert call("POST", "/admin/account_approve.json", other_headers,
                {"username": "review-manager"})[1]["active"] is True
    assert accounts.register_approver(second["id"], "second-admin")["ok"]
    assert call("POST", "/admin/account_approve.json", other_headers,
                {"username": "review-manager"})[1]["active"] is True


def test_concurrent_product_logins_share_one_device_slot(world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    def run():
        barrier.wait(timeout=5)
        return enter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert all(r.get("token") for r in results)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM account_devices").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM agent_devices WHERE revoked_at IS NULL").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(DISTINCT device_id) FROM account_token_sessions").fetchone()[0] == 1


def test_bulk_accepts_semicolon_csv_and_generates_readable_logins(world, tmp_path, capsys):
    import account_access
    from argparse import Namespace
    source = tmp_path / "staff.csv"
    source.write_text("ФИО;офис;логин;пароль\nИванов Иван;Тест;;synthetic123\n"
                      "Ivanov Ivan;Тест;;synthetic123\n", encoding="utf-8-sig")
    assert account_access.cmd_bulk(Namespace(csv=str(source))) == 0
    with db.connect() as conn:
        rows = conn.execute("SELECT username,office FROM accounts WHERE username LIKE 'ivanov%'").fetchall()
    assert [(r["username"], r["office"]) for r in rows] == [("ivanov.ivan", "Тест"), ("ivanov.ivan.2", "Тест")]
    capsys.readouterr()
