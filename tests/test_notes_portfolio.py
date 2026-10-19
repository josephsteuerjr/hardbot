import asyncio
import pytest
from aiohttp.test_utils import TestClient, TestServer
import db
import web


@pytest.mark.parametrize('product', ['hard', 'soft'])
def test_notes_accept_own_contract_and_reject_foreign_with_idempotent_replay(tmp_path, monkeypatch, product):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'notes.db')
    monkeypatch.setattr(web, 'DEV', True)
    db.init_db()
    db.set_bind(810111, 'Synthetic Worker')
    with db.connect() as conn:
        conn.execute('INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES(?,?,?,?)',
                     ('OWN', 'Synthetic', 'Synthetic Worker' if product == 'hard' else 'Other', 'Synthetic Worker' if product == 'soft' else 'Other'))
        conn.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES('FOREIGN','Synthetic','Other','Other')")
        conn.commit()
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as client:
            headers = {'X-Dev-Uid': '810111', 'X-Work-Product': product}
            body = {'rep': 'OWN', 'text': 'Synthetic note', 'op_id': 'synthetic-note'}
            for _ in range(2):
                r = await client.post('/api/note', headers=headers, json=body)
                assert r.status == 200 and (await r.json())['ok']
            r = await client.post('/api/note', headers=headers, json={**body, 'rep': 'FOREIGN', 'op_id': 'foreign'})
            assert r.status == 404
    asyncio.run(run())
    assert len(db.notes_for(810111)) == 1
