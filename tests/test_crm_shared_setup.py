import copy
import pytest
import db
from crm import schema, shared_setup, shared_auth, storage
from crm.contract import CRMError, now


@pytest.fixture
def roster(tmp_path, monkeypatch):
    monkeypatch.delenv('HARD_CRM_SHARED', raising=False)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'crm-roster.db')
    db.init_db()
    with db.connect() as conn:
        schema.initialize(conn)
    return dict(namespace=shared_auth.NAMESPACE, captured_at=now(), source_sha256='a'*64,
                managers=[dict(actor_uid=90100000001, account_id=11, name='Same manager name', role='manager', status='active'),
                          dict(actor_uid=90100000002, account_id=12, name='Same manager name', role='full', status='active')])


def test_independent_roster_preserves_distinct_accounts_without_fake_activity(roster):
    receipt = shared_setup.provision(roster)
    assert shared_setup.provision(roster) == receipt
    first = shared_auth.principal({'uid':90100000001})
    second = shared_auth.principal({'uid':90100000002})
    assert first.employee_uuid != second.employee_uuid
    assert first.all_contracts and not first.owner
    assert first.capabilities == shared_auth.CAPABILITIES
    assert first.employee_scope == second.employee_scope == frozenset([first.employee_uuid,second.employee_uuid])
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_cohort_members').fetchone()[0] == 2
        for table in ['crm_events','crm_planned_shifts','crm_shifts','crm_telemetry']:
            assert conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
    with pytest.raises(CRMError): shared_auth.principal({'uid':90100000003})


@pytest.mark.parametrize('change', ['status','duplicate_account','duplicate_uid','observer','stale','wrong_namespace'])
def test_invalid_roster_is_atomic(roster, change):
    if change == 'status': roster['managers'][1]['status']='disabled'
    elif change == 'observer': roster['managers'][1]['role']='security'
    elif change == 'duplicate_account': roster['managers'][1]['account_id']=11
    elif change == 'duplicate_uid': roster['managers'][1]['actor_uid']=90100000001
    elif change == 'stale': roster['captured_at']='2020-01-01T00:00:00Z'
    else: roster['namespace']='production'
    with pytest.raises(CRMError): shared_setup.provision(roster)
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_employees').fetchone()[0] == 0


def test_changed_roster_does_not_reassign_existing_identity(roster):
    shared_setup.provision(roster)
    changed = copy.deepcopy(roster)
    changed['managers'][0]['actor_uid']=90100000003
    with pytest.raises(CRMError) as exc: shared_setup.provision(changed)
    assert exc.value.code == 'roster_changed'
    assert shared_auth.principal({'uid':90100000001}).all_contracts
    with pytest.raises(CRMError): shared_auth.principal({'uid':90100000003})
