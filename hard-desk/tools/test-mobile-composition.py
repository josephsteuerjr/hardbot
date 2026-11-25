"""Chromium grant composition on an isolated mobile bundle and synthetic CRM."""
from pathlib import Path
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--assets',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
import asyncio,copy,json,os,sys,threading,time,uuid
from playwright.sync_api import sync_playwright
SOURCE=Path(__file__).resolve().parents[2];sys.path[:0]=[str(SOURCE),str(SOURCE/'server')]
from aiohttp import web
from crm.contract import CRMError
from crm.mobile_backend import SyntheticBackend
import crm.mobile_server as mobile_server

GRANTS=[
 dict(grant_id='grant-a',actor_uid=91001,role='employee',territory='self',capabilities=['call','note','visit'],revision=1),
 dict(grant_id='grant-b',actor_uid=91001,role='employee',territory='self',capabilities=['call','note','visit'],revision=1),
]
class GrantsBackend(SyntheticBackend):
 synthetic=False
 def __init__(self,home):
  super().__init__(home);self.crm='synthetic://mobile-composition';self.base_context=None;self.commands=[];self.revoked=set()
 def public_context(self,grant_id):
  value=copy.deepcopy(self.base_context)
  value.update(grant_id=grant_id,grants=copy.deepcopy(GRANTS),access_model=2,policy_revision=1)
  value['workspace']=dict(schema_version=1,revision=1,defaultModule='portfolio',tree=[dict(id='portfolio',title='Портфель',icon='briefcase',order=100,children=[],scopes=[]),dict(id='queue',title='Отправки',icon='outbox',order=200,children=[],scopes=[]),dict(id='connection',title='Подключение',icon='plug',order=300,children=[],scopes=[])])
  return value
 async def login(self,body):
  self.base_context=await self.rpc('employee','context',method='GET')
  grant_id=body.get('grant_id')
  if grant_id is None:raise CRMError('Выберите полномочие.','grant_required',409,dict(grants=copy.deepcopy(GRANTS)))
  return dict(role='employee',context=self.public_context(grant_id),synthetic=False,grant_id=grant_id,grants=copy.deepcopy(GRANTS),access_model=2,policy_revision=1)
 async def select_grant(self,session,grant_id):
  if grant_id in self.revoked:raise CRMError('Полномочие отозвано.','grant_revoked',403)
  if grant_id not in {grant['grant_id'] for grant in GRANTS}:raise CRMError('Полномочие отозвано.','grant_revoked',403)
  session['grant_id']=grant_id;session['context']=self.public_context(grant_id)
  return session['context']
 async def request(self,session,method,action,body=None,query=None):
  if action=='context':return self.public_context(session['grant_id'])
  result=await super().request(session,method,action,body,query)
  if action=='command':
   if session['grant_id'] in self.revoked:raise CRMError('Полномочие отозвано.','grant_revoked',403)
   self.commands.append(dict(grant_id=session['grant_id'],operation_id=body['operation_id']))
  return result

qa=args.output.resolve();qa.mkdir(parents=True,exist_ok=True);mobile_server.ASSETS=args.assets.resolve()
stop=threading.Event();state={}
async def serve():
 backend=GrantsBackend(qa/'data/synthetic');app=mobile_server.create_app(backend,qa/'data',key=os.urandom(32));state['app']=app;state['backend']=backend
 runner=web.AppRunner(app,access_log=None);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
 state['url']='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/mobile/'
 try:
  while not stop.is_set():await asyncio.sleep(.1)
 finally:await runner.cleanup()
thread=threading.Thread(target=lambda:asyncio.run(serve()));thread.start()
try:
 while 'url' not in state:time.sleep(.1)
 with sync_playwright() as pw:
  browser=pw.chromium.launch();context=browser.new_context(viewport={'width':393,'height':852},is_mobile=True,has_touch=True)
  page=context.new_page();page.goto(state['url']);page.wait_for_function('()=>window.crm&&crm.ready')
  page.evaluate('()=>crm.ready()')
  page.evaluate('()=>crm.connect({action:"login",grant_id:"grant-a"})')
  page.wait_for_function('()=>crm.state().then(s=>s.context?.grant_id==="grant-a")')
  owner=page.evaluate('()=>crm.state().then(s=>s.owner)')
  row=page.evaluate('()=>crm.api("GET","work-portfolio").then(r=>r.data.contracts[0])')
  page.evaluate('()=>crm.connect({action:"offline",value:true})')
  operation_id=str(uuid.uuid4())
  page.evaluate('input=>crm.command(input)',dict(schema_version=1,operation_id=operation_id,kind='note',contract_ids=[row['contract_uuid']],payload={'text':'grant-bound offline note'},base_revisions={row['contract_uuid']:{'note':0}}))
  page.wait_for_function('op=>crm.queue().then(r=>r.data.entries.some(e=>e.body.operation_id===op&&e.grant_id==="grant-a"))',arg=operation_id)
  page.close()
  backend=state['backend'];backend.revoked.add('grant-a');store=state['app']['mobile_sessions']
  with store.connect() as conn:ids=[row[0] for row in conn.execute('SELECT id FROM sessions')]
  for sid in ids:
   session=store.read(sid)
   if session and session.get('owner'):
    session['grant_id']='grant-b';session['context']=backend.public_context('grant-b');store.write(session,sid)
  page=context.new_page();page.goto(state['url']);page.wait_for_function('()=>crm.state().then(s=>s.context?.grant_id==="grant-b")')
  page.wait_for_timeout(1200)
  queued=page.evaluate('op=>crm.queue().then(r=>r.data.entries.find(e=>e.body.operation_id===op))',operation_id)
  assert queued['state']=='pending' and queued['grant_id']=='grant-a'
  assert backend.commands==[],backend.commands
  assert page.evaluate('()=>crm.state().then(s=>s.owner)')==owner
  page.evaluate('''async()=>{const host=document.createElement('div');host.id='grant-test';document.body.append(host);await crm.renderConnection(host);}''')
  picker=page.locator('#grant-test [data-grant-picker]');picker.locator('select').select_option('grant-a');picker.locator('button[type=submit]').click()
  page.wait_for_function('()=>document.querySelector("#grant-test #mobile-connect-result")?.textContent.includes("отозвано")')
  assert page.evaluate('()=>crm.state().then(s=>s.context?.grant_id)')=='grant-b'
  page.wait_for_timeout(500);assert backend.commands==[]
  fresh=browser.new_context();login=fresh.new_page();login.goto(state['url']);login.locator('#mobile-login').wait_for()
  login.locator('#mobile-login input[name=username]').fill('synthetic');login.locator('#mobile-login input[name=password]').fill('synthetic');login.locator('#mobile-login button[type=submit]').click()
  login.locator('[data-grant-picker] select').wait_for();assert login.locator('[data-grant-picker] option').count()==2
  fresh.close()
  evidence=dict(passed=True,owner_stable=True,offline_grant_bound=True,revoked_grant_not_replayed=True,server_grant_picker=True,commands=backend.commands)
  (qa/'chromium-composition.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
  print('Chromium mobile composition: stable owner + revoked offline grant isolation PASS',flush=True)
  browser.close()
finally:
 stop.set();thread.join(20)
