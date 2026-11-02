"""Real local Excel, frozen source, local durable state and synthetic ledger.

No production URL, personal workbook, real profile or mail is used here.
"""
import copy
import json
import sys
import time
from pathlib import Path

import pytest
from openpyxl import Workbook,load_workbook

from pult.core.enrichment_queue import Service,file_sha
from pult.core import enrichment as en
import enrich

pytestmark=pytest.mark.skipif(sys.platform!='win32',reason='Native Excel COM validation')

FIELDS=[
 {'key':'contract_id','title':'Номер КД / ID'},
 {'key':'client','title':'Клиент'},
 {'key':'visit_date','title':'Дата выезда','date':True},
 {'key':'call_status','title':'Статус звонка'},
 {'key':'overall_result','title':'Общий результат'},
 {'key':'visit_comment','title':'Комментарий текущего месяца выезда'},
 {'key':'extra_address','title':'Доп. Адрес/Доп.Номер'},
 {'key':'contact_with','title':'С кем был контакт?'},
 {'key':'agreed_payment','title':'Договорились на оплату?'},
 {'key':'who_at_address','title':'Кто был на адресе?'},
 {'key':'client_location','title':'Местоположение клиента'},
 {'key':'purpose_rf','title':'Цель приезда в РФ'},
 {'key':'call_comment','title':'комментарий звонка'},
]


class Ledger:
 def __init__(self):self.books={};self.posts=[];self.lose_reply=False
 def __call__(self,method,path,body=None,timeout=120):
  if method=='GET':return {'ledger':{},'books':copy.deepcopy(self.books)}
  assert path=='/admin/enrichment/ledger.json'
  self.posts.append(copy.deepcopy(body))
  for entry in body['entries']:
   self.books.setdefault(enrich.book_key(entry['file']),{}).setdefault(entry['contract_id'],{})[entry['key']]={'value':entry['value'],'at':'2026-11-01T00:00:00Z'}
  if self.lose_reply:self.lose_reply=False;raise TimeoutError('Synthetic reply lost after commit')
  return {'recorded':len(body['entries'])}


def fixture(tmp_path,name='Alpha',*,old=None,bad_date=None):
 path=tmp_path/(name+'.xlsx');book=Workbook();sheet=book.active;sheet.title=enrich.SHEET_NAME
 fields=copy.deepcopy(FIELDS)
 fields.insert(2,{'key':'excel_visit_edited_on','title':'Дата ручной правки','source':'хард'})
 sheet.append(['Synthetic workbook']);sheet.append([f['title'] for f in fields]);sheet.append(['сх']*len(fields))
 initial={'contract_id':name,'client':'Synthetic '+name,'overall_result':old,'excel_visit_edited_on':bad_date}
 sheet.append([initial.get(f['key']) for f in fields]);book.create_sheet('Witness')['A1']='=SUM(2,3)';book.save(path)
 row={'contract_id':name,'responsible':name,'visit_date':'2026-11-01','_bot_completed':True,
      'call_status':'Обещает оплатить','visit_comment':'Synthetic visit'}
 return {'employee':name,'path':str(path),'rows':[row]},fields


@pytest.fixture(autouse=True)
def no_real_tracking(tmp_path,monkeypatch):
 config=tmp_path/'tracking.json';config.write_text(json.dumps({'enabled':False}),'utf-8')
 monkeypatch.setenv('HARD_MANUAL_DATES_CONFIG',str(config))
 monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local-profile'))
 # The queue's production ten-second backoff is unnecessary in native tests.
 import pult.core.enrichment_queue as queue
 monkeypatch.setattr(queue,'RETRY_SECONDS',0)


def tracking(tmp_path,monkeypatch,jobs):
 config=tmp_path/'tracking.json';config.write_text(json.dumps({'enabled':True,'books':[j['path'] for j in jobs], 'journal':str(tmp_path/'journal')}),'utf-8')
 monkeypatch.setenv('HARD_MANUAL_DATES_CONFIG',str(config))


def value(job,key):
 book=load_workbook(job['path'],data_only=False);sheet=book[enrich.SHEET_NAME]
 column=next(c.column for c in sheet[2] if c.value==next(f['title'] for f in FIELDS if f['key']==key))
 result=sheet.cell(4,column).value;assert book['Witness']['A1'].value=='=SUM(2,3)';book.close();return result


def test_open_excel_book_waits_and_restart_only_delivers_remaining_book(tmp_path):
 import win32com.client
 a,fields=fixture(tmp_path,'Alpha');b,_=fixture(tmp_path,'Beta');ledger=Ledger()
 excel=win32com.client.DispatchEx('Excel.Application');excel.Visible=False;excel.DisplayAlerts=False
 opened=excel.Workbooks.Open(a['path'])
 try:
  before=file_sha(a['path']);service=Service(tmp_path/'queue',lambda:'owner',ledger)
  state=service.submit('native-busy',[a,b],fields,'01.11.2026','01.11.2026',[])
  assert state['completed']==1 and state['remaining']==1 and state['state']=='waiting'
  assert file_sha(a['path'])==before and excel.Workbooks.Count==1
  done_sha=file_sha(b['path']);posts=len(ledger.posts)
  with pytest.raises(RuntimeError,match='не завершено'):service.require_complete()
 finally:opened.Close(False);excel.Quit()
 restarted=Service(tmp_path/'queue',lambda:'owner',ledger)
 state=restarted.process()
 assert state['state']=='done' and state['completed']==2 and not state['remaining']
 assert file_sha(b['path'])==done_sha and len(ledger.posts)==posts+1
 assert value(a,'visit_comment')=='Synthetic visit';restarted.require_complete()


def test_lost_ledger_response_is_checked_without_rewriting_workbook(tmp_path,monkeypatch):
 job,fields=fixture(tmp_path);ledger=Ledger();ledger.lose_reply=True
 original=enrich._replace_on_share;writes=[]
 def publish(*args):writes.append(args);return original(*args)
 monkeypatch.setattr(enrich,'_replace_on_share',publish)
 service=Service(tmp_path/'queue',lambda:'owner',ledger)
 state=service.submit('lost-ledger',[job],fields,'01.11.2026','01.11.2026',[])
 assert state['state']=='done' and len(writes)==1 and len(ledger.posts)==1
 before=file_sha(job['path']);assert Service(tmp_path/'queue',lambda:'owner',ledger).process()['state']=='done'
 assert file_sha(job['path'])==before and len(writes)==1 and len(ledger.posts)==1


def test_crash_after_atomic_replace_recovers_provenance_and_does_not_republish(tmp_path,monkeypatch):
 job,fields=fixture(tmp_path);tracking(tmp_path,monkeypatch,[job]);ledger=Ledger()
 original=enrich._replace_on_share;writes=[]
 def crash(*args):
  original(*args);writes.append(args);raise SystemExit('Synthetic process termination after publication')
 monkeypatch.setattr(enrich,'_replace_on_share',crash)
 service=Service(tmp_path/'queue',lambda:'owner',ledger)
 with pytest.raises(SystemExit):service.submit('crash-after-replace',[job],fields,'01.11.2026','01.11.2026',[])
 before=file_sha(job['path']);assert len(writes)==1 and not ledger.posts
 monkeypatch.setattr(enrich,'_replace_on_share',original)
 restarted=Service(tmp_path/'queue',lambda:'owner',ledger);state=restarted.process()
 assert state['state']=='done' and file_sha(job['path'])==before and len(ledger.posts)==1
 from pult.core.manual_shared import SharedEngine
 result=SharedEngine(tmp_path/'journal',tmp_path/'observer').scan(job['path'])
 assert result['writes']==0 and result['pending']==0


def test_invalid_date_identifies_cell_preserves_file_and_blocks_import(tmp_path,monkeypatch):
 bad,fields=fixture(tmp_path,'Bad',bad_date='ra');good,_=fixture(tmp_path,'Good');tracking(tmp_path,monkeypatch,[bad,good]);ledger=Ledger()
 before=file_sha(bad['path']);service=Service(tmp_path/'queue',lambda:'owner',ledger)
 state=service.submit('invalid-cell',[bad,good],fields,'01.11.2026','01.11.2026',[])
 assert state['state']=='attention' and state['completed']==1 and state['remaining']==1
 item=next(b for b in state['books'] if b['state']!='done')
 assert 'C4' in item['message'] and 'ra' in item['message'] and file_sha(bad['path'])==before
 with pytest.raises(RuntimeError,match='не завершено'):service.require_complete()
 workbook=load_workbook(bad['path']);workbook[enrich.SHEET_NAME]['C4']=None;workbook.save(bad['path']);workbook.close()
 service.retry(state['id']);assert service.process()['state']=='done'


@pytest.mark.parametrize('with_delivery_history',[False,True])
@pytest.mark.parametrize('decision',['server','file'])
def test_old_choice_does_not_approve_new_office_value(tmp_path,with_delivery_history,decision):
 import win32com.client
 job,fields=fixture(tmp_path,old='Previous office value');ledger=Ledger()
 if with_delivery_history:
  ledger.books[enrich.book_key(job['path'])]={'Alpha':{'call_status':{'value':'Обещает оплатить','at':'2026-10-31T00:00:00Z'}}}
 preview=enrich.preview_many([job],fields,ledger={'_books':ledger.books},since='01.11.2026',until='01.11.2026')
 ordinary,office=en.collect_conflicts(preview)
 choices=[{**c,'decision':decision} for c in ordinary+office]
 excel=win32com.client.DispatchEx('Excel.Application');excel.Visible=False;excel.DisplayAlerts=False;book=excel.Workbooks.Open(job['path'])
 try:
  service=Service(tmp_path/'queue',lambda:'owner',ledger)
  state=service.submit('stale-choice',[job],fields,'01.11.2026','01.11.2026',choices);assert state['remaining']==1
  col=next(i+1 for i,f in enumerate([*FIELDS[:2],{'key':'date'},*FIELDS[2:]]) if f['key']=='overall_result')
  book.Worksheets(enrich.SHEET_NAME).Cells(4,col).Value='New office value';book.Save()
 finally:book.Close(False);excel.Quit()
 before=file_sha(job['path']);state=service.process()
 assert state['state']=='attention' and file_sha(job['path'])==before and not ledger.posts
 item=state['books'][0];assert item['conflicts'] and 'новые изменения' in item['message']
 decisions=[{'book_path':job['path'],'contract_id':c['contract_id'],'key':c['key'],'decision':'file'} for c in item['conflicts']]
 service.retry(state['id'],decisions,item['review_token']);assert service.process()['state']=='done'
 assert value(job,'overall_result')=='New office value'


def test_changing_profile_does_not_resume_previous_owners_queue(tmp_path):
 job,fields=fixture(tmp_path);ledger=Ledger();owner=['owner-a'];service=Service(tmp_path/'queue',lambda:owner[0],ledger)
 run=service.store.create('scope',owner[0],{'jobs':[job],'fields':fields,'since':'01.11.2026','until':'01.11.2026'},[])
 before=file_sha(job['path']);owner[0]='owner-b'
 assert service.current() is None
 with pytest.raises(ValueError,match='Вход изменился'):service.process(run)
 assert file_sha(job['path'])==before and not ledger.posts


def test_missing_contract_is_visible_and_never_counted_as_delivered(tmp_path):
 job,fields=fixture(tmp_path);job['rows'].append({**job['rows'][0],'contract_id':'MISSING-IN-BOOK'})
 ledger=Ledger();before=file_sha(job['path']);service=Service(tmp_path/'queue',lambda:'owner',ledger)
 state=service.submit('missing-contract',[job],fields,'01.11.2026','01.11.2026',[])
 assert state['state']=='attention' and state['completed']==0 and state['remaining']==1
 assert 'MISSING-IN-BOOK' in state['books'][0]['message'] and file_sha(job['path'])==before and not ledger.posts


def test_restart_after_last_book_commit_finishes_summary_without_rewriting(tmp_path,monkeypatch):
 job,fields=fixture(tmp_path);ledger=Ledger();service=Service(tmp_path/'queue',lambda:'owner',ledger)
 original=service.store.save
 def crash_at_summary(run):
  if run['state']=='done':raise SystemExit('Crash before final run summary')
  return original(run)
 monkeypatch.setattr(service.store,'save',crash_at_summary)
 with pytest.raises(SystemExit):service.submit('last-book-committed',[job],fields,'01.11.2026','01.11.2026',[])
 before=file_sha(job['path']);restarted=Service(tmp_path/'queue',lambda:'owner',ledger)
 assert restarted.ready(restarted.current())
 assert restarted.process()['state']=='done' and file_sha(job['path'])==before and len(ledger.posts)==1
 restarted.require_complete()


def test_excel_rpc_failure_before_publication_waits_then_retries_safely(tmp_path,monkeypatch):
 job,fields=fixture(tmp_path);ledger=Ledger();original=enrich.enrich_many
 monkeypatch.setattr(enrich,'enrich_many',lambda *a,**k:[{'employee':job['employee'],'file':job['path'],'not_attempted':True,'error':"(-2147023174, 'Сервер RPC недоступен.', None, None)"}])
 before=file_sha(job['path']);service=Service(tmp_path/'queue',lambda:'owner',ledger)
 state=service.submit('rpc-before-write',[job],fields,'01.11.2026','01.11.2026',[])
 assert state['state']=='waiting' and 'не начиналась' in state['books'][0]['message'] and not ledger.posts and file_sha(job['path'])==before
 monkeypatch.setattr(enrich,'enrich_many',original)
 assert Service(tmp_path/'queue',lambda:'owner',ledger).process()['state']=='done' and len(ledger.posts)==1


def test_unplanned_other_sheet_change_stops_before_publication(tmp_path,monkeypatch):
 job,fields=fixture(tmp_path);ledger=Ledger();before=file_sha(job['path']);original=enrich._write_cells
 def corrupt_other_sheet(sheet,writes,*args,**kwargs):
  result=original(sheet,writes,*args,**kwargs);sheet.book.sheets['Witness'].range('A1').value='Unexpected';return result
 monkeypatch.setattr(enrich,'_write_cells',corrupt_other_sheet)
 service=Service(tmp_path/'queue',lambda:'owner',ledger)
 state=service.submit('unplanned-cell',[job],fields,'01.11.2026','01.11.2026',[])
 assert state['state']=='attention' and 'Witness!A1' in state['books'][0]['message']
 assert file_sha(job['path'])==before and not ledger.posts


def test_multiple_books_share_one_native_excel_session(tmp_path,monkeypatch):
 from contextlib import contextmanager
 first,fields=fixture(tmp_path,'First');second,_=fixture(tmp_path,'Second');calls=[];original=enrich.open_excel
 @contextmanager
 def counted(*args,**kwargs):
  calls.append('start')
  with original(*args,**kwargs) as app:yield app
 monkeypatch.setattr(enrich,'open_excel',counted)
 ledger=Ledger();service=Service(tmp_path/'queue',lambda:'owner',ledger)
 state=service.submit('one-excel-session',[first,second],fields,'01.11.2026','01.11.2026',[])
 assert state['state']=='done' and state['completed']==2 and len(ledger.posts)==2 and calls==['start']


def test_reconciled_missing_rows_do_not_block_other_native_writes(tmp_path):
 from pult.core.enrichment_queue import file_sha
 job,fields=fixture(tmp_path);job['profile']='hard';job['rows'].append({**job['rows'][0],'contract_id':'OLD-ASSIGNMENT'})
 ledger=Ledger();service=Service(tmp_path/'queue',lambda:'owner',ledger)
 state=service.submit('partial-book',[job],fields,'01.11.2026','01.11.2026',[])
 assert state['state']=='attention' and state['busy']==0
 path=tmp_path/'master.xlsx';path.write_bytes(b'synthetic approved master')
 book=dict(path=str(path),file_sha256=file_sha(path),grid=[['Номер КД / ID','ответственный Сотрудник','ответственный Сотрудник СОФТ'],['сх','хард','софт'],['OLD-ASSIGNMENT','New owner','']])
 plan=service.preview_reconciliation(book);service.apply_reconciliation(plan)
 state=service.process()
 assert state['state']=='done' and state['excluded']==1 and state['remaining']==0 and len(ledger.posts)==1
 assert all(e['contract_id']=='Alpha' for e in ledger.posts[0]['entries'])
