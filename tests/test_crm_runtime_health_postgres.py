import asyncio
import json
import os
from pathlib import Path
import runpy
import sys

import pytest
from aiohttp.test_utils import TestClient,TestServer
from crm import schema,shared_server,shared_auth,storage
from test_crm_postgres import pg_world

pytestmark=pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_DSN_FILE'),reason='Isolated PostgreSQL')


def test_real_scheduler_lifecycle_health_and_operator_status(pg_world,tmp_path,monkeypatch,capsys):
    key=tmp_path/'session.key';key.write_bytes(os.urandom(32))
    monkeypatch.setenv('HARD_CRM_SESSION_KEY_FILE',str(key))
    with storage.connect() as c:c.execute("INSERT INTO crm_meta VALUES('deployment_namespace',?)",(shared_auth.NAMESPACE,))
    async def run():
        async with TestClient(TestServer(shared_server.create_app())) as client:
            for _ in range(50):
                response=await client.get('/health')
                if response.status==200:break
                await asyncio.sleep(.1)
            assert response.status==200
            assert (await response.json())['scheduler']=='running'
            with storage.connect() as c:c.execute("UPDATE crm_meta SET value='4' WHERE key='schema_version'")
            assert (await client.get('/health')).status==503
            with storage.connect() as c:c.execute("UPDATE crm_meta SET value=? WHERE key='schema_version'",(str(schema.VERSION),))
    asyncio.run(run())
    tool=Path(__file__).parents[1]/'tools/crm_runtime.py'
    monkeypatch.setattr(sys,'argv',[str(tool),'status']);runpy.run_path(str(tool),run_name='__main__')
    result=json.loads(capsys.readouterr().out)
    assert result['meta']['schema_version']==str(schema.VERSION)
    assert result['workers'][0]['worker']=='scheduler' and result['workers'][0]['ok']==1
