import json, uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest
import db
from crm import analytics, shifts, store, telemetry
from crm.contract import CRMError, canonical, now
from test_crm_core import crm_world, call_body


def technical(**extra):
    return dict(
        schema_version=1,
        event_id=str(uuid.uuid4()),
        installation_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
        producer="desktop",
        client_version="3.3.0-pilot.1",
        event_type="client_ready",
        feature="shell",
        **extra
    )


def cohort(w):
    start = (datetime.now(timezone.utc) - timedelta(days=28)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    cid = str(uuid.uuid4())
    employees = [w["employee"], w["stranger"].employee_uuid]
    analytics.configure_cohort(
        w["owner"],
        dict(
            operation_id=str(uuid.uuid4()),
            cohort_id=cid,
            name="Synthetic cohort",
            starts_at=start,
            members=[
                dict(employee_uuid=e, access_at=start, features=["call"])
                for e in employees
            ],
        ),
    )
    return dict(cohort_id=cid, since=start, until=end)


def plan(w, e, start, end, state="planned"):
    shifts.set_plan(
        w["owner"],
        dict(
            operation_id=str(uuid.uuid4()),
            employee_uuid=e,
            shifts=[
                dict(
                    starts_at=start,
                    ends_at=end,
                    timezone="Europe/Samara",
                    state=state,
                    source="Synthetic roster",
                )
            ],
        ),
    )


def test_no_launch_stays_in_denominator_group_call_counts_once(crm_world):
    w = crm_world
    query = cohort(w)
    a = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    b = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    for e in (w["employee"], w["stranger"].employee_uuid):
        plan(w, e, a, b)
    body = call_body(w)
    store.command(w["member"], body)
    store.command(w["member"], body)
    telemetry.ingest(w["member"], {"events": [technical()]})
    assert analytics.aggregate() == 1
    report = analytics.report(w["owner"], query)
    assert report["activation"] == dict(numerator=1, denominator=2, value=0.5)
    assert report["regularity"] == dict(numerator=1, denominator=2, value=0.5)
    assert report["origins"]["call"]["counts"]["app"] == 1
    assert report["origins"]["call"]["app_share"] is None
    assert (
        report["coverage"]["denominator"] == 2 and report["coverage"]["numerator"] == 1
    )
    before = report["activation"]
    assert analytics.aggregate(rebuild=True) == 1
    assert analytics.report(w["owner"], query)["activation"] == before


def test_unknown_and_future_schedule_are_not_zero_workdays(crm_world):
    w = crm_world
    query = cohort(w)
    a = datetime.now(timezone.utc) + timedelta(hours=2)
    plan(w, w["employee"], a.isoformat(), (a + timedelta(hours=8)).isoformat())
    report = analytics.report(w["owner"], query)
    assert report["activation"]["value"] is None
    assert report["counts"]["unknown"] == 1 and report["counts"]["not_started"] == 1


def test_two_on_two_off_and_night_shift(crm_world):
    w = crm_world
    query = cohort(w)
    base = datetime.now(timezone.utc) - timedelta(days=5)
    for day in range(4):
        plan(
            w,
            w["employee"],
            (base + timedelta(days=day)).isoformat(),
            (base + timedelta(days=day, hours=10)).isoformat(),
            "planned" if day < 2 else "off",
        )
    report = analytics.report(w["owner"], query)
    assert (
        report["regularity"]["denominator"] == 2 and report["regularity"]["value"] == 0
    )
    assert report["counts"]["unknown"] == 1


def test_technical_retries_conflicts_pii_and_size(crm_world):
    w = crm_world
    event = technical()
    assert telemetry.ingest(w["member"], {"events": [event]})["new"] == 1
    assert telemetry.ingest(w["member"], {"events": [event]})["new"] == 0
    with pytest.raises(CRMError):
        telemetry.ingest(w["member"], {"events": [{**event, "feature": "call"}]})
    for extra in (
        {"text": "debtor"},
        {"employee_uuid": "forged"},
        {"error_code": "free text"},
        {"event_type": "command_accepted"},
    ):
        with pytest.raises(CRMError):
            telemetry.ingest(w["member"], {"events": [{**technical(), **extra}]})
    with pytest.raises(CRMError):
        telemetry.ingest(w["member"], {"events": [technical() for _ in range(101)]})
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_telemetry").fetchone()[0] == 1


def test_shift_server_clock_transitions_retry_and_foreign_access(crm_world):
    w = crm_world
    body = dict(operation_id=str(uuid.uuid4()), action="start")
    first = shifts.command(w["member"], body)
    assert shifts.command(w["member"], body) == first
    with pytest.raises(CRMError):
        shifts.command(
            w["member"], dict(operation_id=str(uuid.uuid4()), action="start")
        )
    pause = shifts.command(
        w["member"],
        dict(
            operation_id=str(uuid.uuid4()),
            action="break",
            shift_id=first["shift_id"],
            revision=1,
        ),
    )
    assert pause["state"] == "break"
    with pytest.raises(CRMError):
        shifts.command(
            w["member"],
            dict(
                operation_id=str(uuid.uuid4()),
                action="resume",
                shift_id=first["shift_id"],
                revision=1,
            ),
        )
    resumed = shifts.command(
        w["member"],
        dict(
            operation_id=str(uuid.uuid4()),
            action="resume",
            shift_id=first["shift_id"],
            revision=2,
        ),
    )
    final = shifts.command(
        w["member"],
        dict(
            operation_id=str(uuid.uuid4()),
            action="finish",
            shift_id=first["shift_id"],
            revision=3,
        ),
    )
    assert final["state"] == "finished" and final["ends_at"]
    assert len(shifts.summary(w["member"])["shifts"][0]["events"]) == 4
    with pytest.raises(CRMError):
        shifts.summary(w["stranger"], w["employee"])


def test_managers_cannot_learn_foreign_employee_metrics(crm_world):
    w = crm_world
    query = cohort(w)
    principal = replace(
        w["member"], role="manager", capabilities=frozenset(["analytics"])
    )
    report = analytics.report(principal, query)
    assert [r["employee_uuid"] for r in report["employees"]] == [w["employee"]]
    with pytest.raises(CRMError):
        analytics.report(w["stranger"], query)
