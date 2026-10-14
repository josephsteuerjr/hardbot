"""Регрессии ревью 05.10: реальные границы API → БД → книга, без рабочей БД."""
import asyncio
import json
from datetime import date, datetime, time, timedelta

import pytest
from aiohttp import web as aio_web
from aiohttp.test_utils import make_mocked_request

import db
import web
import control
import digest_data
from pult import enrich
from pult.core import registry_import as ri, enrichment as en
from fields import FIELDS


def grid(rows):
    return [[f.title for f in FIELDS] + ["ответственный Сотрудник СОФТ"],
            [f.source for f in FIELDS] + ["софт"],
            *[[r.get(f.key) for f in FIELDS] + [r.get("responsible_soft")] for r in rows]]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "review.db")
    db.init_db()
    monkeypatch.setattr(db, "promise_ahead_days", lambda: 0)
    monkeypatch.setattr(web, "lang_of", lambda uid: "ru")
    rows = [{"contract_id": cid, "client": "Синтетический клиент", "birth_date": "1980-01-01",
             "responsible": hard, "responsible_soft": soft, "partner": "Тест", "debt_total": "50000"}
            for cid, hard, soft in (("A", "H1", "S1"), ("B", "H2", "S1"), ("C", "H1", "S2"))]
    assert not db.import_grid(grid(rows), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)["blocked"]
    return rows


def call(monkeypatch, op, status="Нет контакта", cid="A", employee="S1", **kwargs):
    monkeypatch.setattr(web, "_auth", lambda request: (100, employee))
    values = {"soft_call_status": status, **kwargs.pop("values", {})}
    body = {"rep": cid, "kind": "call", "finish": True, "op_id": op,
            "values": values, "input_keys": list(values),
            "form_version": web.FORM_PROTOCOL_VERSION, **kwargs}
    request = make_mocked_request("POST", "/api/save")
    async def payload():
        return body
    request.json = payload
    return json.loads(asyncio.run(web._save(request)).body)


def test_old_op_and_late_event_cannot_rollback_new_call(world, monkeypatch):
    call(monkeypatch, "old")
    call(monkeypatch, "new", "Обещает оплатить", values={"forecast_sum": "9000"})
    replay = call(monkeypatch, "old")
    assert replay["dedup"]
    assert db.get_client("A")["soft_call_status"] == "Обещает оплатить"
    assert db.get_client("B")["forecast_sum"] == "9000"
    yesterday = date.today() - timedelta(days=1)
    call(monkeypatch, "late", occurred_at=datetime.combine(yesterday, time(12)).timestamp()*1000)
    assert db.get_client("A")["soft_call_status"] == "Обещает оплатить"
    assert len(db.calls_for_person("B")) == 3


def test_changed_amount_is_a_new_result_not_same_text_dedup(world, monkeypatch):
    call(monkeypatch, "one", "Обещает оплатить", values={"forecast_sum": "9000"})
    call(monkeypatch, "two", "Обещает оплатить", values={"forecast_sum": "12000"})
    assert db.get_client("A")["forecast_sum"] == "12000"
    assert len(db.calls_for_person("A")) == 2


def test_soft_group_read_matches_write_and_does_not_read_s2(world, monkeypatch):
    call(monkeypatch, "s1")
    call(monkeypatch, "s2", cid="C", employee="S2")
    assert {r["contract_id"] for r in db.person_contracts_by_soft("A")} == {"A", "B"}
    assert [r["contract_id"] for r in db.calls_for_person("B")] == ["A"]
    data = web._person_full("B", "ru", by_soft=True)
    assert {r["contract_id"] for r in data["contracts"]} == {"A", "B"}
    monkeypatch.setattr(web, "_auth", lambda request: (101, "H1"))
    with pytest.raises(aio_web.HTTPNotFound):
        asyncio.run(web._person(make_mocked_request("GET", "/api/person?rep=A&mode=soft")))


def test_stale_master_preserves_call_but_delivered_office_edit_is_accepted(world, monkeypatch):
    call(monkeypatch, "new", "Обещает оплатить", values={"forecast_sum": "12000", "soft_comment": "новый звонок"})
    world[0].update(soft_call_status="Нет контакта", forecast_sum="100", soft_comment="старый master")
    db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert db.get_client("A")["forecast_sum"] == "12000"
    assert db.get_client("A")["soft_comment"] == "новый звонок"
    db.record_enrichment_deliveries([{"contract_id": "A", "key": "soft_call_status",
        "value": "Обещает оплатить", "visit_date": date.today().isoformat(), "responsible": "S1"}])
    world[0].update(soft_call_status="Обещает оплатить", soft_comm_date=db.call_fact_day(db.get_client('A')),
                    forecast_sum="13000", soft_comment="проверено офисом")
    preview=db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
                           allow_soft_office_edits=True, preview_only=True)
    db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
                   allow_soft_office_edits=True,
                   expected_soft_office_effect_digest=preview['soft_office_effect_digest'])
    exported = next(r for r in db.enrichment_rows() if r["contract_id"] == "A")
    assert exported["forecast_sum"] == "13000"
    sibling = next(r for r in db.enrichment_rows() if r["contract_id"] == "B")
    assert sibling["forecast_sum"] == "13000" and sibling["soft_comment"] == "проверено офисом"
    assert json.loads(db.get_client("A")["bot_call_data"])["forecast_sum"] == "12000"


def test_future_callback_and_old_visit_do_not_hide_todays_call(world, monkeypatch):
    old = (date.today() - timedelta(days=5)).isoformat()
    db.record_bot_visit("A", {"call_status": "Нет контакта", "forecast_sum": "111"}, old)
    call(monkeypatch, "today", "Обещает оплатить", values={"forecast_sum": "9900",
         "soft_comm_date": (date.today() + timedelta(days=4)).isoformat()})
    rows = db.enrichment_rows()
    assert next(r for r in rows if r["contract_id"] == "B")["_call_day"] == date.today().isoformat()
    by_emp = enrich.split_by_employee(rows)
    assert by_emp["soft:s1"]["filled"] == 2
    schema = db.export_schema()
    book = [[f["title"] for f in schema], ["A" if f["key"] == "contract_id" else None for f in schema]]
    plan = enrich.build_plan(book, schema, by_emp["soft:s1"]["rows"],
                             since=date.today().isoformat(), until=date.today().isoformat(), ledger={})
    assert plan["matched_cases"] == 1
    amount_col = next(i for i, f in enumerate(schema) if f["key"] == "forecast_sum")
    assert any(c == amount_col and str(value) == "9900" for _, c, value, _ in plan["writes"])


def test_imported_soft_values_without_call_are_not_app_results(world):
    world[0].update(soft_call_status="Нет контакта", soft_comm_date=date.today().isoformat())
    db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    exported = next(r for r in db.enrichment_rows() if r["contract_id"] == "A")
    assert not enrich.is_soft_filled(exported)
    assert enrich.split_by_employee([exported])["soft:s1"]["filled"] == 0


@pytest.mark.parametrize("profile,picked,cids", [("hard", "h1", {"A","C"}), ("soft", "s1", {"A","B"}), ("both", "s1", {"A","B"})])
def test_import_profile_selection_keeps_exact_proof_grid(world, profile, picked, cids):
    book = {"grid": grid(world), "scope": ri.scope_from_grid(grid(world))}
    ri.select_profile(book, profile)
    narrowed = ri.grid_for(book, {picked})
    assert {r["contract_id"] for r in ri.scope_from_grid(narrowed)} == cids


def test_full_soft_slice_clears_only_absent_soft_ownership(world):
    preview = db.import_grid(grid([world[0]]), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
                            full_registry=True, import_profile="soft", authoritative_employees=["S1"], preview_only=True)
    assert preview["auto_unassigned"] == 1
    assert db.get_client("B")["responsible_soft"] == "S1"
    result = db.import_grid(grid([world[0]]), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        full_registry=True, import_profile="soft", authoritative_employees=["S1"],
        expected_auto_unassigned=1, expected_auto_unassigned_digest=preview["auto_unassigned_digest"])
    assert not result["blocked"], result
    assert db.get_client("B")["responsible_soft"] == ""
    assert db.get_client("B")["responsible"] == "H2"


def test_poor_accuracy_and_standing_are_not_movement():
    points = [{"ts": f"2026-10-05T10:{i:02}:00Z", "lat":40+i*.015, "lon":69,
               "acc_m":1800} for i in range(10)]
    assert control._segments(points) == []
    points = [{"ts": f"2026-10-05T10:{i:02}:00Z", "lat":40+i*.00001, "lon":69,
               "acc_m":50} for i in range(10)]
    assert control._segments(points)[0]["km"] == 0


def test_receipts_group_and_decimal_registry_amount(world):
    db.record_receipt("r1", "A", "H1", 101, paid_at=date.today().isoformat(), amount=1500.5, currency="₽")
    db.record_receipt("r2", "C", "H1", 101, paid_at=date.today().isoformat(), amount=2500, currency="₽")
    db.update_fields("A", {"last_payment_sum": "1500.50", "last_payment_date": date.today().isoformat()})
    assert len(web._person_full("A", "ru")["receipts"]) == 2
    person = db.persons_for_employee("H1")[0]
    data = digest_data._person_payload(person, date.today().isoformat())
    assert data["registry_pay"]["sum"] == 1500.5
    assert data["receipts_total"] == 4000.5
    assert data["payment_checks"][0]["receipt_sum"] == 1500.5
    assert data["payment_checks"][0]["complete"] is False


def test_ambiguous_personal_files_are_blocked(tmp_path):
    (tmp_path/"Сотрудник.xlsb").touch()
    (tmp_path/"Сотрудник .xlsb").touch()
    with pytest.raises(ValueError, match="несколько активных"):
        en.personal_files([{"path":tmp_path}], "Сотрудник")


def test_legacy_calls_recover_only_journal_values_not_imported_amount(world):
    db.update_fields("A", {"forecast_sum":"123456", "soft_comm_date":"2026-10-09"})
    db.log_call("A",100,"Звонок (приложение). Статус: Нет контакта; из старой оболочки",
                op_id="legacy",employee="S1")
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key='migration_call_snapshots_v1'")
        conn.commit()
    db.init_db()
    first = db.bot_call_snapshot(db.get_client("B"))
    assert first["soft_comment"] == "из старой оболочки"
    assert "forecast_sum" not in first
    assert first["soft_comm_date"] == date.today().isoformat()
    assert not first["soft_next_call_date"]
    db.init_db()
    assert db.bot_call_snapshot(db.get_client("B")) == first


def test_inherited_old_call_amount_cannot_override_newer_visit(world, monkeypatch):
    call(monkeypatch,"early","Обещает оплатить",values={"forecast_sum":"100"},
         occurred_at=datetime.combine(date.today(),time(8)).timestamp()*1000)
    db.record_bot_visit("A",{"call_status":"Обещает оплатить","forecast_sum":"900"},
                       date.today().isoformat(),event_at=date.today().isoformat()+"T10:00:00")
    call(monkeypatch,"late", occurred_at=datetime.combine(date.today(),time(12)).timestamp()*1000)
    exported=next(r for r in db.enrichment_rows() if r["contract_id"]=="A")
    assert exported["forecast_sum"] == "900"
    assert db.common_result(db.get_client("A"))["forecast_sum"] == "900"


@pytest.mark.parametrize("profile", ["hard", "soft"])
def test_canonical_profile_projection_has_valid_enrichment_header(world, profile):
    tags = {"сх", "хард" if profile == "hard" else "софт"}
    fields = db.export_schema()
    projected = [f for f in fields if str(f["source"]).lower() in tags]
    _, columns = enrich.find_header([[f["title"] for f in projected]], fields)
    assert "contract_id" in columns
    assert ("visit_date" if profile == "hard" else "soft_call_status") in columns


def test_soft_header_required_before_any_import_mutation(world):
    incoming = [[f.title for f in FIELDS], [f.source for f in FIELDS],
                ["NEW" if f.key == "contract_id" else None for f in FIELDS]]
    result = db.import_grid(incoming, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER, import_profile="soft")
    assert result["blocked"] and "СОФТ" in result["error"]
    assert db.get_client("NEW") is None


def test_assignment_profiles_and_removal_keep_real_employee(world):
    world[0].update(responsible="Unified", responsible_soft="Unified")
    db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER, import_profile="both")
    events = db.assignment_events_between("2000-01-01", "2099-12-31", ["Unified"])
    assert {e["profile"] for e in events if e["contract_id"] == "A"} == {"hard", "soft"}
    world[0]["responsible_soft"] = ""
    db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER, import_profile="soft")
    events = db.assignment_events_between("2000-01-01", "2099-12-31", ["Unified"])
    assert any(e["kind"] == "removed" and e["prev_employee"] == "Unified" and e["profile"] == "soft" for e in events)


def test_old_assignment_schema_migrates_without_losing_history(world):
    with db.connect() as conn:
        conn.execute("DROP TABLE assignment_events")
        conn.execute("""CREATE TABLE assignment_events (id INTEGER PRIMARY KEY,
            contract_id TEXT, employee TEXT, prev_employee TEXT, kind TEXT,
            window_to TEXT, window_from TEXT, claimed_at TEXT, source TEXT, created_at TEXT,
            UNIQUE(contract_id, window_to, employee, prev_employee))""")
        conn.execute("INSERT INTO assignment_events VALUES (1,'A','H1','','assign','2026-10-01',NULL,NULL,'master',NULL)")
        conn.commit()
    db.init_db()
    db.init_db()
    events = db.assignment_events_between("2000-01-01", "2099-12-31")
    assert len(events) == 1 and events[0]["profile"] == "hard"


def test_access_scope_is_applied_before_limit(world):
    db.audit_access(100, "mgr_app_link", "S1")
    for _ in range(501):
        db.audit_access(100, "mgr_app_link", "outside")
    rng = {"from": date.today().isoformat(), "to": date.today().isoformat()}
    assert len(control.access_log(rng, ["S1"])["events"]) == 1
    assert control.access_log(rng, [])["events"] == []


def test_receipt_and_call_activity_keep_historical_employee(world, monkeypatch):
    call(monkeypatch, "history")
    db.record_receipt("historic-receipt", "A", "S1", 100,
                      paid_at=date.today().isoformat(), amount=100, currency="₽")
    db.bind_agent(100, "S2")
    local_day = db.workday.today().isoformat()
    data = control.activity({}, ["S1"], {"from": local_day, "to": local_day}, kinds={"receipt", "call"})
    assert {e["kind"] for e in data["events"]} == {"receipt", "call"}
    assert {e["employee"] for e in data["events"]} == {"S1"}


def test_call_history_survives_representative_reassignment(world, monkeypatch):
    call(monkeypatch, "group-history")
    world[0]["responsible_soft"] = "S2"
    db.import_grid(grid(world), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert [r["op_id"] for r in db.calls_for_person("B")] == ["group-history"]


def test_both_profile_selection_counts_contracts_once(world):
    book={"grid":grid(world), "scope":ri.scope_from_grid(grid(world))}
    ri.select_profile(book,"both")
    assert ri.selected_rows(book,{"h1","s1"}) == 3
