"""Grant invariants on the existing disposable PostgreSQL QA fixture."""
import os

import pytest

from crm import grants, postgres_schema, schema, shared_auth, storage
from test_crm_postgres import pg_world


pytestmark = pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_DSN_FILE'),
                               reason='Requires isolated PostgreSQL QA database')


def test_actor_grants_backfill_opt_in_and_audit_on_postgres(pg_world):
    actor = dict(uid=pg_world['member'].actor_uid, device_id=1101,
                 name='Synthetic actor', role='employee')
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?) ON CONFLICT(key) DO NOTHING",
                     (shared_auth.NAMESPACE,))
    before = shared_auth.principal(actor)
    assert shared_auth.backfill(actor, activate=True)['equivalent']
    after = shared_auth.principal(actor)
    assert after.access_model == 2 and after.role == before.role
    assert after.capabilities == before.capabilities
    assert after.employee_scope == before.employee_scope
    assert after.all_contracts == before.all_contracts
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_grant_changes').fetchone()[0] == 2
        import psycopg
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE crm_grant_changes SET action='forged'")
        conn.rollback()


def test_postgres_schema_9_upgrade_is_additive_and_idempotent(pg_world):
    with storage.connect() as conn:
        conn.execute("UPDATE crm_meta SET value='9' WHERE key='schema_version'")
    with storage.connect() as conn:
        postgres_schema.initialize(conn)
    with storage.connect() as conn:
        assert conn.execute("SELECT value FROM crm_meta WHERE key='schema_version'").fetchone()[0] == str(schema.VERSION)
        assert conn.execute('SELECT COUNT(*) FROM crm_actor_access').fetchone()[0] == 0
        postgres_schema.initialize(conn)
