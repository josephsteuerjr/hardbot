"""Drive the mobile bundle through the offline cache cutoff in real browsers."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import uuid

from aiohttp import web
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--assets', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--engine', choices=('chromium','webkit'), required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(source),str(source/'server')]
os.environ['PYTHONPATH'] = os.pathsep.join(sys.path[:2])
from crm import mobile_server
from crm.mobile_backend import SyntheticBackend
from crm.contract import CRMError

mobile_server.ASSETS = args.assets.resolve()
output = args.output.resolve()
output.mkdir(parents=True,exist_ok=True)
ready = threading.Event()
stop = threading.Event()
state = {'online':True}


class RetentionBackend(SyntheticBackend):
    async def request(self, session, method, action, body=None, query=None):
        if state.get('grant_revoked') and action == 'context':
            raise CRMError('Выбранное полномочие отозвано.', 'grant_revoked', 403)
        return await super().request(session, method, action, body, query)


async def serve():
    @web.middleware
    async def network(request,handler):
        if state.get('revoked') and request.path=='/mobile/boot':
            return web.json_response({'error':'identity_changed'},status=401)
        if not state['online'] and request.path in {'/mobile/boot','/mobile/api','/mobile/receipts'}:
            return web.json_response({'error':'upstream_unavailable'},status=503)
        return await handler(request)

    app = mobile_server.create_app(RetentionBackend(output/'data/synthetic'),output/'data')
    app.middlewares.insert(0,network)
    runner = web.AppRunner(app,access_log=None)
    await runner.setup()
    site = web.TCPSite(runner,'127.0.0.1',0)
    await site.start()
    state['url'] = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/mobile/'
    ready.set()
    try:
        await asyncio.to_thread(stop.wait)
    finally:
        await runner.cleanup()


def change_activity(page,age_ms):
    return page.evaluate("""async age=>{
      const db=await new Promise((ok,fail)=>{
        const open=indexedDB.open('rimborso-crm-mobile-v1');
        open.onsuccess=()=>ok(open.result);open.onerror=()=>fail(open.error);
      });
      const tx=db.transaction('keys','readwrite'),value=Date.now()-age;
      await new Promise((ok,fail)=>{
        tx.oncomplete=ok;tx.onabort=()=>fail(tx.error);
        tx.objectStore('keys').put(value,'read-cache-last-use');
      });
      db.close();return value;
    }""",age_ms)


def inspect_cache(page):
    return page.evaluate("""async()=>{
      const db=await new Promise((ok,fail)=>{
        const open=indexedDB.open('rimborso-crm-mobile-v1');
        open.onsuccess=()=>ok(open.result);open.onerror=()=>fail(open.error);
      });
      const tx=db.transaction(['keys','vault']);
      const get=request=>new Promise((ok,fail)=>{request.onsuccess=()=>ok(request.result);request.onerror=()=>fail(request.error);});
      const [keys,activity]=await Promise.all([
        get(tx.objectStore('vault').getAllKeys()),
        get(tx.objectStore('keys').get('read-cache-last-use'))
      ]);
      db.close();
      return {reads:keys.filter(key=>String(key).includes('\\nread:')).length,activity};
    }""")


def wait_for_owner(page,owner):
    for _ in range(100):
        if page.evaluate('owner=>window.crm?.state().then(state=>state.owner===owner)',owner):
            return
        page.wait_for_timeout(100)
    raise AssertionError('The saved mobile identity did not start')


thread = threading.Thread(target=lambda:asyncio.run(serve()),daemon=True)
thread.start()
try:
    assert ready.wait(20),'The isolated BFF did not start'
    with sync_playwright() as play:
        with getattr(play,args.engine).launch() as browser:
            context=browser.new_context(viewport={'width':393,'height':852},is_mobile=True,has_touch=True)
            page=context.new_page()
            url=state['url']
            page.goto(url)
            page.locator('[data-mobile-role=employee]').click()
            page.locator('[data-person]').first.wait_for()
            owner=page.evaluate('()=>crm.state().then(s=>s.owner)')
            cookie=next(c['value'] for c in context.cookies(url) if c['name']==mobile_server.COOKIE)
            query={'work':'all'}
            portfolio=page.evaluate('q=>crm.api("GET","work-portfolio",undefined,q)',query)
            cid=portfolio['data']['contracts'][0]['contract_uuid']
            assert inspect_cache(page)['reads']>0
            young=48*3600*1000-60_000
            last=change_activity(page,young)
            assert page.evaluate('q=>crm.api("GET","work-portfolio",undefined,q).then(r=>r.ok)',query)
            assert inspect_cache(page)['activity']==last,'A background API read extended retention'
            page.evaluate('()=>crm.draft("retention-draft",{text:"Synthetic draft"})')
            page.evaluate('()=>crm.connect({action:"offline",value:true})')
            operation=str(uuid.uuid4())
            result=page.evaluate('body=>crm.command(body)',dict(schema_version=1,kind='note',operation_id=operation,
                contract_ids=[cid],payload={'text':'Synthetic pending note'}))
            assert result['ok']
            state['online']=False
            page.reload()
            wait_for_owner(page,owner)
            assert page.evaluate('q=>crm.api("GET","work-portfolio",undefined,q).then(r=>Boolean(r.ok&&r.stale))',query)
            change_activity(page,48*3600*1000+1)
            page.reload()
            wait_for_owner(page,owner)
            assert inspect_cache(page)['reads']==0
            assert not page.evaluate('q=>crm.api("GET","work-portfolio",undefined,q).then(r=>r.ok)',query)
            assert page.evaluate('()=>crm.draft("retention-draft").then(r=>r.data.text)')=='Synthetic draft'
            assert page.evaluate('op=>crm.queue().then(r=>r.data.entries.some(e=>e.body.operation_id===op&&e.state==="pending"))',operation)
            state['online']=True
            assert page.evaluate('()=>crm.connect({action:"refresh"}).then(r=>r.ok)')
            assert page.evaluate('q=>crm.api("GET","work-portfolio",undefined,q).then(r=>r.ok&&!r.stale)',query)
            assert any(c['name']==mobile_server.COOKIE and c['value']==cookie for c in context.cookies(url))
            assert page.evaluate('()=>crm.state().then(s=>s.owner)')==owner
            state['grant_revoked']=True
            with page.expect_response(lambda r:r.url.endswith('/mobile/boot') and r.status==403) as denied:
                page.evaluate('()=>crm.connect({action:"refresh"})')
            assert denied.value.json()['error']=='grant_revoked'
            assert page.evaluate('()=>crm.state().then(s=>s.owner)')==''
            assert inspect_cache(page)['reads']==0
            state.update(grant_revoked=False,online=False)
            page.reload()
            page.locator('.mobile-connection-status strong').filter(has_text='Сервер временно недоступен').wait_for()
            assert page.evaluate('()=>crm.state().then(s=>s.owner)')==''
            state['online']=True
            page.reload()
            wait_for_owner(page,owner)
            assert any(c['name']==mobile_server.COOKIE and c['value']==cookie for c in context.cookies(url))
            assert page.evaluate('()=>crm.draft("retention-draft").then(r=>r.data.text)')=='Synthetic draft'
            state['revoked']=True
            page.reload()
            page.get_by_text('Нужно войти снова',exact=True).wait_for()
            state.update(revoked=False,online=False)
            page.reload()
            page.locator('.mobile-connection-status strong').filter(has_text='Сервер временно недоступен').wait_for()
            assert page.evaluate('()=>crm.state().then(s=>s.owner)')==''
            (output/'RESULT.json').write_text(json.dumps({'engine':args.engine,'owner_retained':True,
                'idle_cache_purged':True,'draft_and_queue_kept':True,'same_cookie_after_reconnect':True,
                'does_not_restore_confirmed_revocation':True,'grant_revocation_clears_previous_workspace':True},indent=2))
            print(args.engine,'48h read retention, durable work and session PASS',flush=True)
finally:
    stop.set()
    thread.join(15)
