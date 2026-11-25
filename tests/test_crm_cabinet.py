from dataclasses import replace
import json,uuid
import pytest
from crm import cabinet,storage,store
from crm.contract import canonical,CRMError,now
from test_crm_core import crm_world,call_body


def test_monthly_registry_money_is_exact_and_a_group_call_is_counted_once(crm_world):
    w=crm_world;month=now()[:7]
    with storage.connect() as conn:
        conn.execute('INSERT INTO crm_registry_sources VALUES(?,?,?,?,?,?,?)',('source','a'*64,'b'*64,'Portfolio',now(),2,'{}'))
        for index,key in enumerate(w['contracts']):
            raw=dict(extra_columns=[dict(title='Оплаты за '+month[5:7]+'.'+month[2:4],value='10,25' if index==0 else '')])
            conn.execute('INSERT INTO crm_registry_rows VALUES(?,?,?,?)',(key,'source',index+2,canonical(raw)))
        conn.commit()
    store.command(w['member'],call_body(w))
    result=cabinet.dashboard(w['member'],month)
    assert result['stats']['call']==1 and result['stats']['processed_contracts']==2
    assert result['payments']['totals']==[dict(unit='RUB',currency='RUB',amount='10.25')]
    assert result['payments']['known_contracts']==2 and result['payments']['missing_contracts']==0
    assert len(result['payments']['rows'])==1
    # Work remains authored by the original employee after assignment transfer.
    with storage.connect() as conn:
        conn.execute('DELETE FROM crm_assignments WHERE employee_uuid=?',(w['member'].employee_uuid,));conn.commit()
    moved=cabinet.dashboard(w['member'],month)
    assert moved['stats']['call']==1 and moved['payments']['totals']==[]


def test_unknown_money_is_not_zero_and_other_staff_cabinet_is_not_shared(crm_world):
    w=crm_world;result=cabinet.dashboard(w['member'],now()[:7])
    assert result['payments']['known_contracts']==0 and result['payments']['missing_contracts']==2
    assert result['payments']['totals']==[]
    with pytest.raises(CRMError):cabinet.dashboard(replace(w['stranger'],read_all_contracts=True),now()[:7],w['member'].employee_uuid)


def test_unconfirmed_currencies_of_different_creditors_are_not_added_together(crm_world):
    w=crm_world;month=now()[:7]
    with storage.connect() as conn:
        conn.execute('INSERT INTO crm_registry_sources VALUES(?,?,?,?,?,?,?)',('source','a'*64,'b'*64,'Portfolio',now(),2,'{}'))
        for index,key in enumerate(w['contracts']):
            raw=dict(extra_columns=[dict(title='Оплаты за '+month[5:7]+'.'+month[2:4],value='100.10')])
            conn.execute('INSERT INTO crm_registry_rows VALUES(?,?,?,?)',(key,'source',index+2,canonical(raw)))
            conn.execute('UPDATE crm_contracts SET currency=NULL WHERE contract_uuid=?',(key,))
        conn.commit()
    totals=cabinet.dashboard(w['member'],month)['payments']['totals']
    assert len(totals)==2 and all(row['amount']=='100.10' and row['currency'] is None for row in totals)


def test_operations_show_call_visit_and_completed_task_content(crm_world):
    w=crm_world
    store.command(w['member'],call_body(w,values={'soft_comment':'Уточнить оплату','soft_call_status':'Нет контакта'}))
    store.command(w['member'],dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='visit',
        contract_ids=w['contracts'],base_revisions={key:{'visit':0} for key in w['contracts']},
        payload={'values':{'visit_comment':'Встреча состоялась'}}))
    store.command(w['member'],dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='task',
        contract_ids=[w['contracts'][0]],base_revisions={},
        payload={'title':'Позвонить завтра','work_kind':'call','assignee_uuid':w['member'].employee_uuid,'due_at':now()}))
    entity=next(e for e in store.card(w['member'],w['person'])['entities'] if e['kind']=='task')
    store.command(w['member'],dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='task_complete',
        contract_ids=[w['contracts'][0]],base_revisions={},payload={'entity_id':entity['entity_id'],'entity_revision':entity['revision']}))
    operations={row['kind']:row['text'] for row in cabinet.dashboard(w['member'],now()[:7])['operations']}
    assert 'Уточнить оплату' in operations['call'] and 'Нет контакта' in operations['call']
    assert 'Встреча состоялась' in operations['visit']
    assert operations['task']==operations['task_complete']=='Позвонить завтра'
