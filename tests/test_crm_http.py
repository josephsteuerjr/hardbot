import uuid
import pytest
import db
from crm import migrate
from crm.contract import identifier
from test_manager_account_approval import world, call, MGR
from test_manager_password_reset import activated


@pytest.fixture
def pilot_http(world, monkeypatch):
    token, _ = activated(world)
    with db.connect() as conn:
        uid = conn.execute(
            "SELECT telegram_id FROM accounts WHERE username='self-own'"
        ).fetchone()[0]
    caps = [
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

    def employee(staff, name, actor, role, assignments):
        return dict(
            staff_id=staff,
            name=name,
            team_id="team-A" if staff != "foreign" else "team-B",
            role=role,
            capabilities=caps
            + (["analytics", "exchange", "shift_correct"] if role == "manager" else []),
            timezone="Europe/Samara",
            starts_at="2026-01-01T00:00:00Z",
            actor_uids=actor,
            assignments=assignments,
            evidence="Synthetic confirmed roster",
        )

    mapping = dict(
        source="HTTP fixture",
        contracts={
            cid: dict(creditor_id="demo-creditor") for cid in ["SELF-1", "SELF-2"]
        },
        employees=[
            employee(
                "own",
                "Own Employee",
                [uid],
                "employee",
                [dict(legacy_id="SELF-1", capability="call")],
            ),
            employee("manager", "Synthetic Leader", [MGR], "manager", []),
            employee(
                "foreign",
                "Foreign Employee",
                [],
                "employee",
                [dict(legacy_id="SELF-2", capability="call")],
            ),
        ],
    )
    migrate.apply(mapping)
    monkeypatch.setenv("HARD_CRM_PILOT", "1")
    return {
        "manager": world,
        "employee": {"X-App-Token": token},
        "foreign_contract": identifier("contract", '["demo-creditor","SELF-2"]'),
    }


def test_real_device_auth_and_server_scope(pilot_http):
    p = pilot_http
    assert call("GET", "/api/crm/context", {"X-App-Token": "not-a-token"})[0] == 401
    status, own = call("GET", "/api/crm/portfolio", p["employee"])
    assert status == 200 and own["total_contracts"] == 2
    status, manager = call("GET", "/api/crm/portfolio", p["manager"])
    assert status == 200 and manager["total_contracts"] == 2
    status, tasks = call('GET','/api/crm/tasks',p['employee'])
    assert status==200 and tasks['total']==0
    assert call('GET','/api/crm/tasks?employee_uuid=all',p['employee'])[0]==403
    foreign = dict(
        schema_version=1,
        operation_id=str(uuid.uuid4()),
        kind="call",
        contract_ids=[p["foreign_contract"]],
        payload={"values": {"soft_comment": "forbidden"}},
        base_revisions={p["foreign_contract"]: {"call": 0}},
    )
    status, result = call("POST", "/api/crm/command", p["employee"], foreign)
    assert status == 403 and result["error"] == "scope_changed"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_events").fetchone()[0] == 0


def test_employee_registry_routes_enforce_current_auth_and_field_rights(pilot_http):
    p=pilot_http
    assert call('GET','/api/crm/registry',{'X-App-Token':'wrong'})[0]==401
    status,data=call('GET','/api/crm/registry?view=all',p['employee'])
    assert status==200 and data['total']==2
    own=next(row for row in data['rows'] if row['contract_uuid']!=p['foreign_contract'])
    body=dict(schema_version=1,operation_id=str(uuid.uuid4()),rows=[dict(contract_uuid=own['contract_uuid'],
        values={'soft_comment':'HTTP registry correction'},clear=[],base_revisions={'call':own['revisions'].get('call',0)})])
    status,receipt=call('POST','/api/crm/registry-edit',p['employee'],body)
    assert status==200 and receipt['status']=='accepted'
    assert call('POST','/api/crm/registry-edit',p['employee'],body)[1]==receipt
    body['operation_id']=str(uuid.uuid4());body['rows'][0]['contract_uuid']=p['foreign_contract']
    assert call('POST','/api/crm/registry-edit',p['employee'],body)[0]==403
    assert call('GET','/api/crm/active-time',p['employee'])[0]==200


def test_http_body_spoof_and_legacy_cutover_are_explicit(pilot_http):
    p = pilot_http
    status, result = call(
        "POST",
        "/api/crm/command",
        p["employee"],
        {"actor_uid": MGR, "team_id": "foreign"},
    )
    assert status == 400 and result["error"] == "invalid"
    status, result = call(
        "POST",
        "/api/save",
        p["employee"],
        {"kind": "call", "op_id": "legacy-queue-operation"},
    )
    assert status == 409 and result["error"] == "crm_cutover"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_events").fetchone()[0] == 0


def test_real_manager_demotion_immediately_revokes_mutations(pilot_http):
    p = pilot_http
    status, result = call("GET", "/api/crm/script", p["manager"])
    assert status == 200 and result["can_edit"]
    db.set_manager(MGR, "Synthetic Leader", [], ["Own Employee"], role="security")
    status, result = call("GET", "/api/crm/script", p["manager"])
    assert status == 200 and not result["can_edit"]
    status, result = call(
        "POST",
        "/api/crm/script",
        p["manager"],
        dict(
            operation_id=str(uuid.uuid4()),
            action="save",
            base_revision=0,
            content={"title": "No", "steps": [{"title": "No", "text": "No"}]},
        ),
    )
    assert status == 403


def test_routes_absent_without_explicit_pilot_flag(world, monkeypatch):
    monkeypatch.delenv("HARD_CRM_PILOT", raising=False)
    assert call("GET", "/api/crm/context", world)[0] == 404
