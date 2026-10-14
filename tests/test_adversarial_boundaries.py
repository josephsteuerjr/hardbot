"""New attacks use real HTTP authentication and isolated synthetic SQLite."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

import db
import web
import audit_spool
import recovery_bundle
import sqlite3


@pytest.fixture
def boundary_world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    monkeypatch.setattr(web, "DEV", False)
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO clients(contract_id,client,birth_date,responsible,responsible_soft,partner,debt_today) VALUES('OLD','Synthetic person','1990-01-01','Own Hard','Own Soft','Test','100.25')")
        conn.commit()
    db.save_visit_result("OLD", 101, "Own Hard", {"call_status": "Нет контакта"}, "Own historical visit", op_id="old-visit")
    db.save_call_result("OLD", 102, "Own Soft", {"soft_call_status": "Нет контакта"}, "Own historical call", op_id="old-call", on_date=db.workday.today().isoformat())
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible='Foreign Hard',responsible_soft='Foreign Soft' WHERE contract_id='OLD'")
        conn.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES('RETAINED','Another person','Own Hard','Own Soft')")
        conn.execute("INSERT INTO clients(contract_id,client,birth_date,responsible,responsible_soft,partner,debt_today,phone,address,last_payment_sum,last_payment_date) VALUES('FOREIGN-NEW','Synthetic person','1990-01-01','Foreign Hard','Foreign Soft','Test','900000.75','FOREIGN-PHONE','FOREIGN-ADDRESS','777.25','2026-10-01')")
        conn.commit()
    db.reset_role_caches()
    yield
    db.reset_role_caches()


@pytest.mark.parametrize("role", ["manager", "security"])
@pytest.mark.parametrize("profile,employee", [("hard", "Own Hard"), ("soft", "Own Soft")])
def test_historical_card_never_expands_to_new_foreign_contract(boundary_world, role, profile, employee):
    uid = 701001
    db.set_manager(uid, "Synthetic reviewer", [], [employee], role=role)
    token = db.redeem_app_link(db.create_app_link(uid))

    async def attack():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            response = await client.get("/api/manager/person", params={"employee": employee, "rep": "OLD", "mode": profile, "date": "2026-10-01"}, headers={"X-App-Token": token})
            assert response.status == 200, await response.text()
            data = await response.json()
            serialized = json.dumps(data)
            for foreign in ("FOREIGN-NEW", "FOREIGN-PHONE", "FOREIGN-ADDRESS", "900000", "777.25"):
                assert foreign not in serialized, (foreign, data)
            assert data["n"] == 1
            assert {c["contract_id"] for c in data["contracts"]} == {"OLD"}
            current = await client.get("/api/manager/person", params={"employee": employee, "rep": "OLD", "mode": profile, "current_portfolio": "1"}, headers={"X-App-Token": token})
            assert current.status == 404
        finally:
            await client.close()

    asyncio.run(attack())


def test_backup_does_not_lose_audit_moved_between_databases(financial_world, monkeypatch):
    source = db.DB_PATH.parent
    event = ("preexisting-audit", datetime.now(timezone.utc).isoformat(), 101, "synthetic", "A", "Before backup")
    audit_spool.save(event)
    original_connect = sqlite3.connect
    triggered = []

    class DrainAfterSnapshot:
        def __init__(self, connection):
            self.connection = connection
        def close(self):
            self.connection.close()
        def backup(self, destination):
            self.connection.backup(destination)
            # Deterministic interleaving: spool->main drain after the main DB
            # snapshot and before a later spool snapshot. No network or sleeps.
            main = original_connect(db.DB_PATH)
            spool = original_connect(audit_spool.path())
            try:
                main.execute("INSERT INTO audit(event_id,ts,actor,action,target,detail) VALUES(?,?,?,?,?,?)", event)
                main.commit()
                spool.execute("DELETE FROM events WHERE id=?", (event[0],))
                spool.commit()
                triggered.append(True)
            finally:
                main.close()
                spool.close()

    def interleaved_connect(path, *args, **kwargs):
        connection = original_connect(path, *args, **kwargs)
        if str(path) == db.DB_PATH.resolve().as_uri() + "?mode=ro":
            return DrainAfterSnapshot(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", interleaved_connect)
    destination = source.parent / (source.name + "-backup")
    recovery_bundle.create(source, destination)
    assert triggered
    primary = original_connect(destination / "hardbot.db")
    spool = original_connect(destination / "audit-spool.sqlite3")
    try:
        copies = primary.execute("SELECT COUNT(*) FROM audit WHERE event_id=?", (event[0],)).fetchone()[0]
        copies += spool.execute("SELECT COUNT(*) FROM events WHERE id=?", (event[0],)).fetchone()[0]
        assert copies >= 1, "An audit committed before backup disappeared from both snapshots"
    finally:
        primary.close()
        spool.close()


@pytest.fixture
def financial_world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    monkeypatch.setattr(web, "DEV", False)
    db.init_db()
    with db.connect() as conn:
        for cid in ("A", "FOREIGN-TRANSFER"):
            conn.execute("INSERT INTO clients(contract_id,client,birth_date,responsible,responsible_soft,partner,debt_today) VALUES(?,'Shared person','1990-01-01','H1','S1','Test','100.25')", (cid,))
        conn.commit()
    db.bind_agent(101, "S1")
    db.reset_role_caches()
    yield
    db.reset_role_caches()


def test_future_payment_time_cannot_lock_status_until_future_year(financial_world):
    token = db.redeem_app_link(db.create_app_link(101))
    async def attack():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            now = datetime.now(timezone.utc)
            future = now + timedelta(days=3650)
            response = await client.post("/api/save", json={"kind": "payment", "rep": "A", "finish": True, "op_id": "future-payment", "amount": "10.25", "paid_at": db.workday.today().isoformat(), "result_status": "Оплачен", "payment_revision": db.payment_card_revision(db.person_contracts_by_soft("A")), "occurred_at": future.timestamp() * 1000}, headers={"X-App-Token": token})
            assert response.status in (200, 400), await response.text()
            if response.status == 200:
                saved = db.reported_payments_for(["A"])["A"][0]
                assert saved["event_day"] == db.workday.today().isoformat(), saved
                timestamp = datetime.fromisoformat(saved["created_at"]).replace(tzinfo=timezone.utc)
                assert timestamp <= now + timedelta(minutes=5)
                later = (now + timedelta(seconds=10)).replace(tzinfo=None).isoformat()
                db.save_call_result("A", 101, "S1", {"soft_call_status": "Нет контакта"}, "Later real call", op_id="later-call", on_date=db.workday.today().isoformat(), saved_at=later)
                assert db.effective_soft_status(db.get_client("A")) == "Нет контакта"
        finally:
            await client.close()
    asyncio.run(attack())


def test_future_visit_timestamp_cannot_block_next_real_visit(financial_world):
    db.set_bind(101, "H1")
    token = db.redeem_app_link(db.create_app_link(101))
    async def attack():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            now = datetime.now(timezone.utc)
            body = {"rep": "A", "finish": True, "op_id": "future-visit", "form_version": web.FORM_PROTOCOL_VERSION, "input_keys": ["call_status"], "values": {"call_status": "Нет контакта"}, "occurred_at": (now + timedelta(days=3650)).timestamp() * 1000, "photos_pending": 0}
            response = await client.post("/api/save", json=body, headers={"X-App-Token": token})
            assert response.status == 200, await response.text()
            row = db.get_client("A")
            assert datetime.fromisoformat(row["bot_visit_event_at"]).replace(tzinfo=timezone.utc) <= now + timedelta(minutes=5), dict(row)
        finally:
            await client.close()
    asyncio.run(attack())


@pytest.mark.parametrize("role", ["manager", "security", "full"])
def test_role_capabilities_are_enforced_by_real_http(financial_world, role):
    uid = 702001
    db.set_manager(uid, "Synthetic reviewer", [], ["S1"], role=role)
    token = db.redeem_app_link(db.create_app_link(uid))
    async def attack():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            headers = {"X-App-Token": token}
            own = await client.get("/api/manager/portfolio?mode=soft", headers=headers)
            assert own.status == 200
            foreign = await client.get("/api/manager/portfolio?mode=soft&employee=Foreign", headers=headers)
            assert foreign.status == 404
            control = await client.get("/api/control/soft", headers=headers)
            assert control.status == (403 if role == "manager" else 200)
            links = await client.get("/api/manager/app_links", headers=headers)
            assert links.status == (403 if role == "security" else 200)
            if role == "security":
                for path in ("/api/manager/app_link", "/api/manager/app_revoke", "/api/manager/binds"):
                    response = await client.post(path, headers=headers, json={"employee": "S1"})
                    assert response.status == 403, (path, await response.text())
            admin = await client.get("/admin/stats", headers=headers)
            assert admin.status in (401, 403)
        finally:
            await client.close()
    asyncio.run(attack())


@pytest.mark.parametrize("profile,employee", [("hard", "H1"), ("soft", "S1")])
def test_employee_card_keeps_same_cents_as_portfolio(financial_world, profile, employee):
    db.set_bind(103, employee)
    token = db.redeem_app_link(db.create_app_link(103))
    async def attack():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            response = await client.get("/api/person", params={"rep": "A", "mode": profile}, headers={"X-App-Token": token})
            assert response.status == 200
            data = await response.json()
            card = data.get("person", data)
            assert card["totals"] == [{"unit": "₽", "sum": 200.50}], card["totals"]
            assert all(c["total"] == 100.25 for c in card["contracts"]), card["contracts"]
        finally:
            await client.close()
    asyncio.run(attack())


def test_clock_skew_across_office_midnight_keeps_event_day_consistent(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 12, 18, 58, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(web, "datetime", Clock)
    monkeypatch.setattr(db.workday, "today", lambda: date(2026, 10, 12))
    body = {"occurred_at": datetime(2026, 10, 12, 19, 1, tzinfo=timezone.utc).timestamp() * 1000}
    assert web._occurred_on(body) == "2026-10-12"
    stamp = datetime.fromisoformat(web._saved_at(body)).replace(tzinfo=timezone.utc)
    assert stamp.astimezone(web.evidence.FIELD_TZ).date() == date(2026, 10, 12)


def test_shared_promise_hides_transferred_contract_identifier(financial_world):
    day = db.workday.today() - timedelta(days=2)
    db.save_call_result("A", 101, "S1", {"soft_call_status": "Обещает оплатить", "promise_date": (day + timedelta(days=1)).isoformat(), "forecast_sum": "100.25"}, "Shared promise", op_id="shared-promise", on_date=day.isoformat(), saved_at=day.isoformat() + "T10:00:00")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='S2' WHERE contract_id='FOREIGN-TRANSFER'")
        conn.commit()
    db.set_manager(701001, "Synthetic reviewer", [], ["S1"], role="security")
    token = db.redeem_app_link(db.create_app_link(701001))
    async def attack():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            response = await client.get("/api/manager/promises", params={"mode": "soft"}, headers={"X-App-Token": token})
            assert response.status == 200
            data = await response.json()
            assert "FOREIGN-TRANSFER" not in json.dumps(data), data
            promise = data["employees"][0]["items"][0]
            assert promise["contracts"] == ["A"]
            assert promise["promised_sum"] == 100.25
        finally:
            await client.close()
    asyncio.run(attack())
