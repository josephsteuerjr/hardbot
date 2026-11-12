"""Real PostgreSQL invariants and concurrent writers, in disposable QA schemas."""
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import os
from pathlib import Path
import re
import threading
import uuid
from urllib.parse import quote

import pytest
import db
from crm import schema, storage, store
from crm.contract import CRMError
import test_crm_core as core
import test_crm_exchange as exchange_cases
import test_crm_adoption as adoption_cases
import test_crm_rebuild as rebuild_cases
import test_crm_review_20261110 as review_cases
import test_crm_common_access as common_cases
import test_crm_staff_bridge as staff_cases
import test_crm_client_tasks as task_cases
import test_crm_delivery as delivery_cases
import test_crm_contract_list as lookup_cases
import test_crm_cabinet as cabinet_cases
import test_crm_shift_report as shift_report_cases
import test_crm_three_hour as three_hour_cases

pytestmark = pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_DSN_FILE'),
                               reason='Requires isolated PostgreSQL QA database')


@pytest.fixture
def pg_world(tmp_path, monkeypatch):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    base = Path(os.environ['HARD_CRM_TEST_DSN_FILE']).read_text().strip()
    info = conninfo_to_dict(base)
    assert info['dbname'] == 'crm_qa' and info['user'] == 'crm_qa'
    monkeypatch.delenv('HARD_CRM_SHARED', raising=False)
    world = core.crm_world.__wrapped__(tmp_path, monkeypatch)
    with db.connect() as local:
        names = [r[0] for r in local.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'crm_%' ORDER BY rowid")]
        snapshot = []
        for name in names:
            columns = [r[1] for r in local.execute('PRAGMA table_info(' + name + ')')]
            rows = [tuple(r) for r in local.execute('SELECT * FROM ' + name)]
            snapshot.append((name, columns, rows))
    namespace = 'crm_test_' + uuid.uuid4().hex
    assert re.fullmatch('crm_test_[a-f0-9]{32}', namespace)
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute('CREATE SCHEMA ' + namespace)
    scoped = base + ('&' if '?' in base else '?') + 'options=' + quote('-csearch_path=' + namespace)
    monkeypatch.setenv('HARD_CRM_SHARED', '1')
    monkeypatch.delenv('HARD_CRM_DATABASE_URL_FILE', raising=False)
    monkeypatch.setenv('HARD_CRM_DATABASE_URL', scoped)
    monkeypatch.setenv('HARD_CRM_DOCUMENTS', str(tmp_path / 'documents'))
    try:
        with storage.connect() as target:
            schema.initialize(target)
            for name, columns, rows in snapshot:
                if name == 'crm_meta' or not rows:
                    continue
                target.executemany('INSERT INTO ' + name + '(' + ','.join(columns)
                                   + ') VALUES(' + ','.join('?' for _ in columns) + ')', rows)
        # Existing business cases inspect db.connect() after invoking CRM. Their
        # fixture is already copied; assertions must inspect PostgreSQL too.
        monkeypatch.setattr(db, 'connect', storage.connect)
        yield world
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute('DROP SCHEMA ' + namespace + ' CASCADE')


CASES = [function for module in (core, exchange_cases, adoption_cases, rebuild_cases, review_cases, common_cases, task_cases, delivery_cases, lookup_cases,cabinet_cases,shift_report_cases,three_hour_cases)
         for name, function in vars(module).items()
         if name.startswith('test_') and callable(function)
         and set(inspect.signature(function).parameters) == {'crm_world'}
         # The SQLite-specific assertion expects sqlite3.IntegrityError. Its
         # actual PG counterpart below also verifies TRUNCATE is rejected.
         and name != 'test_history_cannot_be_mutated_or_deleted']


@pytest.mark.parametrize('case', CASES, ids=lambda case: case.__name__)
def test_business_invariants_on_postgres(pg_world, case):
    case(pg_world)


@pytest.mark.parametrize('role', ['employee', 'courier', 'manager', 'analyst', 'observer'])
def test_common_read_and_separate_write_on_postgres(pg_world, role):
    common_cases.test_all_roles_read_other_portfolios_without_assignment(pg_world, role)
    staff_cases.test_server_identity_and_roster_produce_common_read_and_role_limited_writes(pg_world, role)


@pytest.mark.parametrize('revocation', ['capability', 'assignment'])
def test_correction_authority_on_postgres(pg_world, revocation):
    review_cases.test_correction_requires_current_original_action_authority(pg_world, revocation)


def run_together(functions):
    barrier = threading.Barrier(len(functions))
    def run(fn):
        barrier.wait(timeout=10)
        return fn()
    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        return list(pool.map(run, functions))


def test_simultaneous_retries_return_one_exact_receipt(pg_world):
    w = pg_world
    body = core.call_body(w)
    responses = run_together([lambda: store.command(w['member'], body)] * 8)
    assert all(response == responses[0] for response in responses)
    with storage.connect() as conn:
        for table in ('crm_events', 'crm_commands', 'crm_outbox'):
            assert conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM crm_event_contracts').fetchone()[0] == 2


def test_two_writers_preserve_both_events_but_only_one_current_revision(pg_world):
    w = pg_world
    bodies = [core.call_body(w, values={'soft_comment': value}) for value in ['One', 'Two']]
    responses = run_together([lambda b=b: store.command(w['member'], b) for b in bodies])
    assert sorted(r['projection_status'] for r in responses) == ['applied', 'needs_review']
    card = store.card(w['member'], w['person'])
    assert len(card['history']) == 2
    assert all(c['revisions']['call'] == 1 for c in card['contracts'])
    assert len({c['fields']['soft_comment'] for c in card['contracts']}) == 1


def test_unrelated_contract_is_not_blocked_by_another_writer(pg_world):
    w = pg_world
    first, second = w['contracts']
    body = core.call_body(w)
    body['contract_ids'] = [second]
    body['base_revisions'] = {second: {'call': 0}}
    with storage.connect() as held:
        storage.begin(held, ['contract:' + first])
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(store.command, w['member'], body).result(timeout=5)
        assert result['projection_status'] == 'applied'


def test_reversed_multi_contract_order_does_not_deadlock(pg_world):
    w = pg_world
    a, b = core.call_body(w), core.call_body(w)
    b['contract_ids'] = list(reversed(b['contract_ids']))
    results = run_together([lambda: store.command(w['member'], a), lambda: store.command(w['member'], b)])
    assert len(results) == 2


def test_changed_payload_same_operation_never_commits_twice(pg_world):
    w = pg_world
    operation = str(uuid.uuid4())
    bodies = [core.call_body(w, op=operation, values={'soft_comment': value}) for value in ['A', 'B']]
    def write(body):
        try:
            return store.command(w['member'], body)['status']
        except CRMError as exc:
            return exc.code
    assert sorted(run_together([lambda b=b: write(b) for b in bodies])) == ['accepted', 'operation_conflict']


def test_history_rejects_update_delete_and_truncate(pg_world):
    import psycopg
    w = pg_world
    store.command(w['member'], core.call_body(w))
    for statement in ["UPDATE crm_events SET kind='note'", 'DELETE FROM crm_events', 'TRUNCATE crm_events CASCADE']:
        with pytest.raises(psycopg.Error):
            with storage.connect() as conn:
                conn.execute(statement)
    assert len(store.card(w['member'], w['person'])['history']) == 1


def test_failed_projection_rolls_back_event_outbox_and_ack(pg_world):
    import psycopg
    w = pg_world
    body = core.call_body(w)
    with storage.connect() as conn:
        conn.execute("""CREATE FUNCTION fail_projection() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'Synthetic write failure'; END; $$;
            CREATE TRIGGER fail_projection BEFORE UPDATE ON crm_contracts FOR EACH ROW EXECUTE FUNCTION fail_projection();""")
    with pytest.raises(psycopg.Error):
        store.command(w['member'], body)
    with storage.connect() as conn:
        for table in ('crm_events', 'crm_commands', 'crm_outbox'):
            assert conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
        conn.execute('DROP TRIGGER fail_projection ON crm_contracts')
    first = store.command(w['member'], body)
    assert store.command(w['member'], body) == first


def test_parameters_preserve_literal_question_percent_and_quotes():
    assert storage.parameters("SELECT '?' AS q, '%' AS p, 'it''s?' AS s WHERE x=?") == "SELECT '?' AS q, '%%' AS p, 'it''s?' AS s WHERE x=%s"


def test_postgres_portfolio_exact_totals_search_and_empty_scope(pg_world):
    from dataclasses import replace
    w = pg_world
    full = replace(w['member'], all_contracts=True)
    portfolio = store.portfolio(full)
    assert portfolio['total_contracts'] == 2 and portfolio['total_people'] == 1
    assert portfolio['people'][0]['currencies'] == {'RUB': '246.90'}
    assert store.portfolio(full, 'DUPLICATE')['total_contracts'] == 2
    assert store.portfolio(full, 'No matching person')['total_contracts'] == 0
    outsider = replace(w['member'], employee_scope=frozenset())
    assert store.portfolio(outsider)['total_contracts'] == 0
