"""Exercise the shipped Python dependencies and unchanged enrichment engine on an isolated XLSB.

The disposable runtime is unchanged. Preview, check and apply use its actual
HTTP API against a synthetic loopback server and a synthetic workbook.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import python_calamine
import win32com.client
from openpyxl import Workbook

repo = Path(__file__).resolve().parents[2]
soft_payment = os.environ.get('HARD_QA_PROFILE') == 'soft_payment'
qa = Path(os.environ.get('HARD_QA_ROOT', repo / '_local/redesign-2026-10-09')) / ('soft-excel-qa' if soft_payment else 'excel-qa')
qa.mkdir(parents=True, exist_ok=True)
runtime = qa / 'runtime'
if runtime.exists():
    assert runtime.resolve().parent == qa.resolve() and not runtime.is_symlink()
    shutil.rmtree(runtime)
shutil.copytree(repo / 'hard-desk/backend-dist/hard-backend', runtime)
os.environ['APP_DATA'] = str(qa / 'server-data')
sys.path.insert(0, str(repo / 'server'))
import db
fields = [f for f in db.export_schema() if f['source'] in ('сх', 'хард')]
if soft_payment:
    hard_keys={'visit_date','who_at_address','contact_with','visit_comment','client_location','purpose_rf','agreed_payment'}
    fields=[f for f in db.export_schema() if f['key'] not in hard_keys]
columns = {f['key']: index for index, f in enumerate(fields)}
xlsx = qa / 'sample.xlsx'
book = Workbook()
sheet = book.active
sheet.title = 'Портфели'
sheet.append(['Инфо', None, None, 'Выезд'])
sheet.append([f['title'] for f in fields])
sheet.append([f['source'] for f in fields])
initial = {'contract_id':'TEST-01', 'client':'Синтетический клиент', 'debt_total':123.45}
sheet.append([initial.get(f['key']) for f in fields])
book.create_sheet('Не менять')['A1'] = 'Preserved witness'
if soft_payment:
    from openpyxl.utils import get_column_letter
    date_column=get_column_letter(columns['last_payment_date']+1)
    statistic=book.create_sheet('Статистика сотрудника')
    statistic['C1']='Взыск'
    original_formula=f"=SUMIF(Портфели!A:A,'Статистика сотрудника'!$A2,Портфели!{date_column}:{date_column})"
    statistic['C2']=original_formula
book.save(xlsx)
employee = 'QA Employee'
base = qa / 'books'
xlsb = base / 'Таджикистан/Портфели отдела взыскания РТ/City' / employee / (employee + '.xlsb')
if soft_payment:
    xlsb=base/'Узбекистан/Портфели сотрудников УЗБ/Soft'/employee/(employee+'.xlsb')
xlsb.parent.mkdir(parents=True, exist_ok=True)
excel = win32com.client.DispatchEx('Excel.Application')
try:
    excel.Visible = False
    excel.DisplayAlerts = False
    wb = excel.Workbooks.Open(str(xlsx))
    wb.SaveAs(str(xlsb), FileFormat=50)
    wb.Close(False)
finally:
    excel.Quit()
# Use the exact current exchange schema with synthetic rows only.
os.environ['APP_DATA'] = str(qa / 'server-data')
sys.path.insert(0, str(repo / 'server'))
import db
from exchange_contract import contract_binding
export = {'data_exchange_contract': contract_binding(), 'fields': db.export_schema(),
          'rows': [{'contract_id': 'TEST-01', 'responsible': employee,
                    '_bot_completed': True, 'visit_date': '2026-10-09',
                    'call_status': 'Обещает оплатить', 'visit_comment': 'Synthetic COM verification'}]}
if soft_payment:
    export['rows']=[{'contract_id':'TEST-01','responsible_soft':employee,
        '_bot_call_completed':True,'_call_day':'2026-10-12','soft_call_status':'Нет контакта',
        'soft_comm_date':'2026-10-12','soft_comment':'Synthetic completed call',
        '_bot_payment_recorded':True,'_payment_day':'2026-10-12',
        '_reported_payment_sum':1249.3,'_reported_payment_date':'2026-10-12'}]
seen = []
class SyntheticAdmin(BaseHTTPRequestHandler):
    def reply(self, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
    def do_GET(self):
        seen.append(self.path)
        assert self.headers['X-Device-Token'] == 'synthetic-excel-token'
        assert len(self.headers['X-Device-Id']) == 64
        self.reply(export if self.path == '/admin/export.json' else {'ledger': {}, 'books': {}})
    def do_POST(self):
        seen.append(self.path)
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.reply({'ok': True})
    def log_message(self, *_):
        pass
remote = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticAdmin)
threading.Thread(target=remote.serve_forever, daemon=True).start()
settings = qa / 'settings.json'
settings.write_text(json.dumps({'profiles': {'Test': {'url': f'http://127.0.0.1:{remote.server_port}',
                            'device_token': 'synthetic-excel-token'}}, 'active': 'Test'}), encoding='utf-8')
env = dict(os.environ, HARD_PULT_SETTINGS=str(settings), HARD_PULT_PROTECT_TOKENS='1', PYTHONIOENCODING='utf-8')
p = subprocess.Popen([str(runtime / 'hard-backend.exe')], env=env, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
try:
    line = p.stdout.readline().strip()
    assert line.startswith('HARD-BACKEND '), line
    _, port, secret = line.split()
    def request(path, body):
        req = urllib.request.Request(f'http://127.0.0.1:{port}'+path,
              data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'X-Local-Secret': secret})
        with urllib.request.urlopen(req, timeout=30) as response:
            value = json.load(response)
        assert value['ok'], value
        return value['data']
    def job(path, body):
        identifier = request(path, body)['job']
        deadline = time.monotonic()+90
        while time.monotonic()<deadline:
            result = request('/job', {'id':identifier})
            if result['state'] == 'done': return result['result']
            assert result['state'] == 'running', result
            time.sleep(.1)
        raise RuntimeError('Frozen enrichment job timeout: '+str(result))
    request('/enr/base', {'path': str(base)})
    preview = job('/enr/preview', {})
    assert len(preview['matched']) == 1, preview
    keys = [item['key'] for item in preview['matched']]
    check = job('/enr/check', {'keys':keys})
    assert not check['conflicts'] and not check['office'], check
    applied = job('/enr/apply', {'keys':keys})
    assert not applied['ledger_error'], applied
    if soft_payment:
        assert preview['matched'][0]['profile']=='soft'
        assert applied['toast_err'] and any('формула суммирует даты' in line for line in applied['lines']),applied
    else:
        assert not applied['toast_err'],applied
    assert '/admin/export.json' in seen and '/admin/enrichment/ledger.json' in seen
    with python_calamine.CalamineWorkbook.from_path(xlsb) as result:
        rows = result.get_sheet_by_name('Портфели').to_python()
        assert [rows[3][columns[k]] for k in ('contract_id','client','debt_total')] == ['TEST-01', 'Синтетический клиент', 123.45]
        if soft_payment:
            assert rows[3][columns['soft_comment']]=='Synthetic completed call'
            assert rows[3][columns['last_payment_sum']]==1249.3
        else:
            assert rows[3][columns['visit_comment']] == 'Synthetic COM verification'
        assert result.get_sheet_by_name('Не менять').to_python()[0][0] == 'Preserved witness'
    if soft_payment:
        app=win32com.client.DispatchEx('Excel.Application')
        try:
            app.Visible=False;app.DisplayAlerts=False
            checked=app.Workbooks.Open(str(xlsb),UpdateLinks=False,ReadOnly=True)
            assert checked.Worksheets('Статистика сотрудника').Range('C2').Formula==original_formula
            checked.Close(False)
        finally:app.Quit()
    (qa / 'RESULTS.json').write_text(json.dumps({'frozenRuntime': True, 'unchangedEnrichmentEngine': True,
        'realExcelCOM': True, 'xlsbWriteVerified': True, 'unrelatedSheetPreserved': True,
        'realFrozenHTTPPreviewCheckApply': True, 'loopbackSyntheticAdmin': True,
        'softMissingPaymentColumnRegression':soft_payment,
        'applied': applied}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Frozen runtime Excel/COM QA passed through actual HTTP preview/check/apply: isolated XLSB write and unrelated sheet preservation.')
finally:
    p.terminate()
    p.wait(timeout=15)
    remote.shutdown()
    remote.server_close()
