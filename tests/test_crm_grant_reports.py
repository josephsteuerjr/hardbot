import base64
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
import uuid

from python_calamine import CalamineWorkbook
import pytest

from crm import grants, logistics, manager_reports, report_presets, shared_auth, storage, three_hour
from crm.contract import CRMError, canonical
from crm.roles import GRANT_ROLE_CAPABILITIES
from test_crm_assignments import allocation_world
from test_crm_core import crm_world
from test_crm_logistics import create_staff, setup as logistics_setup, source as logistics_source


def namespace():
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?) ON CONFLICT(key) DO NOTHING",
                     (shared_auth.NAMESPACE,))


def principal(uid, entry, grant_id):
    return shared_auth.principal(dict(uid=uid, role=entry), grant_id=grant_id)


def manager_grant(world, *, capabilities=None, employees=None, role='manager', territory='employees'):
    manager = world['managers'][0]
    with storage.connect() as conn:
        grant = grants.upsert(
            conn, manager.actor_uid, role=role, territory=territory,
            capabilities=capabilities or {'analytics'},
            employees=set(employees) if employees is not None else (
                {world['employee']} if territory == 'employees' else set()),
            all_contracts=territory == 'all', read_all_contracts=territory == 'all', is_default=True,
        )
        grants.set_model(conn, manager.actor_uid, 2)
    return manager, grant


def test_report_does_not_mix_manager_role_with_another_grant_capability(crm_world):
    world = allocation_world(crm_world)
    namespace()
    legacy, manager_record = manager_grant(world, capabilities={'assign'})
    with storage.connect() as conn:
        analyst_grant = grants.upsert(
            conn, legacy.actor_uid, role='analyst', territory='employees',
            capabilities={'analytics'}, employees={world['employee']},
        )
    manager = principal(legacy.actor_uid, 'manager', manager_record.grant_id)
    analyst = principal(legacy.actor_uid, 'analyst', analyst_grant.grant_id)

    assert manager.capabilities == frozenset({'assign'})
    with pytest.raises(CRMError) as error:
        manager_reports.require(manager)
    assert error.value.status == 403
    manager_reports.require(analyst)
    manager_reports.require(world['managers'][0])
    with storage.connect() as conn:
        manager_reports.remember_authority(conn, manager, 'assignment:synthetic', 'assignment')
        raw = conn.execute("SELECT authority_json FROM crm_background_authorities WHERE work_id='assignment:synthetic'").fetchone()[0]
    with storage.connect(readonly=True) as conn:
        background = manager_reports.current_authority(conn, raw)
    assert background.capabilities == frozenset({'assign'})
    assert manager_reports.report_work_id(manager) != manager_reports.report_work_id(analyst)
    assert manager_reports.report_work_id(world['managers'][0]) == 'report:' + str(manager.actor_uid)

    foreign = grants.Grant('foreign-grant', 999999, 'analyst', 'all', frozenset({'analytics'}), 1,
                           all_contracts=True, read_all_contracts=True)
    forged = replace(manager, role='analyst', capabilities=frozenset({'analytics'}),
                     grant_id=foreign.grant_id, grants=(foreign,))
    with pytest.raises(CRMError) as error:
        manager_reports.require(forged)
    assert error.value.code == 'grant_revoked'


def test_country_snapshot_excel_and_background_keep_selected_grant(crm_world, monkeypatch):
    world = allocation_world(crm_world)
    namespace()
    manager, grant = manager_grant(world)
    with storage.connect() as conn:
        conn.execute("UPDATE crm_memberships SET timezone='Asia/Dushanbe' WHERE employee_uuid=?",
                     (manager.employee_uuid,))
    current = principal(manager.actor_uid, 'manager', grant.grant_id)

    def countries(conn, value):
        available = {'tj': frozenset({world['employee']}), 'uz': frozenset({world['target']})}
        return {code: members for code, members in available.items() if members <= value.employee_scope}

    monkeypatch.setattr(three_hour, 'country_scopes', countries)
    monkeypatch.setattr(manager_reports, 'now', lambda: '2026-11-24T02:04:00.000+00:00')
    monkeypatch.setattr(three_hour, 'now', lambda: '2026-11-24T02:04:00.000+00:00')

    result = manager_reports.read(current, country='tj')
    assert result['report']['timezone'] == 'Asia/Dushanbe'
    assert result['report']['country'] == 'tj'
    assert result['snapshots'][0]['slot'] == '2026-11-24T06:00:00+05:00'
    assert {row['employee_uuid'] for sheet in result['report']['sheets'] for row in sheet['rows']} == {world['employee']}
    snapshot_id = result['snapshots'][0]['snapshot_id']
    assert manager_reports.read(current, snapshot_id=snapshot_id, country='tj')['report'] == json.loads(json.dumps(result['report']))
    with pytest.raises(CRMError) as error:
        manager_reports.read(current, country='uz')
    assert error.value.status == 403

    downloaded = report_presets.download(current, preset='three-hour', country='tj')
    payload = base64.b64decode(downloaded['content_base64'])
    assert sha256(payload).hexdigest() == downloaded['sha256']
    assert downloaded['timezone'] == 'Asia/Dushanbe'
    assert downloaded['cuts'][0]['slot'] == '2026-11-24T06:00:00+05:00'
    with CalamineWorkbook.from_filelike(BytesIO(payload)) as book:
        assert book.sheet_names[:2] == ['Таджикистан · звонки', 'Таджикистан · SMS']

    with storage.connect(readonly=True) as conn:
        authority = json.loads(conn.execute(
            "SELECT authority_json FROM crm_background_authorities WHERE work_id=?",
            (manager_reports.report_work_id(current),)).fetchone()[0])
    assert authority['grant_id'] == grant.grant_id
    assert authority['access_model'] == 2
    assert authority['policy_revision'] == current.policy_revision
    assert authority['grants'] == []


def test_scope_revision_timezone_and_background_refresh_use_the_same_grant(crm_world):
    world = allocation_world(crm_world)
    namespace()
    manager, grant = manager_grant(world)
    current = principal(manager.actor_uid, 'manager', grant.grant_id)
    first_key = manager_reports.scope_key(current)
    manager_reports.capture(current)
    with storage.connect() as conn:
        raw = conn.execute("SELECT authority_json FROM crm_background_authorities WHERE work_id=?",
                           (manager_reports.report_work_id(current),)).fetchone()[0]
        updated = grants.upsert(
            conn, current.actor_uid, grant_id=grant.grant_id, role='manager', territory='employees',
            capabilities={'analytics'}, employees={world['target']}, expected_revision=current.policy_revision,
        )
    refreshed = principal(manager.actor_uid, 'manager', grant.grant_id)
    with storage.connect(readonly=True) as conn:
        background = manager_reports.current_authority(conn, raw)
    assert refreshed.employee_scope == background.employee_scope == frozenset({world['target']})
    assert background.policy_revision == refreshed.policy_revision == updated.revision
    assert manager_reports.scope_key(refreshed) != first_key
    assert manager_reports.scope_key(replace(refreshed, timezone='Asia/Tashkent')) != manager_reports.scope_key(refreshed)

    legacy = world['managers'][0]
    expected = [legacy.employee_uuid, legacy.team_id, legacy.role,
                sorted(legacy.employee_scope), sorted(legacy.capabilities)]
    assert manager_reports.scope_key(legacy) == sha256(canonical(expected).encode()).hexdigest()


def test_background_scheduler_blocks_a_revoked_grant(crm_world):
    world = allocation_world(crm_world)
    namespace()
    manager, grant = manager_grant(world)
    current = principal(manager.actor_uid, 'manager', grant.grant_id)
    manager_reports.capture(current)
    with storage.connect() as conn:
        raw = conn.execute("SELECT authority_json FROM crm_background_authorities WHERE work_id=?",
                           (manager_reports.report_work_id(current),)).fetchone()[0]
        conn.execute("UPDATE crm_background_authorities SET updated_at='2000-01-01T00:00:00+00:00' WHERE work_id=?",
                     (manager_reports.report_work_id(current),))
        grants.revoke(conn, current.actor_uid, grant.grant_id, expected_revision=current.policy_revision)
        before = conn.execute('SELECT COUNT(*) FROM crm_report_snapshots').fetchone()[0]
    with pytest.raises(CRMError) as error:
        with storage.connect(readonly=True) as conn:
            manager_reports.current_authority(conn, raw)
    assert error.value.code == 'grant_revoked'
    assert manager_reports.tick() == {'completed': 0, 'blocked': 1}
    with storage.connect(readonly=True) as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_report_snapshots').fetchone()[0] == before


def test_preset_scheduler_uses_default_grant_and_its_current_revision(crm_world, monkeypatch):
    world = allocation_world(crm_world)
    namespace()
    manager, grant = manager_grant(world)
    monkeypatch.setattr(report_presets, 'active_managers', lambda: {manager.actor_uid: 'manager'})
    monkeypatch.setattr(three_hour, 'country_scopes', lambda conn, value: {'tj': frozenset({world['employee']})})
    monkeypatch.setattr(report_presets.assignments, 'team_results_roster', lambda conn, value: {
        world['employee']: {'name': 'Employee', 'country': 'tj', 'offices': [], 'group': None},
    })
    with storage.connect() as conn:
        conn.execute("UPDATE crm_meta SET value='cutover' WHERE key='distribution_mode'")

    assert report_presets.schedule() == {'captured': 1, 'archived': 1, 'denied': 0}
    assert report_presets.schedule() == {'captured': 0, 'archived': 0, 'denied': 0}
    current = principal(manager.actor_uid, 'manager', grant.grant_id)
    with storage.connect() as conn:
        updated = grants.upsert(
            conn, manager.actor_uid, grant_id=grant.grant_id, role='manager', territory='employees',
            capabilities={'analytics', 'note'}, employees={world['employee']}, is_default=True,
            expected_revision=current.policy_revision,
        )
    assert updated.revision > current.policy_revision
    assert report_presets.schedule() == {'captured': 1, 'archived': 1, 'denied': 0}
    with storage.connect() as conn:
        grants.revoke(conn, manager.actor_uid, grant.grant_id, expected_revision=updated.revision)
    assert report_presets.schedule() == {'captured': 0, 'archived': 0, 'denied': 1}


def test_owner_grant_runs_the_existing_report(crm_world, monkeypatch):
    world = allocation_world(crm_world)
    namespace()
    manager = world['managers'][0]
    with storage.connect() as conn:
        owner_grant = grants.upsert(
            conn, manager.actor_uid, role='owner', territory='all',
            capabilities=GRANT_ROLE_CAPABILITIES['owner'], all_contracts=True,
            read_all_contracts=True, is_default=True,
        )
        grants.set_model(conn, manager.actor_uid, 2)
    owner = principal(manager.actor_uid, 'manager', owner_grant.grant_id)
    monkeypatch.setattr(three_hour, 'now', lambda: '2026-11-24T02:04:00.000+00:00')
    manager_reports.require(owner)
    report = three_hour.report(owner)
    assert owner.owner and owner.role == 'owner' and owner.grant_id == owner_grant.grant_id
    assert report['timezone'] == owner.timezone
    assert {row['employee_uuid'] for sheet in report['sheets'] for row in sheet['rows']} <= owner.employee_scope


def test_equivalent_default_grant_preserves_report_values(crm_world, monkeypatch):
    world = allocation_world(crm_world)
    namespace()
    legacy = world['managers'][0]
    manager, grant = manager_grant(
        world, capabilities=legacy.capabilities,
        employees=legacy.employee_scope,
    )
    current = principal(manager.actor_uid, 'manager', grant.grant_id)
    monkeypatch.setattr(three_hour, 'now', lambda: '2026-11-24T02:04:00.000+00:00')
    assert three_hour.report(current) == three_hour.report(legacy)


def test_logistics_uses_role_capability_and_territory_from_one_grant(crm_world):
    logist, _, _ = logistics_setup(crm_world)
    with storage.connect() as conn:
        manager_employee = create_staff(conn, 'Grant manager', 306)
        conn.execute("UPDATE crm_memberships SET role='manager',capabilities_json='[\"analytics\"]' WHERE employee_uuid=?",
                     (manager_employee,))
        analytics = grants.upsert(
            conn, 306, role='manager', territory='self', capabilities={'analytics'}, is_default=True,
        )
        pools = grants.upsert(
            conn, 306, role='manager', territory='employees', capabilities={'logistics'},
            employees={logist.employee_uuid},
        )
        grants.set_model(conn, 306, 2)
    with storage.connect(readonly=True) as conn:
        assert logistics.countries(conn, logist) == ['UZ']
        assert logistics.countries(conn, principal(306, 'manager', analytics.grant_id)) == []
        pool_manager = principal(306, 'manager', pools.grant_id)
        assert pool_manager.employee_scope == frozenset({logist.employee_uuid})
        assert logistics.countries(conn, pool_manager) == ['UZ']
    assert logistics.read(pool_manager, country='UZ')['total'] == 0
    with pytest.raises(CRMError) as error:
        logistics.read(pool_manager, country='TJ')
    assert error.value.status == 403


def test_owner_grant_reads_all_logistics_countries_and_revocation_blocks_replay(crm_world):
    logistics_setup(crm_world)
    with storage.connect() as conn:
        owner_employee = create_staff(conn, 'Owner grant', 306)
        conn.execute("UPDATE crm_memberships SET role='manager',capabilities_json='[\"analytics\"]' WHERE employee_uuid=?",
                     (owner_employee,))
        owner_grant = grants.upsert(
            conn, 306, role='owner', territory='all', capabilities=GRANT_ROLE_CAPABILITIES['owner'],
            all_contracts=True, read_all_contracts=True, is_default=True,
        )
        grants.set_model(conn, 306, 2)
    owner = principal(306, 'manager', owner_grant.grant_id)
    with storage.connect(readonly=True) as conn:
        assert logistics.countries(conn, owner) == ['TJ', 'UZ']
    body = logistics_source(country='TJ', partner='creditor-1')
    plan = logistics.preview(owner, body)
    command = dict(source=body, proof=plan['proof'], operation_id=str(uuid.uuid4()))
    assert logistics.apply(owner, command)['imported'] == 1
    assert logistics.apply(owner, command)['imported'] == 1
    with storage.connect() as conn:
        grants.revoke(conn, owner.actor_uid, owner_grant.grant_id, expected_revision=owner.policy_revision)
    with pytest.raises(CRMError) as error:
        logistics.apply(owner, command)
    assert error.value.code == 'grant_revoked'
