from uuid import uuid4

import pytest

from crm import access, assignments, storage, store, tasks, withdrawals, work_portfolio
from crm.contract import CRMError
from test_crm_core import call_body, crm_world


def test_confirmed_withdrawal_overrides_assignment_and_open_task(crm_world):
    world = crm_world
    cid, other = world["contracts"]
    employee = world["member"]
    owner = world["owner"]
    legacy_id = "test-registry-case"
    evidence = {
        "legacy_id": legacy_id, "creditor_id": "creditor-0", "day": "2026-11-19",
        "source": "verified status workbook", "sha256": "f" * 64, "withdrawn": True,
    }
    with storage.connect() as conn:
        conn.execute("UPDATE crm_contracts SET legacy_id=? WHERE contract_uuid=?", (legacy_id, cid))
    task = dict(schema_version=1, operation_id=str(uuid4()), kind="task", contract_ids=[cid],
                base_revisions={}, payload=dict(title="Очередь обзвона", work_kind="call",
                    assignee_uuid=world["stranger"].employee_uuid, due_at="2026-12-01T12:00:00Z"))
    store.command(owner, task)
    assert work_portfolio.read(world["stranger"])["total_contracts"] == 1
    before = assignments.changes(employee)
    with storage.connect() as conn:
        assert withdrawals.apply_confirmed(conn, [evidence])["new"] == 1
        assert withdrawals.blocked(conn) == set()
        withdrawals.apply_confirmed(conn, [evidence], apply=True)
        assert withdrawals.apply_confirmed(conn, [evidence], apply=True)["new"] == 0
        assert withdrawals.blocked(conn) == {cid}
        assert conn.execute("SELECT COUNT(*) FROM crm_effective_assignments WHERE contract_uuid=?", (cid,)).fetchone()[0] == 2
        assert cid not in access.readable_contracts(conn, employee)
        assert cid in access.readable_contracts(conn, owner)
        assert "call" not in access.writable_actions(conn, owner, cid)
    assert assignments.changes(employee, **before["cursor"])["resync"]
    assert {row["contract_uuid"] for row in work_portfolio.read(employee)["contracts"]} == {other}
    assert work_portfolio.read(world["stranger"])["total_contracts"] == 0
    assert tasks.overview(world["stranger"])["counts"]["open"] == 0
    with pytest.raises(CRMError, match="отозван"):
        store.command(employee, call_body(world))
    with pytest.raises(ValueError, match="do not match"):
        with storage.connect() as conn:
            withdrawals.apply_confirmed(conn, [dict(evidence, creditor_id="creditor-1")], apply=True)
    with storage.connect() as conn:
        withdrawals.apply_confirmed(conn, [dict(evidence, day="2026-11-20", withdrawn=False)], apply=True)
        assert withdrawals.blocked(conn) == set()
        assert conn.execute("SELECT COUNT(*) FROM crm_contact_status").fetchone()[0] == 2
    assert work_portfolio.read(employee)["total_contracts"] == 2
