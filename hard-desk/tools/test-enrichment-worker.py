"""Real durable worker/journal behind the real renderer; only synthetic jobs."""
from pathlib import Path
import json,os,sys,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
root=Path(__file__).resolve().parents[2]
base=Path(os.environ.get('HARD_ENR_QA_ROOT',root/'_local/enrichment-jobs-headless'))
base.mkdir(exist_ok=True)
settings=base/'backend-settings.json'
settings.write_text(json.dumps({'profiles':{'Synthetic':{'url':'http://127.0.0.1:1','device_token':'synthetic'}},'active':'Synthetic','mail':{'enabled':False}}))
os.environ['HARD_PULT_SETTINGS']=str(settings)
sys.path[:0]=[str(root),str(root/'pult'),str(root/'server')]
from pult import desk_backend as backend
mode='poll';polls=0;ids=[]
def work(say):
    for i in range(12):say('Тестовая книга '+str(i+1));time.sleep(.2)
    say('Подготавливаю список решений и итог проверки…');time.sleep(1.1)
    conflicts = [{'contract_id': str(i), 'book_path': 'synthetic', 'key': 'comment', 'row': [str(i)]}
                 for i in range(190)]
    return {'lines':['HEADLESS_ENRICH_COMPLETED'], 'conflicts':conflicts, 'office':[]}
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        global mode,polls
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or '{}')
        if self.path=='/scenario':mode=body['mode'];polls=0;result={'ok':True}
        elif self.path in ('/enr/state', '/enr/base'):
            result={'base': body['path'] if self.path == '/enr/base' else 'synthetic',
                    'since':'01.01.2026', 'until':'02.01.2026', 'writes':True,
                    'conflict_columns':[['contract_id', 'Договор']],
                    'decision_labels':{'file':'Оставить в книге','server':'Записать сервер'}}
        elif self.path=='/enr/preview':
            def preview(say):return {'pairs':[],'warning':'','matched':[{'key':'synthetic','employee':'Тестовый сотрудник','note':'synthetic','profile':'hard'}]}
            result={'job':backend.start_job('enr_preview',preview,request_id=body['request_id'],fingerprint='synthetic-preview')}
        elif self.path=='/enr/check':
            jid=backend.start_job('enr_check',work,request_id=body['request_id'],fingerprint='synthetic-check');ids.append(jid);result={'job':jid}
        elif self.path=='/job':
            polls+=1
            if mode=='poll' and len(ids) and body['id']==ids[-1] and polls==2:
                self.reply({'ok':False,'error':'Synthetic interrupted local response'},status=503);return
            result=backend.job_state(body['id'])
        elif self.path=='/inspect':result={'jobs':[backend.job_state(x) for x in ids]}
        else:result={}
        self.reply({'ok':True,'data':result})
    def reply(self,payload,status=200):
        raw=json.dumps(payload,ensure_ascii=False).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print('ENR-FIXTURE '+str(server.server_port),flush=True)
server.serve_forever()
