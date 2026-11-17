"""Opening automatically replaces stale shell files without deleting business data."""
from pathlib import Path
import argparse,asyncio,hashlib,json,os,sys,threading,time,uuid
from playwright.sync_api import sync_playwright
parser=argparse.ArgumentParser();parser.add_argument('--assets',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
S=Path(__file__).resolve().parents[2];B=args.output.resolve();B.mkdir(parents=True,exist_ok=True);assets=args.assets.resolve();version=json.loads((assets/'BUILD.json').read_text())['version']
sys.path[:0]=list(map(str,[S,S/'server',S/'engine']));os.environ['PYTHONPATH']=os.pathsep.join(map(str,[S,S/'server',S/'engine']))
from aiohttp import web
from crm.mobile_backend import SyntheticBackend
import crm.mobile_server as mobile
mobile.ASSETS=assets
def until(p,code,arg=None):
 end=time.monotonic()+35
 while time.monotonic()<end:
  if p.evaluate(code,arg):return
  p.wait_for_timeout(100)
 raise AssertionError(code)
rows=[]
for engine in ['chromium','webkit']:
 qa=B/(engine+'-'+str(int(time.time())));qa.mkdir();stop=threading.Event();state={'mode':'normal'}
 async def serve():
  @web.middleware
  async def failure(request,handler):
   if state['mode']=='fail':return web.Response(status=503,text='Controlled offline fallback')
   if state['mode']=='bad-js' and request.path.endswith('/app.js'):return web.Response(text='wrong-version',content_type='application/javascript')
   return await handler(request)
  app=mobile.create_app(SyntheticBackend(qa/'data/synthetic'),qa/'data');app.middlewares.insert(0,failure);runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();state['url']='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/mobile/'
  try:
   while not stop.is_set():await asyncio.sleep(.1)
  finally:await runner.cleanup()
 thread=threading.Thread(target=lambda:asyncio.run(serve()));thread.start()
 try:
  end=time.monotonic()+30
  while 'url' not in state and time.monotonic()<end:time.sleep(.1)
  assert 'url' in state
  with sync_playwright() as pw:
   browser=getattr(pw,engine).launch();ctx=browser.new_context(viewport=dict(width=393,height=852),is_mobile=True,has_touch=True);p=ctx.new_page();p.goto(state['url']);p.locator('[data-mobile-role=employee]').click();p.locator('[data-person]').first.wait_for();until(p,'()=>!!navigator.serviceWorker.controller')
   person=p.locator('[data-person]').first.get_attribute('data-person');cid=p.evaluate('(id)=>crm.api("GET","card",undefined,{person_uuid:id}).then(r=>r.data.contracts[0].contract_uuid)',person)
   owner=p.evaluate('()=>crm.state().then(s=>s.owner)');p.evaluate('()=>crm.draft("qa-refresh",{text:"Keep encrypted draft"})');p.evaluate('()=>crm.connect({action:"offline",value:true})');op=str(uuid.uuid4());p.evaluate('({cid,op})=>crm.command({schema_version:1,operation_id:op,kind:"note",contract_ids:[cid],payload:{text:"Keep pending result"}})',dict(cid=cid,op=op))
   p.evaluate('()=>{localStorage.setItem("qa-preserve-local-storage","keep");return caches.open("crm-mobile:other-scope:preserve").then(c=>c.put("/other",new Response("keep")))}')
   cache_name=p.evaluate('(v)=>navigator.serviceWorker.getRegistration().then(r=>"crm-mobile:"+encodeURIComponent(r.scope)+":"+v)',version)
   cached_js='(name)=>caches.open(name).then(c=>c.match(new URL("./app.js",location.href))).then(r=>r.text())'
   original=p.evaluate(cached_js,cache_name)
   # Unreachable server and a bad new response preserve the last verified shell.
   state['mode']='fail';p.reload();p.locator('[data-person]').first.wait_for();assert p.evaluate(cached_js,cache_name)==original
   state['mode']='bad-js';p.reload();p.locator('[data-person]').first.wait_for();assert p.evaluate(cached_js,cache_name)==original
   state['mode']='normal'
   p.evaluate('(name)=>caches.open(name).then(async c=>{await c.put(new URL("./index.html",location.href),new Response("stale-html"));await c.put(new URL("./app.js",location.href),new Response("stale-js"))})',cache_name)
   p.close();p=ctx.new_page();p.goto(state['url']);p.locator('[data-person]').first.wait_for()
   assert p.locator('meta[name=crm-build]').get_attribute('content')==version;assert p.locator('#mobile-update, #mobile-app-update').count()==0
   actual=p.evaluate(cached_js,cache_name);assert hashlib.sha256(actual.encode()).hexdigest()==hashlib.sha256((assets/'app.js').read_bytes()).hexdigest()
   assert p.evaluate('()=>crm.state().then(s=>s.owner)')==owner
   assert p.evaluate('()=>crm.draft("qa-refresh").then(r=>r.data.text)')=='Keep encrypted draft'
   assert p.evaluate('(op)=>crm.queue().then(r=>r.data.entries.some(e=>e.body.operation_id===op))',op)
   assert p.evaluate('()=>localStorage.getItem("qa-preserve-local-storage")')=='keep'
   assert p.evaluate('()=>caches.has("crm-mobile:other-scope:preserve")')
   # Same genuine SW fallback for both engines, avoiding WebKit's DevTools-offline bug.
   state['mode']='fail';p.reload();p.locator('[data-person]').first.wait_for();assert p.locator('meta[name=crm-build]').get_attribute('content')==version
   p.screenshot(path=str(qa/'refreshed.png'));rows.append(dict(engine=engine,passed=True,automatic_fresh_open=True,poisoned_shell_replaced=True,failed_network_preserves_cache=True,wrong_asset_hash_preserves_cache=True,owner_draft_queue_preserved=True,localstorage_preserved=True,other_scope_preserved=True,server_503_cold_fallback=True,output=str(qa)));browser.close();print(engine,'automatic fresh opening + poisoned cache + offline/data preservation PASS',flush=True)
 finally:stop.set();thread.join(20)
(B/'RESULT.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
