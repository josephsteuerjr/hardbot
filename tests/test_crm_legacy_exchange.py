import json
import sqlite3
import uuid
from pathlib import Path

import pytest
import db
from crm import legacy_exchange as exchange, legacy_mirror as mirror, storage, store
from crm.contract import CRMError, canonical, digest, now
from crm.registry_import import typed_values
from test_crm_core import call_body, crm_world


def world(crm_world):
    path=Path(db.DB_PATH).parent/('legacy-'+uuid.uuid4().hex+'.sqlite3')
    with sqlite3.connect(db.DB_PATH) as original,sqlite3.connect(path) as primary:original.backup(primary)
    with sqlite3.connect(path) as primary:
        primary.row_factory=sqlite3.Row
        for i,cid in enumerate(crm_world['contracts']):
            primary.execute('INSERT INTO clients(contract_id,client,partner,responsible,responsible_soft,soft_comment,visit_comment,debt_total) VALUES(?,?,?,?,?,?,?,?)',
                ('R'+str(i),'Synthetic person','P'+str(i),'Employee','Employee','Base','Base','123.45'))
        raw=[dict(r) for r in primary.execute('SELECT * FROM clients ORDER BY contract_id')]
    with storage.connect() as conn:
        conn.execute("INSERT INTO crm_meta VALUES('deployment_namespace','crm-shared-pilot-v1') ON CONFLICT(key) DO NOTHING")
        for i,cid in enumerate(crm_world['contracts']):
            fields,_=typed_values(raw[i])
            conn.execute('UPDATE crm_contracts SET legacy_id=?,external_id=?,creditor_id=?,baseline_json=?,fields_json=? WHERE contract_uuid=?',
                ('R'+str(i),'R'+str(i),'registry-partner:P'+str(i),canonical(fields),canonical(fields),cid))
            conn.execute('INSERT INTO crm_authority_rows VALUES(?,?,?)',(cid,'a'*64,canonical(raw[i])))
    return path


def primary_call(path):
    with sqlite3.connect(path) as conn:
        conn.execute('INSERT INTO calls(contract_id,agent_id,employee,created_at,values_json,contracts_json,op_id) VALUES(?,?,?,?,?,?,?)',
            ('R0',101,'Employee',now()[:10],canonical({'soft_comment':'Phone'}),canonical(['R0','R1']),str(uuid.uuid4())))
        conn.execute("UPDATE clients SET soft_comment='Phone',visit_comment='Field' WHERE contract_id='R0'")


def test_incremental_history_projection_replay_and_rebuild(crm_world):
    path=world(crm_world);primary_call(path);snapshot=exchange.capture(path)
    review=exchange.preview(snapshot)
    assert review['events']=={'call':1} and review['changed_fields']==2 and review['issues']=={}
    receipt=exchange.apply(snapshot,review)
    assert exchange.apply(snapshot,review)==receipt
    with storage.connect(readonly=True) as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0]==2
        fields=json.loads(conn.execute('SELECT fields_json FROM crm_contracts WHERE contract_uuid=?',(crm_world['contracts'][0],)).fetchone()[0])
        assert fields['soft_comment']=='Phone' and fields['visit_comment']=='Field'
        original=json.loads(conn.execute('SELECT record_json FROM crm_authority_rows WHERE contract_uuid=?',(crm_world['contracts'][0],)).fetchone()[0])
        assert original['soft_comment']=='Base'
    store.rebuild(crm_world['owner'])
    with storage.connect(readonly=True) as conn:
        assert json.loads(conn.execute('SELECT fields_json FROM crm_contracts WHERE contract_uuid=?',(crm_world['contracts'][0],)).fetchone()[0])==fields
    fresh=exchange.capture(path);review=exchange.preview(fresh)
    assert review['events']=={} and review['changed_fields']==0
    exchange.apply(fresh,review)


def test_source_history_change_is_an_issue_not_a_rewrite(crm_world):
    path=world(crm_world);primary_call(path);snapshot=exchange.capture(path)
    exchange.apply(snapshot,exchange.preview(snapshot))
    with sqlite3.connect(path) as conn:conn.execute("UPDATE calls SET employee='Changed author'")
    fresh=exchange.capture(path);review=exchange.preview(fresh)
    assert review['issues']=={'legacy_event_changed':1}
    exchange.apply(fresh,review)
    with storage.connect(readonly=True) as conn:
        event=conn.execute("SELECT payload_json FROM crm_events WHERE legacy_source='authority:calls:1'").fetchone()
        assert json.loads(event[0])['legacy_employee']=='Employee'


def test_source_and_crm_concurrency_keeps_both_results(crm_world):
    path=world(crm_world);primary_call(path);snapshot=exchange.capture(path);review=exchange.preview(snapshot)
    command=call_body(crm_world,values={'soft_comment':'CRM newer'})
    store.command(crm_world['member'],command)
    with pytest.raises(CRMError,match='изменилась'):exchange.apply(snapshot,review)
    review=exchange.preview(snapshot)
    assert review['issues']=={'legacy_field_conflict':1}
    exchange.apply(snapshot,review)
    with storage.connect(readonly=True) as conn:
        fields=json.loads(conn.execute('SELECT fields_json FROM crm_contracts WHERE contract_uuid=?',(crm_world['contracts'][0],)).fetchone()[0])
        assert fields['soft_comment']=='CRM newer' and fields['visit_comment']=='Field'


def test_mirror_commit_without_ack_recovers_and_does_not_loop(crm_world):
    path=world(crm_world)
    result=store.command(crm_world['member'],call_body(crm_world,values={'soft_comment':'CRM phone'}))
    def crash(receipt):raise RuntimeError('after SQLite commit')
    with pytest.raises(RuntimeError,match='SQLite commit'):
        mirror.deliver(path,{crm_world['employee']},after_sqlite_commit=crash)
    receipts=mirror.deliver(path,{crm_world['employee']})
    assert receipts[0]['event_id']==result['event_id'] and receipts[0]['state']=='delivered'
    assert mirror.deliver(path,{crm_world['employee']})==[]
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
        assert [r[0] for r in conn.execute('SELECT soft_comment FROM clients')]==['CRM phone','CRM phone']
    snapshot=exchange.capture(path);review=exchange.preview(snapshot)
    assert review['events']=={} and review['changed_fields']==0 and review['issues']=={}
    exchange.apply(snapshot,review)
    with storage.connect(readonly=True) as conn:assert conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0]==1


def test_mirror_does_not_overwrite_unobserved_primary_work(crm_world):
    path=world(crm_world);store.command(crm_world['member'],call_body(crm_world,values={'soft_comment':'CRM phone'}))
    with sqlite3.connect(path) as conn:conn.execute("UPDATE clients SET soft_comment='Concurrent phone' WHERE contract_id='R0'")
    receipts=mirror.deliver(path,{crm_world['employee']})
    assert receipts[0]['state']=='needs_review'
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==0
        assert conn.execute("SELECT soft_comment FROM clients WHERE contract_id='R0'").fetchone()[0]=='Concurrent phone'
    assert mirror.deliver(path,{crm_world['employee']})==[]


def test_call_and_visit_exact_scope_and_original_authors(crm_world):
    path=world(crm_world)
    call=call_body(crm_world,values={'soft_comment':'CRM phone','soft_call_status':'Нет контакта'});call['contract_ids']=crm_world['contracts'][:1]
    store.command(crm_world['member'],call)
    visit=dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='visit',contract_ids=crm_world['contracts'][:1],payload={'values':{'visit_comment':'CRM field','visit_date':now()[:10]}},base_revisions={crm_world['contracts'][0]:{'visit':0}})
    store.command(crm_world['member'],visit)
    assert mirror.deliver(path,{crm_world['stranger'].employee_uuid})==[]
    receipts=mirror.deliver(path,{crm_world['employee']})
    assert len(receipts)==2 and all(r['state']=='delivered' for r in receipts)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT soft_comment,visit_comment FROM clients WHERE contract_id='R0'").fetchone()==('CRM phone','CRM field')
        assert conn.execute("SELECT soft_comment,visit_comment FROM clients WHERE contract_id='R1'").fetchone()==('Base','Base')
        assert conn.execute('SELECT employee,agent_id,contracts_json FROM calls').fetchone()==('Employee',101,'["R0"]')
        assert conn.execute('SELECT COUNT(*) FROM visits').fetchone()[0]==1
    snapshot=exchange.capture(path);review=exchange.preview(snapshot)
    assert review['events']=={} and review['changed_fields']==0 and review['issues']=={}


def test_correction_updates_current_fields_without_counting_another_call(crm_world):
    path=world(crm_world)
    body=call_body(crm_world,values={'soft_comment':'Initial'})
    result=store.command(crm_world['member'],body)
    mirror.deliver(path,{crm_world['employee']})
    snapshot=exchange.capture(path);exchange.apply(snapshot,exchange.preview(snapshot))
    correction=dict(body,kind='correction',operation_id=str(uuid.uuid4()),correction_of=result['event_id'],reason='Correct typo',
        payload={'target_kind':'call','values':{'soft_comment':'Corrected'}},base_revisions={cid:{'call':1} for cid in body['contract_ids']})
    assert store.command(crm_world['member'],correction)['projection_status']=='applied'
    assert mirror.deliver(path,{crm_world['employee']})[0]['state']=='delivered'
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
        assert conn.execute('SELECT soft_comment FROM clients LIMIT 1').fetchone()[0]=='Corrected'

