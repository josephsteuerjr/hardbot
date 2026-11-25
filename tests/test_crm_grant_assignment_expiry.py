import uuid

import pytest

from crm import assignments, grants, storage
from crm.contract import CRMError
from test_crm_assignments import allocation_world
from test_crm_core import crm_world


def _manager(crm_world, access_model):
    world = allocation_world(crm_world)
    legacy = world['managers'][0]
    if access_model == 1:
        return world, legacy
    with storage.connect() as conn:
        grant = grants.upsert(
            conn, legacy.actor_uid, role=legacy.role, territory='team',
            capabilities=legacy.capabilities,
        )
        grants.set_model(conn, legacy.actor_uid, grants.GRANTS_MODEL)
        manager = grants.resolve(conn, legacy, grant_id=grant.grant_id)
    assert manager.access_model == access_model
    return world, manager


def _preview(world, manager):
    return assignments.preview(manager, dict(
        schema_version=1, action='replace', employee_uuid=world['target'],
        work_kinds=['call'],
        selection=dict(kind='contracts', ids=world['contracts'], part='whole', filters={}),
        comment='expiry fixture',
    ))


def _command(preview):
    return dict(schema_version=1, operation_id=str(uuid.uuid4()), preview_id=preview['preview_id'])


def _expire(preview):
    with storage.connect() as conn:
        conn.execute(
            'UPDATE crm_assignment_previews SET expires_at=? WHERE preview_id=?',
            ('2000-01-01T00:00:00+00:00', preview['preview_id']),
        )


@pytest.mark.parametrize('access_model', [1, 2], ids=['legacy', 'grant'])
def test_expired_preview_cannot_start_assignment(crm_world, access_model):
    world, manager = _manager(crm_world, access_model)
    prepared = _preview(world, manager)
    _expire(prepared)

    with pytest.raises(CRMError) as denied:
        assignments.commit(manager, _command(prepared))

    assert denied.value.code == 'preview_expired'
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_assignment_changes').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM crm_assignment_operations').fetchone()[0] == 0


@pytest.mark.parametrize('access_model', [1, 2], ids=['legacy', 'grant'])
def test_consumed_replay_survives_preview_expiry(crm_world, access_model):
    world, manager = _manager(crm_world, access_model)
    prepared = _preview(world, manager)
    command = _command(prepared)
    result = assignments.commit(manager, command)
    _expire(prepared)

    assert assignments.commit(manager, command) == result
    assert assignments.receipt(manager, operation_id=command['operation_id']) == result
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_assignment_changes').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM crm_assignment_operations').fetchone()[0] == 1
