"""Synthetic office-day, scope, concurrency and replay acceptance."""
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import debt
import office_work as office
import web
import workday
REAL_AUTH = web._auth
REAL_WRITE_GATE = web._require_field_write


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "office.db")
    db.init_db()
    with db.connect() as conn:
        for cid, client, owner, soft, partner in [
                ("0001", "Synthetic A", "Hard A", "Soft A", "Test"),
                ("0002", "Synthetic A", "Hard A", "Soft A", "Test"),
                ("0003", "Synthetic B", "Hard B", "Soft B", "Test")]:
            conn.execute("""INSERT INTO clients(contract_id,client,birth_date,responsible,
                responsible_soft,partner,debt_today,address,phone) VALUES(?,?,?,?,?,?,?, ?,?)""",
                (cid, client, "1990-01-01", owner, soft, partner, "1000", "Synthetic address", "000001"))
        conn.commit()
    monkeypatch.setattr(web, "_auth", lambda request: (101, "Hard A"))
    monkeypatch.setattr(web, "_require_field_write", lambda uid: None)
    return monkeypatch


def card():
    return office.person_data(db.person_contracts("0001"))


def body(op="office-one", action="contact", values=None):
    return {"rep": "0001", "kind": "office", "finish": True, "action": action,
            "op_id": op, "occurred_at": int(time.time() * 1000), "revision": card()["revision"],
            "values": values if values is not None else {"note": "Synthetic note", "contact": "000002", "next_date": workday.today().isoformat()}}


def test_office_day_does_not_manufacture_call_visit_or_change_import(world):
    before = dict(db.get_client("0001"))
    saved = office.save(101, "Hard A", body())
    assert saved["ok"] and saved["event"]["employee"] == "Hard A"
    assert dict(db.get_client("0001")) == before
    assert card()["contacts"] == ["000002"]
    assert card()["next_date"] == workday.today().isoformat()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 0
    report = office.report("Hard A", "hard", workday.today().isoformat(), workday.today().isoformat())
    assert report["stats"] == {"office": 1, "persons": 1, "visits": 0, "promised": 0}
    assert report["portfolio_contracts"] == 2 and len(report["events"]) == 1
    assert office.report("Hard B", "hard", report["from"], report["to"])["events"] == []


def test_lost_response_identical_retry_one_fact_changed_payload_conflicts(world):
    operation = body()
    office.save(101, "Hard A", operation)
    assert office.save(101, "Hard A", operation)["duplicate"]
    with pytest.raises(office.OfficeError, match="Идентификатор"):
        office.save(101, "Hard A", {**operation, "values": {"note": "Replacement"}})
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM office_actions").fetchone()[0] == 1


def test_two_writer_threads_one_revision_one_winner(world):
    a = body("concurrent-a")
    b = {**a, "op_id": "concurrent-b"}
    def write(operation):
        try:
            return office.save(101, "Hard A", operation)["ok"]
        except office.OfficeError as exc:
            return exc.status
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(write, [a, b]), key=str) == sorted([True, 409], key=str)


@pytest.mark.parametrize("change", ["phone", "debt_today", "responsible", "bot_call_at"])
def test_import_phone_and_assignment_conflict_preserve_unaccepted_input(world, change):
    operation = body()
    with db.connect() as conn:
        conn.execute(f"UPDATE clients SET {change}=? WHERE contract_id='0001'", ("Changed",))
        conn.commit()
    with pytest.raises(office.OfficeError) as exc:
        office.save(101, "Hard A", operation)
    assert exc.value.status in (404, 409)
    assert office.report("Hard A", "hard", workday.today().isoformat(), workday.today().isoformat())["events"] == []


def test_wrong_owner_and_cross_card_contract_cannot_write(world):
    with pytest.raises(office.OfficeError) as exc:
        office.save(202, "Hard B", body())
    assert exc.value.status == 404
    with pytest.raises(office.OfficeError) as exc:
        office.save(101, "Hard A", body(action="promise", values={"contract_id": "0003", "promise_date": workday.today().isoformat(), "amount": "10.25"}))
    assert exc.value.status == 404


@pytest.mark.parametrize("amount", ["0", "-1", "1.001", "nan", "Infinity", "1000000001", "1e2"])
def test_money_validation_atomic(world, amount):
    with pytest.raises(office.OfficeError):
        office.save(101, "Hard A", body(action="promise", values={"contract_id": "0001", "promise_date": workday.today().isoformat(), "amount": amount}))
    assert card()["history"] == []


def test_contract_promise_once_manual_payment_does_not_fulfil_registry_does(world):
    today = workday.today().isoformat()
    office.save(101, "Hard A", body(action="promise", values={"contract_id": "0002", "promise_date": today, "amount": "100,25"}))
    p = card()["promises"]
    assert len(p) == 1 and p[0]["contract_id"] == "0002" and p[0]["amount"] == 100.25
    office.save(101, "Hard A", body("payment", "payment", {"contract_id": "0002", "payment_date": today, "amount": "100.25"}))
    assert card()["promises"][0]["received"] == 0
    with db.connect() as conn:
        conn.execute("UPDATE clients SET last_payment_date=?,last_payment_sum='100.25' WHERE contract_id='0002'", (today,))
        db._remember_registry_payments(conn)
        conn.commit()
    assert card()["promises"][0]["state"] == "kept"
    assert office.report("Soft A", "soft", today, today)["stats"]["kp"] == 0


def test_cancel_preserves_immutable_promise_and_requires_current_revision(world):
    today = workday.today().isoformat()
    office.save(101, "Hard A", body("ptp", "promise", {"contract_id": "0001", "promise_date": today, "amount": "50"}))
    office.save(101, "Hard A", body("cancel", "cancel_promise", {"contract_id": "0001", "target_op": "ptp", "note": "New agreement"}))
    assert card()["promises"] == [] and card()["history_count"] == 2


def test_empty_input_no_implicit_erasure_explicit_clear_is_journalled(world):
    office.save(101, "Hard A", body())
    office.save(101, "Hard A", body("empty", values={"next_date": "", "note": "Keep prior date"}))
    assert card()["next_date"] == workday.today().isoformat()
    clear = body("clear", values={"next_date": "", "note": "Remove next date"})
    clear["cleared_keys"] = ["next_date"]
    office.save(101, "Hard A", clear)
    assert card()["next_date"] == "" and card()["history_count"] == 3


def test_reassigned_representative_history_only_visible_contract_keys(world):
    today = workday.today().isoformat()
    office.save(101, "Hard A", body("p", "promise", {"contract_id": "0001", "promise_date": today, "amount": "50"}))
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible='Hard B' WHERE contract_id='0001'")
        conn.commit()
    data = office.person_data(db.person_contracts("0002"))
    event = data["history"][0]
    assert event["rep"] == "0002" and event["contracts"] == ["0002"]
    assert "amount" not in event["values"] and "contract_id" not in event["values"]
    assert data["promises"] == []


def test_employee_report_cannot_select_another_employee(world):
    office.save(101, "Hard A", body())
    request = make_mocked_request("GET", "/api/workspace/report?employee=Hard%20B&profile=hard")
    report = json.loads(asyncio.run(web._workspace_report(request)).text)
    assert report["employee"] == "Hard A" and len(report["events"]) == 1


def test_soft_full_day_same_calls_and_existing_rpc_ptp_kp(world):
    today = workday.today().isoformat()
    db.save_call_result("0001", 303, "Soft A", {"soft_call_status": "Обещает оплатить", "promise_date": today, "forecast_sum": "50.25"},
                        "Статус: Обещает оплатить", op_id="call", on_date=today, saved_at=today + "T08:00:00")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET last_payment_date=?,last_payment_sum='50.25' WHERE contract_id='0001'", (today,))
        db._remember_registry_payments(conn)
        conn.commit()
    report = office.report("Soft A", "soft", today, today)
    assert [report["stats"][k] for k in ("calls", "rpc", "ptp", "kp")] == [1, 1, 1, 1]
    assert len(report["events"]) == 1 and len(report["promises"]) == 1
    assert report["promises"][0]["state"] == "kept"
    assert office.report("Soft B", "soft", today, today)["events"] == []
    assert card()["history"] == []


def test_security_cannot_edit_office(world):
    world.setattr(web, "_require_field_write", lambda uid: (_ for _ in ()).throw(web.web.HTTPForbidden(text="read_only")))
    with pytest.raises(web.web.HTTPForbidden):
        asyncio.run(web._office_action(make_mocked_request("POST", "/api/office/action")))


def test_month_comment_is_versioned_and_cleared_only_explicitly(world):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET visit_comment='Imported comment' WHERE responsible='Hard A'")
        conn.commit()
    with pytest.raises(office.OfficeError):
        office.save(101,"Hard A",body("empty-comment","month_comment",{"note":""}))
    office.save(101,"Hard A",body("comment","month_comment",{"note":"New shared comment"}))
    assert db.get_client("0002")["visit_comment"] == "New shared comment"
    clear=body("clear-comment","month_comment",{"note":""});clear["cleared_keys"]=["note"]
    office.save(101,"Hard A",clear)
    assert db.get_client("0001")["visit_comment"] == "" and card()["history_count"]==2


def test_soft_revision_is_atomic_optional_for_old_clients_and_replay_is_stable(world):
    today=workday.today().isoformat()
    revision=office.person_data(db.person_contracts_by_soft("0001"))["revision"]
    values={"soft_call_status":"В работе","soft_comment":"A new note"}
    result=db.save_call_result("0001",303,"Soft A",values,"Synthetic",op_id="soft-revision",on_date=today,expected_revision=revision)
    assert result["applied"]
    assert db.save_call_result("0001",303,"Soft A",values,"Synthetic",op_id="soft-revision",on_date=today,expected_revision=revision)["dedup"]
    with pytest.raises(ValueError,match="изменилась"):
        db.save_call_result("0001",303,"Soft A",{**values,"soft_comment":"Other"},"Synthetic",op_id="new-stale",on_date=today,expected_revision=revision)
    assert db.save_call_result("0001",303,"Soft A",{**values,"soft_comment":"Legacy"},"Synthetic",op_id="legacy",on_date=today)["applied"]


def test_decimals_and_currencies_match_person_portfolio_report(world):
    world.setattr(debt,"unit",lambda partners:"TJS" if "Other" in partners else "RUB" if any(partners) else "")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET partner='Other',debt_today='100.25' WHERE contract_id='0002'")
        conn.execute("UPDATE clients SET debt_today='200.50' WHERE contract_id='0001'")
        conn.commit()
    groups=db.persons_for_employee("Hard A")
    briefs=[web._person_brief(p) for p in groups]
    office.decorate(briefs,groups)
    report=office.report("Hard A","hard",workday.today().isoformat(),workday.today().isoformat())
    assert briefs[0]["totals"]==report["money"]==[{"unit":"RUB","sum":200.5},{"unit":"TJS","sum":100.25}]
    office.save(101,"Hard A",body("tjs","promise",{"contract_id":"0002","promise_date":workday.today().isoformat(),"amount":"10.25"}))
    assert card()["promises"][0]["unit"]=="TJS"


def test_actual_http_agent_tokens_scope_revoke_and_old_phone_compatibility(world):
    from aiohttp.test_utils import TestClient,TestServer
    world.setattr(web,"_auth",REAL_AUTH)
    world.setattr(web,"_require_field_write",REAL_WRITE_GATE)
    world.setattr(web,"DEV",False)
    db.bind_agent(101,"Hard A");db.bind_agent(202,"Hard B")
    tokens={}
    for uid in (101,202):
        enrollment=db.enroll_agent_device(db.create_app_link(uid),"Synthetic PC")
        tokens[uid]=enrollment
    operation=body()
    async def exercise():
        client=TestClient(TestServer(web.build_app()))
        await client.start_server()
        try:
            headers={"X-App-Token":tokens[101][-1]}
            r=await client.get("/api/bootstrap",headers=headers);assert r.status==200
            bootstrap=await r.json();assert {p["rep"] for p in bootstrap["persons"]}=={"0001"}
            assert bootstrap["persons"][0]["address"]=="Synthetic address"
            r=await client.post("/api/office/action",headers=headers,json=operation);assert r.status==200
            assert (await r.json())["ok"]
            r=await client.post("/api/office/action",headers=headers,json=operation);assert (await r.json())["duplicate"]
            r=await client.get("/api/person?rep=0003",headers=headers);assert r.status==404
            r=await client.get("/api/workspace/report?profile=hard&employee=Hard%20B",headers=headers)
            report=await r.json();assert report["employee"]=="Hard A" and report["stats"]["office"]==1
            other={"X-App-Token":tokens[202][-1]}
            r=await client.get("/api/workspace/report?profile=hard",headers=other);assert (await r.json())["events"]==[]
            r=await client.post("/api/office/action",headers=other,json={**operation,"op_id":"forged"});assert r.status==404
            db.agent_device_revoke(tokens[101][2],by="synthetic audit")
            r=await client.get("/api/workspace/report?profile=hard",headers=headers);assert r.status==401
            r=await client.post("/api/office/action",headers=headers,json=operation);assert r.status==401
        finally:
            await client.close()
    asyncio.run(exercise())


def test_zero_balance_is_known_and_missing_balance_is_not_zero(world):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET debt_today='0',debt_total='500' WHERE contract_id='0001'")
        conn.execute("UPDATE clients SET debt_today=NULL,debt_total=NULL WHERE contract_id='0002'")
        conn.commit()
    assert office.portfolio_totals([db.get_client('0001')]) == [{'unit':'₽','sum':0.0}]
    assert office.portfolio_totals([db.get_client('0002')]) == []


def test_hard_zero_day_limit_keeps_existing_unlimited_policy(world):
    world.setattr(db,'promise_ahead_days',lambda:0)
    due=(workday.today()+timedelta(days=30)).isoformat()
    office.save(101,'Hard A',body('unlimited','promise',{'contract_id':'0001','promise_date':due,'amount':'12.50'}))
    assert card()['promises'][0]['due']==due


def test_office_cannot_fall_through_legacy_visit_endpoint(world):
    from unittest.mock import AsyncMock
    request=type('Request',(),{'json':AsyncMock(return_value=body())})()
    with pytest.raises(web.web.HTTPBadRequest) as error:
        asyncio.run(web._save(request))
    assert 'office/action' in error.value.text
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM visits').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==0


def test_prefilled_month_comment_is_not_new_office_activity(world):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET visit_comment='Old comment' WHERE responsible='Hard A'")
        conn.commit()
    with pytest.raises(office.OfficeError,match='не изменился'):
        office.save(101,'Hard A',body('no-op','month_comment',{'note':'Old comment'}))
    assert card()['history_count']==0
