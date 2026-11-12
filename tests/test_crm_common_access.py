"""Common client visibility never grants a business write or an export."""
from dataclasses import replace
import uuid

import pytest

from crm import access, documents, exchange, storage, store
from crm.contract import CRMError
from test_crm_core import crm_world, call_body


@pytest.mark.parametrize("role", ["employee", "courier", "manager", "analyst", "observer"])
def test_all_roles_read_other_portfolios_without_assignment(crm_world, role):
    w = crm_world
    principal = replace(w["stranger"], role=role, read_all_contracts=True)
    portfolio = store.portfolio(principal)
    assert portfolio["total_contracts"] == 2
    card = store.card(principal, w["person"])
    assert {row["contract_uuid"] for row in card["contracts"]} == set(w["contracts"])
    assert not principal.all_contracts
    with storage.connect() as conn:
        assert not access.scoped_contracts(conn, principal)


def test_common_visibility_does_not_grant_any_contract_write(crm_world):
    w = crm_world
    principal = replace(w["stranger"], read_all_contracts=True)
    with storage.connect() as conn:
        for capability in ("call", "visit", "contact", "note", "promise", "task",
                           "reported_payment", "document", "accounting"):
            with pytest.raises(CRMError) as exc:
                access.contracts(conn, principal, w["contracts"], capability=capability)
            assert exc.value.status == 403
    with pytest.raises(CRMError):
        store.command(principal, call_body(w))
    assert store.card(principal, w["person"])["history"] == []


def test_assignment_revocation_keeps_reading_but_removes_writing(crm_world):
    w = crm_world
    principal = replace(w["member"], read_all_contracts=True)
    store.command(principal, call_body(w))
    with storage.connect() as conn:
        conn.execute("UPDATE crm_assignments SET ends_at='2026-01-02T00:00:00+00:00'")
    assert len(store.card(principal, w["person"])["history"]) == 1
    with pytest.raises(CRMError):
        store.command(principal, call_body(w))


def test_common_reader_can_download_but_not_replace_another_clients_document(crm_world):
    w = crm_world
    doc = str(uuid.uuid4())
    documents.upload(w["member"], doc, w["contracts"][0], "test.pdf", "application/pdf", b"fixture")
    principal = replace(w["stranger"], read_all_contracts=True)
    row, path = documents.download(principal, doc)
    assert row["document_id"] == doc and path.read_bytes() == b"fixture"
    with pytest.raises(CRMError):
        documents.upload(principal, str(uuid.uuid4()), w["contracts"][0], "test.pdf", "application/pdf", b"changed")


def test_export_scope_does_not_inherit_common_read(crm_world):
    w = crm_world
    principal = replace(w["stranger"], read_all_contracts=True,
                        capabilities=frozenset({"exchange"}))
    with pytest.raises(CRMError) as exc:
        exchange.export(principal, {"operation_id": str(uuid.uuid4()), "period": "2026-11",
                                    "target_id": "test", "contract_ids": w["contracts"]})
    assert exc.value.status == 403


def test_from_uid_enables_common_read_only_for_server_selected_mode(crm_world):
    w = crm_world
    with storage.connect() as conn:
        old = access.from_uid(conn, w["stranger"].actor_uid)
        current = access.from_uid(conn, w["stranger"].actor_uid, common_read=True)
    assert not old.read_all_contracts
    assert current.read_all_contracts and not current.all_contracts
    assert current.capabilities == old.capabilities
