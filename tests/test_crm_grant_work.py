import json
import uuid

import pytest

from crm import assignments, grants, manager_reports, registry, storage, store, tasks, work_portfolio
from crm.contract import CRMError, identifier, now
from test_crm_assignments import allocation_world
from test_crm_core import crm_world, call_body
from test_crm_employee_registry import edit as registry_edit


def make_grant(conn, principal, *, territory, capabilities=None, employees=(), role=None):
    role = role or principal.role
    grant = grants.upsert(
        conn, principal.actor_uid, role=role, territory=territory,
        capabilities=principal.capabilities if capabilities is None else capabilities,
        employees=employees, all_contracts=territory == 'all',
        read_all_contracts=territory == 'all',
    )
    grants.set_model(conn, principal.actor_uid, grants.GRANTS_MODEL)
    return grants.resolve(conn, principal, role=role, grant_id=grant.grant_id,
                          allow_owner=principal.owner)


def one_contract_call(world, operation_id=None):
    body = call_body(world, op=operation_id)
    body['contract_ids'] = [world['contracts'][0]]
    body['base_revisions'] = {world['contracts'][0]: {'call': 0}}
    return body


def manager_grants(world):
    legacy = world['managers'][0]
    with storage.connect() as conn:
        team = make_grant(conn, legacy, territory='team')
        narrow = make_grant(conn, legacy, territory='employees', employees={world['employee']},
                            capabilities=legacy.capabilities - {'visit'})
        template = dict(conn.execute('SELECT * FROM crm_contracts WHERE contract_uuid=?',
                                     (world['contracts'][0],)).fetchone())
        foreign = identifier('contract', 'grant-team-b')
        columns = list(template)
        row = dict(template, contract_uuid=foreign, external_id='TEAM-B-ONLY')
        conn.execute('INSERT INTO crm_contracts(' + ','.join(columns) + ') VALUES('
                     + ','.join('?' for _ in columns) + ')', tuple(row[key] for key in columns))
        for work in ('call', 'visit'):
            conn.execute('INSERT INTO crm_distribution_managers VALUES(?,?,?)',
                         (legacy.employee_uuid, 'team-B', work))
            conn.execute('INSERT INTO crm_managed_contracts VALUES(?,?,?)',
                         ('team-B', foreign, work))
            conn.execute('INSERT INTO crm_assignments VALUES(?,?,?,?,?,NULL,?)',
                         (str(uuid.uuid4()), world['stranger'].employee_uuid, foreign, work,
                          '2026-01-01T00:00:00Z', 'grant-fixture'))
    return team, narrow, foreign


def assignment_body(world, target='default', works=None, ids=None):
    return dict(schema_version=1, action='replace',
                employee_uuid=world['target'] if target == 'default' else target,
                work_kinds=works or ['call'],
                selection=dict(kind='contracts', ids=ids or world['contracts'], part='whole', filters={}),
                comment='grant fixture')


def test_employee_reads_selected_territory_but_never_writes_as_another_person(crm_world):
    world = crm_world
    cid = world['contracts'][1]
    with storage.connect() as conn:
        conn.execute('UPDATE crm_assignments SET ends_at=? WHERE employee_uuid=? AND contract_uuid=?',
                     (now(), world['employee'], cid))
        for work in ('call', 'visit'):
            conn.execute('INSERT INTO crm_assignments VALUES(?,?,?,?,?,NULL,?)',
                         (str(uuid.uuid4()), world['stranger'].employee_uuid, cid, work,
                          '2026-01-01T00:00:00Z', 'grant-fixture'))
        principal = make_grant(conn, world['member'], territory='employees',
                               employees={world['stranger'].employee_uuid})

    result = work_portfolio.read(principal, employee=world['stranger'].employee_uuid)
    assert [row['contract_uuid'] for row in result['contracts']] == [cid]
    assert result['contracts'][0]['work_kinds'] == ['call', 'visit']
    with pytest.raises(CRMError):
        work_portfolio.read(principal)

    with pytest.raises(CRMError) as denied:
        store.command(principal, one_contract_call(world))
    assert denied.value.status == 403
    with pytest.raises(CRMError):
        registry.edit(principal, registry_edit(world))
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0] == 0

    rows = registry.read(principal)['rows']
    assert [row['contract_uuid'] for row in rows] == [cid]
    assert rows[0]['writable_fields'] == []
    card = store.card(principal, world['person'])
    assert [row['contract_uuid'] for row in card['contracts']] == [cid]
    assert card['contracts'][0]['allowed_actions'] == []

    with storage.connect() as conn:
        grants.upsert(conn, principal.actor_uid, grant_id=principal.grant_id, role='employee',
                      territory='employees', capabilities=principal.capabilities,
                      employees={world['stranger'].employee_uuid}, all_contracts=True,
                      read_all_contracts=True)
        broad_reader = grants.resolve(conn, world['member'], role='employee', grant_id=principal.grant_id)
    with pytest.raises(CRMError):
        store.command(broad_reader, one_contract_call(world))


def test_employee_cannot_use_other_persons_task_on_a_shared_contract(crm_world):
    world = crm_world
    cid = world['contracts'][0]
    with storage.connect() as conn:
        conn.execute("UPDATE crm_assignments SET ends_at=? WHERE employee_uuid=? "
                     "AND contract_uuid=? AND capability='visit'",
                     (now(), world['employee'], cid))
        principal = make_grant(conn, world['member'], territory='employees',
                               employees={world['employee'], world['stranger'].employee_uuid})
    store.command(world['owner'], dict(
        schema_version=1, operation_id=str(uuid.uuid4()), kind='task', contract_ids=[cid],
        base_revisions={}, payload=dict(title='Other person visit', due_at=now(), work_kind='visit',
                                       assignee_uuid=world['stranger'].employee_uuid)))

    own = work_portfolio.read(principal)
    own_cid = next(row for row in own['contracts'] if row['contract_uuid'] == cid)
    assert 'visit' not in own_cid['work_kinds']
    other = work_portfolio.read(principal, employee=world['stranger'].employee_uuid)
    assert other['contracts'][0]['work_kinds'] == ['visit']

    body = one_contract_call(world)
    body.update(kind='visit', payload={'values': {'visit_comment': 'Must not pass'}},
                base_revisions={cid: {'visit': 0}})
    with pytest.raises(CRMError):
        store.command(principal, body)
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM crm_events WHERE kind='visit'").fetchone()[0] == 0


def test_selected_grants_do_not_mix_capabilities_and_revocation_blocks_offline_replay(crm_world):
    world = crm_world
    with storage.connect() as conn:
        narrow = make_grant(conn, world['member'], territory='self', capabilities={'call'})
        wide = make_grant(conn, world['member'], territory='all', capabilities={'call', 'visit'})
    assert work_portfolio.read(narrow)['counts']['visit'] == 0
    assert work_portfolio.read(wide)['counts']['visit'] == 2

    accepted = store.command(narrow, one_contract_call(world))
    registry_command = registry_edit(world, revisions={'call': 1})
    edited = registry.edit(narrow, registry_command)
    with pytest.raises(CRMError) as denied:
        store.command(wide, one_contract_call(world, accepted['operation_id']))
    assert denied.value.code == 'grant_required'
    with pytest.raises(CRMError) as denied:
        registry.edit(wide, registry_command)
    assert denied.value.code == 'grant_required'
    visit = one_contract_call(world)
    visit.update(kind='visit', payload={'values': {'visit_comment': 'Wide grant'}},
                 base_revisions={world['contracts'][0]: {'visit': 0}})
    with pytest.raises(CRMError):
        store.command(narrow, visit)
    store.command(wide, visit)

    with storage.connect() as conn:
        grants.revoke(conn, narrow.actor_uid, narrow.grant_id)
        before = conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0]
        anchors = conn.execute('SELECT COUNT(*) FROM crm_clock_anchors').fetchone()[0]
    for operation in (
        lambda: store.command(narrow, one_contract_call(world, accepted['operation_id'])),
        lambda: registry.edit(narrow, registry_edit(world, revisions={'call': 1})),
        lambda: store.command(narrow, one_contract_call(world)),
        lambda: store.anchor(narrow, 'offline-installation'),
    ):
        with pytest.raises(CRMError) as denied:
            operation()
        assert denied.value.code == 'grant_revoked'
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0] == before
        assert conn.execute('SELECT COUNT(*) FROM crm_clock_anchors').fetchone()[0] == anchors
    assert edited['status'] == 'accepted'


def test_manager_distribution_team_tasks_preview_and_replay_share_one_grant(crm_world):
    world = allocation_world(crm_world)
    team, narrow, foreign = manager_grants(world)
    for principal in (team, narrow):
        portfolio = assignments.portfolio(principal)
        ids = {contract['contract_uuid'] for person in portfolio['people']
               for contract in person['contracts']}
        assert ids == set(world['contracts'])
        assert foreign not in ids
    with pytest.raises(CRMError):
        assignments.preview(team, assignment_body(world, ids=[foreign]))

    with storage.connect() as conn:
        staff = assignments.targets(conn, narrow)
        assert [row['employee_uuid'] for row in staff] == [world['employee']]
        assert staff[0]['work_kinds'] == ['call']
        assert [row['employee_uuid'] for row in tasks.staff(conn, narrow)] == [world['employee']]
    with pytest.raises(CRMError):
        assignments.preview(narrow, assignment_body(world))
    with pytest.raises(CRMError):
        assignments.preview(narrow, assignment_body(world, target=world['employee'], works=['visit']))

    for assignee, title in ((world['employee'], 'In territory'), (world['target'], 'Outside territory')):
        store.command(world['owner'], dict(
            schema_version=1, operation_id=str(uuid.uuid4()), kind='task',
            contract_ids=[world['contracts'][0]], base_revisions={},
            payload=dict(title=title, due_at=now(), work_kind='call', assignee_uuid=assignee)))
    overview = tasks.overview(narrow, status='all')
    assert overview['total'] == 1 and overview['tasks'][0]['title'] == 'In territory'
    with pytest.raises(CRMError):
        tasks.overview(narrow, employee=world['target'])
    with pytest.raises(CRMError):
        work_portfolio.read(narrow, employee=world['target'])

    prepared = assignments.preview(team, assignment_body(world))
    command = dict(schema_version=1, operation_id=str(uuid.uuid4()), preview_id=prepared['preview_id'])
    with pytest.raises(CRMError) as denied:
        assignments.commit(narrow, command)
    assert denied.value.code == 'grant_required'
    result = assignments.commit(team, command)
    assert assignments.commit(team, command) == result
    with pytest.raises(CRMError) as denied:
        assignments.commit(narrow, command)
    assert denied.value.code == 'grant_required'
    with pytest.raises(CRMError) as denied:
        assignments.receipt(narrow, operation_id=command['operation_id'])
    assert denied.value.code == 'grant_required'
    with storage.connect() as conn:
        authors = {row[0] for row in conn.execute('SELECT actor_uid FROM crm_assignment_changes')}
        assert authors == {team.actor_uid}


def test_background_pack_rechecks_revoked_grant_and_cannot_switch_grant(crm_world, monkeypatch):
    world = allocation_world(crm_world)
    team, narrow, _ = manager_grants(world)
    monkeypatch.setattr(assignments, 'MAX_SLOTS', 1)
    prepared = assignments.preview(team, assignment_body(world))
    command = dict(schema_version=1, operation_id=str(uuid.uuid4()), preview_id=prepared['preview_id'])
    result = assignments.commit(team, command)
    assert result['status'] == 'job_started'
    with storage.connect() as conn:
        authority = json.loads(conn.execute(
            "SELECT authority_json FROM crm_background_authorities WHERE work_id=?",
            ('assignment:' + result['job_id'],)).fetchone()[0])
        assert authority['grant_id'] == team.grant_id and authority['grants'] == []

    step = dict(schema_version=1, job_id=result['job_id'], pack_index=0)
    with pytest.raises(CRMError) as denied:
        assignments.job_step(narrow, step)
    assert denied.value.code == 'grant_required'
    assert manager_reports.tick({team.actor_uid: team}) == dict(completed=1, blocked=0)
    assert assignments.job(team, job_id=result['job_id'])['applied_slots'] == 1

    with storage.connect() as conn:
        grants.revoke(conn, team.actor_uid, team.grant_id)
    with pytest.raises(CRMError) as denied:
        assignments.job_step(team, dict(step, pack_index=1))
    assert denied.value.code == 'grant_revoked'
    assert manager_reports.tick({team.actor_uid: team}) == dict(completed=0, blocked=1)
    with pytest.raises(CRMError) as denied:
        assignments.commit(team, command)
    assert denied.value.code == 'grant_revoked'
    assert assignments.job(team, job_id=result['job_id'])['applied_slots'] == 1
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_assignment_changes').fetchone()[0] == 1



def test_replay_requires_exact_grant_even_for_legacy_unbound_receipt(crm_world):
    world = crm_world
    with storage.connect() as conn:
        narrow = make_grant(conn, world['member'], territory='self', capabilities={'call'})
        wide = make_grant(conn, world['member'], territory='all', capabilities={'call', 'visit'})
        operation = str(uuid.uuid4())
        body = {'operation_id': operation, 'synthetic': 'replay-binding'}
        store.acknowledge(conn, narrow, operation, body, {'ok': True}, now())
        assert store.replay(conn, narrow, operation, body)['grant_id'] == narrow.grant_id
        with pytest.raises(CRMError) as denied:
            store.replay(conn, wide, operation, body)
        assert denied.value.code == 'grant_required'
        conn.execute("UPDATE crm_commands SET result_json='{\"ok\":true}' WHERE operation_id=?", (operation,))
        with pytest.raises(CRMError) as denied:
            store.replay(conn, narrow, operation, body)
        assert denied.value.code == 'grant_required'
