import copy, json, uuid
from dataclasses import replace
import pytest
import db
from crm import exchange, store
from crm.contract import CRMError, canonical
from test_crm_core import crm_world, call_body


def exported(w):
    return exchange.export(
        w["owner"],
        dict(operation_id=str(uuid.uuid4()), period="2026-11", target_id="general"),
    )


def imported(snapshot, changes=None):
    rows = [
        dict(contract_uuid=r["contract_uuid"], values=copy.deepcopy(r["values"]))
        for r in snapshot["rows"]
    ]
    if changes:
        rows[0]["values"].update(changes)
    return dict(
        export_id=snapshot["manifest"]["export_id"],
        schema_version=1,
        manifest_hash=snapshot["manifest"]["manifest_hash"],
        file_sha256="a" * 64,
        rows=rows,
    )


def apply(w, preview, resolutions=None):
    return exchange.apply(
        w["owner"],
        dict(
            operation_id=str(uuid.uuid4()),
            preview_id=preview["preview_id"],
            file_sha256="a" * 64,
            resolutions=resolutions or {},
        ),
    )


def test_projection_roundtrip_creates_no_work(crm_world):
    w = crm_world
    store.command(w["member"], call_body(w))
    snapshot = exported(w)
    plan = exchange.preview(w["owner"], imported(snapshot))
    assert not plan["changes"]
    outcome = apply(w, plan)
    assert outcome["changed_rows"] == 0 and not outcome["events"]
    assert len(store.card(w["owner"], w["person"])["history"]) == 1


def test_import_names_forbidden_field_and_keeps_role_restrictions(crm_world):
    w=crm_world
    analyst=replace(w['member'],role='analyst',capabilities=frozenset({'exchange','contact'}))
    snapshot=exchange.export(analyst,dict(operation_id=str(uuid.uuid4()),period='2026-11',target_id='general'))
    with pytest.raises(CRMError,match='Нет права изменять поле') as error:
        exchange.preview(analyst,imported(snapshot,{'soft_comment':'Forbidden call edit'}))
    assert error.value.status==403 and error.value.details['key']=='soft_comment'
    assert not store.card(analyst,w['person'])['history']
    plan=exchange.preview(analyst,imported(snapshot,{'phone_main':'+79990000002'}))
    assert len(plan['changes'])==1 and plan['changes'][0]['key']=='phone_main'


def test_three_way_conflict_requires_explicit_resolution(crm_world):
    w = crm_world
    snapshot = exported(w)
    store.command(w["member"], call_body(w))
    plan = exchange.preview(
        w["owner"], imported(snapshot, {"soft_comment": "Manual Excel"})
    )
    assert plan["conflicts"] == 1
    with pytest.raises(CRMError) as err:
        apply(w, plan)
    assert err.value.code == "unresolved"
    key = plan["changes"][0]["contract_uuid"] + ":soft_comment"
    result = apply(w, plan, {key: "excel"})
    assert result["changed_rows"] == 1
    assert store.card(w["owner"], w["person"])["history"][0]["origin"] == "excel"


def test_changed_server_after_preview_invalidates_choice(crm_world):
    w = crm_world
    snapshot = exported(w)
    plan = exchange.preview(w["owner"], imported(snapshot, {"soft_comment": "Excel"}))
    store.command(w["member"], call_body(w))
    with pytest.raises(CRMError) as err:
        apply(w, plan)
    assert err.value.code == "preview_stale"


def test_empty_zero_and_explicit_clear_are_distinct(crm_world):
    w = crm_world
    snapshot = exported(w)
    body = imported(snapshot, {"debt_total": None})
    assert not exchange.preview(w["owner"], body)["changes"]
    body["rows"][0]["clear"] = ["debt_total"]
    plan = exchange.preview(w["owner"], body)
    assert plan["changes"][0]["incoming"] is None
    apply(w, plan)
    snapshot = exported(w)
    plan = exchange.preview(w["owner"], imported(snapshot, {"debt_total": "0"}))
    assert plan["changes"][0]["incoming"] == "0.00"
    apply(w, plan)


def test_omission_never_unassigns_or_clears(crm_world):
    w = crm_world
    body = imported(exported(w))
    body["rows"] = body["rows"][:1]
    plan = exchange.preview(w["owner"], body)
    assert plan["missing_rows"] == 1 and not plan["changes"]
    apply(w, plan)
    assert store.portfolio(w["member"])["total_contracts"] == 2


def test_duplicate_or_forged_baseline_rejected(crm_world):
    w = crm_world
    body = imported(exported(w))
    body["rows"].append(body["rows"][0])
    with pytest.raises(CRMError, match="повторён"):
        exchange.preview(w["owner"], body)
    body["rows"].pop()
    body["manifest_hash"] = "b" * 64
    with pytest.raises(CRMError) as err:
        exchange.preview(w["owner"], body)
    assert err.value.code == "baseline_missing"


def test_exact_readback_receipt_cannot_ack_same_text_other_event(crm_world):
    w = crm_world
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO crm_route_policies VALUES(?,?,?,?,?,?)",
            (
                "route",
                1,
                "team-A",
                "call",
                canonical([{"target_id": "general", "delay_seconds": 0}]),
                1,
            ),
        )
        conn.commit()
    first = store.command(w["member"], call_body(w))
    snapshot = exported(w)
    cells = [
        dict(
            contract_uuid=r["contract_uuid"], key=k, value=v, event_id=first["event_id"]
        )
        for r in snapshot["rows"]
        for k, v in r["values"].items()
        if k in {"soft_comment", "soft_call_status"}
    ]
    receipt = dict(
        receipt_id=str(uuid.uuid4()),
        export_id=snapshot["manifest"]["export_id"],
        manifest_hash=snapshot["manifest"]["manifest_hash"],
        target_id="general",
        file_sha256="a" * 64,
        cells=cells,
    )
    forged = copy.deepcopy(receipt)
    forged["cells"][0]["event_id"] = str(uuid.uuid4())
    with pytest.raises(CRMError) as err:
        exchange.receipt(w["owner"], forged)
    assert err.value.code == "readback_mismatch"
    partial = copy.deepcopy(receipt)
    partial["cells"] = partial["cells"][:1]
    with pytest.raises(CRMError) as err:
        exchange.receipt(w["owner"], partial)
    assert err.value.code == "incomplete_receipt"
    assert exchange.receipt(w["owner"], receipt)["event_ids"] == [first["event_id"]]
    assert exchange.receipt(w["owner"], receipt)["deduplicated"]
    receipt["file_sha256"] = "b" * 64
    with pytest.raises(CRMError) as err:
        exchange.receipt(w["owner"], receipt)
    assert err.value.code == "receipt_conflict"
