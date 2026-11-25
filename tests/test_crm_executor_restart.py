"""Real Windows DPAPI and Python processes, synthetic authority and no mail."""
import asyncio,contextlib,hashlib,os
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from crm.mobile_backend import WindowsExecutor,ServerExecutor
from crm.contract import CRMError
from crm import executor_state

@pytest.mark.parametrize('portable',[False,True],ids=['windows-dpapi','server-aes'])
def test_bff_restart_reattaches_queue_and_new_token_does_not_duplicate_process(tmp_path,portable):
    if not portable and os.name!='nt':pytest.skip('Real Windows DPAPI executor')
    key=tmp_path/'server.key';key.write_bytes(os.urandom(32))
    async def run():
        active=[True]
        async def identity(request):return web.json_response(dict(ok=True,subject=dict(actor_uid=77001),me=dict(role='analyst')),status=200 if active[0] else 403)
        app=web.Application();app.router.add_get('/api/crm-identity',identity)
        async with TestServer(app) as authority:
            session=dict(owner='synthetic-executor-owner',role='analyst',credential='synthetic-executor-token-0123456789',context=dict(actor_uid=77001))
            # Only the synthetic fixture redirects transport. Production remains HTTPS.
            primary=str(authority.make_url('')).rstrip('/')
            factory=lambda:ServerExecutor(tmp_path,primary,key) if portable else WindowsExecutor(tmp_path,primary)
            executor=factory();await executor.start()
            folder=tmp_path/hashlib.sha256(session['owner'].encode()).hexdigest()
            try:
                result=await executor.admin(session,'/jobs',{});assert result['ok']
                first=executor.state.read(folder/'runtime.bin')
                assert session['credential'].encode() not in (folder/'authority.bin').read_bytes()
                assert session['credential'] not in (folder/'settings.json').read_text(encoding='utf-8')
                assert (await executor.admin(session,'/reg/state',{}))['ok']
                await executor.close()
                executor=factory();await executor.start()
                assert (await executor.admin(session,'/jobs',{}))['ok']
                assert executor.state.read(folder/'runtime.bin')['pid']==first['pid']
                session['credential']='synthetic-replacement-token-0123456789'
                assert (await executor.admin(session,'/jobs',{}))['ok']
                assert executor.state.read(folder/'runtime.bin')['pid']!=first['pid']
                active[0]=False
                with pytest.raises(CRMError):await executor.admin(session,'/jobs',{})
            finally:
                executor.supervisor.cancel()
                with contextlib.suppress(asyncio.CancelledError):await executor.supervisor
                await executor.stop(session['owner']);await executor.close()
    asyncio.run(run())
