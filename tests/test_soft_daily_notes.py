"""Office calendar, repeated contact attempts and a cumulative comment."""
import json
from datetime import datetime,timezone,timedelta

import pytest
import db
import web
import workday
from pult import enrich
from test_review_soft_integrity import world,call,grid

def moment(hour,minute=0):
    return datetime.combine(workday.today(),datetime.min.time()).replace(hour=hour,minute=minute,tzinfo=timezone(timedelta(hours=5))).timestamp()*1000

def test_same_result_later_is_a_second_attempt_but_retry_and_double_tap_are_not(world,monkeypatch):
    first=call(monkeypatch,'one',occurred_at=moment(8))
    double=call(monkeypatch,'double',occurred_at=moment(8)+1000)
    later=call(monkeypatch,'two',occurred_at=moment(8,1))
    retry=call(monkeypatch,'two',occurred_at=moment(8,1))
    assert first['applied'] and double['dedup'] and later['applied'] and retry['dedup']
    assert len(db.calls_for_person('A'))==2

def test_new_call_notes_preserve_office_text_and_all_intermediate_notes(world,monkeypatch):
    db.update_fields('A',{'soft_comment':'Synthetic office context'})
    first=call(monkeypatch,'note-one',occurred_at=moment(8),append_comment=True,values={'soft_comment':'First new note'})
    second=call(monkeypatch,'note-two',occurred_at=moment(8,1),append_comment=True,values={'soft_comment':'Second new note'})
    assert first['applied'] and second['applied']
    comment=db.get_client('A')['soft_comment']
    assert comment.startswith('Synthetic office context\n')
    assert 'First new note' in comment and 'Second new note' in comment
    assert comment.count('First new note')==comment.count('Second new note')==1
    assert db.get_client('B')['soft_comment'].endswith('Second new note')
    history=db.calls_for_person('A')
    assert {json.loads(r['values_json'])['soft_comment'] for r in history}=={'First new note','Second new note'}
    # One enrichment after both calls transports the full accumulated text.
    schema=db.export_schema()
    book=[[f['title'] for f in schema],[f['source'] for f in schema],
          ['A' if f['key']=='contract_id' else '' for f in schema]]
    row=next(r for r in db.enrichment_rows() if r['contract_id']=='A')
    row['_enrichment_profile']='soft'
    plan=enrich.build_plan(book,schema,[row],ledger={})
    col=next(i for i,f in enumerate(schema) if f['key']=='soft_comment')
    assert any(c==col and 'First new note' in str(v) and 'Second new note' in str(v) for _,c,v,_ in plan['writes'])

def test_empty_new_note_keeps_accumulated_text(world,monkeypatch):
    call(monkeypatch,'note',occurred_at=moment(8),append_comment=True,values={'soft_comment':'Keep this note'})
    call(monkeypatch,'no-note',occurred_at=moment(8,1),append_comment=True,values={'soft_comment':''})
    assert db.get_client('A')['soft_comment'].endswith('Keep this note')

def test_call_after_local_midnight_has_correct_office_day_and_import_anchor(world,monkeypatch):
    office_day=workday.today()
    happened=datetime.combine(office_day,datetime.min.time()).replace(minute=15,tzinfo=timezone(timedelta(hours=5)))
    saved=call(monkeypatch,'midnight',occurred_at=happened.timestamp()*1000,
               values={'soft_comment':'Original note'})
    assert saved['applied']
    history=db.calls_for_person('A')
    assert history[0]['created_at']==office_day.isoformat()
    assert str(history[0]['saved_at']).startswith((office_day-timedelta(days=1)).isoformat())
    exported=next(r for r in db.enrichment_rows() if r['contract_id']=='A')
    assert exported['_call_day']==office_day.isoformat()
    assert db.registry_rows(visit_from=office_day.isoformat(),visit_to=office_day.isoformat())
    db.record_enrichment_deliveries([{'contract_id':'A','key':'soft_call_status','value':'Нет контакта',
                                     'visit_date':office_day.isoformat(),'responsible':'S1'}])
    world[0].update(soft_call_status='Нет контакта',soft_comm_date=office_day.isoformat(),soft_comment='Synthetic office correction')
    preview=db.import_grid(grid(world),source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
                           allow_soft_office_edits=True,preview_only=True)
    report=db.import_grid(grid(world),source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
                          allow_soft_office_edits=True,
                          expected_soft_office_effect_digest=preview['soft_office_effect_digest'])
    assert report['office_cells']>0
    assert db.get_client('A')['soft_comment']=='Synthetic office correction'

def test_employee_admission_list_includes_soft_only_and_counts_shared_contract_once(world):
    db.update_fields('A',{'responsible':'S1'})
    rows={r['employee']:r for r in db.employees_with_bindings()}
    assert rows['S2']['hard_contracts']==0 and rows['S2']['soft_contracts']==1
    assert rows['S1']['contracts']==2 and rows['S1']['hard_contracts']==1 and rows['S1']['soft_contracts']==2
    assert rows['S1']['profiles']==['hard','soft']
