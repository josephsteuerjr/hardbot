import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import threading

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from crm import grant_admin, grants, http, shared_auth, storage
from crm.access import Principal
from crm.contract import CRMError, canonical
from crm.roles import GRANT_ROLE_CAPABILITIES
from test_crm_core import crm_world


def operation(number):
    return "grant-admin-operation-%04d" % number


def grant(role="employee", territory="self", capabilities=("note",), **kwargs):
    return dict(
        role=role,
        territory=territory,
        capabilities=list(capabilities),
        employees=list(kwargs.pop("employees", ())),
        all_contracts=kwargs.pop("all_contracts", False),
        read_all_contracts=kwargs.pop("read_all_contracts", False),
        is_default=kwargs.pop("is_default", False),
        **kwargs,
    )


@pytest.fixture
def admin_world(crm_world):
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('distribution_mode','isolated')")
        conn.execute(
            "INSERT INTO crm_meta VALUES('deployment_namespace',?)",
            (shared_auth.NAMESPACE,),
        )
    return crm_world


def preview(world, revision, desired, number=1, actor_uid=101, admin=None):
    return grant_admin.preview(
        admin or world["owner"],
        dict(
            operation="preview",
            actor_uid=actor_uid,
            base_revision=revision,
            grants=desired,
            operation_id=operation(number),
        ),
    )


def apply_body(checked):
    return copy.deepcopy(dict(
        {key: checked[key] for key in (
            "actor_uid",
            "base_revision",
            "grants",
            "diff",
            "warnings",
            "preview_sha256",
            "operation_id",
        )},
        operation="apply",
    ))


def apply_preview(world, checked, admin=None):
    return grant_admin.apply(admin or world["owner"], apply_body(checked))


def actor_state(actor_uid=101):
    with storage.connect() as conn:
        access_model, revision = grant_admin._revision(conn, actor_uid)
        return access_model, revision, grants.load(conn, actor_uid)


def editable(grant_value):
    return {key: grant_value[key] for key in grant_admin.GRANT_KEYS if key in grant_value}


def identity(world, role="employee", uid=101):
    return dict(uid=uid, device_id=uid + 1000, name="Synthetic actor", role=role)


def test_catalog_preview_apply_replay_and_composite_default(admin_world):
    world = admin_world
    catalog = grant_admin.catalog(world["owner"])
    assert catalog["isolated"] and catalog["global_owner_only"]
    assert "owner" in {item["role"] for item in catalog["roles"]}
    listed = grant_admin.list_state(world["owner"])
    target = next(item for item in listed["actors"] if item["actor_uid"] == 101)
    assert target["base_revision"] == 0 and target["access_model"] == 1
    assert target["grants"] == [] and target["manageable"]

    checked = preview(
        world,
        0,
        [
            grant(capabilities=("note", "call"), is_default=True),
            grant(role="analyst", capabilities=("note",)),
        ],
    )
    assert checked["operation_id"] == operation(1)
    assert checked["grants"][0]["capabilities"] == ["note"]
    assert len(checked["preview_sha256"]) == 64
    assert {item["action"] for item in checked["diff"]} == {"create", "activate"}
    assert all("Добавить:" in item["text"] for item in checked["diff"] if item["action"] == "create")
    assert any("access_model=2" in item["text"] for item in checked["diff"])

    receipt = apply_preview(world, checked)
    assert receipt["status"] == "applied" and receipt["changed"]
    assert receipt["policy_revision"] == 3 and receipt["access_model"] == 2
    assert not receipt["production_access_changed"]
    assert apply_preview(world, checked) == receipt
    assert actor_state()[:2] == (2, 3)
    assert actor_state(202)[:2] == (1, 0)

    employee = shared_auth.principal(identity(world))
    employee_grant = next(item for item in receipt["grants"] if item["role"] == "employee")
    assert employee.grant_id == employee_grant["grant_id"]
    assert employee.capabilities == frozenset({"call", "note"})
    analyst = shared_auth.principal(identity(world, "analyst"))
    assert analyst.role == "analyst" and analyst.capabilities == frozenset({"note"})
    assert "call" not in analyst.capabilities

    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_commands").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM crm_audit WHERE kind='grant_admin_applied'").fetchone()[0] == 1
        changes = list(conn.execute("SELECT action,changed_by_uid FROM crm_grant_changes ORDER BY revision"))
        assert [row["action"] for row in changes] == [
            "grant_created",
            "grant_created",
            "access_model_changed",
        ]
        assert {row["changed_by_uid"] for row in changes} == {999}


def test_no_op_apply_keeps_revision_and_returns_a_durable_receipt(admin_world):
    first = apply_preview(
        admin_world,
        preview(admin_world, 0, [grant(is_default=True)]),
    )
    current = [editable(item) for item in first["result_grants"]]
    checked = preview(admin_world, first["policy_revision"], current, number=2)
    assert checked["diff"] == [] and checked["warnings"] == []
    receipt = apply_preview(admin_world, checked)
    assert not receipt["changed"] and receipt["policy_revision"] == first["policy_revision"]
    assert apply_preview(admin_world, checked) == receipt
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_grant_changes").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM crm_commands").fetchone()[0] == 2


def test_revoke_is_scoped_to_one_actor_and_default_keeps_old_client_working(admin_world):
    world = admin_world
    first = apply_preview(
        world,
        preview(
            world,
            0,
            [grant(is_default=True), grant(role="analyst", capabilities=("note",))],
        ),
    )
    employee_grant = next(
        item for item in first["result_grants"] if item["role"] == "employee"
    )
    checked = preview(world, 3, [editable(employee_grant)], number=2)
    receipt = apply_preview(world, checked)
    assert receipt["policy_revision"] == 4
    assert [item["action"] for item in receipt["diff"]] == ["revoke"]
    assert shared_auth.principal(identity(world)).role == "employee"
    with pytest.raises(CRMError) as exc:
        shared_auth.principal(identity(world, "analyst"))
    assert exc.value.code == "grant_required"
    assert actor_state(202)[2] == ()


def test_stale_preview_is_409_and_does_not_mutate_or_consume_operation(admin_world):
    world = admin_world
    checked = preview(world, 0, [grant()], number=1)
    with storage.connect() as conn:
        grants.upsert(
            conn,
            101,
            role="employee",
            territory="self",
            capabilities={"call"},
            is_default=True,
        )
    with pytest.raises(CRMError) as exc:
        apply_preview(world, checked)
    assert exc.value.status == 409 and exc.value.code == "policy_revision_conflict"
    assert exc.value.details["policy_revision"] == 1
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_commands").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM crm_audit").fetchone()[0] == 0
        assert len(grants.load(conn, 101)) == 1


def test_concurrent_replay_returns_one_receipt_and_one_audit(admin_world):
    world = admin_world
    checked = preview(world, 0, [grant(is_default=True)])
    body = apply_body(checked)
    barrier = threading.Barrier(2)

    def run():
        barrier.wait(timeout=10)
        return grant_admin.apply(world["owner"], copy.deepcopy(body))

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: run(), range(2)))
    assert receipts[0] == receipts[1]
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_commands").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM crm_grants").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM crm_grant_changes").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM crm_audit").fetchone()[0] == 1


def test_concurrent_different_previews_have_one_winner_and_one_stale(admin_world):
    world = admin_world
    first = preview(world, 0, [grant(capabilities=("note",), is_default=True)], number=1)
    second = preview(world, 0, [grant(capabilities=("call",), is_default=True)], number=2)
    barrier = threading.Barrier(2)

    def run(checked):
        barrier.wait(timeout=10)
        try:
            return grant_admin.apply(world["owner"], apply_body(checked))
        except CRMError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, (first, second)))
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    conflict = next(item for item in outcomes if isinstance(item, CRMError))
    assert conflict.status == 409 and conflict.code == "policy_revision_conflict"
    assert actor_state()[:2] == (2, 2)
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_commands").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM crm_grants").fetchone()[0] == 1


def test_reused_operation_id_with_another_body_is_conflict(admin_world):
    world = admin_world
    checked = preview(world, 0, [grant(is_default=True)])
    receipt = apply_preview(world, checked)
    changed = apply_body(checked)
    changed["grants"][0]["capabilities"] = ["call"]
    with pytest.raises(CRMError) as exc:
        grant_admin.apply(world["owner"], changed)
    assert exc.value.status == 409 and exc.value.code == "operation_conflict"
    assert apply_preview(world, checked) == receipt


def analyst_admin(world, *, capabilities=("access_manage", "note", "analytics"), scope=None):
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO crm_actor_links VALUES(?,?,?,?) ON CONFLICT(actor_uid) DO NOTHING",
            (303, world["employee"], "synthetic-grant-admin", 1),
        )
        existing = json.loads(conn.execute("SELECT capabilities_json FROM crm_memberships WHERE employee_uuid=?",
                                          (world["employee"],)).fetchone()[0])
        fresh = set(existing) | ({'access_manage'} if 'access_manage' in capabilities else set())
        conn.execute("UPDATE crm_memberships SET capabilities_json=? WHERE employee_uuid=?",
                     (canonical(sorted(fresh)), world["employee"]))
    return Principal(
        303,
        world["employee"],
        "team-A",
        "analyst",
        frozenset(capabilities),
        frozenset(scope or {world["employee"]}),
    )


def test_analyst_needs_separate_access_manage_and_stays_in_own_area(admin_world):
    world = admin_world
    without = analyst_admin(world, capabilities=("note",))
    with pytest.raises(CRMError) as exc:
        grant_admin.catalog(without)
    assert exc.value.status == 403

    admin = analyst_admin(world)
    checked = preview(world, 0, [grant(is_default=True)], admin=admin)
    receipt = apply_preview(world, checked, admin=admin)
    assert receipt["policy_revision"] == 2
    listed = grant_admin.list_state(admin)
    assert {item["actor_uid"] for item in listed["actors"]} == {101, 303}
    with pytest.raises(CRMError) as exc:
        preview(world, 0, [grant()], number=2, actor_uid=202, admin=admin)
    assert exc.value.code == "grant_escalation"


@pytest.mark.parametrize(
    "malicious",
    [
        grant(role="owner", territory="all", capabilities=GRANT_ROLE_CAPABILITIES["owner"], all_contracts=True),
        grant(role="manager", territory="all", capabilities=("note",), all_contracts=True),
        grant(role="manager", territory="self", capabilities=("note",), read_all_contracts=True),
        grant(role="server_operator", capabilities=()),
        grant(role="root", capabilities=()),
        grant(capabilities=("access_manage",)),
        grant(capabilities=("call",)),
        grant(role="manager", territory="employees", capabilities=("note",), employees=("outsider",)),
    ],
)
def test_analyst_cannot_escalate_global_service_capability_or_scope(admin_world, malicious):
    world = admin_world
    admin = analyst_admin(world)
    with pytest.raises(CRMError) as exc:
        preview(world, 0, [malicious], admin=admin)
    assert exc.value.status == 403 and exc.value.code == "grant_escalation"
    assert actor_state()[:2] == (1, 0)


def test_analyst_cannot_exceed_target_membership_or_manage_existing_global(admin_world):
    world = admin_world
    admin = analyst_admin(world)
    with pytest.raises(CRMError) as exc:
        preview(world, 0, [grant(role="analyst", capabilities=("analytics",))], admin=admin)
    assert exc.value.code == "grant_escalation"

    owner_checked = preview(
        world,
        0,
        [grant(role="manager", territory="all", capabilities=("note",), all_contracts=True, read_all_contracts=True)],
        number=2,
    )
    owner_receipt = apply_preview(world, owner_checked)
    listed = grant_admin.list_state(admin)
    target = next(item for item in listed["actors"] if item["actor_uid"] == 101)
    assert not target["manageable"] and target["reason"]
    with pytest.raises(CRMError) as exc:
        preview(
            world,
            owner_receipt["policy_revision"],
            [editable(item) for item in owner_receipt["result_grants"]],
            number=3,
            admin=admin,
        )
    assert exc.value.code == "grant_escalation"


def test_apply_rechecks_escalation_instead_of_trusting_preview_hash(admin_world):
    world = admin_world
    admin = analyst_admin(world)
    checked = preview(world, 0, [grant(is_default=True)], admin=admin)
    body = apply_body(checked)
    body["grants"].append(
        grant(role="manager", territory="all", capabilities=("note",), all_contracts=True)
    )
    with pytest.raises(CRMError) as exc:
        grant_admin.apply(admin, body)
    assert exc.value.status == 403 and exc.value.code == "grant_escalation"
    assert actor_state()[:2] == (1, 0)


@pytest.mark.parametrize("mode", [None, "cutover"])
def test_non_isolated_runtime_never_mutates_grants_or_receipts(crm_world, mode):
    if mode is not None:
        with storage.connect() as conn:
            conn.execute("INSERT INTO crm_meta VALUES('distribution_mode',?)", (mode,))
    with storage.connect() as conn:
        tables = ("crm_grants", "crm_actor_access", "crm_grant_changes", "crm_commands", "crm_audit")
        before = {table: list(conn.execute("SELECT * FROM " + table)) for table in tables}
    for call in (
        lambda: grant_admin.catalog(crm_world["owner"]),
        lambda: grant_admin.list_state(crm_world["owner"]),
        lambda: preview(crm_world, 0, [grant()]),
        lambda: grant_admin.apply(
            crm_world["owner"],
            dict(
                operation="apply",
                actor_uid=101,
                base_revision=0,
                grants=[grant()],
                preview_sha256="0" * 64,
                operation_id=operation(9),
            ),
        ),
    ):
        with pytest.raises(CRMError) as exc:
            call()
        assert exc.value.status == 403 and exc.value.code == "grant_admin_unavailable"
    with storage.connect() as conn:
        assert before == {table: list(conn.execute("SELECT * FROM " + table)) for table in tables}


def test_http_grant_contract_list_preview_apply_stale_and_replay(admin_world):
    world = admin_world

    async def authenticate(request):
        return world["owner"]

    async def scenario():
        app = web.Application()
        http.install(app, authenticate=authenticate)
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/api/crm/grant-catalog")
            assert response.status == 200 and (await response.json())["isolated"]
            response = await client.post("/api/crm/grant-admin", json={"operation": "list"})
            assert response.status == 200
            assert len((await response.json())["actors"]) == 2
            response = await client.post(
                "/api/crm/grant-admin",
                json=dict(
                    operation="preview",
                    actor_uid=101,
                    base_revision=0,
                    grants=[grant(is_default=True)],
                    operation_id=operation(20),
                ),
            )
            assert response.status == 200
            checked = await response.json()
            body = dict(checked, operation="apply")
            response = await client.post("/api/crm/grant-admin", json=body)
            assert response.status == 200
            receipt = await response.json()
            response = await client.post("/api/crm/grant-admin", json=body)
            assert response.status == 200 and await response.json() == receipt

            stale = dict(body, operation_id=operation(21))
            response = await client.post("/api/crm/grant-admin", json=stale)
            assert response.status == 409
            assert (await response.json())["error"] == "policy_revision_conflict"

            response = await client.post(
                "/api/crm/grant-admin",
                json=dict(
                    operation="preview",
                    actor_uid=101,
                    base_revision=0,
                    grants=[grant(is_default=True)],
                    operation_id=operation(22),
                ),
            )
            assert response.status == 409
            assert (await response.json())["error"] == "policy_revision_conflict"

    asyncio.run(scenario())



def test_apply_takes_actor_lock_before_reading_revision(admin_world, monkeypatch):
    checked = preview(admin_world, 0, [grant(is_default=True)])
    events = []
    original_begin = grants._begin
    original_load = grants.load

    def begin(conn, actor_uid):
        events.append('begin')
        return original_begin(conn, actor_uid)

    def load(conn, actor_uid):
        events.append('load')
        return original_load(conn, actor_uid)

    monkeypatch.setattr(grants, '_begin', begin)
    monkeypatch.setattr(grants, 'load', load)
    apply_preview(admin_world, checked)
    assert events[:2] == ['begin', 'load']
