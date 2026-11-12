"""Real PWA/SW/IndexedDB: synthetic courier, offline restart and exact retries."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]/'server/miniapp'
output=Path(sys.argv[1]).resolve();output.mkdir(parents=True,exist_ok=True)
state={'bodies':[],'ack':False}
contracts=[dict(contract_uuid='contract-one',external_id='001',creditor_id='Credit A',client_name='Клиент проверки',
               allowed_actions=['delivery'],fields={'address':'Проверочный адрес','phone':'-','debt_today':'1500.00'}),
           dict(contract_uuid='contract-two',external_id='002',creditor_id='Credit B',client_name='Второй клиент',
               allowed_actions=['delivery'],fields={'address':'Другой адрес','phone':'+70000000000'}),
           dict(contract_uuid='read-only',external_id='003',creditor_id='Credit C',client_name='Клиент для просмотра',
               allowed_actions=[],fields={'address':'Открытый адрес','debt_today':'2500.00'})]
class Handler(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,value,status=200):
        body=json.dumps(value,ensure_ascii=False).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers()
        try:self.wfile.write(body)
        except (ConnectionError,OSError):pass
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/api/identity':return self.reply(dict(ok=True,uid='synthetic-uid',employee='Курьер проверки'))
        if path=='/api/bootstrap':return self.reply(dict(uid='synthetic-uid',employee='Курьер проверки',persons=[],kind='agent',lang='ru',meta={},roles={'field':True}))
        if path=='/api/courier/context':return self.reply(dict(role='courier',employee_uuid='staff-one',contracts=contracts[:2],fields=[{'key':'address','title':'Адрес'},{'key':'debt_today','title':'Долг'}]))
        if path=='/api/courier/contracts':return self.reply(dict(contracts=contracts))
        if path.startswith('/api/'):return self.reply({'ok':True})
        if path.startswith('/crm-courier/assets/'):
            self.path='/courier/'+path.rsplit('/',1)[1]
        return super().do_GET()
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
        if urlsplit(self.path).path=='/api/courier/command':
            state['bodies'].append(body)
            return self.reply({'ok':state['ack']},200 if state['ack'] else 503)
        return self.reply({'ok':True})

server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':390,'height':844})
        context.add_init_script("localStorage.setItem('app_token','synthetic-courier-device')")
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        page.locator('#courier-contract').wait_for()
        page.wait_for_function('navigator.serviceWorker.controller!==null')
        page.locator('#courier-contract').select_option('contract-one')
        page.locator('#courier-name').filter(has_text='Клиент проверки').wait_for()
        page.locator('#courier-comment').fill('Черновик первого клиента')
        page.locator('#courier-address').fill('Адрес передачи')
        page.locator('#courier-contract').select_option('contract-two')
        page.wait_for_function("document.querySelector('#courier-phone').value==='+70000000000'")
        assert page.locator('#courier-comment').input_value()==''
        assert page.locator('#courier-address').input_value()==''
        page.locator('#courier-contract').select_option('contract-one')
        page.wait_for_function("document.querySelector('#courier-comment').value==='Черновик первого клиента'")
        page.locator('.courier-client-info summary').click()
        assert '1500.00' in page.locator('.courier-client-info').inner_text()
        page.screenshot(path=str(output/'courier-mobile.png'),full_page=True)
        context.set_offline(True)
        page.reload();page.locator('#courier-comment').wait_for()
        assert page.locator('#courier-comment').input_value()=='Черновик первого клиента'
        page.locator('#courier-comment').fill('Передан получателю без связи')
        page.locator('button[type=submit]').click()
        page.get_by_text('Результат сохранён',exact=True).wait_for()
        queued=page.evaluate("IDB.all('queue')")
        assert len(queued)==1 and queued[0]['kind']=='courier' and queued[0]['_owner']['uid']=='synthetic-uid'
        body=queued[0]['body'];assert body['payload']['delivery_phone']=='-' and body['payload']['delivered'] is True
        page.reload();page.locator('#courier-contract').wait_for()
        assert page.evaluate("IDB.all('queue')")[0]['body']==body
        context.set_offline(False);page.wait_for_function('!_flushing');page.evaluate('flushQueue()')
        assert len(page.evaluate("IDB.all('queue')"))==1 and state['bodies'][-1]==body
        state['ack']=True;page.evaluate('flushQueue()')
        assert page.evaluate("IDB.all('queue')")==[] and state['bodies'][-2:]==[body,body]
        page.locator('#courier-search').fill('003')
        page.wait_for_function("!!document.querySelector('option[value=\"read-only\"]')")
        page.locator('#courier-contract').select_option('read-only')
        page.get_by_text('Данные доступны для просмотра. Передача конверта не назначена.',exact=True).wait_for()
        assert page.locator('button[type=submit]').is_disabled()
        page.locator('.courier-client-info summary').click()
        assert '2500.00' in page.locator('.courier-client-info').inner_text()
        # The cached courier context is never lifted under another credential.
        page.evaluate("localStorage.setItem('app_token','another-device');prepareCourier(false)")
        assert page.evaluate('STATE.courier') is None
        assert not errors,errors
        result={'passed':True,'checks':['five fields and FIO','per-client draft separation','shared readable client details',
                'cold offline SW start','draft survives offline restart','durable owned queue','exact operation retry after 503',
                'ack removes only accepted operation','unassigned client read only','other credential cannot load courier snapshot'],
                'requests':len(state['bodies']),'errors':errors}
        (output/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False));browser.close()
finally:server.shutdown();server.server_close()
