import json, sqlite3, uuid
import pytest
import db
from crm import migrate, store, documents, legacy
from crm.access import from_uid
from crm.contract import CRMError, canonical
from test_crm_core import crm_world, call_body


def test_migration_does_not_merge_names_or_rewrite_old_history(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    db.init_db()
    with db.connect() as conn:
        for cid in ["ONE", "TWO"]:
            conn.execute(
                "INSERT INTO clients(contract_id,client,partner,debt_total) VALUES(?,?,?,?)",
                (cid, "Same name", "Creditor", "1 234,50"),
            )
        conn.execute(
            "INSERT INTO calls(contract_id,agent_id,created_at,payload,op_id) VALUES(?,?,?,?,?)",
            ("ONE", 101, "2026-10-31", "Legacy original", "old-operation-123"),
        )
        conn.commit()
        before = [tuple(r) for r in conn.execute("SELECT * FROM clients")]
        calls = [tuple(r) for r in conn.execute("SELECT * FROM calls")]
    mapping = {"source": "Synthetic migration"}
    first = migrate.apply(mapping)
    assert migrate.apply(mapping) == first
    assert first["contracts"] == 2 and first["events"] == 1
    with db.connect() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM clients")] == before
        assert [tuple(r) for r in conn.execute("SELECT * FROM calls")] == calls
        assert conn.execute("SELECT COUNT(*) FROM crm_people").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM crm_metric_facts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM crm_outbox").fetchone()[0] == 0
        assert (
            json.loads(
                conn.execute(
                    "SELECT fields_json FROM crm_contracts LIMIT 1"
                ).fetchone()[0]
            )["debt_total"]
            == "1234.50"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE clients SET soft_comment='legacy writer'")
        legacy.release(conn)
        conn.execute("UPDATE clients SET soft_comment='rollback writer'")
        conn.commit()
    assert db.get_client("ONE")["soft_comment"] == "rollback writer"


def test_explicit_identity_and_legacy_projection_are_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    db.init_db()
    with db.connect() as conn:
        for cid in ["ONE", "TWO"]:
            conn.execute(
                "INSERT INTO clients(contract_id,client,partner) VALUES(?,?,?)",
                (cid, "Same name", "Creditor"),
            )
        conn.commit()
    mapping = dict(
        source="Fixture",
        contracts={
            cid: dict(
                creditor_id="C",
                person_ref="confirmed-person",
                evidence="Fixture master key",
            )
            for cid in ["ONE", "TWO"]
        },
        employees=[
            dict(
                staff_id="STAFF-1",
                name="Employee",
                team_id="team-A",
                role="employee",
                capabilities=["call"],
                timezone="Europe/Samara",
                starts_at="2026-01-01T00:00:00Z",
                actor_uids=[101],
                assignments=[
                    dict(legacy_id=cid, capability="call") for cid in ["ONE", "TWO"]
                ],
                evidence="Confirmed roster",
            )
        ],
    )
    migrate.apply(mapping)
    with db.connect() as conn:
        principal = from_uid(conn, 101)
        ids = [r[0] for r in conn.execute("SELECT contract_uuid FROM crm_contracts")]
    result = store.command(
        principal,
        dict(
            schema_version=1,
            operation_id=str(uuid.uuid4()),
            kind="call",
            contract_ids=ids,
            payload={"values": {"soft_comment": "Canonical result"}},
            base_revisions={cid: {"call": 0} for cid in ids},
        ),
    )
    assert result["projection_status"] == "applied"
    assert db.get_client("ONE")["soft_comment"] == "Canonical result"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_events").fetchone()[0] == 1


def test_documents_require_persisted_scoped_exact_content(crm_world):
    w = crm_world
    cid = w["contracts"][0]
    data = b"%PDF-1.4 synthetic fixture"
    document = documents.upload(
        w["member"], str(uuid.uuid4()), cid, "test.pdf", "application/pdf", data
    )
    body = dict(
        schema_version=1,
        operation_id=str(uuid.uuid4()),
        kind="document",
        contract_ids=[cid],
        payload=dict(
            document_id=document["document_id"],
            sha256=document["sha256"],
            filename="test.pdf",
        ),
    )
    assert store.command(w["member"], body)["status"] == "accepted"
    with pytest.raises(CRMError):
        documents.download(w["stranger"], document["document_id"])
    row, path = documents.download(w["member"], document["document_id"])
    assert path.read_bytes() == data
    path.write_bytes(b"corrupt")
    with pytest.raises(CRMError):
        documents.download(w["member"], document["document_id"])
