import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from crm import pilot_server, shared_server, workspace
from crm.access import Principal
from crm.contract import CRMError
from crm.roles import ROLE_CAPABILITIES
from test_crm_core import crm_world


def principal(role, capabilities=None, *, server_operator=False):
    return Principal(
        101,
        "employee-one",
        "team-one",
        role,
        frozenset(ROLE_CAPABILITIES.get(role, ()) if capabilities is None else capabilities),
        frozenset(["employee-one"]),
        server_operator=server_operator,
    )


def ids(manifest):
    return [node["id"] for node in manifest["tree"]]


def flatten(nodes):
    return [node for parent in nodes for node in [parent, *flatten(parent["children"])]]


@pytest.mark.parametrize(
    "role,expected,default",
    [
        (
            "employee",
            ["portfolio", "registry", "cabinet", "queue", "field", "tasks", "active-time", "script", "connection", "help"],
            "portfolio",
        ),
        (
            "manager",
            ["portfolio", "registry", "manager-statistics", "active-time", "team", "script", "exchange", "connection"],
            "portfolio",
        ),
        (
            "analyst",
            ["registry", "exchange", "statistics", "team", "connection"],
            "registry",
        ),
        (
            "observer",
            ["portfolio", "tasks", "cabinet", "shift", "script", "queue", "adoption", "connection", "help"],
            "portfolio",
        ),
        (
            "courier",
            ["portfolio", "tasks", "cabinet", "queue", "shift", "script", "connection", "help"],
            "portfolio",
        ),
    ],
)
def test_legacy_five_roles_keep_their_existing_modules(role, expected, default):
    result = workspace.manifest(principal(role))
    assert ids(result) == expected
    assert result["defaultModule"] == default
    assert result["schema_version"] == 1 and result["revision"] == 1


def test_manager_optional_modules_and_task_group_follow_legacy_flags():
    result = workspace.manifest(principal("manager", {"assign", "exchange", "logistics"}, server_operator=True))
    assert ids(result) == [
        "portfolio", "registry", "manager-statistics", "logistics", "active-time",
        "team", "script", "exchange", "operator", "connection",
    ]
    script = workspace.require(result, "script")
    assert [node["id"] for node in script["children"]] == ["tasks"]
    assert workspace.require(result, "tasks")["title"] == "Задачи"


def test_owner_receives_the_complete_existing_catalog():
    result = workspace.manifest(principal("owner", ()))
    assert ids(result) == list(workspace.catalog_ids())
    assert len(flatten(result["tree"])) == len(workspace.catalog_ids())
    assert result["defaultModule"] == "portfolio"


def test_composite_grants_union_roles_without_mixing_their_capabilities():
    grants = [
        SimpleNamespace(role="manager", capabilities=frozenset({"analytics"}), territory="TJ", revision=4),
        SimpleNamespace(role="employee", capabilities=frozenset({"visit"}), territory="UZ", revision=7),
    ]
    legacy = principal("observer", {"exchange", "analytics"})
    composite = SimpleNamespace(
        role=legacy.role,
        capabilities=legacy.capabilities,
        employee_scope=legacy.employee_scope,
        server_operator=False,
        owner=False,
        grants=grants,
        access_model=2,
        policy_revision=7,
    )
    result = workspace.manifest(composite)
    assert "manager-statistics" in ids(result) and "team" in ids(result)
    assert "field" in ids(result) and "cabinet" in ids(result)
    assert "exchange" not in ids(result)
    assert "adoption" not in ids(result)
    assert result["defaultModule"] == "portfolio" and result["revision"] == 7
    assert result == workspace.manifest(SimpleNamespace(**{**vars(composite), "grants": list(reversed(grants))}))


def test_legacy_shadow_grants_never_replace_the_legacy_menu():
    value = SimpleNamespace(
        role="manager",
        capabilities=frozenset({"exchange"}),
        server_operator=False,
        owner=False,
        grants=[SimpleNamespace(role="employee", capabilities=frozenset(), revision=1)],
        access_model=1,
        policy_revision=3,
    )
    result = workspace.manifest(value)
    assert ids(result) == ids(workspace.manifest(principal("manager", {"exchange"})))
    assert "cabinet" not in ids(result) and result["revision"] == 3


def test_explicit_empty_grants_do_not_reactivate_legacy_modules():
    value = SimpleNamespace(
        role="manager",
        capabilities=ROLE_CAPABILITIES["manager"],
        server_operator=True,
        owner=False,
        grants=[],
        access_model=2,
    )
    result = workspace.manifest(value)
    assert ids(result) == ["connection"]
    assert result["defaultModule"] == "connection"


def test_grants_from_another_actor_do_not_expand_the_workspace():
    value = SimpleNamespace(
        actor_uid=101,
        role="employee",
        capabilities=ROLE_CAPABILITIES["employee"],
        server_operator=False,
        owner=False,
        grants=[SimpleNamespace(actor_uid=202, role="manager", capabilities=frozenset({"exchange"}), territory="all")],
        access_model=2,
    )
    assert ids(workspace.manifest(value)) == ["connection"]


def test_manifest_is_json_metadata_without_code_or_urls():
    result = workspace.manifest(principal("owner"))
    json.dumps(result, ensure_ascii=False)
    nodes = flatten(result["tree"])
    assert len({node["id"] for node in nodes}) == len(nodes)
    assert all(set(node) == {"id", "title", "icon", "order", "children", "scopes"} for node in nodes)
    assert all(node["title"] and node["icon"] and isinstance(node["order"], int) for node in nodes)
    assert all("url" not in node and "code" not in node and "component" not in node for node in nodes)


def test_unknown_module_is_rejected_before_any_action_authorization_changes():
    result = workspace.manifest(principal("employee"))
    with pytest.raises(CRMError) as error:
        workspace.require(result, "not-a-module")
    assert error.value.status == 404 and error.value.code == "unknown_module"
    with pytest.raises(CRMError) as error:
        workspace.require(result, "team")
    assert error.value.status == 404 and error.value.code == "unknown_module"
    assert workspace.require(result, "portfolio")["id"] == "portfolio"


def test_http_context_is_additive_and_rejects_unknown_module(crm_world):
    class Auth:
        async def authenticate(self, request):
            return crm_world["member"]

    async def scenario():
        async with TestClient(TestServer(shared_server.create_app(sessions=Auth()))) as client:
            response = await client.get("/api/crm/context")
            assert response.status == 200
            body = await response.json()
            assert body["role"] == "employee"
            assert set(body["capabilities"]) == set(crm_world["member"].capabilities)
            assert body["workspace"]["defaultModule"] == "portfolio"
            assert ids(body["workspace"]) == ids(workspace.manifest(crm_world["member"]))
            response = await client.get("/api/crm/context?module=not-a-module")
            assert response.status == 404
            assert (await response.json())["error"] == "unknown_module"
            response = await client.get("/api/crm/context?module=registry")
            assert response.status == 200

    asyncio.run(scenario())


def test_local_pilot_context_uses_the_same_workspace_contract(crm_world):
    body = pilot_server.context(crm_world["member"])
    assert body["schema_version"] == 1
    assert body["role"] == "employee" and body["synthetic"] is True
    assert body["workspace"] == workspace.manifest(crm_world["member"])
    with pytest.raises(CRMError) as error:
        pilot_server.context(crm_world["member"], "not-a-module")
    assert error.value.status == 404 and error.value.code == "unknown_module"
