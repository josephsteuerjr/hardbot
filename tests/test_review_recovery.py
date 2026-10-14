import asyncio
import json
import threading
import time

import pytest

import db
import notification_outbox as outbox
import recovery_bundle
import trusted_proxy
from pult.core.durable_jobs import Journal


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    monkeypatch.setattr(db, "DB_PATH", root / "hardbot.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES('A','Synthetic','H1','S1')")
        conn.commit()
    return root


def test_enroll_recovers_commit_without_response(world):
    db.bind_agent(100, "S1")
    code = db.create_app_link(100)
    proof = "s" * 43
    first = db.enroll_agent_device(code, "Synthetic", enroll_token=proof)
    again = db.enroll_agent_device(code, "Synthetic", enroll_token=proof)
    assert first == again and first[-1] == proof
    with db.connect() as conn:
        assert conn.execute("SELECT uses FROM app_links WHERE code=?", (code,)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM agent_devices").fetchone()[0] == 1
    assert db.enroll_agent_device(code, "Other", enroll_token="t" * 43) is None
    db.agent_device_revoke(first[2], by="test")
    assert db.enroll_agent_device(code, "Synthetic", enroll_token=proof) is None


def test_outbox_is_atomic_durable_and_does_not_repeat_uncertain(world, monkeypatch):
    original = db.record_bot_visit
    def broken(*args, **kwargs):
        raise RuntimeError("interrupted")
    monkeypatch.setattr(db, "record_bot_visit", broken)
    with pytest.raises(RuntimeError):
        db.save_visit_result("A", 100, "H1", {}, "Test", op_id="test-one", notification={})
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM notification_outbox").fetchone()[0] == 0
    monkeypatch.setattr(db, "record_bot_visit", original)
    assert db.save_visit_result("A", 100, "H1", {"call_status": "Нет контакта"}, "Test", op_id="test-one", notification={})
    assert not db.save_visit_result("A", 100, "H1", {}, "Changed", op_id="test-one", notification={})
    item = outbox.claim("test-one")
    assert json.loads(item["payload"])["row"]["call_status"] == "Нет контакта"
    outbox.recover()
    assert outbox.claim("test-one") is None
    with db.connect() as conn:
        assert conn.execute("SELECT state FROM notification_outbox").fetchone()[0] == "uncertain"


def test_pending_notification_survives_restart_and_records_attempt(world, monkeypatch):
    import web
    db.save_visit_result("A", 100, "H1", {}, "Test", op_id="pending", notification={})
    outbox.recover()
    async def send(*args, **kwargs):
        db.log_push(db.PUSH_VISIT, db.PUSH_SENT, target_id=200, contract_id="A")
    monkeypatch.setattr(web, "_visit_notify", send)
    assert asyncio.run(outbox.deliver({}, "pending"))
    assert not asyncio.run(outbox.deliver({}, "pending"))
    with db.connect() as conn:
        assert conn.execute("SELECT state FROM notification_outbox").fetchone()[0] == "delivered"
        assert conn.execute("SELECT count(*) FROM notification_attempts").fetchone()[0] == 1


def test_job_id_survives_backend_restart_and_cannot_change_request(tmp_path, monkeypatch):
    from pult import desk_backend as backend
    from pult.core.settings import Settings
    backend_settings = Settings({"profiles": {"Test": {"url": "https://example.invalid"}}, "active": "Test"}, tmp_path / "settings.json")
    monkeypatch.setattr(backend, "_settings", backend_settings)
    monkeypatch.setattr(backend, "_journal", None)
    monkeypatch.setattr(backend, "_jobs", {})
    invoked = []
    jid = backend.start_job("test", lambda say: invoked.append(1) or {"value": 42}, request_id="synthetic-job", fingerprint="same")
    for _ in range(100):
        if backend.job_state(jid)["state"] != "running":
            break
        time.sleep(.02)
    assert backend.job_state(jid)["result"] == {"value": 42}
    monkeypatch.setattr(backend, "_journal", None)
    monkeypatch.setattr(backend, "_jobs", {})
    assert backend.start_job("test", lambda say: invoked.append(2), request_id=jid, fingerprint="same") == jid
    assert invoked == [1]
    with pytest.raises(ValueError):
        backend.start_job("test", lambda say: None, request_id=jid, fingerprint="changed")
    journal = Journal(tmp_path / "interrupted.sqlite3")
    journal.save({"id": "interrupted", "state": "running"}, "p", "f")
    Journal(journal.path).recover()
    assert journal.load("interrupted", "p")["state"] == "uncertain"
    assert b'"state": "running"' not in journal.path.read_bytes() if __import__('os').name == 'nt' else True


def test_restore_bundle_preserves_wal_facts_photos_ledger_and_access(world, tmp_path):
    with db.connect() as conn:
        conn.execute("INSERT INTO enrichment_ledger(contract_id,field_key,value,delivered_at) VALUES('A','visit_comment','Synthetic note','2026-10-10')")
        conn.commit()
    db.bind_agent(100, "H1")
    db.save_visit_result("A", 100, "H1", {}, "Synthetic visit", op_id="bundle-visit", notification={})
    (world / "photos" / "A").mkdir(parents=True)
    (world / "photos" / "A" / "photo.jpg").write_bytes(b"synthetic image bytes")
    bundle = tmp_path / "backup"
    expected = recovery_bundle.create(world, bundle)
    restored = tmp_path / "restored"
    actual = recovery_bundle.restore(bundle, restored)
    assert actual["tables"] == expected["tables"]
    assert actual["tables"]["visits"]["rows"] == 1
    assert actual["tables"]["enrichment_ledger"]["rows"] == 1
    assert actual["tables"]["agent_bind"]["rows"] == 1
    assert (restored / "photos" / "A" / "photo.jpg").read_bytes() == b"synthetic image bytes"
    with pytest.raises(ValueError):
        recovery_bundle.restore(bundle, restored)
    (bundle / "photos" / "A" / "photo.jpg").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        recovery_bundle.verify(bundle)


def test_forwarded_ip_requires_known_proxy_and_ignores_forged_left_hop():
    assert trusted_proxy.peer("192.0.2.1", "198.51.100.1", []) == "192.0.2.1"
    assert trusted_proxy.peer("127.0.0.1", "203.0.113.9,198.51.100.1", ["127.0.0.0/8"]) == "198.51.100.1"
    assert trusted_proxy.peer("127.0.0.1", "invalid", ["127.0.0.0/8"]) == "127.0.0.1"


def test_backup_space_budget_includes_photos_before_creating_bundle(world, tmp_path, monkeypatch):
    from types import SimpleNamespace
    (world / "photos").mkdir()
    (world / "photos" / "synthetic.jpg").write_bytes(b"synthetic photo")
    monkeypatch.setattr(recovery_bundle.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=(world / "hardbot.db").stat().st_size * 2 + 1))
    destination = tmp_path / "does-not-fit"
    with pytest.raises(ValueError, match="space"):
        recovery_bundle.create(world, destination)
    assert not destination.exists()


def test_employee_ids_keep_history_and_detect_explicit_collision(world):
    import employee_identity as people
    with db.connect() as conn:
        people.ensure(conn, ["Synthetic Employee"])
        original = conn.execute("SELECT employee_id FROM employee_aliases WHERE name='Synthetic Employee'").fetchone()[0]
        conn.commit()
    people.add_alias(original, "New Synthetic Name")
    with db.connect() as conn:
        assert conn.execute("SELECT employee_id FROM employee_aliases WHERE name='Synthetic Employee'").fetchone()[0] == original
        assert conn.execute("SELECT employee_id FROM employee_aliases WHERE name='New Synthetic Name'").fetchone()[0] == original
        conn.execute("INSERT INTO employee_identities(id,display_name) VALUES('distinct','SYNTHETIC EMPLOYEE')")
        conn.execute("INSERT INTO employee_aliases(name,employee_id) VALUES('SYNTHETIC EMPLOYEE','distinct')")
        assert people.conflicts(conn, ["Synthetic Employee"]) == ["Synthetic Employee"]
        conn.commit()
    with pytest.raises(ValueError):
        people.add_alias("distinct", "New Synthetic Name")


def test_access_audit_survives_writer_contention_and_replays_once(world):
    import audit_spool
    held = db.connect()
    try:
        held.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        db.audit_access(101, "view_card", "A", "Synthetic")
        assert time.monotonic() - started < .5
        assert audit_spool.path().exists()
    finally:
        held.rollback(); held.close()
    assert audit_spool.drain() == 1
    assert audit_spool.drain() == 0
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM audit WHERE action='view_card'").fetchone()[0] == 1


def test_route_health_blackhole_wrong_app_and_stable_origin():
    from aiohttp import web as aio_web
    from aiohttp.test_utils import TestServer
    from route_health import Controller, probe
    async def scenario():
        async def okay(req):
            return aio_web.json_response({"ok": True})
        app = aio_web.Application(); app.router.add_get("/ready.json", okay)
        async with TestServer(app) as server:
            assert await probe(str(server.make_url('/')), allow_http=True)
        async def blackhole(req):
            await asyncio.sleep(.3)
            return aio_web.json_response({"ok": True})
        app = aio_web.Application(); app.router.add_get("/ready.json", blackhole)
        async with TestServer(app) as server:
            assert not await probe(str(server.make_url('/')), timeout=.03, allow_http=True)
    asyncio.run(scenario())
    controller = Controller("https://example.invalid", "entry-a")
    for _ in range(2):
        assert controller.observe({"entry-a": False, "entry-b": True}) is None
    plan = controller.observe({"entry-a": False, "entry-b": True})
    assert plan["to"] == "entry-b" and plan["origin"] == "https://example.invalid"
    assert controller.observe({"entry-a": True, "entry-b": False}) is None
