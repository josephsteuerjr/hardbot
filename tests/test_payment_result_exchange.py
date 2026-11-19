"""Payment result, personal columns and registry round-trip without fake calls."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web as aio_web
from aiohttp.test_utils import make_mocked_request

import db
import web
from pult import enrich
from test_review_soft_integrity import world, grid, call
from test_reported_payments import payment


def revision(cid='A'):
    return db.payment_card_revision(db.person_contracts_by_soft(cid))


def stamp(hours=0):
    return (datetime.combine(db.workday.today(),datetime.min.time(),timezone.utc)
            +timedelta(hours=7+hours)).isoformat()


def save_result(monkeypatch,op='result-pay',cid='A',**extra):
    return payment(monkeypatch,op=op,cid=cid,result_status='Оплачен',
                   payment_revision=extra.pop('payment_revision',revision(cid)),**extra)


def test_result_is_atomic_selected_contract_and_does_not_create_call(world,monkeypatch):
    call(monkeypatch,'promise',values={'forecast_sum':'1500.50'})
    before=db.calls_for_person('A')
    saved=save_result(monkeypatch,cid='B')
    assert saved['ok']
    assert db.get_client('B')['soft_call_status']=='Оплачен'
    assert db.get_client('A')['soft_call_status']=='Нет контакта'
    assert [dict(r) for r in db.calls_for_person('A')]==[dict(r) for r in before]
    assert db.reported_payments_for(['B'])['B'][0]['result_status']=='Оплачен'
    assert web._person_full('A','ru',by_soft=True)['soft']['status']=='Оплачен'


def test_conflict_rolls_back_amount_and_status_and_retry_precedes_revision(world,monkeypatch):
    old=revision()
    call(monkeypatch,'changed')
    with pytest.raises(aio_web.HTTPConflict):
        save_result(monkeypatch,payment_revision=old)
    assert not db.reported_payments_for()
    saved=save_result(monkeypatch)
    call(monkeypatch,'after-payment','Рассрочка')
    retry=save_result(monkeypatch,payment_revision=old)
    assert retry['dedup'] and retry['id']==saved['id']
    assert len(db.reported_payments_for()['A'])==1
    with pytest.raises(aio_web.HTTPConflict):
        payment(monkeypatch,op='result-pay')


def test_payment_without_result_keeps_call_and_legacy_client_works(world,monkeypatch):
    call(monkeypatch,'old','Рассрочка')
    payment(monkeypatch)
    assert db.get_client('A')['soft_call_status']=='Рассрочка'
    assert db.reported_payments_for()['A'][0]['result_status'] is None


def test_offline_payment_cannot_rollback_newer_call(world,monkeypatch):
    day=db.workday.today().isoformat()
    db.save_call_result('A',100,'S1',{'soft_call_status':'Рассрочка'},'new',op_id='new-call',on_date=day,saved_at=stamp(3))
    db.record_reported_payment('old-pay','A','S1',100,paid_at=day,amount=10,currency='₽',created_at=stamp(),
                               result_status='Оплачен',expected_revision=revision())
    assert db.effective_soft_status(db.get_client('A'))=='Рассрочка'
    assert db.common_result(db.get_client('A'))['overall_result']=='Рассрочка'
    assert next(r for r in db.enrichment_rows() if r['contract_id']=='A')['soft_call_status']=='Рассрочка'


def test_old_offline_call_does_not_rollback_newer_payment_and_later_call_wins(world):
    day=db.workday.today().isoformat()
    db.record_reported_payment('paid','A','S1',100,paid_at=day,amount=10,currency='₽',created_at=stamp(2),
                               result_status='Оплачен',expected_revision=revision())
    db.save_call_result('A',100,'S1',{'soft_call_status':'Рассрочка'},'old',op_id='old-call',on_date=day,saved_at=stamp())
    assert db.effective_soft_status(db.get_client('A'))=='Оплачен'
    assert db.common_result(db.get_client('A'))['overall_result']=='Оплачен'
    db.save_call_result('A',100,'S1',{'soft_call_status':'Нет контакта'},'next',op_id='next-call',on_date=day,saved_at=stamp(3))
    assert db.effective_soft_status(db.get_client('A'))=='Нет контакта'


def test_payment_only_exports_typed_columns_and_status_without_communication_date(world,monkeypatch):
    save_result(monkeypatch)
    exported=next(r for r in db.enrichment_rows() if r['contract_id']=='A')
    assert not exported['_bot_call_completed']
    assert exported['soft_call_status']=='Оплачен' and exported['call_status']=='Оплачен'
    fields=db.export_schema()
    keys={'contract_id','soft_call_status','soft_comm_date','soft_comment','promise_date','forecast_sum','forecast',
          'extra_address','overall_result','last_payment_sum','last_payment_date'}
    columns=[f for f in fields if f['key'] in keys]
    book=[[f['title'] for f in columns],['софт']*len(columns),
          ['A' if f['key']=='contract_id' else None for f in columns]]
    plan=enrich.build_plan(book,fields,[{**exported,'_profile':'soft'}],ledger={})
    writes={columns[c]['key']:v for r,c,v,d in plan['writes']}
    assert writes['last_payment_sum']==1500.5 and isinstance(writes['last_payment_sum'],float)
    assert writes['last_payment_date']==db.workday.today()
    assert writes['soft_call_status']=='Оплачен' and writes['overall_result']=='Оплачен'
    assert 'soft_comm_date' not in writes
    for r,c,v,d in plan['writes']:
        book[r][c]=v
    assert not enrich.build_plan(book,fields,[{**exported,'_profile':'soft'}],ledger={})['writes']


def test_manual_copy_to_master_does_not_become_registry_payment(world,monkeypatch):
    payment(monkeypatch)
    world[0].update(last_payment_sum='1500.50',last_payment_date=db.workday.today().isoformat())
    assert not db.import_grid(grid(world),source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)['blocked']
    assert not db.registry_payments_for(['A'])
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM registry_payments').fetchone()[0]==0


def test_independent_registry_observation_survives_matching_manual_copy(world,monkeypatch):
    day=db.workday.today().isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE clients SET last_payment_date=?,last_payment_sum='1500.50' WHERE contract_id='A'",(day,))
        db._remember_registry_payments(conn)
        conn.commit()
    payment(monkeypatch)
    assert db.registry_payments_for(['A'])[0]['amount']==1500.5


@pytest.mark.parametrize('extra',[{'result_status':'Нет контакта'},{'result_status':'Оплачен','payment_revision':None}])
def test_invalid_result_is_rejected_before_writing(world,monkeypatch,extra):
    with pytest.raises(aio_web.HTTPBadRequest):
        payment(monkeypatch,**extra)
    assert not db.reported_payments_for()


@pytest.mark.parametrize('profile', ['hard', 'soft'])
def test_app_receipt_routes_payment_only_without_fake_call_or_bank_confirmation(world, profile):
    day = db.workday.today().isoformat()
    db.record_receipt('app-receipt', 'A', 'H1', 100, paid_at=day, amount=2500.5,
                      currency='₽', source='app', file='synthetic.jpg', sha256='a'*64)
    db.record_receipt('incomplete', 'A', 'H1', 100, source='app', file='legacy.jpg')
    assert not db.reported_payments_for(['A'])
    exported = next(r for r in db.enrichment_rows() if r['contract_id'] == 'A')
    assert exported['_reported_payment_sum'] == 2500.5
    assert not exported['_bot_call_completed']
    split = enrich.split_by_employee([exported])
    assert split['h1']['filled'] == split['soft:s1']['filled'] == 1
    schema = db.export_schema()
    columns = schema
    book = [[f['title'] for f in columns], ['сх'] * len(columns),
            ['A' if f['key']=='contract_id' else None for f in columns]]
    row = {**exported, '_profile': profile}
    plan = enrich.build_plan(book, schema, [row], ledger={}, since=day, until=day)
    expected = {'last_payment_sum','last_payment_date'} | ({'soft_comment'} if profile == 'soft' else set())
    assert {columns[c]['key'] for _,c,_,_ in plan['writes']} == expected
    for r,c,v,d in plan['writes']: book[r][c] = v
    assert not enrich.build_plan(book,schema,[row],ledger={})['writes']
    world[0].update(last_payment_sum='2500.50',last_payment_date=day)
    assert not db.import_grid(grid(world),source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)['blocked']
    assert not db.registry_payments_for(['A'])
    assert not db.calls_for_person('A')
