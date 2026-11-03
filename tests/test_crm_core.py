import json
import uuid
import pytest
import db
from crm import schema, store
from crm.access import Principal
from crm.contract import CRMError, canonical, digest, identifier, now


@pytest.fixture
def crm_world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "pilot.db")
    db.init_db()
    person = identifier("person", "synthetic-one")
    other = identifier("person", "synthetic-other")
    employee = identifier("staff", "one")
    outsider = identifier("staff", "other")
    start = "2026-01-01T00:00:00.000+00:00"
    contracts = [identifier("contract", "A"), identifier("contract", "B")]
    with db.connect() as conn:
        schema.initialize(conn)
        for p in (person, other):
            conn.execute(
                "INSERT INTO crm_people VALUES(?,?,?,?)",
                (p, "Synthetic person", "explicit_fixture", start),
            )
        for e, name in [(employee, "Employee"), (outsider, "Outsider")]:
            conn.execute(
                "INSERT INTO crm_employees VALUES(?,?,?,?,?)",
                (e, e, name, "explicit_fixture", 1),
            )
            conn.execute(
                "INSERT INTO crm_memberships VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    e,
                    "team-A" if e == employee else "team-B",
                    "employee",
                    canonical(
                        [
                            "call",
                            "visit",
                            "note",
                            "contact",
                            "promise",
                            "task",
                            "reported_payment",
                            "document",
                            "correct",
                        ]
                    ),
                    "Europe/Samara",
                    start,
                    None,
                ),
            )
        conn.execute(
            "INSERT INTO crm_actor_links VALUES(?,?,?,?)", (101, employee, "fixture", 1)
        )
        conn.execute(
            "INSERT INTO crm_actor_links VALUES(?,?,?,?)", (202, outsider, "fixture", 1)
        )
        for i, c in enumerate(contracts):
            fields = {
                "contract_id": "DUPLICATE",
                "client": "Synthetic person",
                "soft_comment": "Base",
                "visit_comment": "Base",
                "debt_total": "123.45",
            }
            conn.execute(
                "INSERT INTO crm_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    c,
                    person,
                    "creditor-" + str(i),
                    "DUPLICATE",
                    None,
                    "RUB",
                    canonical(fields),
                    canonical(fields),
                    "{}",
                    "{}",
                    start,
                ),
            )
            for kind in ("call", "visit"):
                conn.execute(
                    "INSERT INTO crm_assignments VALUES(?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), employee, c, kind, start, None, "fixture"),
                )
        conn.commit()
    member = Principal(
        101,
        employee,
        "team-A",
        "employee",
        frozenset(
            [
                "call",
                "visit",
                "note",
                "contact",
                "promise",
                "task",
                "reported_payment",
                "document",
                "correct",
            ]
        ),
        frozenset([employee]),
    )
    stranger = Principal(
        202, outsider, "team-B", "employee", member.capabilities, frozenset([outsider])
    )
    owner = Principal(999, employee, "team-A", "owner", frozenset(), frozenset())
    return dict(
        member=member,
        stranger=stranger,
        owner=owner,
        contracts=contracts,
        person=person,
        employee=employee,
    )


def call_body(world, op=None, values=None):
    return dict(
        schema_version=1,
        operation_id=op or str(uuid.uuid4()),
        kind="call",
        contract_ids=world["contracts"],
        payload={
            "values": values
            or {"soft_comment": "Fresh", "soft_call_status": "Нет контакта"}
        },
        base_revisions={c: {"call": 0} for c in world["contracts"]},
    )


def test_one_group_call_is_one_event_with_fixed_contracts(crm_world):
    w = crm_world
    body = call_body(w)
    first = store.command(w["member"], body)
    assert first == store.command(w["member"], body)
    card = store.card(w["member"], w["person"])
    assert len(card["history"]) == 1 and len(card["contracts"]) == 2
    assert all(
        r["fields"]["soft_comment"] == "Fresh" and r["revisions"]["call"] == 1
        for r in card["contracts"]
    )
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_outbox").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM crm_event_contracts").fetchone()[0] == 2
        )


def test_changed_request_same_id_rolls_back(crm_world):
    w = crm_world
    body = call_body(w)
    store.command(w["member"], body)
    body["payload"]["values"]["soft_comment"] = "Changed"
    with pytest.raises(CRMError, match="другого запроса"):
        store.command(w["member"], body)
    assert len(store.card(w["member"], w["person"])["history"]) == 1


def test_stale_offline_action_keeps_history_and_fresh_projection(crm_world):
    w = crm_world
    store.command(w["member"], call_body(w))
    result = store.command(
        w["member"], call_body(w, values={"soft_comment": "Old offline"})
    )
    assert (
        result["status"] == "accepted" and result["projection_status"] == "needs_review"
    )
    card = store.card(w["member"], w["person"])
    assert len(card["history"]) == 2
    assert all(r["fields"]["soft_comment"] == "Fresh" for r in card["contracts"])


def test_unauthorized_contract_never_enters_journal(crm_world):
    w = crm_world
    with pytest.raises(CRMError) as error:
        store.command(w["stranger"], call_body(w))
    assert error.value.status == 403
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_events").fetchone()[0] == 0
    with pytest.raises(CRMError):
        store.card(w["stranger"], w["person"])


def test_phone_number_and_name_are_not_contract_identity(crm_world):
    w = crm_world
    card = store.card(w["member"], w["person"])
    assert len({r["creditor_id"] for r in card["contracts"]}) == 2
    assert len({r["external_id"] for r in card["contracts"]}) == 1


def test_employee_cannot_edit_debt_via_call(crm_world):
    w = crm_world
    with pytest.raises(CRMError) as error:
        store.command(w["member"], call_body(w, values={"debt_total": "0.00"}))
    assert error.value.status == 403


def test_history_cannot_be_mutated_or_deleted(crm_world):
    w = crm_world
    store.command(w["member"], call_body(w))
    import sqlite3

    with db.connect() as conn:
        for sql in ["UPDATE crm_events SET origin='excel'", "DELETE FROM crm_events"]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)


def test_client_cannot_forge_actor_or_origin(crm_world):
    w = crm_world
    for key in ("actor_uid", "origin", "employee_uuid", "team_id"):
        with pytest.raises(CRMError):
            store.command(w["member"], {**call_body(w), key: "forged"})


def test_correction_is_new_fact_preserving_author(crm_world):
    w = crm_world
    first = store.command(w["member"], call_body(w))
    body = dict(
        schema_version=1,
        operation_id=str(uuid.uuid4()),
        kind="correction",
        contract_ids=w["contracts"],
        correction_of=first["event_id"],
        reason="Исправлена опечатка",
        payload={"target_kind": "call", "values": {"soft_comment": "Corrected"}},
        base_revisions={c: {"call": 1} for c in w["contracts"]},
    )
    store.command(w["member"], body)
    history = store.card(w["member"], w["person"])["history"]
    assert len(history) == 2 and history[0]["correction_of"] == history[1]["event_id"]
    assert history[1]["payload"]["values"]["soft_comment"] == "Fresh"


def test_zero_money_is_known_and_float_rejected(crm_world):
    w = crm_world
    body = dict(
        schema_version=1,
        operation_id=str(uuid.uuid4()),
        kind="reported_payment",
        contract_ids=[w["contracts"][0]],
        payload={"amount": "0.00", "currency": "RUB"},
        base_revisions={},
    )
    store.command(w["member"], body)
    entity = store.card(w["member"], w["person"])["entities"][0]
    assert entity["data"]["amount"] == "0.00" and entity["kind"] == "reported_payment"
    body.update(
        operation_id=str(uuid.uuid4()), payload={"amount": 0.1, "currency": "RUB"}
    )
    with pytest.raises(CRMError):
        store.command(w["member"], body)


def test_server_clock_anchor_ignores_wrong_device_wall_clock(crm_world):
    w = crm_world
    anchor = store.anchor(w["member"], "installation-one")
    body = call_body(w)
    body["clock"] = {
        "anchor_id": anchor["anchor_id"],
        "installation_id": "installation-one",
        "elapsed_ms": 0,
    }
    body["occurred_at"] = "1999-01-01T00:00:00+00:00"
    result = store.command(w["member"], body)
    assert (
        result["occurred_at"] == anchor["server_at"]
        and result["time_quality"] == "anchored"
    )


def test_revoked_assignment_preserves_no_false_acceptance(crm_world):
    w = crm_world
    with db.connect() as conn:
        conn.execute("UPDATE crm_assignments SET ends_at=?", (now(),))
        conn.commit()
    with pytest.raises(CRMError) as error:
        store.command(w["member"], call_body(w))
    assert error.value.code == "scope_changed"
