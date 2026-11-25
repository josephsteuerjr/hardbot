"""Native Excel + real backend HTTP + process restart; synthetic local data only."""
from pathlib import Path
import copy,json,os,re,subprocess,sys,threading,time,urllib.request,queue
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

repo=Path(__file__).resolve().parents[2];qa=Path(os.environ['HARD_ENR_QUEUE_QA_ROOT']);qa.mkdir(parents=True,exist_ok=True)
sys.path[:0]=[str(repo),str(repo/'pult'),str(repo/'server')]
os.environ['APP_DATA']=str(qa/'synthetic-server')
import db,enrich
from exchange_contract import contract_binding
from openpyxl import Workbook
import win32com.client
fields=db.export_schema();hard_fields=[f for f in fields if f['source'] in ('сх','хард')]
base=qa/'books';jobs=[]
excel=win32com.client.DispatchEx('Excel.Application');excel.Visible=False;excel.DisplayAlerts=False
try:
 for number,name in enumerate(['Test Alpha','Test Beta'],1):
  folder=base/'Таджикистан/Портфели отдела взыскания РТ/City'/name;folder.mkdir(parents=True)
  xlsx=qa/(name+'.xlsx');book=Workbook();sheet=book.active;sheet.title=enrich.SHEET_NAME
  sheet.append(['Synthetic workbook']);sheet.append([f['title'] for f in hard_fields]);sheet.append([f['source'] for f in hard_fields])
  row={'contract_id':'TEST-'+str(number),'client':'Synthetic client '+str(number),'responsible':name,'debt_total':123.45}
  sheet.append([row.get(f['key']) for f in hard_fields]);book.create_sheet('Witness')['A1']='=SUM(2,3)';book.save(xlsx)
  path=folder/(name+'.xlsb');wb=excel.Workbooks.Open(str(xlsx));wb.SaveAs(str(path),FileFormat=50);wb.Close(False)
  jobs.append(dict(path=path,employee=name,contract_id=row['contract_id']))
finally:excel.Quit()
rows=[dict(contract_id=j['contract_id'],client='Synthetic client',responsible=j['employee'],
           _bot_completed=True,visit_date='2026-11-01',call_status='Обещает оплатить',visit_comment='Synthetic native queue visit') for j in jobs]
export={'data_exchange_contract':contract_binding(),'fields':fields,'rows':copy.deepcopy(rows)};books={};seen=[];posts=[];lose=[True]
omission_mode=os.environ.get('HARD_ENR_QUEUE_OMISSION_QA')=='1'
if omission_mode:export['rows'].append({**rows[0],'contract_id':'OLD-ASSIGNMENT'})
class Admin(BaseHTTPRequestHandler):
 def reply(self,payload):
  raw=json.dumps(payload,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  seen.append(self.path);assert self.headers.get('X-Device-Token')=='synthetic-native-queue'
  if self.path=='/admin/export.json':self.reply(export)
  elif self.path=='/admin/stats':self.reply({'employees':[{'employee':j['employee'],'contracts':1} for j in jobs],'soft_employees':[]})
  else:self.reply({'ledger':{},'books':copy.deepcopy(books)})
 def do_POST(self):
  seen.append(self.path);body=json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)))
  assert self.path=='/admin/enrichment/ledger.json','Unexpected mutation '+self.path;posts.append(body)
  for e in body['entries']:books.setdefault(enrich.book_key(e['file']),{}).setdefault(e['contract_id'],{})[e['key']]={'value':e['value'],'at':'2026-11-01T00:00:00Z'}
  if lose[0]:lose[0]=False;self.connection.shutdown(2);self.connection.close();return
  self.reply({'recorded':len(body['entries'])})
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Admin);threading.Thread(target=server.serve_forever,daemon=True).start()
settings=qa/'profile/backend-settings.json';settings.parent.mkdir()
settings.write_text(json.dumps({'active':'Synthetic','profiles':{'Synthetic':{'url':f'http://127.0.0.1:{server.server_port}','device_token':'synthetic-native-queue'}},'scatterBase':str(base),'mail':{'enabled':False,'recipients':[],'schedules':[]}}),'utf-8')
tracking=qa/'tracking.json';tracking.write_text(json.dumps({'enabled':False}),'utf-8')
env=dict(os.environ,HARD_PULT_SETTINGS=str(settings),HARD_MANUAL_DATES_CONFIG=str(tracking),HARD_CLIENT_VERSION='3.2.18',HARD_CLIENT_PRODUCT='analyst',PYTHONIOENCODING='utf-8',LOCALAPPDATA=str(qa/'local-profile'))
command=[os.environ['HARD_QA_BACKEND']] if os.environ.get('HARD_QA_BACKEND') else [sys.executable,'-X','utf8','-m','pult.desk_backend']
process=None;port=None;secret=None
def start(label):
 global process,port,secret
 stderr=(qa/(label+'.private.log')).open('wb');process=subprocess.Popen(command,cwd=repo,env=env,stdout=subprocess.PIPE,stderr=stderr,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 lines=queue.Queue()
 def read_lines():
  for line in iter(process.stdout.readline,b''):lines.put(line)
 threading.Thread(target=read_lines,daemon=True).start();deadline=time.monotonic()+45
 while time.monotonic()<deadline:
  try:line=lines.get(timeout=1)
  except queue.Empty:
   if process.poll() is not None:break
   continue
  match=re.search(rb'HARD-BACKEND (\d+) (\S+)',line)
  if match:port=int(match[1]);secret=match[2].decode();break
 else:raise RuntimeError('Backend startup exceeded45s; inspect exact process and private log')
 if process.poll() is not None:raise RuntimeError('Backend exited before startup')
 (qa/'renderer.private.json').write_text(json.dumps({'port':port,'secret':secret,'master':str(qa/'master.xlsx')}),'utf-8')
def stop():
 if process and process.poll() is None:process.terminate();process.wait(timeout=20)
def call(path,body=None,expect=True):
 request=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(body or {}).encode(),headers={'Content-Type':'application/json','X-Local-Secret':secret})
 with urllib.request.urlopen(request,timeout=60) as response:value=json.load(response)
 if expect:assert value.get('ok'),value;return value['data']
 return value
def job(path,body=None):
 payload={**(body or {}),'request_id':'qa-'+str(time.time_ns())};identifier=call(path,payload)['job'];deadline=time.monotonic()+120
 while time.monotonic()<deadline:
  state=call('/job',{'id':identifier})
  if state['state']!='running':return identifier,state
  time.sleep(.1)
 raise RuntimeError('Backend job did not finish')
def sha(path):
 import hashlib
 with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def renderer(phase):
 if os.environ.get('HARD_ENR_QUEUE_RENDERER')!='1':return
 result=subprocess.run(['node','tools/test-enrichment-queue-ui.cjs',phase],cwd=repo/'hard-desk',env={**env,'HARD_ENR_QUEUE_QA_ROOT':str(qa)},capture_output=True,text=True,encoding='utf-8')
 (qa/('renderer-'+phase+'.log')).write_text(result.stdout+result.stderr,'utf-8');assert result.returncode==0,result.stdout+result.stderr
master_fields=[*fields,{'key':'responsible_soft','title':'ответственный Сотрудник СОФТ','source':'софт'}]
master=Workbook();sheet=master.active;sheet.title=enrich.SHEET_NAME;sheet.append(['Synthetic master']);sheet.append([f['title'] for f in master_fields]);sheet.append([f['source'] for f in master_fields]);
for row in rows:sheet.append([row.get(f['key']) for f in master_fields])
master.save(qa/'master.xlsx')
held=None;excel=None
try:
 start('first');excel=win32com.client.DispatchEx('Excel.Application');excel.Visible=False;excel.DisplayAlerts=False;held=excel.Workbooks.Open(str(jobs[0]['path']))
 before=sha(jobs[0]['path']);_,preview=job('/enr/preview');assert preview['state']=='done';keys=[r['key'] for r in preview['result']['matched']];assert len(keys)==2,preview
 _,checked=job('/enr/check',{'keys':keys,'since':'01.11.2026','until':'01.11.2026'});assert checked['state']=='done' and not checked['result']['conflicts']
 identifier,applied=job('/enr/apply',{'keys':keys,'since':'01.11.2026','until':'01.11.2026'});assert applied['state']=='waiting',applied
 operation=call('/enr/operation');assert operation['completed']==1 and operation['remaining']==1 and sha(jobs[0]['path'])==before
 protected=call('/reg/preview',{},False);assert not protected['ok'] and 'не завершено' in protected['error'] and not any('/import' in p for p in seen)
 done_sha=sha(jobs[1]['path']);assert len(posts)==1,'Lost ledger response must be reconciled, not re-posted'
 renderer('waiting')
 stop();held.Close(False);held=None;excel.Quit();excel=None
 start('restarted');deadline=time.monotonic()+120
 while time.monotonic()<deadline:
  state=call('/job',{'id':identifier});operation=call('/enr/operation')
  if omission_mode and operation['state']=='attention' and operation.get('missing')==1:
   assert operation.get('busy')==0 and operation['completed']==1
   renderer('attention')
   _,review=job('/enr/reconcile-preview',{'path':str(qa/'master.xlsx')});assert review['state']=='done',review
   plan=review['result'];assert len(plan['proposals'])==1 and not plan['blocked']
   call('/enr/reconcile-apply',{'token':plan['token']})
  if state['state']=='done' and operation['state']=='done':break
  time.sleep(.25)
 else:raise RuntimeError('Automatic restart recovery timed out: '+str(operation))
 assert operation['completed']==2 and operation['remaining']==0 and sha(jobs[1]['path'])==done_sha and len(posts)==2
 if omission_mode:assert operation['excluded']==1 and not any(e['contract_id']=='OLD-ASSIGNMENT' for p in posts for e in p['entries'])
 renderer('done')
 result={'stage':'verified','actual_backend':command[0],'waiting_job_persisted':True,'restart_automatically_completed':True,'completed_book_not_rewritten':True,'lost_ledger_reply_not_reposted':True,'import_blocked_before_any_remote_import':True,'ledger_posts':len(posts),'books':2,'native_excel':True,'synthetic_only':True,'operation':operation}
 (qa/'RESULT.private.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8');print(json.dumps({k:v for k,v in result.items() if k!='operation'},ensure_ascii=False),flush=True)
finally:
 if held is not None:held.Close(False)
 if excel is not None:excel.Quit()
 stop();server.shutdown()
