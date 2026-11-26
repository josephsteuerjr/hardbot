"""Grant invariants on the existing disposable PostgreSQL QA fixture."""
import os
from pathlib import Path

import pytest

from crm import grants, postgres_schema, schema, shared_auth, storage, store
from crm.contract import CRMError
from test_crm_core import call_body
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
        conn.execute('DROP TABLE IF EXISTS crm_grant_changes,crm_grant_employees,crm_grants,crm_actor_access CASCADE')
        conn.execute("UPDATE crm_meta SET value='9' WHERE key='schema_version'")
    with storage.connect() as conn:
        postgres_schema.initialize(conn)
    with storage.connect() as conn:
        assert conn.execute("SELECT value FROM crm_meta WHERE key='schema_version'").fetchone()[0] == str(schema.VERSION)
        assert conn.execute('SELECT COUNT(*) FROM crm_actor_access').fetchone()[0] == 0
        tables = {row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")}
        assert {'crm_actor_access','crm_grants','crm_grant_employees','crm_grant_changes'} <= tables
        postgres_schema.initialize(conn)



def test_schema_9_rollback_preserves_new_legacy_work_and_blocks_after_activation(pg_world):
    from tools.crm_runtime import rollback_schema_9
    from crm import runtime

    identity = dict(uid=pg_world['member'].actor_uid, device_id=1101, name='Synthetic', role='employee')
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?)", (shared_auth.NAMESPACE,))
    principal = shared_auth.principal(identity)
    operation = call_body(pg_world)
    receipt = store.command(principal, operation)
    runtime.remember(principal.actor_uid, 'synthetic-legacy-credential')
    preview = rollback_schema_9()
    assert preview['eligible'] and not preview['applied']
    with pytest.raises(ValueError, match='conditions changed'):
        rollback_schema_9('stale-proof')
    assert rollback_schema_9(preview['rollback_sha256'])['applied']
    with storage.connect() as conn:
        assert conn.execute("SELECT value FROM crm_meta WHERE key='schema_version'").fetchone()[0] == '9'
        assert conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0] == 1
        assert conn.execute('SELECT sealed FROM crm_background_credentials').fetchone()[0] == 'synthetic-legacy-credential'
    if old_source := os.environ.get('HARD_CRM_TEST_SCHEMA9_SOURCE'):
        _verify_previous_api(Path(old_source), identity, operation, receipt)
    with storage.connect() as conn:
        postgres_schema.initialize(conn)
    assert store.command(principal, operation) == receipt
    shared_auth.backfill(identity, activate=True)
    with storage.connect() as conn:
        grants.set_model(conn, principal.actor_uid, 1)
    blocked = rollback_schema_9()
    assert not blocked['eligible'] and not blocked['blockers']['grant_accounts']
    assert blocked['blockers']['grant_activations'] > 0
    with pytest.raises(ValueError, match='grant-based work'):
        rollback_schema_9(blocked['rollback_sha256'])


def _verify_previous_api(source, identity, operation, receipt):
    import json
    import subprocess
    import sys

    script = """
import asyncio, json, sys
from aiohttp.test_utils import TestClient, TestServer
from crm import schema, shared_auth, shared_server

identity, operation, receipt = json.load(sys.stdin)
assert schema.VERSION == 9

async def verify(token, **entry):
    return identity

async def run():
    sessions = shared_auth.Sessions(b'v' * 32, verify, audit=lambda *_: None)
    async with TestClient(TestServer(shared_server.create_app(sessions=sessions))) as client:
        response = await client.get('/health')
        assert response.status == 200 and (await response.json())['schema_version'] == 9
        response = await client.post('/auth/exchange', headers={'X-App-Token':'synthetic-schema-nine-client'})
        assert response.status == 200, await response.text()
        headers = {'Authorization':'Bearer ' + (await response.json())['token']}
        response = await client.post('/api/crm/command', headers=headers, json=operation)
        assert response.status == 200 and await response.json() == receipt

asyncio.run(run())
"""
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(map(str,(source,source/'server')))}
    result = subprocess.run([sys.executable,'-c',script],cwd=source,env=env,
                            input=json.dumps([identity,operation,receipt]),text=True,capture_output=True,timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_APP_DSN_FILE'), reason='Requires restricted QA role')
def test_migrated_schema_accepts_existing_login_under_app_permissions(pg_world, monkeypatch):
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    with storage.connect() as conn:
        namespace = conn.execute('SELECT current_schema()').fetchone()[0]
        # Match the schema-9 privileges before installing the grant tables.
        conn.execute('DROP TABLE crm_grant_changes,crm_grant_employees,crm_grants,crm_actor_access')
        conn.execute("UPDATE crm_meta SET value='9' WHERE key='schema_version'")
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?)", (shared_auth.NAMESPACE,))
        conn.execute('GRANT USAGE ON SCHEMA ' + namespace + ' TO crm_app')
        conn.execute('GRANT SELECT ON ' + ','.join((
            'crm_meta','crm_actor_links','crm_employees','crm_memberships','crm_authority_scopes')) + ' TO crm_app')
    with storage.connect() as conn:
        postgres_schema.initialize(conn)
        conn.execute((Path(__file__).parents[1] / 'tools/crm_runtime_permissions.sql').read_text())
    parts = urlsplit(Path(os.environ['HARD_CRM_TEST_APP_DSN_FILE']).read_text().strip())
    query = [(key,value) for key,value in parse_qsl(parts.query) if key != 'options']
    query.append(('options','-csearch_path=' + namespace))
    monkeypatch.setenv('HARD_CRM_DATABASE_URL', urlunsplit((parts.scheme,parts.netloc,'/crm_qa',urlencode(query),'')))
    principal = shared_auth.principal(dict(uid=pg_world['member'].actor_uid, device_id=1101, role='employee'))
    assert principal.access_model == 1 and principal.role == 'employee'
    with storage.connect() as conn:
        assert conn.execute('SELECT current_user').fetchone()[0] == 'crm_app'
        assert not conn.execute("SELECT has_table_privilege(current_user,'crm_actor_access','UPDATE')").fetchone()[0]


def test_grant_admin_concurrent_apply_allows_only_one_revision(pg_world):
    from concurrent.futures import ThreadPoolExecutor
    from crm import grant_admin
    from test_crm_grant_admin import apply_body, grant, preview
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('distribution_mode','isolated')")
        conn.commit()
    first = preview(pg_world, 0, [grant(role='employee', capabilities=('call',), is_default=True)])
    second = preview(pg_world, 0, [grant(role='analyst', capabilities=('analytics',), is_default=True)], number=2)
    owner = pg_world['owner']
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda body: _apply_catching(grant_admin, owner, body),
                                 (apply_body(first), apply_body(second))))
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    conflict = next(item for item in outcomes if isinstance(item, CRMError))
    assert conflict.status == 409 and conflict.code == 'policy_revision_conflict'


def _apply_catching(grant_admin, principal, body):
    try:
        return grant_admin.apply(principal, body)
    except CRMError as exc:
        return exc
