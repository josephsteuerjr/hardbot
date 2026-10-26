"""Ручная запись оплаты: владение, повтор, оффлайн-время и комментарий Excel."""
import asyncio,json
from datetime import datetime,timedelta,timezone
import pytest
from aiohttp import web as aio_web
from aiohttp.test_utils import make_mocked_request
import db,web
from pult import enrich
from test_review_soft_integrity import world,grid,call

def payment(monkeypatch,op='payment-one',cid='A',employee='S1',**extra):
 monkeypatch.setattr(web,'_auth',lambda request:(100,employee))
 body={'kind':'payment','rep':cid,'op_id':op,'finish':True,'paid_at':db.workday.today().isoformat(),'amount':'1500,50',**extra}
 request=make_mocked_request('POST','/api/save')
 async def payload():return body
 request.json=payload
 return json.loads(asyncio.run(web._save(request)).body)

def test_payment_is_for_selected_contract_and_keeps_registry_figures(world,monkeypatch):
 db.update_fields('A',{'paid_current':900,'last_payment_sum':'100','last_payment_date':'2026-10-01'})
 response=payment(monkeypatch,cid='B')
 assert response['ok'] and not response['dedup']
 assert set(db.reported_payments_for())=={'B'}
 saved=db.reported_payments_for(['B'])['B'][0]
 assert saved['amount']==1500.5 and saved['paid_at']==db.workday.today().isoformat()
 assert saved['employee']=='S1' and saved['source']=='manual' and not saved['file']
 assert not db.receipts_for(['A','B'])
 data=web._person_full('A','ru',by_soft=True)
 assert [p['contract_id'] for p in data['reported_payments']]==['B']
 assert data['payments'][0]['current']==900
 assert db.get_client('A')['last_payment_sum']=='100'
 assert not db.calls_for_person('A')
 assert not db.bot_call_snapshot(db.get_client('B'))

def test_retry_is_one_record_and_changed_body_conflicts(world,monkeypatch):
 first=payment(monkeypatch)
 repeat=payment(monkeypatch)
 assert repeat['dedup'] and repeat['id']==first['id']
 assert len(db.reported_payments_for(['A'])['A'])==1
 with pytest.raises(aio_web.HTTPConflict):payment(monkeypatch,amount='2000')
 with pytest.raises(aio_web.HTTPConflict):payment(monkeypatch,cid='B')

@pytest.mark.parametrize('extra',[{'amount':'0'},{'amount':'-1'},{'amount':'NaN'},{'amount':'Infinity'},
 {'amount':'1000000001'},{'amount':'1.123'},{'paid_at':'wrong'},
 {'paid_at':(db.workday.today()+timedelta(days=1)).isoformat()},{'op_id':''}])
def test_bad_data_does_not_create_payment(world,monkeypatch,extra):
 with pytest.raises(aio_web.HTTPBadRequest):payment(monkeypatch,**extra)
 assert not db.reported_payments_for()

def test_other_soft_cannot_record_or_read_payment(world,monkeypatch):
 with pytest.raises(aio_web.HTTPNotFound):payment(monkeypatch,cid='C')
 payment(monkeypatch,cid='C',employee='S2')
 assert not web._person_full('A','ru',by_soft=True)['reported_payments']

def test_security_role_cannot_record_payment(world,monkeypatch):
 db.set_manager(100,'Synthetic Security',[],['S1'],role='security')
 with pytest.raises(aio_web.HTTPForbidden):payment(monkeypatch)
 assert not db.reported_payments_for()

def test_payment_reaches_excel_without_inventing_a_call(world,monkeypatch):
 payment(monkeypatch)
 rows=db.enrichment_rows()
 exported=next(r for r in rows if r['contract_id']=='A')
 assert exported['_bot_payment_recorded'] and not exported['_bot_call_completed']
 assert '1500.5' in exported['soft_comment'] and 'КД A' in exported['soft_comment']
 assert enrich.split_by_employee(rows)['soft:s1']['filled']==1
 schema=db.export_schema()
 keys={'contract_id','soft_call_status','soft_comm_date','soft_comment','promise_date','forecast_sum','forecast','extra_address','overall_result'}
 columns=[f for f in schema if f['key'] in keys]
 book=[[f['title'] for f in columns],['софт']*len(columns),
       ['A' if f['key']=='contract_id' else None for f in columns]]
 plan=enrich.build_plan(book,schema,[{**exported,'_profile':'soft'}],ledger={},
                       since=db.workday.today().isoformat(),until=db.workday.today().isoformat())
 assert {columns[col]['key'] for _,col,_,_ in plan['writes']}=={'soft_comment'}
 assert '1500.5' in plan['writes'][0][2]
 assert [r['contract_id'] for r in db.registry_rows(employees=['S1'],visit_from=db.workday.today(),visit_to=db.workday.today())]==['A']

def test_payment_survives_import_and_does_not_duplicate_comment(world,monkeypatch):
 call(monkeypatch,'call-first',values={'soft_comment':'разговор'})
 payment(monkeypatch)
 exported=next(r for r in db.enrichment_rows() if r['contract_id']=='A')
 world[0].update(soft_comment=exported['soft_comment'],soft_call_status='Нет контакта',soft_comm_date=db.workday.today().isoformat())
 db.record_enrichment_deliveries([{'contract_id':'A','key':'soft_call_status','value':'Нет контакта',
  'visit_date':db.workday.today().isoformat(),'responsible':'S1'}])
 db.import_grid(grid(world),source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
 again=next(r for r in db.enrichment_rows() if r['contract_id']=='A')
 assert again['soft_comment'].count('Оплата №')==1
 assert len(db.reported_payments_for()['A'])==1

def test_offline_record_uses_office_event_day(world,monkeypatch):
 event=datetime.combine(db.workday.today()-timedelta(days=2),datetime.min.time(),tzinfo=timezone.utc).replace(hour=21,minute=30)
 payment(monkeypatch,paid_at=event.date().isoformat(),occurred_at=event.timestamp()*1000)
 saved=db.reported_payments_for()['A'][0]
 assert saved['event_day']==(event.date()+timedelta(days=1)).isoformat() and saved['paid_at']==event.date().isoformat()

def test_draft_cannot_record_payment(world,monkeypatch):
 with pytest.raises(aio_web.HTTPBadRequest):payment(monkeypatch,finish=False)
 assert not db.reported_payments_for()

def test_security_activity_distinguishes_manual_payment_from_receipt(world,monkeypatch):
 import control
 payment(monkeypatch)
 today=db.workday.today().isoformat()
 data=control.activity({'telegram_id':999,'employees':['S1'],'direction':''},['S1'],{'from':today,'to':today})
 event=next(e for e in data['events'] if e['kind']=='receipt')
 assert event['title']=='Записана оплата' and '1500.5' in event['detail']
 assert data['employees'][0]['calls']==0
