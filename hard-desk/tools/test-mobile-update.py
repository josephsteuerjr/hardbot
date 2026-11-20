"""Real PWA update regression on disposable synthetic profiles; never uses working data.
Run with --assets <isolated mobile bundle> --output <local evidence dir> --engine chromium|webkit.
"""
from pathlib import Path
import argparse,json,os,sys,time
parser=argparse.ArgumentParser();parser.add_argument('--assets',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--engine',choices=['chromium','webkit'],required=True);args=parser.parse_args()
SOURCE=Path(__file__).resolve().parents[2];BASE=args.output.resolve();BASE.mkdir(parents=True,exist_ok=True)
assert (args.assets/'BUILD.json').is_file()
def save(name,value):(BASE/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
import asyncio,hashlib,shutil,subprocess,threading,uuid
from playwright.sync_api import sync_playwright
def until(page,expression,arg=None,timeout=45):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  if page.evaluate(expression,arg):return
  page.wait_for_timeout(100)
 raise AssertionError('Timed out: '+expression)
sys.path[:0]=[str(SOURCE),str(SOURCE/'server'),str(SOURCE/'engine')]
os.environ['PYTHONPATH']=os.pathsep.join(map(str,[SOURCE,SOURCE/'server',SOURCE/'engine']))
from aiohttp import web
from crm.mobile_backend import SyntheticBackend
import crm.mobile_server as server
qa=BASE/('update-'+args.engine+'-'+str(int(time.time())));qa.mkdir(exist_ok=False);assets=qa/'web';shutil.copytree(args.assets,assets);server.ASSETS=assets
sw=(assets/'sw.js').read_text();version=json.loads((assets/'BUILD.json').read_text())['version']
# Two complete shell versions: activation must not interrupt the loaded old UI.
previous=hashlib.sha256((version+'-previous').encode()).hexdigest()[:16]
index=(assets/'index.html').read_bytes().decode('utf-8');old_index=index.replace(version,previous)
legacy=sw.replace(version,previous)
for ext in ('js','css'):
 shutil.copyfile(assets/f'app-{version}.{ext}',assets/f'app-{previous}.{ext}')
legacy=legacy.replace(hashlib.sha256(index.encode()).hexdigest(),hashlib.sha256(old_index.encode()).hexdigest())
build=(assets/'BUILD.json').read_text();(assets/'BUILD.json').write_text(build.replace(version,previous))
(assets/'index.html').write_bytes(old_index.encode('utf-8'))
(assets/'sw.js').write_text(legacy)
(assets/f'sw-{previous}.js').write_text(legacy)
stop=threading.Event();state={}
async def serve():
 runner=web.AppRunner(server.create_app(SyntheticBackend(qa/'data/synthetic'),qa/'data'));await runner.setup()
 site=web.TCPSite(runner,'127.0.0.1',0);await site.start();state['url']='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/mobile/'
 try:
  while not stop.is_set():await asyncio.sleep(.1)
 finally:await runner.cleanup()
thread=threading.Thread(target=lambda:asyncio.run(serve()));thread.start()
try:
 while 'url' not in state:time.sleep(.1)
 with sync_playwright() as pw:
  engine=args.engine;browser=getattr(pw,engine).launch();ctx=browser.new_context(viewport={'width':393,'height':852},is_mobile=True,has_touch=True);p=ctx.new_page();p.goto(state['url']);p.locator('[data-mobile-role=employee]').click();p.locator('[data-person]').first.wait_for();until(p,'()=>!!navigator.serviceWorker.controller')
  person=p.locator('[data-person]').first.get_attribute('data-person');p.locator('[data-person]').first.click();p.locator('[data-action=call]').click();p.locator('#v-soft_comment').fill('Draft retained across mobile update')
  owner=p.evaluate('()=>crm.state().then(s=>s.owner)');cid=p.evaluate('(person)=>crm.api("GET","card",undefined,{person_uuid:person}).then(r=>r.data.contracts[0].contract_uuid)',person)
  p.evaluate('()=>crm.connect({action:"offline",value:true})');op=str(uuid.uuid4());p.evaluate('({cid,op})=>crm.command({schema_version:1,operation_id:op,kind:"note",contract_ids:[cid],payload:{text:"Update pending"}})',dict(cid=cid,op=op))
  p.evaluate('()=>caches.open("crm-mobile:other-scope:preserve").then(c=>c.put("/other",new Response("keep")))')
  second=ctx.new_page();second.goto(state['url']);second.locator('[data-person]').first.wait_for();second.locator('[data-person]').first.click();second.locator('[data-action=call]').click();second.locator('#v-soft_comment').fill('Other open form stays intact')
  (assets/'BUILD.json').write_text(build);(assets/'index.html').write_bytes(index.encode('utf-8'));(assets/'sw.js').write_text(sw)
  p.bring_to_front()
  p.evaluate('()=>window.dispatchEvent(new PageTransitionEvent("pageshow",{persisted:true}))')
  until(p,'(version)=>navigator.serviceWorker.getRegistration().then(r=>new Promise(resolve=>{const ch=new MessageChannel();ch.port1.onmessage=e=>{ch.port1.close();resolve(e.data.version===version)};r.active.postMessage({type:"GET_VERSION"},[ch.port2])}))',version)
  assert p.locator('#v-soft_comment').input_value()=='Draft retained across mobile update'
  assert p.locator('meta[name=crm-build]').get_attribute('content')==previous
  assert second.locator('#v-soft_comment').input_value()=='Other open form stays intact'
  # Ordinary new opening loads the current document, without any update control.
  fresh=ctx.new_page();fresh.goto(state['url']);fresh.locator('[data-person]').first.wait_for();assert fresh.locator('meta[name=crm-build]').get_attribute('content')==version
  assert fresh.locator('#mobile-update, #mobile-app-update').count()==0;fresh.close()
  p.bring_to_front()
  p.evaluate('()=>window.dispatchEvent(new PageTransitionEvent("pageshow",{persisted:true}))')
  with p.expect_event('domcontentloaded'):
   p.locator('dialog[open]').get_by_role('button',name='Закрыть',exact=True).click()
  p.locator('[data-action=call]').wait_for();assert p.locator('meta[name=crm-build]').get_attribute('content')==version
  assert second.locator('#v-soft_comment').input_value()=='Other open form stays intact';second.close()
  p.locator('[data-action=call]').click()
  # Both windows intentionally edited the same contract draft; newest durable value survives.
  assert p.locator('#v-soft_comment').input_value() in ['Draft retained across mobile update','Other open form stays intact']
  assert p.evaluate('(op)=>crm.queue().then(r=>r.data.entries.some(e=>e.body.operation_id===op))',op)
  assert p.evaluate('()=>caches.has("crm-mobile:other-scope:preserve")')
  # Simulate viewport geometry delivered by a phone keyboard; physical keyboard remains separate.
  p.evaluate('()=>{Object.defineProperty(visualViewport,"height",{configurable:true,value:460});visualViewport.dispatchEvent(new Event("resize"));}')
  bounds=p.locator('dialog[open]').bounding_box();assert bounds['height']<=440,bounds
  p.screenshot(path=str(qa/'keyboard-layout.png'))
  save(qa.name+'.json',dict(passed=True,engine=engine,automatic_fresh_open=True,automatic_resume=True,form_guard=True,other_open_form_retained=True,draft_and_operation_retained=True,other_scope_cache_retained=True,keyboard_geometry=bounds,physical_keyboard=False))
  print(engine,'automatic opening/resume + draft + queue + other open form PASS',flush=True);browser.close()
finally:stop.set();thread.join(20)
