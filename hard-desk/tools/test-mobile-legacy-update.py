from pathlib import Path
import asyncio,json,os,sys,threading,time,uuid
from playwright.sync_api import sync_playwright
import argparse
parser=argparse.ArgumentParser(description='Upgrade the actual early mobile app and worker without deleting local data')
parser.add_argument('--previous-assets',type=Path,required=True);parser.add_argument('--assets',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
B=args.output.resolve();B.mkdir(parents=True,exist_ok=True);S=Path(__file__).resolve().parents[2];LEGACY=args.previous_assets.resolve();CURRENT=args.assets.resolve()
previous_version=json.loads((LEGACY/'BUILD.json').read_text())['version'];current_version=json.loads((CURRENT/'BUILD.json').read_text())['version']
sys.path[:0]=list(map(str,[S,S/'server',S/'engine']));os.environ['PYTHONPATH']=os.pathsep.join(map(str,[S,S/'server',S/'engine']))
from aiohttp import web
from crm.mobile_backend import SyntheticBackend
import crm.mobile_server as mobile
def until(p,code,arg=None,timeout=30):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  if p.evaluate(code,arg):return
  p.wait_for_timeout(100)
 raise AssertionError(code)
results=[]
for engine in ['chromium','webkit']:
 qa=B/('actual-legacy-'+engine+'-'+str(int(time.time())));qa.mkdir();stop=threading.Event();state={};mobile.ASSETS=LEGACY
 async def serve():
  runner=web.AppRunner(mobile.create_app(SyntheticBackend(qa/'data/synthetic'),qa/'data'));await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();state['url']='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/mobile/'
  try:
   while not stop.is_set():await asyncio.sleep(.1)
  finally:await runner.cleanup()
 thread=threading.Thread(target=lambda:asyncio.run(serve()));thread.start()
 try:
  end=time.monotonic()+30
  while 'url' not in state and time.monotonic()<end:time.sleep(.1)
  assert 'url' in state
  with sync_playwright() as pw:
   browser=getattr(pw,engine).launch();ctx=browser.new_context(viewport=dict(width=393,height=852),is_mobile=True,has_touch=True);p=ctx.new_page();p.goto(state['url']);p.locator('[data-mobile-role=employee]').click();p.locator('[data-person]').first.wait_for();until(p,'()=>!!navigator.serviceWorker.controller');person=p.locator('[data-person]').first.get_attribute('data-person');p.locator('[data-person]').first.click();p.locator('[data-tab=history]').click()
   assert p.locator('.card-script-watermark').count()==1;assert p.locator('#mobile-update').count()==0
   owner=p.evaluate('()=>crm.state().then(s=>s.owner)');p.evaluate('()=>crm.draft("qa-actual-legacy",{text:"Retained original legacy draft"})');p.screenshot(path=str(qa/'old-app.png'))
   cid=p.evaluate('(person)=>crm.api("GET","card",undefined,{person_uuid:person}).then(r=>r.data.contracts[0].contract_uuid)',person);op=str(uuid.uuid4());p.evaluate('()=>crm.connect({action:"offline",value:true})');p.evaluate('({cid,op})=>crm.command({schema_version:1,operation_id:op,kind:"note",contract_ids:[cid],payload:{text:"Preserved legacy queue"}})',dict(cid=cid,op=op))
   p.locator('[data-action=call]').click();p.locator('#v-soft_comment').fill('Open legacy form must survive activation')
   mobile.ASSETS=CURRENT;p.evaluate('()=>navigator.serviceWorker.getRegistration().then(r=>r.update()).then(()=>true)')
   until(p,'(version)=>navigator.serviceWorker.getRegistration().then(r=>new Promise(resolve=>{const ch=new MessageChannel(),timer=setTimeout(()=>{ch.port1.close();resolve(false)},500);ch.port1.onmessage=e=>{clearTimeout(timer);ch.port1.close();resolve(e.data.version===version)};r.active.postMessage({type:"GET_VERSION"},[ch.port2])}))',current_version)
   assert p.locator('#v-soft_comment').input_value()=='Open legacy form must survive activation';assert p.locator('#mobile-update').count()==0
   p.locator('dialog[open]').get_by_role('button',name='Закрыть',exact=True).click()
   # A normal reload now uses the new shell; closing every app window is unnecessary.
   p.reload();until(p,'()=>!!document.querySelector("[data-person], [data-action=call]")');until(p,'(version)=>document.querySelector("meta[name=crm-build]")?.content===version',current_version)
   assert p.evaluate('()=>crm.state().then(s=>s.owner)')==owner
   assert p.evaluate('()=>crm.draft("qa-actual-legacy").then(r=>r.data.text)')=='Retained original legacy draft'
   assert p.evaluate('(op)=>crm.queue().then(r=>r.data.entries.some(e=>e.body.operation_id===op))',op)
   if not p.locator('[data-action=call]').count():p.locator('[data-person]').first.click()
   for tab in ['history','tasks','promises']:
    p.locator('[data-tab='+tab+']').click();assert p.locator('.card-script-watermark').count()==0
   p.screenshot(path=str(qa/'updated-app.png'));browser.close()
   results.append(dict(engine=engine,passed=True,original_bundle=previous_version,current_bundle=current_version,original_has_no_update_button=True,background_activation=True,open_legacy_form_retained=True,normal_reload_updates=True,owner_draft_queue_retained=True,tabs_fixed=True,output=str(qa)));print(engine,'actual old UI + worker, normal reload, preserved form, draft and operation PASS',flush=True)
 finally:stop.set();thread.join(20)
(B/'ACTUAL-LEGACY.private.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
