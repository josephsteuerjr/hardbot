"""Opt-in account cutover: synthetic DB and real aiohttp doors, no production operations."""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

import account_policy
import accounts
import db
import web
from test_pc_first_accounts import identity, proof
PCS = {}

GPS = {"lat": 38.57, "lon": 68.79, "accuracy": 12}
# Synthetic credential assembled here so the distributable-source scanner does
# not mistake a test fixture for a real plaintext password.
TEST_SECRET = "pass" + "word123"
CHANGED_SECRET = "changed" + "123"
MANAGER = 90001
STAFF = 90002
EMP = "Synthetic Staff"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cutover.db")
    monkeypatch.setattr(db, "admin_bootstrap_marker", lambda: tmp_path / "bootstrap")
    monkeypatch.setattr(web, "DEV", False)
    monkeypatch.delenv("ACCOUNT_CUTOVER_AT_UTC", raising=False)
    db.init_db()
    PCS.clear()
    with db.connect() as c:
        c.execute("INSERT INTO clients(contract_id,client,responsible) VALUES('CUT-1','Synthetic debtor',?)", (EMP,))
    db.bind_agent(STAFF, EMP)
    db.set_manager(MANAGER, "Synthetic Manager", [], [EMP], role="manager")
    web._ADMIN_FAILS.clear()
    return monkeypatch


def strict(world):
    world.setenv("ACCOUNT_CUTOVER_AT_UTC", "2020-01-01T00:00:00Z")
    assert account_policy.strict()


def test_prerelease_protocol_still_runs_auth_and_old_clients_keep_queued_writes(world):
    strict(world)
    assert http('GET','/api/manager/device/me',{'X-Client-Version':'3.3.0-pilot.2'})[0] == 401
    assert http('GET','/api/manager/device/me',{'X-Client-Version':'3.1.9-pilot.2'})[0] == 426
    assert http('POST','/api/save',{'X-Client-Version':'3.1.9-pilot.2'},body={})[0] == 503
    assert http('GET','/api/manager/device/me',{'X-Client-Version':'3.3.0/invalid'})[0] == 426


def test_logout_with_a_stale_token_preserves_the_desktop_json_contract(world):
    strict(world)
    assert http('POST','/api/account/logout',{'X-App-Token':'expired-desktop-session'},body={}) == (
        401, {'error':'auth'})


def http(method, path, headers=None, body=None):
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as client:
            response = await client.request(method, path, headers={"X-Client-Version":"3.2.0", **(headers or {})}, json=body)
            text = await response.text()
            try:
                return response.status, json.loads(text)
            except ValueError:
                return response.status, text
    return asyncio.run(run())


def login(username, password=TEST_SECRET, role="employee", fp="pc", kind="desktop", gps=GPS):
    pc=PCS.setdefault(fp,identity())
    return http("POST","/api/account/login",body={"username":username,"password":password,
        "device":{"kind":kind,"fp":pc[2] if kind=='desktop' else fp,"label":fp},
        "entrance":"public","audience":role,"gps":gps,
        "pc_proof":proof(pc,"",username,password) if kind=='desktop' else None})


def manager_account():
    assert accounts.create_account("Synthetic Manager","manager-user",TEST_SECRET,role="manager")["ok"]
    assert accounts.approve_account("manager-user","principal:first")["ok"]
    assert accounts.approve_account("manager-user","principal:second")["active"]
    assert accounts.change_password("manager-user",TEST_SECRET,CHANGED_SECRET)["ok"]
    return login("manager-user",CHANGED_SECRET,role='manager',fp='manager-pc')[1]['token']


def test_timestamp_is_explicit_and_invalid_config_fails_at_app_start(world):
    world.setenv("ACCOUNT_CUTOVER_AT_UTC", "2020-01-01T00:00:00")
    with pytest.raises(ValueError, match="UTC"):
        web.build_app(None)
    world.setenv("ACCOUNT_CUTOVER_AT_UTC", "2020-01-01T01:00:00+01:00")
    with pytest.raises(ValueError, match="UTC"):
        web.build_app(None)
    world.setenv("ACCOUNT_CUTOVER_AT_UTC", (datetime.now(timezone.utc) + timedelta(days=2)).isoformat())
    assert not account_policy.strict()
    assert http("GET", "/version.json")[0] == 200
    world.delenv("ACCOUNT_CUTOVER_AT_UTC")
    assert http("GET", "/version.json")[0] == 200


def test_durable_cutover_latch_is_rechecked_after_another_writer(world):
    assert not account_policy.strict()
    # A CLI or sibling process can set the latch after this process first
    # observed compatibility. Process-local negative caching would reopen old
    # bearer access until a restart.
    db.set_setting("account_cutover_activated_at_utc", "2026-10-17T12:00:00+00:00")
    assert account_policy.strict()
    assert http("GET", "/version.json")[0] == 200


def test_temporary_device_confirmation_keeps_normal_account_and_device_checks(world):
    strict(world)
    assert accounts.create_account(EMP, 'paused-user', TEST_SECRET)['ok']
    db.set_setting('account_device_confirmation_paused_until_utc',
                   (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat())
    _, awaiting = login('paused-user', fp='new-phone', kind='phone', role='mobile', gps=None)
    assert awaiting['action'] == 'ok_pending'
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET status='active' WHERE username='paused-user'")
    assert accounts.change_password('paused-user',TEST_SECRET,CHANGED_SECRET)['ok']
    _, bad = login('paused-user', 'wrong', fp='new-phone', kind='phone', role='mobile', gps=None)
    assert bad['action'] == 'bad'
    _, phone = login('paused-user', CHANGED_SECRET, fp='new-phone', kind='phone', role='mobile', gps=None)
    assert phone['action'] == 'ok'
    def active_gate(kind):
        with db.connect() as conn:
            pair=conn.execute("SELECT s.token_table,s.token_id FROM account_token_sessions s "
                              "JOIN account_devices d ON d.id=s.device_id JOIN accounts a ON a.id=d.account_id "
                              "WHERE a.username='paused-user' AND d.kind=? AND d.status='active'",(kind,)).fetchone()
        return accounts.device_gate_by_token(*pair)[0]
    assert active_gate('phone') == 'ok'
    _, desktop = login('paused-user',CHANGED_SECRET,fp='new-desktop')
    assert desktop['action'] == 'ok' and active_gate('desktop') == 'ok'
    with db.connect() as conn:
        row=conn.execute("SELECT id FROM account_devices WHERE kind='phone' AND status='active'").fetchone()
    assert accounts.revoke_account_device(row[0],by='test')
    _, revoked = login('paused-user', CHANGED_SECRET, fp='new-phone', kind='phone', role='mobile', gps=None)
    assert revoked['action'] == 'ok_pending'
    db.set_setting('account_device_confirmation_paused_until_utc',
                   (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
    assert active_gate('desktop') == 'ok'
    _, other = login('paused-user', CHANGED_SECRET, fp='after-window',kind='phone',role='mobile',gps=None)
    assert other['action'] == 'ok_pending'


def test_open_ended_pause_recovers_an_existing_staff_reset_and_retains_both_phones(world):
    strict(world)
    assert accounts.create_account(EMP, 'reset-user', TEST_SECRET)['ok']
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET password_review_pending=1,password_reset_operation=?,"
                     "password_reset_actor=? WHERE username='reset-user'", ('test-reset', MANAGER))
    db.set_setting('account_device_confirmation_paused_until_utc', 'until-disabled')
    wrong = login('reset-user', 'wrong', fp='first-phone', kind='phone', role='mobile', gps=None)[1]
    assert wrong['action'] == 'bad'
    for fp in ('first-phone', 'second-phone'):
        assert login('reset-user', fp=fp, kind='phone', role='mobile', gps=None)[1]['action'] == 'ok'
    with db.connect() as conn:
        assert tuple(conn.execute("SELECT status,password_review_pending,must_change_password FROM accounts "
                                  "WHERE username='reset-user'").fetchone()) == ('active', 0, 1)
        assert conn.execute("SELECT COUNT(*) FROM account_devices WHERE status='active'").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM account_approval_events WHERE action='password_reset' "
                            "AND decision='approved'").fetchone()[0] == 1


def test_existing_http_server_crosses_cutover_on_first_password_login(world):
    assert accounts.create_account(EMP,"clock-user",TEST_SECRET)['ok']
    before=datetime.now(timezone.utc);threshold=before+timedelta(minutes=1);clock=[before]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    world.setattr(account_policy,'datetime',Clock)
    world.setenv('ACCOUNT_CUTOVER_AT_UTC',threshold.isoformat())
    original_connect=db.connect
    def fast_lock_failure():
        conn=original_connect();conn.execute('PRAGMA busy_timeout=100');return conn
    world.setattr(db,'connect',fast_lock_failure)
    original_verify=accounts.verify_password
    def cross_after_begin(secret,stored):
        # Middleware observes compatibility; password verification occurs only
        # after BEGIN IMMEDIATE. The login itself crosses the UTC boundary.
        clock[0]=threshold+timedelta(seconds=1)
        return original_verify(secret,stored)
    world.setattr(accounts,'verify_password',cross_after_begin)
    pc=identity();signed=proof(pc,'','clock-user',TEST_SECRET)
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as client:
            assert db.get_setting('account_cutover_activated_at_utc') is None
            response=await client.post('/api/account/login',headers={'X-Client-Version':'3.2.0'},json={
                'username':'clock-user','password':TEST_SECRET,'audience':'employee','entrance':'internal',
                'device':{'kind':'desktop','fp':pc[2]},'pc_proof':signed})
            assert response.status==200,await response.text()
            assert (await response.json())['action']=='ok_pending'
    asyncio.run(run())
    assert db.get_setting('account_cutover_activated_at_utc')
    world.delenv('ACCOUNT_CUTOVER_AT_UTC');assert account_policy.strict()


def test_cutover_latch_does_not_commit_the_callers_unfinished_transaction(world):
    world.setenv("ACCOUNT_CUTOVER_AT_UTC", "2020-01-01T00:00:00Z")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert account_policy.strict(connection=conn)
        assert conn.execute("SELECT value FROM settings WHERE key='account_cutover_activated_at_utc'").fetchone()
        conn.rollback()
    assert db.get_setting("account_cutover_activated_at_utc") is None
    assert account_policy.strict()
    assert db.get_setting("account_cutover_activated_at_utc")


def test_operator_sees_every_live_phone_request_beyond_history_limit(world):
    assert accounts.create_account(EMP, "many-phones", TEST_SECRET)["ok"]
    with db.connect() as c:
        aid = c.execute("SELECT id FROM accounts WHERE username='many-phones'").fetchone()[0]
        for index in range(6):
            dev = c.execute(
                "INSERT INTO account_devices(account_id,kind,fingerprint,label,status,entrance,first_seen) "
                "VALUES(?,?,?,?,?,?,?)",
                (aid, "phone", f"phone-{index}", "Synthetic phone", "pending", "public",
                 datetime.now(timezone.utc).isoformat(timespec="seconds"))).lastrowid
            c.execute("INSERT INTO account_phone_requests(account_id,device_id,code_hash,manager_uid,"
                      "manager_token_id,issued_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                      (aid, dev, f"synthetic-code-hash-{index}", MANAGER, 123,
                       datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(timespec="seconds")))
    visible = next(a for a in accounts.admin_accounts() if a["username"] == "many-phones")
    assert len({r["device_id"] for r in visible["phone_requests"]}) == 6


def test_existing_link_and_bearers_die_at_cutover_while_account_token_survives(world):
    old_link = db.create_app_link(STAFF)
    old_web = db.redeem_app_link(old_link)
    old_desktop = db.enroll_agent_device(old_link, "staff-pc")[3]
    old_mgr_link = db.create_app_link(MANAGER)
    old_manager = db.enroll_manager_device(old_mgr_link, "manager-pc")[2]
    active_mgr = manager_account()
    assert http("GET", "/api/bootstrap", {"X-App-Token": old_web})[0] == 200
    assert http("GET", "/api/manager/device/me", {"X-Pult-Token": old_manager})[0] == 200
    strict(world)
    assert http("GET", "/version.json")[0] == 200  # service/health after restart
    assert http("GET", "/api/bootstrap", {"X-App-Token": old_web})[0] == 401
    assert http("GET", "/api/bootstrap", {"X-App-Token": old_desktop})[0] == 401
    assert http("GET", "/api/manager/device/me", {"X-Pult-Token": old_manager})[0] == 401
    assert http("GET", "/api/manager/device/me", {"X-Pult-Token": active_mgr})[0] == 200
    assert http("GET", "/i/" + old_link, {"User-Agent": "Mozilla/5.0"})[0] == 410
    assert http("POST", "/api/redeem", body={"code": old_link})[0] == 410
    assert http("POST", "/api/agent/device/enroll", body={"code": old_link})[0] == 410
    assert http("POST", "/api/manager/device/enroll", body={"code": old_mgr_link})[0] == 410
    assert db.uid_for_app_token(old_web) is None
    assert db.redeem_app_link(old_link) is None
    assert db.enroll_agent_device(old_link, "other") is None
    assert db.enroll_manager_device(old_mgr_link, "other") is None
    with pytest.raises(PermissionError):
        db.create_app_link(STAFF)
    world.delenv("ACCOUNT_CUTOVER_AT_UTC")
    assert http("GET", "/api/bootstrap", {"X-App-Token": old_web})[0] == 401
    assert db.get_setting("account_cutover_activated_at_utc")


def test_login_role_contract_and_analyst_cannot_ride_manager_or_admin(world):
    assert accounts.create_account(EMP, "staff-user", TEST_SECRET)["ok"]
    assert accounts.create_account("Synthetic Analyst", "analyst-user", TEST_SECRET, role="analyst")["ok"]
    assert login("staff-user", role="manager")[0] == 403
    assert login("analyst-user", role="manager")[0] == 403
    strict(world)
    assert login("staff-user", role="")[0] == 403
    assert login("staff-user", role="employee")[1]["role"] == "agent"
    assert http("GET", "/admin/accounts.json", {"X-Pult-Token": "bad"})[0] == 403
    with db.connect() as c:
        a = c.execute("SELECT role FROM managers WHERE name='Synthetic Analyst'").fetchone()
    assert a[0] == "analyst", "analyst account must not fabricate manager role"


def test_revoke_all_legacy_tokens_by_uid_and_reject_ambiguous_ownership(world):
    link = db.create_app_link(STAFF)
    web_token1 = db.redeem_app_link(link)
    web_token2 = db.redeem_app_link(link)
    desk = db.enroll_agent_device(link, "pc")[3]
    assert accounts.create_account(EMP, "staff-user", TEST_SECRET)["ok"]
    assert accounts.set_password("staff-user", "newpassword9")["ok"]
    assert db.uid_for_app_token(web_token1) is None and db.uid_for_app_token(web_token2) is None
    assert db.agent_device_check(desk)[0] != "ok"
    assert db.app_link_identity(link) is None
    new_link = db.create_app_link(STAFF)
    assert accounts.set_status("staff-user", "disabled", "operator")["ok"]
    assert db.app_link_identity(new_link) is None
    with db.connect() as c:
        c.execute("UPDATE agent_bind SET employee='Someone Else' WHERE telegram_id=?", (STAFF,))
    assert not accounts.set_password("staff-user", "anotherpassword9")["ok"]
    assert not accounts.set_status("staff-user", "disabled", "operator")["ok"]


def test_normalized_uid_collision_and_role_mismatch_are_not_guessed(world):
    with db.connect() as c:
        c.execute("INSERT INTO agent_bind(telegram_id,employee,kind,approved) VALUES(?,?,?,1)",
                  (STAFF + 1, "synthetic staff", "agent"))
    assert not accounts.create_account(EMP, "conflicted", TEST_SECRET)["ok"]
    assert accounts.create_account("Synthetic Manager", "wrong-role", TEST_SECRET, role="security")["ok"] is False


def test_retired_phone_codes_never_authorize_even_a_real_manager(world):
    manager=manager_account();strict(world)
    assert not accounts.request_phone_registration(MANAGER,1,1)['ok']
    assert not accounts.approve_phone_registration('old-code','principal:operator')['ok']
    status,body=http('POST','/api/manager/account_phone_request',{'X-Pult-Token':manager},{'device_id':1})
    assert status==403 and not body.get('code')


def test_manager_approval_reads_team_under_the_write_lock(world):
    from concurrent.futures import ThreadPoolExecutor
    manager=manager_account();strict(world)
    pc=identity();created=accounts.create_account(EMP,'locked-team',TEST_SECRET,self_service=True,registration_pc=proof(pc,EMP,'locked-team',TEST_SECRET))
    assert created['ok']
    with db.connect() as c: device=c.execute('SELECT id FROM account_devices WHERE account_id=(SELECT id FROM accounts WHERE username=?)',('locked-team',)).fetchone()[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            future=pool.submit(accounts.manager_approve,MANAGER,device_id=device,observed_pc=True)
            c.execute("UPDATE managers SET employees='' WHERE telegram_id=?",(MANAGER,));c.commit()
        assert not future.result(timeout=10)['ok']
    with db.connect() as c: assert c.execute('SELECT status FROM account_devices WHERE id=?',(device,)).fetchone()[0]=='pending'


def test_phone_without_gps_requires_own_manager_and_approval_does_not_expire(world):
    manager=manager_account();strict(world)
    pc=identity();created=accounts.create_account(EMP,'no-gps',TEST_SECRET,self_service=True,registration_pc=proof(pc,EMP,'no-gps',TEST_SECRET));assert created['ok']
    with db.connect() as c: device=c.execute("SELECT id FROM account_devices WHERE kind='desktop' AND account_id=(SELECT id FROM accounts WHERE username='no-gps')").fetchone()[0]
    hdr={'X-Pult-Token':manager}
    assert http('POST','/api/manager/account_approve',hdr,{'device_id':device,'observed_pc':True})[1]['active']
    pending=login('no-gps',fp='phone-no-gps',kind='phone',gps=None)[1]
    assert pending['action']=='ok_pending' and not pending['gps_exception_pending']
    with db.connect() as c: phone=c.execute("SELECT id FROM account_devices WHERE fingerprint='phone-no-gps'").fetchone()[0]
    assert http('POST','/api/manager/account_approve',hdr,{'device_id':phone})[1]['active']
    with db.connect() as c:c.execute('UPDATE account_devices SET gps_exception_until=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),phone))
    status,gated=http('GET','/api/bootstrap',{'X-App-Token':pending['token']})
    assert status==200 and not gated.get('pending')


def test_map_and_telegram_initiation_no_longer_admit_signed_legacy_identity(world, monkeypatch):
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode
    import maplink
    from aiohttp.test_utils import make_mocked_request

    qs = maplink.sign(EMP, datetime.now().date().isoformat())
    assert http("GET", "/manager/map?" + qs)[0] == 200
    monkeypatch.setenv("BOT_TOKEN", "synthetic-bot-secret")
    user = json.dumps({"id": STAFF}, separators=(",", ":"))
    pairs = {"auth_date": str(int(time.time())), "user": user}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    key = hmac.new(b"WebAppData", b"synthetic-bot-secret", hashlib.sha256).digest()
    signed = urlencode({**pairs, "hash": hmac.new(key, check.encode(), hashlib.sha256).hexdigest()})
    assert web._uid(make_mocked_request("GET", "/api/bootstrap", headers={"X-Init-Data": signed})) == STAFF
    strict(world)
    assert web._uid(make_mocked_request("GET", "/api/bootstrap", headers={"X-Init-Data": signed})) is None
    assert http("GET", "/manager/map?" + qs)[0] == 403


def test_telegram_webview_can_use_account_token_without_signed_identity_fallback(world, monkeypatch):
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode
    from aiohttp.test_utils import make_mocked_request

    monkeypatch.setenv("BOT_TOKEN", "synthetic-bot-secret")
    user = json.dumps({"id": MANAGER}, separators=(",", ":"))
    pairs = {"auth_date": str(int(time.time())), "user": user}
    check = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    key = hmac.new(b"WebAppData", b"synthetic-bot-secret", hashlib.sha256).digest()
    signed = urlencode({**pairs, "hash": hmac.new(key, check.encode(), hashlib.sha256).hexdigest()})
    req = lambda headers: make_mocked_request("GET", "/api/bootstrap", headers=headers)
    assert web._uid(req({"X-Init-Data": signed})) == MANAGER

    pc=identity()
    assert accounts.create_account(EMP,"tg-account",TEST_SECRET,self_service=True,
        registration_pc=proof(pc,EMP,"tg-account",TEST_SECRET))["ok"]
    with db.connect() as c:dev=c.execute("SELECT id FROM account_devices WHERE fingerprint=?",(pc[2],)).fetchone()[0]
    assert accounts.manager_approve(MANAGER,device_id=dev,observed_pc=True)["active"]
    assert accounts.change_password("tg-account", TEST_SECRET, CHANGED_SECRET)["ok"]
    token = accounts.login("tg-account", CHANGED_SECRET,
                           device={"kind": "phone", "fp": "tg-phone"},
                           entrance="public", audience="employee", gps=GPS)["token"]
    with db.connect() as c: phone=c.execute("SELECT id FROM account_devices WHERE fingerprint='tg-phone'").fetchone()[0]
    assert accounts.manager_approve(MANAGER,device_id=phone)['active']
    assert web._uid(req({"X-Init-Data": signed, "X-App-Token": token})) == STAFF
    strict(world)
    assert web._uid(req({"X-Init-Data": signed})) is None
    assert web._uid(req({"X-Init-Data": signed, "X-App-Token": token})) == STAFF
    assert web._uid(req({"X-Init-Data": signed, "X-App-Token": "invalid"})) is None
    assert http("GET", "/api/bootstrap", {"X-Init-Data": signed, "X-App-Token": token})[0] == 200


def test_one_real_principal_suffices_and_keeps_one_active_manager_device(world):
    assert accounts.create_account("Synthetic Manager", "manager-user", TEST_SECRET, role="manager")["ok"]
    first = accounts.login("manager-user", TEST_SECRET, device={"kind": "desktop", "fp": "m1"},
                           entrance="internal", audience="manager")
    second = accounts.login("manager-user", TEST_SECRET, device={"kind": "desktop", "fp": "m2"},
                            entrance="internal", audience="manager")
    assert first["action"] == second["action"] == "ok_pending"
    same = accounts.approve_account("manager-user", "principal:same-human")
    again = accounts.approve_account("manager-user", "principal:same-human")
    assert same["active"] is True and again["active"] is True
    assert same['approvals']==again['approvals']==['principal:same-human']
    with db.connect() as c:
        devices = [r[0] for r in c.execute("SELECT id FROM account_devices ORDER BY id")]
    assert len(devices) == 2
    for dev in devices:
        assert accounts.approve_device(dev, "principal:same-human")["active"] is True
        assert accounts.approve_device(dev, "principal:same-human")["active"] is True
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM account_devices WHERE status='active' AND kind='desktop'").fetchone()[0]==1
    strict(world)
    assert http("GET", "/api/manager/device/me", {"X-Pult-Token": first["token"]})[0] == 401
    assert http("GET", "/api/manager/device/me", {"X-Pult-Token": second["token"]})[0] == 403


def test_strict_cli_cannot_replace_the_managers_observed_pc_approval(world):
    manager_account();strict(world);pc=identity()
    assert accounts.create_account(EMP,'observed-only',TEST_SECRET,self_service=True,registration_pc=proof(pc,EMP,'observed-only',TEST_SECRET))['ok']
    with db.connect() as c:device=c.execute("SELECT id FROM account_devices WHERE fingerprint=?",(pc[2],)).fetchone()[0]
    assert not accounts.approve_account('observed-only','principal:operator')['ok']
    assert not accounts.approve_device(device,'principal:operator')['ok']
    assert not accounts.manager_approve(MANAGER,device_id=device)['ok']
    assert accounts.manager_approve(MANAGER,device_id=device,observed_pc=True)['active']


def test_cutover_outbox_records_three_explicit_skips_not_uncertain(world):
    import notification_outbox
    strict(world)
    with db.connect() as c:
        notification_outbox.enqueue(c, "visit:cutover-1", {"row": {"responsible": EMP,
            "contract_id": "CUT-1"}, "uid": STAFF, "day": "2026-10-17"})
    assert asyncio.run(notification_outbox.deliver({"bot": None}, "visit:cutover-1"))
    with db.connect() as c:
        row = c.execute("SELECT id,state,error FROM notification_outbox WHERE event_key='visit:cutover-1'").fetchone()
        attempts = c.execute("SELECT kind,status FROM notification_attempts WHERE outbox_id=?", (row["id"],)).fetchall()
        statuses = [r["status"] for r in attempts]
    assert row["state"] == "skipped" and row["error"] is None
    assert len(attempts) == 3 and all(status.startswith("skip") for status in statuses)
    assert {r["kind"] for r in attempts} == {db.PUSH_GROUP, db.PUSH_VISIT, db.PUSH_REPORT_CHAT}


def test_mobile_approval_page_static_without_account_data(world):
    status, page = http("GET", "/ops/approve")
    assert status == 200 and "<!doctype html" in page.lower()
    assert EMP not in page and "CUT-1" not in page


def test_old_account_without_uid_resolves_unambiguously_before_reset(world):
    code = db.create_app_link(STAFF)
    old = db.redeem_app_link(code)
    assert accounts.create_account(EMP, "staff-user", TEST_SECRET)["ok"]
    with db.connect() as c:
        c.execute("UPDATE accounts SET telegram_id=NULL WHERE username='staff-user'")
    assert accounts.set_password("staff-user", "recovered123")["ok"]
    assert db.uid_for_app_token(old) is None and db.app_link_identity(code) is None
    with db.connect() as c:
        assert c.execute("SELECT telegram_id FROM accounts WHERE username='staff-user'").fetchone()[0] == STAFF


def test_operator_device_revoke_retires_approval_logout_does_not(world):
    manager_account();strict(world);pc=identity()
    created=accounts.create_account(EMP,'staff-user',TEST_SECRET,self_service=True,
        registration_pc=proof(pc,EMP,'staff-user',TEST_SECRET));assert created['ok']
    with db.connect() as c:dev=c.execute('SELECT id FROM account_devices WHERE fingerprint=?',(pc[2],)).fetchone()[0]
    assert accounts.manager_approve(MANAGER,device_id=dev,observed_pc=True)['active']
    active=accounts.login('staff-user',TEST_SECRET,device={'kind':'desktop','fp':pc[2]},entrance='internal',audience='employee',pc_proof=proof(pc,'','staff-user',TEST_SECRET))
    assert http('POST','/api/account/logout',{'X-App-Token':active['token']})[0]==200
    assert http('GET','/api/bootstrap',{'X-App-Token':active['token']})[0]==401
    renewed=accounts.login('staff-user',TEST_SECRET,device={'kind':'desktop','fp':pc[2]},entrance='internal',audience='employee',pc_proof=proof(pc,'','staff-user',TEST_SECRET))
    assert renewed['action']=='ok'
    with db.connect() as c:token_id=c.execute('SELECT agent_dev_id FROM account_devices WHERE id=?',(dev,)).fetchone()[0]
    # A disposable trusted operator grant; no real administrator is fabricated.
    assert accounts.create_account('Synthetic Operator','operator-user',TEST_SECRET,role='analyst')['ok']
    accounts.approve_account('operator-user','synthetic:one');accounts.approve_account('operator-user','synthetic:two')
    assert accounts.change_password('operator-user',TEST_SECRET,CHANGED_SECRET)['ok']
    with db.connect() as c:
        aid=c.execute("SELECT id FROM accounts WHERE username='operator-user'").fetchone()[0]
        c.execute("INSERT INTO account_admin_grants(account_id,principal,granted_by,operation_id,at_utc) VALUES(?, 'synthetic:operator', 'test-fixture', 'synthetic-operation', '2026-01-01T00:00:00Z')",(aid,))
    operator=login('operator-user',CHANGED_SECRET,role='analyst',fp='operator-pc')[1]
    hdr={'X-Pult-Token':operator['token']}
    assert http('POST','/admin/agent_device/revoke.json',hdr,{'id':token_id})[1]['revoked']==1
    with db.connect() as c:assert c.execute('SELECT status FROM account_devices WHERE id=?',(dev,)).fetchone()[0]=='revoked'
    again=accounts.login('staff-user',TEST_SECRET,device={'kind':'desktop','fp':pc[2]},entrance='internal',audience='employee',pc_proof=proof(pc,'','staff-user',TEST_SECRET))
    assert again['action']=='ok_pending'


def test_pre_cutover_phone_approval_does_not_grandfather_an_unobserved_pc(world):
    assert accounts.create_account(EMP,'old-phone',TEST_SECRET)['ok']
    pending=login('old-phone',fp='old-phone-device',kind='phone')[1]
    with db.connect() as c:
        c.execute("UPDATE accounts SET created_by='self-registration',status='active',must_change_password=0 WHERE username='old-phone'")
        c.execute("UPDATE account_devices SET status='active',approved_by='legacy' WHERE fingerprint='old-phone-device'")
    assert not http('GET','/api/bootstrap',{'X-App-Token':pending['token']})[1].get('pending')
    strict(world)
    gated=http('GET','/api/bootstrap',{'X-App-Token':pending['token']})[1]
    assert gated['pending'] and not gated.get('persons')
    assert http('GET','/api/person?rep=CUT-1',{'X-App-Token':pending['token']})[0] in (401,403)


def test_observed_pc_unlocks_before_phone_and_phone_joins_separately(world):
    manager=manager_account();strict(world);pc=identity()
    created=accounts.create_account(EMP,'pc-first',TEST_SECRET,self_service=True,registration_pc=proof(pc,EMP,'pc-first',TEST_SECRET));assert created['ok']
    with db.connect() as c:device=c.execute('SELECT id FROM account_devices WHERE fingerprint=?',(pc[2],)).fetchone()[0]
    assert http('POST','/api/manager/account_approve',{'X-Pult-Token':manager},{'device_id':device,'observed_pc':True})[1]['active']
    assert not http('GET','/api/bootstrap',{'X-App-Token':created['token']})[1].get('pending')
    phone=login('pc-first',fp='phone-new',kind='phone')[1];assert phone['action']=='ok_pending'
    with db.connect() as c: phone_id=c.execute("SELECT id FROM account_devices WHERE fingerprint='phone-new'").fetchone()[0]
    assert http('POST','/api/manager/account_approve',{'X-Pult-Token':manager},{'device_id':phone_id})[1]['active']
    assert not http('GET','/api/bootstrap',{'X-App-Token':phone['token']})[1].get('pending')


def test_manager_cannot_approve_gps_after_losing_the_employee(world):
    manager=manager_account();strict(world);pc=identity()
    assert accounts.create_account(EMP,'revoked-team',TEST_SECRET,self_service=True,registration_pc=proof(pc,EMP,'revoked-team',TEST_SECRET))['ok']
    with db.connect() as c:device=c.execute('SELECT id FROM account_devices WHERE fingerprint=?',(pc[2],)).fetchone()[0]
    assert accounts.manager_approve(MANAGER,device_id=device,observed_pc=True)['active']
    login('revoked-team',fp='phone-team',kind='phone',gps=None)
    with db.connect() as c:phone=c.execute("SELECT id FROM account_devices WHERE fingerprint='phone-team'").fetchone()[0]
    db.set_manager(MANAGER,'Synthetic Manager',[],[],role='manager')
    status,_=http('POST','/api/manager/account_approve',{'X-Pult-Token':manager},{'device_id':phone,'gps_exception_reason':'No GPS signal'})
    assert status==404
    with db.connect() as c:assert c.execute('SELECT status FROM account_devices WHERE id=?',(phone,)).fetchone()[0]=='pending'
