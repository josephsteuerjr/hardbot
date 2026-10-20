"""Analyst card transport uses real admin auth; manager scope remains separate."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

import accounts
import db
import web
from test_soft_manager_stats import world, DAY
from test_pc_first_accounts import identity, proof


def get(path, headers=None):
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as client:
            response = await client.get(path, headers={'X-Client-Version': '3.2.8', **(headers or {})})
            data = await response.json() if response.content_type == 'application/json' else await response.read()
            return response.status, data
    return asyncio.run(run())


def analyst(monkeypatch):
    monkeypatch.setattr(web, 'DEV', False)
    monkeypatch.setenv('ACCOUNT_CUTOVER_AT_UTC', '2000-01-01T00:00:00Z')
    secret = 'Synthetic123!'
    assert accounts.create_account('Synthetic Analyst', 'synthetic-analyst', secret, role='analyst')['ok']
    assert accounts.approve_account('synthetic-analyst', 'principal:test')['ok']
    assert accounts.change_password('synthetic-analyst', secret, 'Changed123!')['ok']
    with db.connect() as conn:
        aid = conn.execute("SELECT id FROM accounts WHERE username='synthetic-analyst'").fetchone()[0]
        conn.execute("INSERT INTO account_admin_grants(account_id,principal,granted_by,operation_id,at_utc) "
                     "VALUES(?,'synthetic:analyst','test-fixture','synthetic-operation','2026-01-01T00:00:00Z')", (aid,))
        conn.commit()
    pc = identity()
    result = accounts.login('synthetic-analyst', 'Changed123!',
        device={'kind': 'desktop', 'fp': pc[2], 'label': 'Synthetic PC'}, entrance='internal',
        audience='analyst', pc_proof=proof(pc, '', 'synthetic-analyst', 'Changed123!'))
    assert result['action'] == 'ok', result
    return {'X-Pult-Token': result['token']}


@pytest.mark.parametrize('path', ['person?employee=S1&rep=A&mode=soft', 'photos?contract_id=A',
                                  'photo?file=A/test.jpg', 'receipt_photo?file=A/test.jpg'])
def test_admin_card_routes_require_authenticated_admin(world, monkeypatch, path):
    monkeypatch.setattr(web, 'DEV', False)
    assert get('/admin/control/' + path)[0] in (401, 403)
    assert accounts.create_account('Synthetic Manager', 'synthetic-manager', 'Synthetic123!', role='manager')['ok']
    pending = accounts.login('synthetic-manager', 'Synthetic123!',
        device={'kind': 'phone', 'fp': 'synthetic-manager-phone'}, entrance='public')
    assert get('/admin/control/' + path, {'X-Pult-Token': pending['token']})[0] == 403


def test_strict_analyst_reads_card_and_images_through_admin_routes(world, monkeypatch, tmp_path):
    headers = analyst(monkeypatch)
    photo = tmp_path / 'photos' / 'A'; photo.mkdir(parents=True)
    receipt = tmp_path / 'receipts' / 'A'; receipt.mkdir(parents=True)
    (photo / 'test.jpg').write_bytes(b'synthetic-photo')
    (receipt / 'test.jpg').write_bytes(b'synthetic-receipt')
    monkeypatch.setattr(web, 'PHOTO_DIR', photo.parent)
    monkeypatch.setattr(web, 'receipt_dir', lambda: receipt.parent)
    status, body = get('/admin/control/person?employee=S1&rep=A&mode=soft&date=' + DAY, headers)
    assert status == 200, body
    assert body['profile'] == 'soft' and {r['contract_id'] for r in body['contracts']} == {'A', 'B'}
    assert get('/admin/control/photos?contract_id=A', headers)[0] == 200
    assert get('/admin/control/photo?file=A/test.jpg', headers) == (200, b'synthetic-photo')
    assert get('/admin/control/receipt_photo?file=A/test.jpg', headers) == (200, b'synthetic-receipt')
    assert get('/admin/control/person?employee=missing&rep=A', headers)[0] == 404
    assert get('/admin/control/person?employee=S1&rep=C&mode=soft', headers)[0] == 404
    assert get('/admin/control/photo?file=../outside.jpg', headers)[0] == 404
    # An analyst token is not silently accepted as a manager token on the old route.
    assert get('/api/manager/person?employee=S1&rep=A&mode=soft', headers)[0] == 403


def test_desktop_bridge_reports_installed_version_instead_of_protocol_floor(monkeypatch):
    from pult.core.api import auth_headers
    import client_versions
    monkeypatch.setenv('HARD_CLIENT_VERSION', '3.2.8')
    monkeypatch.setenv('HARD_CLIENT_PRODUCT', 'analyst')
    headers = auth_headers(device_token='account:synthetic-token')
    assert headers['X-Client-Version'] == '3.2.8' and headers['X-Work-Product'] == 'analyst'
    meta = client_versions.metadata({**headers, 'User-Agent': 'Python-urllib/3.12'})
    assert meta['source'] == 'desktop' and meta['client_version'] == '3.2.8'
    monkeypatch.delenv('HARD_CLIENT_VERSION')
    fallback = auth_headers(device_token='account:synthetic-token')
    assert client_versions.metadata({**fallback, 'User-Agent': 'Python-urllib/3.12'})['source'] == 'compatibility'
