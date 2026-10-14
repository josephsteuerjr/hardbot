"""Сквозные остатки ревью на новой БД, без рабочих данных."""
import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import call_review
import current_promises
import db
import web


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "remainders.db")
    db.init_db()
    with db.connect() as conn:
        for cid, owner in [("A", "S1"), ("B", "S2")]:
            conn.execute("INSERT INTO clients(contract_id,client,birth_date,responsible_soft,partner,debt_today) VALUES(?,?,?,?,?,?)",
                         (cid, "Клиент " + cid, "1990-01-01", owner, "Тест", "1000"))
        conn.commit()
    return monkeypatch


def call(op, day="2026-10-06", due="2026-10-07", rep="A", employee="S1"):
    return db.save_call_result(rep, 100, employee, {"soft_call_status": "Обещает оплатить",
        "promise_date": due, "forecast_sum": "100.25"}, "Статус: Обещает оплатить;",
        op_id=op, on_date=day, saved_at=day + "T10:00:00")


def pay(amount, day):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET last_payment_date=?,last_payment_sum=? WHERE contract_id='A'", (day, str(amount)))
        db._remember_registry_payments(conn)
        conn.commit()


def test_current_soft_promises_partial_late_and_new_promise(world):
    call("first")
    assert current_promises.soft_data(["S1"], "2026-10-08")["totals"]["persons"] == 1
    assert current_promises.soft_data(["S2"], "2026-10-08")["totals"]["persons"] == 0
    pay("30.25", "2026-10-08")
    p = current_promises.soft_data(["S1"], "2026-10-09")["employees"][0]["items"][0]
    assert p["state"] == "partial" and p["received"] == 30.25
    pay("70", "2026-10-09")
    assert current_promises.soft_data(["S1"], "2026-10-10")["totals"]["persons"] == 0
    call("second", "2026-10-10", "2026-10-11")
    d = current_promises.soft_data(["S1"], "2026-10-12")
    assert d["totals"]["persons"] == 1 and d["employees"][0]["items"][0]["received"] == 0


def test_soft_api_uses_today_independent_of_report_period(world):
    call("promise")
    world.setattr(web, "_manager_gate", lambda req: (200, {}, ["S1"]))
    world.setattr(web, "_manager_envelope", lambda *args: {})
    world.setattr(web, "_manager_date", lambda req: ("2026-09-01", "2026-10-08", None, None))
    r = asyncio.run(web._manager_promises(make_mocked_request("GET", "/api/manager/promises?mode=soft")))
    d = json.loads(r.text)
    assert d["profile"] == "soft" and d["asof"] == "2026-10-08" and d["totals"]["persons"] == 1


def test_call_review_scope_source_revision_history_and_retry(world):
    call("result")
    cid = db.calls_for_person("A")[0]["id"]
    assert call_review.read(cid, ["S2"]) is None
    body = dict(state="checking", due="2026-10-12", source="employee_report", note="Нужна проверка", op_id="review-one")
    d = call_review.update(cid, ["S1"], 200, body)
    assert d["review"]["revision"] == 1 and d["review"]["checker_uid"] == 200
    assert len(call_review.update(cid, ["S1"], 200, body)["history"]) == 1
    with pytest.raises(RuntimeError):
        call_review.update(cid, ["S1"], 201, {**body, "op_id": "review-two"})
    with pytest.raises(ValueError):
        call_review.update(cid, ["S1"], 200, {**body, "revision": 1, "op_id": "review-three", "state": "closed", "decision": "confirmed"})
    d = call_review.update(cid, ["S1"], 200, {**body, "revision": 1, "op_id": "review-four", "state": "closed", "decision": "disputed", "source": "customer_callback", "source_ref": "Контрольный звонок 12.10", "note": "Клиент отрицает контакт; требуется разбор альтернативных объяснений"})
    assert len(d["history"]) == 2 and d["review"]["decision"] == "disputed"
    assert db.calls_for_person("A")[0]["values_json"] == d["call"]["values_json"]


def test_shared_promise_counts_payment_on_transferred_contract_without_exposing_new_owner(world):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET client='Shared Synthetic',responsible_soft='S1'")
        conn.commit()
    call("shared")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='S2',last_payment_date='2026-10-08',last_payment_sum='30.25' WHERE contract_id='B'")
        db._remember_registry_payments(conn)
        conn.commit()
    partial = current_promises.soft_data(["S1"], "2026-10-09")
    assert [person["employee"] for person in partial["employees"]] == ["S1"]
    assert partial["employees"][0]["items"][0]["received"] == 30.25
    with db.connect() as conn:
        conn.execute("UPDATE clients SET last_payment_date='2026-10-09',last_payment_sum='70' WHERE contract_id='B'")
        db._remember_registry_payments(conn)
        conn.commit()
    assert current_promises.soft_data(["S1"], "2026-10-09")["totals"]["persons"] == 0
    assert current_promises.soft_data(["S2"], "2026-10-09")["totals"]["persons"] == 0


def test_transferred_contract_new_promise_owns_its_later_payment(world):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET client='Shared Synthetic',responsible_soft='S1'")
        conn.commit()
    call("original-shared")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='S2' WHERE contract_id='B'")
        conn.commit()
    call("new-owner-promise", day="2026-10-08", due="2026-10-09", rep="B", employee="S2")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET last_payment_date='2026-10-09',last_payment_sum='100.25' WHERE contract_id='B'")
        db._remember_registry_payments(conn)
        conn.commit()
    remaining = current_promises.soft_data(["S1"], "2026-10-10")
    assert remaining["totals"]["persons"] == 1
    assert remaining["employees"][0]["items"][0]["received"] == 0
    assert current_promises.soft_data(["S2"], "2026-10-10")["totals"]["persons"] == 0
