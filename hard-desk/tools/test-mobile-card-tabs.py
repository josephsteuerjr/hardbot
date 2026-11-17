from pathlib import Path
import asyncio,json,os,sqlite3,sys,threading,time,uuid
from playwright.sync_api import sync_playwright
import argparse
parser=argparse.ArgumentParser(description='Employee card tabs against isolated canonical CRM')
parser.add_argument('--assets',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
B=args.output.resolve();B.mkdir(parents=True,exist_ok=True);S=Path(__file__).resolve().parents[2]
sys.path[:0]=list(map(str,[S,S/'server',S/'engine']));os.environ['PYTHONPATH']=os.pathsep.join(map(str,[S,S/'server',S/'engine']))
from aiohttp import web
from crm.mobile_backend import SyntheticBackend
import crm.mobile_server as mobile
mobile.ASSETS=args.assets.resolve()
def until(p,code,arg=None):
 for _ in range(300):
  if p.evaluate(code,arg):return
  p.wait_for_timeout(100)
 raise AssertionError(code)
def api(p,action,body=None,query=None):
 r=p.evaluate('(q)=>crm.api(q.body?"POST":"GET",q.action,q.body,q.query)',dict(action=action,body=body,query=query));assert r['ok'],r;return r['data']
results=[]
for engine in ['chromium','webkit']:
 qa=B/(engine+'-'+str(int(time.time())));qa.mkdir();stop=threading.Event();state={}
 async def serve():
  runner=web.AppRunner(mobile.create_app(SyntheticBackend(qa/'data/synthetic'),qa/'data'));await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();state['url']='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/mobile/'
  try:
   while not stop.is_set():await asyncio.sleep(.1)
  finally:await runner.cleanup()
 thread=threading.Thread(target=lambda:asyncio.run(serve()));thread.start()
 try:
  deadline=time.monotonic()+30
  while 'url' not in state and time.monotonic()<deadline:time.sleep(.1)
  assert 'url' in state
  with sync_playwright() as pw:
   browser=getattr(pw,engine).launch();ctx=browser.new_context(viewport=dict(width=393,height=852),is_mobile=True,has_touch=True);p=ctx.new_page();errors=[];p.on('pageerror',lambda e:errors.append(str(e)));p.goto(state['url']);p.locator('[data-mobile-role=employee]').click();p.locator('[data-person]').first.wait_for();person=p.locator('[data-person]').first.get_attribute('data-person');p.locator('[data-person]').first.click();p.locator('[data-action=call]').wait_for()
   assert p.locator('.card-script-watermark').count()==1
   for tab in ['history','tasks','promises','payments','documents']:
    p.locator('[data-tab='+tab+']').click();assert p.locator('.card-script-watermark').count()==0
    assert 'пока нет' in p.locator('#card-content').inner_text()
   card=api(p,'card',query=dict(person_uuid=person));cid=card['contracts'][0]['contract_uuid']
   # Import-like registry baseline, only in this newly created marked synthetic database.
   with sqlite3.connect(qa/'data/synthetic/crm-pilot.sqlite3') as conn:
    assert conn.execute("select 1 from crm_meta where key='synthetic_candidate'").fetchone()
    fields=json.loads(conn.execute('select fields_json from crm_contracts where contract_uuid=?',(cid,)).fetchone()[0]);fields.update(soft_history='Архив звонков: клиент просил перезвонить\nКонтакт подтверждён',historical_visit_comment='Архив выездов: адрес проверен',promise_date='2026-11-25',forecast_sum='0',forecast='Платёж согласован',soft_next_call_date='2026-11-23',meeting_date='2026-11-24')
    conn.execute('update crm_contracts set fields_json=? where contract_uuid=?',(json.dumps(fields,ensure_ascii=False),cid))
   p.locator('#card-refresh').click();until(p,'()=>!!document.querySelector("#card-content")')
   for tab,key,text in [('history','soft_history','Архив звонков'),('tasks','soft_next_call_date','23.11.2026'),('promises','forecast_sum','0 RUB')]:
    p.locator('[data-tab='+tab+']').click();until(p,'(key)=>!!document.querySelector(`[data-registry-field="${key}"]`)',key);actual=p.locator('[data-registry-field='+key+']').inner_text();assert text in actual,(tab,text,actual);assert p.locator('.card-script-watermark').count()==0
   for kind,payload in [('note',dict(text='Факт связи из CRM')),('task',dict(title='Перезвонить клиенту после оплаты',due_at='2026-11-23T09:00:00+04:00')),('promise',dict(amount='123.45',currency='RUB',due_at='2026-11-25T09:00:00+04:00'))]:
    latest=api(p,'card',query=dict(person_uuid=person));c=next(c for c in latest['contracts'] if c['contract_uuid']==cid)
    api(p,'command',dict(schema_version=1,operation_id=str(uuid.uuid4()),kind=kind,contract_ids=[cid],base_revisions={cid:c['revisions']},payload=payload))
   p.locator('#card-refresh').click();until(p,'()=>document.querySelector("#card-content").textContent.includes("123,45")')
   for tab,text in [('history','Факт связи из CRM'),('tasks','Перезвонить клиенту после оплаты'),('promises','123,45 RUB')]:
    p.locator('[data-tab='+tab+']').click();assert text in p.locator('#card-content').inner_text();assert p.locator('.card-script-watermark').count()==0
    assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
    p.screenshot(path=str(qa/(tab+'.png')))
   assert '25.11.2026' in p.locator('#card-content').inner_text()
   p.locator('[data-tab=tasks]').click();p.locator('[data-complete]').click();until(p,'()=>document.querySelector("#card-content").textContent.includes("Завершено")')
   # Script remains available in the call action on any tab.
   p.locator('[data-tab=history]').click();p.locator('[data-action=call]').click();p.locator('#show-script').click();assert 'Тестовый сценарий звонка' in p.locator('#script-inline').inner_text();p.locator('#cancel').click()
   p.locator('[data-tab=overview]').click();assert p.locator('.card-script-watermark').count()==1
   assert not errors,errors;results.append(dict(engine=engine,passed=True,empty_tabs=True,registry_fields=True,canonical_events=True,task_completed=True,promise_amount_and_due=True,call_script=True,output=str(qa)))
   browser.close();print(engine,'card tabs PASS',flush=True)
 finally:stop.set();thread.join(20)
(B/'RESULT.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
