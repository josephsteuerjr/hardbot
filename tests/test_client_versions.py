"""Version observations follow authenticated devices, not claimed usernames."""
import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

import accounts
import client_versions
import db
import web


NATIVE = {'X-Client-Version': '3.2.7', 'X-Work-Product': 'manager',
          'User-Agent': 'Synthetic Electron/39.0.0'}


@pytest.fixture(params=['agent', 'manager'])
def world(tmp_path, monkeypatch, request):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'versions.db')
    monkeypatch.setattr(db, 'admin_bootstrap_marker', lambda: tmp_path / 'bootstrap')
    monkeypatch.setattr(web, 'DEV', False)
    monkeypatch.delenv('ACCOUNT_CUTOVER_AT_UTC', raising=False)
    db.init_db()
    assert accounts.create_account('Synthetic Version User', 'version-user', 'initial123',
                                   role=request.param)['ok']
    return 'manager_devices' if request.param == 'manager' else 'agent_devices'


def login_body(fp='synthetic-version-phone'):
    return {'username': 'version-user', 'password': 'initial123', 'audience': 'mobile',
            'device': {'kind': 'phone', 'fp': fp, 'label': 'Synthetic Phone'}}


def http(method, path, headers=None, body=None):
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as client:
            response = await client.request(method, path, headers=headers, json=body)
            payload = await response.json() if response.content_type == 'application/json' else await response.text()
            return response.status, payload
    return asyncio.run(run())


def enter():
    status, result = http('POST', '/api/account/login', NATIVE, login_body())
    assert status == 200 and result['token'], result
    return result['token']


def rows():
    with db.connect() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM account_client_versions ORDER BY id')]


def bearer(table, token):
    return {'X-Pult-Token' if table == 'manager_devices' else 'X-App-Token': token}


def test_login_persists_version_with_real_session_device_and_pending_access(world):
    token = enter()
    observed = rows()
    assert len(observed) == 1
    row = observed[0]
    assert row['client_version'] == '3.2.7' and row['source'] == 'desktop'
    assert row['product'] == 'manager' and row['shell_version'] == 0 and row['login_at']
    with db.connect() as conn:
        session = conn.execute('SELECT d.id,d.account_id FROM account_devices d '
                               'JOIN account_token_sessions s ON s.device_id=d.id '
                               'WHERE s.token_table=? AND s.token_id=?',
                               (world, row['token_id'])).fetchone()
        assert (row['device_id'], row['account_id']) == tuple(session)
        assert conn.execute('SELECT status FROM account_devices WHERE id=?',
                            (row['device_id'],)).fetchone()[0] == 'pending'
    assert accounts.device_gate_by_token(world, row['token_id'])[0] == 'pending'
    assert token not in json.dumps(observed)
    assert http('GET', '/admin/accounts.json', {**NATIVE, **bearer(world, token)})[0] in (401, 403)


def test_existing_session_records_upgrade_without_relogin_and_keeps_history(world):
    token = enter()
    newer = {**NATIVE, 'X-Client-Version': '3.2.8'}
    http('GET', '/api/bootstrap', {**newer, **bearer(world, token)})
    observed = rows()
    assert [r['client_version'] for r in observed] == ['3.2.7', '3.2.8']
    assert observed[0]['token_id'] == observed[1]['token_id']
    assert observed[0]['login_at'] and observed[1]['login_at'] is None
    # A restart and schema initialization preserve observations.
    db.init_db()
    assert rows() == observed
    devices = accounts.admin_accounts()[0]['devices']
    assert devices[0]['client']['client_version'] == '3.2.8'
    assert len(devices[0]['client_versions']) == 2
    assert 'token_id' not in json.dumps(devices[0]['client_versions'])


def test_repeated_requests_do_not_write_on_every_poll(world):
    token = enter()
    with db.connect() as conn:
        conn.execute("CREATE TRIGGER refuse_observation_update BEFORE UPDATE ON account_client_versions "
                     "BEGIN SELECT RAISE(ABORT, 'unexpected per-request write'); END")
        conn.commit()
    before = rows()
    for _ in range(3):
        client_versions.observe(world, token, client_versions.metadata(NATIVE))
    assert rows() == before


def test_observation_refreshes_after_minute_and_preserves_first_seen(world):
    token = enter()
    with db.connect() as conn:
        conn.execute("UPDATE account_client_versions SET last_seen_at='2000-01-01T00:00:00+00:00'")
        conn.commit()
    before = rows()[0]
    client_versions.observe(world, token, client_versions.metadata(NATIVE))
    after = rows()[0]
    assert after['last_seen_at'] > before['last_seen_at']
    assert after['first_seen_at'] == before['first_seen_at']
    assert after['login_at'] == before['login_at']


def test_unknown_missing_and_revoked_bearers_cannot_attribute_a_version(world):
    http('GET', '/api/account/staff', NATIVE)
    http('GET', '/api/account/staff', {**NATIVE, **bearer(world, 'synthetic-invalid-bearer')})
    assert not rows()
    token = enter()
    before = rows()
    http('GET', '/api/account/staff', bearer(world, token))
    http('GET', '/api/account/staff', {**bearer(world, token), 'X-Client-Version': '<script>bad</script>'})
    assert rows() == before
    assert accounts.set_password('version-user', 'reset1234')['ok']
    http('GET', '/api/account/staff', {**NATIVE, **bearer(world, token), 'X-Client-Version': '3.2.9'})
    assert rows() == before


def test_pwa_shell_and_python_compatibility_header_are_distinct(world):
    token = enter()
    pwa = {'X-Client-Version': '3.2.0', 'X-Shell-Version': '102', 'X-Work-Product': 'soft'}
    client_versions.observe(world, token, client_versions.metadata(pwa))
    client_versions.observe(world, token, client_versions.metadata({
        'X-Client-Version': '3.2.0', 'User-Agent': 'Python-urllib/3.11'}))
    observed = rows()
    assert observed[1]['source'] == 'pwa' and observed[1]['shell_version'] == 102
    assert observed[1]['client_version'] == '3.2.0' and observed[1]['product'] == 'soft'
    assert observed[2]['source'] == 'compatibility'
    view = accounts.admin_accounts()[0]['devices'][0]
    assert view['client']['source'] != 'compatibility'
    assert {r['source'] for r in view['client_versions']} == {'desktop', 'pwa', 'compatibility'}


def test_tokens_in_different_tables_and_devices_remain_separate(world):
    first = enter()
    status, second = http('POST', '/api/account/login', {**NATIVE, 'X-Client-Version': '3.2.6'},
                          login_body('synthetic-second-phone'))
    assert status == 200
    observed = rows()
    assert len(observed) == 2 and observed[0]['device_id'] != observed[1]['device_id']
    wrong_table = 'agent_devices' if world == 'manager_devices' else 'manager_devices'
    client_versions.observe(wrong_table, first, client_versions.metadata(NATIVE))
    assert rows() == observed
    with db.connect() as conn:
        conn.execute("UPDATE account_devices SET status='revoked' WHERE id=?", (observed[0]['device_id'],))
        conn.commit()
    client_versions.observe(world, first, client_versions.metadata({**NATIVE, 'X-Client-Version': '3.2.9'}))
    assert rows() == observed
    assert second['token'] != first


def test_rejected_old_client_still_reports_version_without_bypassing_cutover(world, monkeypatch):
    token = enter()
    monkeypatch.setenv('ACCOUNT_CUTOVER_AT_UTC', '2020-01-01T00:00:00Z')
    status, _ = http('GET', '/api/bootstrap', {**NATIVE, **bearer(world, token), 'X-Client-Version': '3.1.8'})
    assert status == 426
    assert rows()[-1]['client_version'] == '3.1.8'
    assert accounts.device_gate_by_token(world, rows()[0]['token_id'])[0] != 'ok'


def test_diagnostic_failure_does_not_break_login(world, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic diagnostic outage')
    monkeypatch.setattr(client_versions, 'observe', fail)
    enter()
    assert not rows()


def test_account_deletion_cleans_up_observations(world):
    enter()
    with db.connect() as conn:
        aid = conn.execute("SELECT id FROM accounts WHERE username='version-user'").fetchone()[0]
    assert accounts.delete_account('version-user', 'principal:test', expected_id=aid)['ok']
    assert not rows()


@pytest.mark.parametrize('headers', [
    {}, {'X-Client-Version': 'x' * 500}, {'X-Shell-Version': '-1'},
    {'X-Shell-Version': '100000'}, {'X-Shell-Version': 'Infinity'},
])
def test_bad_or_absent_version_headers_do_not_invent_a_version(headers):
    assert client_versions.metadata(headers) is None
