import json, uuid
import pytest
import db
from crm import store, scripts, analytics
from crm.contract import CRMError, digest
from test_crm_core import crm_world, call_body


def test_business_projections_rebuild_with_stable_entity_ids(crm_world):
    w = crm_world
    store.command(w["member"], call_body(w))
    cid = w["contracts"][0]
    task = store.command(
        w["member"],
        dict(
            schema_version=1,
            operation_id=str(uuid.uuid4()),
            kind="task",
            contract_ids=[cid],
            payload=dict(title="Synthetic task", due_at="2026-12-01T09:00:00+04:00"),
        ),
    )
    complete = dict(
        schema_version=1,
        operation_id=str(uuid.uuid4()),
        kind="task_complete",
        contract_ids=[cid],
        payload=dict(entity_id=task["entity_id"], entity_revision=1),
    )
    store.command(w["member"], complete)
    with pytest.raises(CRMError):
        store.command(w["member"], {**complete, "operation_id": str(uuid.uuid4())})
    promise = store.command(
        w["member"],
        dict(
            schema_version=1,
            operation_id=str(uuid.uuid4()),
            kind="promise",
            contract_ids=[cid],
            payload=dict(
                amount="0.00", currency="RUB", due_at="2026-12-01T09:00:00+04:00"
            ),
            base_revisions={cid: {"forecast": 0}},
        ),
    )
    before = store.card(w["owner"], w["person"])
    assert before["contracts"][0]["fields"]["forecast_sum"] == "0.00"
    store.rebuild(w["owner"])
    assert store.card(w["owner"], w["person"]) == before
    with pytest.raises(CRMError):
        store.rebuild(w["member"])


def test_script_draft_is_private_and_publish_is_one_canonical_fact(crm_world):
    w = crm_world
    content = dict(
        title="Synthetic",
        steps=[dict(title="Step", text="<script>Plain text only</script>")],
    )
    owner = w["owner"]
    saved = scripts.mutate(
        owner,
        dict(
            operation_id=str(uuid.uuid4()),
            action="save",
            base_revision=0,
            content=content,
        ),
    )
    assert (
        scripts.state(w["member"])["draft"] is None
        and scripts.state(w["member"])["script"] is None
    )
    body = dict(
        operation_id=str(uuid.uuid4()),
        action="publish",
        base_revision=saved["revision"],
    )
    published = scripts.mutate(owner, body)
    assert scripts.mutate(owner, body) == published
    assert (
        scripts.state(w["member"])["script"] == content
        and scripts.state(w["stranger"])["script"] is None
    )
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM crm_events WHERE kind='script_publish'"
            ).fetchone()[0]
            == 1
        )
    with pytest.raises(CRMError):
        scripts.mutate(w["member"], body)


def test_analytics_worker_failure_cannot_lose_committed_business(
    crm_world, monkeypatch
):
    w = crm_world
    body = call_body(w)
    result = store.command(w["member"], body)
    import sqlite3

    original = db.connect

    class Broken:
        def __enter__(self):
            self.conn = original()
            self.conn.__enter__()
            return self

        def __exit__(self, *args):
            return self.conn.__exit__(*args)

        def execute(self, sql, *args):
            if sql.startswith("INSERT INTO crm_metric_facts"):
                raise sqlite3.OperationalError("Synthetic projection unavailable")
            return self.conn.execute(sql, *args)

        def commit(self):
            self.conn.commit()

    monkeypatch.setattr(db, "connect", Broken)
    with pytest.raises(sqlite3.OperationalError):
        analytics.aggregate()
    monkeypatch.setattr(db, "connect", original)
    # A missing projection remains in the transactional outbox and is rebuilt.
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT state FROM crm_outbox WHERE event_id=?", (result["event_id"],)
            ).fetchone()[0]
            == "pending"
        )
    assert analytics.aggregate() == 1
    assert analytics.aggregate() == 0
    assert analytics.aggregate(rebuild=True) == 1
    assert store.command(w["member"], body) == result
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_metric_facts").fetchone()[0] == 1
