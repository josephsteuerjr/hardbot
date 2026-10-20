"""Logical approval revocation survives password reset and credential cleanup."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

import accounts
import db
import web


@pytest.fixture(params=['agent', 'manager'])
def world(tmp_path, monkeypatch, request):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'revoke.db')
    monkeypatch.setattr(db, 'admin_bootstrap_marker', lambda: tmp_path / 'bootstrap')
    monkeypatch.setattr(web, 'DEV', False)
    monkeypatch.delenv('ACCOUNT_CUTOVER', raising=False)
    db.init_db()
    assert accounts.create_account('Synthetic Revocation', 'revoke-user', 'initial123',
                                   role=request.param)['ok']
    assert accounts.approve_account('revoke-user', 'principal:test')['ok']
    assert accounts.change_password('revoke-user', 'initial123', 'changed123')['ok']
    return 'manager_devices' if request.param == 'manager' else 'agent_devices'


def enter(fp='synthetic-phone', kind='phone', password='changed123', username='revoke-user'):
    result = accounts.login(username, password, device={'kind': kind, 'fp': fp, 'label': fp},
                            entrance='internal', gps={'lat': 1, 'lon': 2, 'accuracy': 3})
    assert result.get('token'), result
    return result


def device(fp='synthetic-phone'):
    with db.connect() as conn:
        return dict(conn.execute('SELECT * FROM account_devices WHERE fingerprint=? '
                                 'ORDER BY id DESC LIMIT 1', (fp,)).fetchone())


def check(table, token):
    return (db.manager_device_check if table == 'manager_devices' else db.agent_device_check)(token)


def call(body, headers=None):
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as client:
            response = await client.post('/admin/account_action.json', json=body, headers=headers)
            payload = (await response.json() if response.content_type == 'application/json'
                       else {'error': await response.text()})
            return response.status, payload
    return asyncio.run(run())


def test_reset_then_token_cleanup_does_not_make_approval_irrevocable(world, monkeypatch):
    old = enter()
    phone = device()
    assert accounts.approve_device(phone['id'], 'principal:test')['active']
    assert accounts.set_password('revoke-user', 'reset1234', by='principal:test')['ok']
    assert check(world, old['token'])[0] == 'revoked'
    with db.connect() as conn:
        assert not conn.execute(f'SELECT 1 FROM {world} WHERE id=?',
                                (phone['agent_dev_id'],)).fetchone()
        assert conn.execute('SELECT status FROM account_devices WHERE id=?',
                            (phone['id'],)).fetchone()[0] == 'active'
        protected = tuple(conn.execute('SELECT * FROM accounts WHERE username=?',
                                       ('revoke-user',)).fetchone())
    body = {'action': 'revoke', 'device_id': phone['id']}
    assert call(body)[0] in (401, 403)
    monkeypatch.setenv('ADMIN_API_KEY', 'synthetic-master')
    operator = db.enroll_admin_device('synthetic-operator', 'Synthetic Operator')
    headers = {'X-Device-Token': operator['token'], 'X-Device-Id': 'synthetic-operator'}
    assert call(body, headers)[0] == 403
    assert accounts.register_approver(operator['id'], 'synthetic-operator')['ok']
    assert call(body, headers) == (200, {'ok': True})
    assert call(body, headers) == (200, {'ok': True})
    with db.connect() as conn:
        assert protected == tuple(conn.execute('SELECT * FROM accounts WHERE username=?',
                                               ('revoke-user',)).fetchone())
        events = conn.execute("SELECT actor FROM account_approval_events WHERE device_id=? "
                              "AND decision='revoked'", (phone['id'],)).fetchall()
        assert len(events) == 1 and events[0]['actor'] == 'principal:synthetic-operator'
    assert device()['status'] == 'revoked'
    again = enter(password='reset1234')
    assert again['action'] == 'ok_pending'
    assert device()['id'] != phone['id'] and device()['status'] == 'pending'


def test_all_rotated_sessions_revoked_without_affecting_other_devices_or_accounts(world):
    first = enter()
    phone = device()
    assert accounts.approve_device(phone['id'], 'principal:test')['active']
    second = enter()
    desktop = enter('synthetic-desktop', 'desktop')
    pc = device('synthetic-desktop')
    assert accounts.approve_device(pc['id'], 'principal:test')['active']
    assert accounts.create_account('Synthetic Other', 'other-user', 'initial123')['ok']
    other = enter('other-phone', password='initial123', username='other-user')
    with db.connect() as conn:
        preserved = [tuple(r) for r in conn.execute('SELECT * FROM account_devices WHERE id<>?',
                                                   (phone['id'],))]
        # The latest token pointer is absent, while an older rotated session remains.
        newest = device()['agent_dev_id']
        conn.execute(f'DELETE FROM {world} WHERE id=?', (newest,))
        conn.commit()
    assert accounts.revoke_account_device(phone['id'], by='principal:test')
    assert check(world, first['token'])[0] == 'revoked'
    assert check(world, second['token'])[0] == 'unknown'
    assert check(world, desktop['token'])[0] == 'ok'
    assert db.agent_device_check(other['token'])[0] == 'ok'
    with db.connect() as conn:
        assert preserved == [tuple(r) for r in conn.execute('SELECT * FROM account_devices WHERE id<>?',
                                                          (phone['id'],))]
        assert accounts.device_gate_by_token(world, phone['agent_dev_id'])[0] == 'blocked'


def test_pending_request_with_no_token_can_be_retired(world):
    enter()
    phone = device()
    with db.connect() as conn:
        conn.execute(f'DELETE FROM {world} WHERE id=?', (phone['agent_dev_id'],))
        conn.commit()
    assert accounts.revoke_account_device(phone['id'])
    assert device()['status'] == 'revoked'
    assert not accounts.revoke_account_device(phone['id'] + 10000)
    assert not accounts.revoke_account_device(True)


def test_failure_rolls_back_token_and_approval_revocation(world):
    entered = enter()
    phone = device()
    assert accounts.approve_device(phone['id'], 'principal:test')['active']
    with db.connect() as conn:
        conn.execute("CREATE TRIGGER refuse_revoke BEFORE UPDATE OF status ON account_devices "
                     "WHEN NEW.status='revoked' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        conn.commit()
    with pytest.raises(Exception, match='synthetic failure'):
        accounts.revoke_account_device(phone['id'])
    assert device()['status'] == 'active'
    assert check(world, entered['token'])[0] == 'ok'
