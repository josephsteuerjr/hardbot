from dataclasses import replace
from datetime import timedelta
import json
import pytest
from crm import storage,three_hour
from crm.contract import CRMError,instant,now,canonical
from test_crm_core import crm_world


def test_ordinary_summary_keeps_call_sms_and_contract_money_semantics(crm_world):
    w=crm_world;today=now()[:10];yesterday=(instant(now())-timedelta(days=1)).date().isoformat()
    with storage.connect() as conn:
        for index,cid in enumerate(w['contracts']):
            before=dict(client='Same client',soft_comm_date=yesterday,soft_call_status='В работе',debt_today='100.10')
            current={**before,'soft_comm_date':today,'soft_call_status':'Обещает оплатить' if index==0 else 'Рассрочка','agreed_payment':'50.25'}
            conn.execute('UPDATE crm_contracts SET baseline_json=?,fields_json=? WHERE contract_uuid=?',(canonical(before),canonical(current),cid))
        conn.commit()
    report=three_hour.report(w['owner']);row=report['sheets'][0]['rows'][0]
    assert row['current']==[0,1,2,'50.25',0,'0',0,'0',0,'0']
    assert row['yesterday']==[0,1,0,'0',2,'200.20',0,'0',0,'0']
    assert report['sheets'][1]['rows'][0]['current']==[0,0,0,'0',0,'0',0,'0',0,'0']
    assert sum(count*3 for _,count in report['groups'])+1==31
    with pytest.raises(CRMError):three_hour.report(replace(w['member'],read_all_contracts=True))


def test_incomplete_previous_day_is_not_reported_as_zero(crm_world):
    w=crm_world
    with storage.connect() as conn:
        conn.execute('UPDATE crm_contracts SET created_at=?',(now(),));conn.commit()
    row=three_hour.report(w['owner'])['sheets'][0]['rows'][0]
    assert row['yesterday']==[None]*10
