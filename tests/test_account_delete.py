"""Deletion is distinct from disable; old sessions cannot survive re-enrollment."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

import accounts
import db
import web


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'delete.db')
    monkeypatch.setattr(db, 'admin_bootstrap_marker', lambda: tmp_path / 'bootstrap')
    monkeypatch.setattr(web, 'DEV', False)
    db.init_db()
    assert accounts.create_account('Synthetic Delete', 'delete-user', 'initial123')['ok']
    with db.connect() as conn:
        return dict(conn.execute('SELECT * FROM accounts').fetchone())


def enter(fp, kind='phone'):
    return accounts.login('delete-user', 'initial123', device={'kind': kind, 'fp': fp, 'label': fp},
                          entrance='internal', gps={'lat': 1, 'lon': 2, 'accuracy': 3})['token']


def call(body, headers=None):
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as client:
            r = await client.post('/admin/account_action.json', json=body, headers=headers)
            return r.status, await r.json() if r.content_type == 'application/json' else {'error': await r.text()}
    return asyncio.run(run())


def test_actual_removal_revokes_all_devices_and_preserves_other_accounts(world):
    tokens = [enter('first'), enter('second'), enter('pc', 'desktop'), enter('pc', 'desktop')]
    assert accounts.create_account('Other Synthetic', 'other-user', 'initial123')['ok']
    other = accounts.login('other-user', 'initial123', device={'kind': 'desktop', 'fp': 'other'}, entrance='internal')['token']
    assert accounts.delete_account('delete-user', 'principal:test', expected_id=world['id'])['deleted']
    assert all(db.agent_device_check(token)[0] != 'ok' for token in tokens)
    assert db.agent_device_check(other)[0] == 'ok'
    assert accounts.login('delete-user', 'initial123', device={'fp': 'pc'}, entrance='internal')['action'] == 'bad'
    with db.connect() as conn:
        assert not conn.execute('SELECT 1 FROM accounts WHERE id=?', (world['id'],)).fetchone()
        assert not conn.execute('SELECT 1 FROM account_devices WHERE account_id=?', (world['id'],)).fetchone()
        assert not conn.execute('SELECT 1 FROM account_token_sessions s JOIN agent_devices t ON t.id=s.token_id WHERE t.account_id=?', (world['id'],)).fetchone()
        assert conn.execute("SELECT 1 FROM account_approval_events WHERE action='account_delete'").fetchone()
    assert accounts.legacy_uid_deleted(world['telegram_id'])


def test_recreating_username_cannot_restore_old_token_or_accept_stale_dialog(world):
    old = enter('same')
    assert accounts.delete_account('delete-user', 'principal:test', expected_id=world['id'])['ok']
    assert accounts.create_account('Synthetic Delete', 'delete-user', 'initial123')['ok']
    fresh = enter('same')
    assert db.agent_device_check(old)[0] != 'ok'
    assert db.agent_device_check(fresh)[0] == 'ok'
    assert not accounts.delete_account('delete-user', 'principal:test', expected_id=world['id'])['ok']
    assert db.agent_device_check(fresh)[0] == 'ok'


def test_deletion_rejects_ambiguous_uid_without_partial_revocation(world):
    token = enter('pc', 'desktop')
    assert accounts.create_account('Other Synthetic', 'other-user', 'initial123')['ok']
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET telegram_id=? WHERE username='other-user'", (world['telegram_id'],))
        conn.commit()
    assert not accounts.delete_account('delete-user', 'principal:test', expected_id=world['id'])['ok']
    assert db.agent_device_check(token)[0] == 'ok'


def test_root_owner_cannot_be_deleted_even_through_model(world):
    with db.connect() as conn:
        conn.execute("INSERT INTO account_admin_grants VALUES(?, 'root-owner', 'ssh-root', 'synthetic-operation', 'now', 1)", (world['id'],))
        conn.commit()
    assert not accounts.delete_account('delete-user', 'principal:test', expected_id=world['id'])['ok']


def test_http_deletion_requires_registered_admin_and_generation_confirmation(world, monkeypatch):
    monkeypatch.setenv('ADMIN_API_KEY', 'synthetic-master')
    body = dict(action='delete', username='delete-user', confirm_username='delete-user', account_id=world['id'])
    assert call(body)[0] in (401, 403)
    dev = db.enroll_admin_device('synthetic-admin', 'Synthetic Admin')
    headers = {'X-Device-Token': dev['token'], 'X-Device-Id': 'synthetic-admin'}
    assert call(body, headers)[0] == 403
    assert accounts.register_approver(dev['id'], 'synthetic-admin')['ok']
    assert call({**body, 'confirm_username': 'other-user'}, headers)[0] == 400
    assert call({**body, 'account_id': world['id'] + 1}, headers)[0] == 400
    assert call(body, headers)[1]['deleted']
    assert call(body, headers)[0] == 400


def test_transaction_rolls_back_if_deletion_fails(world, monkeypatch):
    token = enter('pc', 'desktop')
    with db.connect() as conn:
        conn.execute("CREATE TRIGGER refuse_delete BEFORE DELETE ON accounts BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        conn.commit()
    with pytest.raises(Exception, match='synthetic failure'):
        accounts.delete_account('delete-user', 'principal:test', expected_id=world['id'])
    assert db.agent_device_check(token)[0] == 'ok'
    assert not accounts.legacy_uid_deleted(world['telegram_id'])


def test_legacy_binding_cannot_give_recreated_account_its_deleted_queue_owner(world):
    # The real transition has a pre-existing binding; an unbound synthetic-only
    # test would allocate a new UID accidentally and miss the regression.
    uid=world['telegram_id']
    db.set_bind(uid,'Synthetic Delete')
    old_token=enter('same-pc','desktop')
    assert accounts.delete_account('delete-user','principal:test',expected_id=world['id'])['ok']
    assert accounts.create_account('Synthetic Delete','delete-user','initial123')['ok']
    with db.connect() as conn:
        fresh=conn.execute("SELECT id,telegram_id FROM accounts WHERE username='delete-user'").fetchone()
        assert fresh['telegram_id']!=uid
        assert conn.execute('SELECT employee FROM agent_bind WHERE telegram_id=?',(uid,)).fetchone()[0]=='Synthetic Delete'
    # Existing clients bind unsent work to server+UID and send expected_uid.
    # New enrollment must not transfer that old journal to a new account.
    login=accounts.login('delete-user','initial123',device={'kind':'desktop','fp':'same-pc','label':'Synthetic'},entrance='internal',expected_uid=uid)
    assert login['action']=='conflict' and not login.get('token')
    assert db.agent_device_check(old_token)[0]!='ok'


def test_explicit_retired_agent_uid_is_refused(world):
    assert accounts.delete_account('delete-user','principal:test',expected_id=world['id'])['ok']
    assert not accounts.create_account('Synthetic Delete','delete-user','initial123',telegram_id=world['telegram_id'])['ok']


def test_random_uid_allocator_reserves_unbound_deleted_generations(world, monkeypatch):
    uid=world['telegram_id'];assert uid>=90_000_000_000
    assert accounts.delete_account('delete-user','principal:test',expected_id=world['id'])['ok']
    candidates=iter([uid-90_000_000_000,999999 if uid!=90_000_999_999 else 999998])
    monkeypatch.setattr(accounts.secrets,'randbelow',lambda _:next(candidates))
    assert accounts.create_account('Synthetic Delete','delete-user','initial123')['ok']
    with db.connect() as conn:
        assert conn.execute("SELECT telegram_id FROM accounts WHERE username='delete-user'").fetchone()[0]!=uid


def test_recreated_profile_keeps_business_portfolio_without_old_identity(world):
    uid=world['telegram_id'];db.set_bind(uid,'Synthetic Delete')
    with db.connect() as conn:
        conn.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES('OWN','Synthetic Client','Synthetic Delete','Synthetic Delete')")
        conn.commit()
    db.log_visit('OWN',uid,'visit',op_id='synthetic-before-delete')
    assert accounts.delete_account('delete-user','principal:test',expected_id=world['id'])['ok']
    assert accounts.create_account('Synthetic Delete','delete-user','initial123')['ok']
    token=enter('new-pc','desktop')
    with db.connect() as conn:
        acc=conn.execute("SELECT * FROM accounts WHERE username='delete-user'").fetchone()
        dev=conn.execute('SELECT id FROM account_devices WHERE account_id=?',(acc['id'],)).fetchone()
        assert acc['telegram_id']!=uid
        assert conn.execute('SELECT count(*) FROM clients').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM visits').fetchone()[0]==1
    assert accounts.approve_account('delete-user','principal:test')['ok']
    assert accounts.approve_device(dev['id'],'principal:test')['ok']
    assert accounts.change_password('delete-user','initial123','changed123')['ok']
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as client:
            r=await client.get('/api/bootstrap',headers={'X-App-Token':token})
            assert r.status==200
            body=await r.json();assert body['uid']==acc['telegram_id'] and body['persons']
    asyncio.run(run())
