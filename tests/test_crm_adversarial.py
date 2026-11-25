import json, uuid
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import db
from crm import analytics, store, telemetry, exchange, pilot_server
from crm.contract import canonical, identifier
from test_crm_core import crm_world, call_body
from test_crm_adoption import cohort, plan, technical


def test_two_devices_gap_version_leave_and_role_denominators(crm_world):
    w = crm_world
    q = cohort(w)
    a = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    b = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    plan(w, w["employee"], a, b)
    plan(w, w["stranger"].employee_uuid, a, b, "leave")
    store.command(w["member"], call_body(w), client_version="3.3.0-pilot.1")
    analytics.aggregate()
    telemetry.ingest(
        w["member"],
        {
            "events": [
                technical(),
                technical(),
                {**technical(), "event_type": "queue_gap", "dropped_count": 7},
            ]
        },
    )
    report = analytics.report(
        w["owner"], {**q, "role": "employee", "version": "3.3.0-pilot.1"}
    )
    assert (
        report["activation"]["denominator"] == 1
        and report["activation"]["numerator"] == 1
    )
    assert len(report["employees"]) == 2 and report["counts"]["with_gaps"] == 1
    assert (
        next(r for r in report["employees"] if r["employee_uuid"] == w["employee"])[
            "dropped_events"
        ]
        == 7
    )
    assert analytics.report(w["owner"], {**q, "version": "0.0.0"})[
        "activation"
    ] == dict(numerator=0, denominator=1, value=0)
    assert (
        analytics.report(w["owner"], {**q, "role": "manager"})["activation"]["value"]
        is None
    )


def test_required_targets_and_undefined_sla_are_not_success(crm_world):
    w = crm_world
    q = cohort(w)
    a = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    b = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    plan(w, w["employee"], a, b)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO crm_route_policies VALUES(?,?,?,?,?,?)",
            (
                "dual",
                1,
                "team-A",
                "call",
                canonical(
                    [
                        {"target_id": "personal", "delay_seconds": 0},
                        {"target_id": "general", "delay_seconds": 0},
                    ]
                ),
                1,
            ),
        )
        conn.commit()
    event = store.command(w["member"], call_body(w))
    analytics.aggregate()

    def receipt(target):
        snapshot = exchange.export(
            w["owner"],
            dict(operation_id=str(uuid.uuid4()), period="2026-11", target_id=target),
        )
        cells = [
            dict(
                contract_uuid=r["contract_uuid"],
                key=k,
                value=r["values"][k],
                event_id=v["event_id"],
            )
            for r in snapshot["rows"]
            for k, v in r["provenance"]["_fields"].items()
        ]
        return exchange.receipt(
            w["owner"],
            dict(
                receipt_id=str(uuid.uuid4()),
                export_id=snapshot["manifest"]["export_id"],
                manifest_hash=snapshot["manifest"]["manifest_hash"],
                target_id=target,
                file_sha256="a" * 64,
                cells=cells,
            ),
        )

    receipt("personal")
    report = analytics.report(w["owner"], q)
    assert (
        report["delivery"]["numerator"] == 0 and report["delivery"]["denominator"] == 1
    )
    receipt("general")
    report = analytics.report(w["owner"], q)
    assert report["delivery"]["numerator"] == 1
    with db.connect() as conn:
        conn.execute("UPDATE crm_delivery_targets SET due_at=NULL")
        conn.commit()
    report = analytics.report(w["owner"], q)
    assert (
        report["delivery"]["without_sla"] == 1 and report["delivery"]["value"] is None
    )


def test_interrupted_demo_seed_resumes_same_operations(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "synthetic.db")
    db.init_db()
    real = pilot_server.shifts.set_plan
    failed = [False]

    def interrupt(*args, **kwargs):
        if not failed[0]:
            failed[0] = True
            raise RuntimeError("Synthetic interruption")
        return real(*args, **kwargs)

    monkeypatch.setattr(pilot_server.shifts, "set_plan", interrupt)
    try:
        pilot_server.seed()
    except RuntimeError:
        pass
    pilot_server.seed()
    pilot_server.seed()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_contracts").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM crm_cohorts").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM crm_events WHERE kind='script_publish'"
            ).fetchone()[0]
            == 1
        )
