"""Actual failure shapes: stale master after delivery and missing payments column."""
import json
from datetime import datetime, time, timezone, timedelta
from types import SimpleNamespace
import pytest
import db
from pult import enrich, daily_cycle
from pult.core import enrichment as en
from test_review_soft_integrity import world, call, grid


def _deliver_current(cid='A'):
    row=next(r for r in db.enrichment_rows() if r['contract_id']==cid)
    db.record_enrichment_deliveries([{'contract_id':cid,'key':key,'value':row[key],
        'visit_date':row['_call_day'],'responsible':'S1','file':'U:/Synthetic/Soft.xlsb'}
        for key in ('soft_call_status','soft_comm_date','soft_comment')])
    return row


@pytest.mark.parametrize('same_day', [False,True])
def test_delivery_to_personal_book_does_not_authorize_stale_master(world,monkeypatch,same_day):
    call(monkeypatch,'current',occurred_at=datetime.combine(db.workday.today()-timedelta(days=1),time(8),tzinfo=timezone.utc).timestamp()*1000,values={'soft_comment':'Current result','forecast_sum':'12000'})
    current=_deliver_current()
    world[0].update(soft_call_status='Нет контакта',soft_comment='Stale master note',
                    soft_comm_date=current['_call_day'] if same_day else (db.workday.today()-timedelta(days=3)).isoformat(),
                    forecast_sum='300')
    report=db.import_grid(grid(world),source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not report['blocked'] and report['soft_office_applied']=={}
    assert db.get_client('A')['soft_comment']=='Current result'
    assert db.get_client('A')['forecast_sum']=='12000'
    assert db.get_client('A')['call_office_data'] is None


def test_explicit_soft_edit_requires_exact_fresh_preview_and_keeps_call_history(world,monkeypatch):
    call(monkeypatch,'current',occurred_at=datetime.combine(db.workday.today()-timedelta(days=1),time(8),tzinfo=timezone.utc).timestamp()*1000,values={'soft_comment':'Current result'})
    current=_deliver_current()
    original=[dict(r) for r in db.calls_for_person('A')]
    world[0].update(soft_call_status='Нет контакта',soft_comm_date=current['_call_day'],soft_comment='Office correction',excel_call_edited_on=db.workday.today().isoformat())
    args={'source_kind':db.IMPORT_SOURCE_PUBLISHED_MASTER,'allow_soft_office_edits':True}
    rejected=db.import_grid(grid(world),**args)
    assert rejected['blocked']
    preview=db.import_grid(grid(world),preview_only=True,**args)
    assert preview['soft_office_applied']['A']['soft_comment']['after']=='Office correction'
    changed=[*world];changed[0]={**world[0],'soft_comment':'Different correction'}
    stale=db.import_grid(grid(changed),expected_soft_office_effect_digest=preview['soft_office_effect_digest'],**args)
    assert stale['blocked'] and stale['preview_stale']
    assert db.get_client('A')['soft_comment']=='Current result'
    applied=db.import_grid(grid(world),expected_soft_office_effect_digest=preview['soft_office_effect_digest'],**args)
    assert not applied['blocked']
    assert db.get_client('A')['soft_comment']=='Office correction'
    assert [dict(r) for r in db.calls_for_person('A')]==original


def test_missing_confirmed_payment_column_warns_without_blocking_fact_plan(monkeypatch):
    cells={'C1':SimpleNamespace(value='Взыск')}
    for i in range(2,7):
        cells[f'C{i}']=SimpleNamespace(formula=f"=SUMIF(Портфели!A:A,'Статистика сотрудника'!$A{i},Портфели!Z:Z)")
    sheet=SimpleNamespace(name='Статистика сотрудника',range=lambda cell:cells[cell])
    wb=SimpleNamespace(sheets=[sheet])
    monkeypatch.setattr(enrich,'find_header',lambda grid,fields:(0,{'last_payment_date':25}))
    warnings=[];before=[cells[f'C{i}'].formula for i in range(2,7)]
    assert enrich._payment_statistics_plan(wb,[['Partner']],[],
        [{'_bot_payment_recorded':True,'_payment_day':'2026-10-12','_profile':'soft'}],
        '12.10.2026','12.10.2026',warnings=warnings)==[]
    assert len(warnings)==5
    assert [cells[f'C{i}'].formula for i in range(2,7)]==before
    result={'employee':'Synthetic','file':'Synthetic.xlsb','cells_written':3,'statistics_warnings':warnings}
    assert any('формула суммирует даты' in line for line in en.report_lines([result]))
    assert en.toast_text([result])[1]


def test_soft_office_preview_is_bound_to_latest_call_event_even_for_same_values(world,monkeypatch):
    yesterday=db.workday.today()-timedelta(days=1)
    stamp=datetime.combine(yesterday,time(8),tzinfo=timezone.utc).timestamp()*1000
    first=call(monkeypatch,'first-attempt',occurred_at=stamp,values={'soft_comment':'Same current result'})
    assert first['applied']
    current=_deliver_current()
    world[0].update(soft_call_status='Нет контакта',soft_comm_date=current['_call_day'],soft_comment='Office correction',excel_call_edited_on=db.workday.today().isoformat())
    options={'source_kind':db.IMPORT_SOURCE_PUBLISHED_MASTER,'allow_soft_office_edits':True}
    preview=db.import_grid(grid(world),preview_only=True,**options)
    later=call(monkeypatch,'later-attempt',occurred_at=stamp+60000,values={'soft_comment':'Same current result'})
    assert later['applied']
    result=db.import_grid(grid(world),expected_soft_office_effect_digest=preview['soft_office_effect_digest'],**options)
    assert result['blocked'] and result['preview_stale']
    assert db.get_client('A')['soft_comment']=='Same current result'


def test_person_sibling_conflicting_corrections_are_not_order_dependent(world,monkeypatch):
    call(monkeypatch,'current',occurred_at=datetime.combine(db.workday.today()-timedelta(days=1),time(8),tzinfo=timezone.utc).timestamp()*1000,values={'soft_comment':'Original'})
    current=_deliver_current()
    _deliver_current('B')
    world[0].update(soft_call_status='Нет контакта',soft_comm_date=current['_call_day'],soft_comment='First correction',excel_call_edited_on=db.workday.today().isoformat())
    world[1].update(soft_call_status='Нет контакта',soft_comm_date=current['_call_day'],soft_comment='Second correction',excel_call_edited_on=db.workday.today().isoformat())
    preview=db.import_grid(grid(world),source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
                           allow_soft_office_edits=True,preview_only=True)
    assert preview['blocked'] and preview['office_conflicts']
    for cid in ('A','B'):
        assert db.bot_call_snapshot(db.get_client(cid))['soft_comment']=='Original'


def test_daily_dry_export_uses_employee_name_for_soft_profile(monkeypatch,tmp_path):
    folder=tmp_path/'Soft'/'Synthetic';folder.mkdir(parents=True)
    (folder/'Synthetic.xlsb').write_bytes(b'synthetic')
    monkeypatch.setattr(daily_cycle,'api',lambda *a,**k:{'rows':[{'responsible_soft':'Synthetic',
        '_bot_call_completed':True,'_call_day':'2026-10-12','soft_call_status':'Нет контакта'}]})
    monkeypatch.setattr(daily_cycle,'folder_map',lambda base:{'synthetic':[{'path':folder,'kind':'soft'}]})
    monkeypatch.setattr(en,'workbook_profile',lambda path:'soft')
    # daily_cycle imports top-level core; use its same profile detector.
    import core.enrichment as legacy_en
    monkeypatch.setattr(legacy_en,'workbook_profile',lambda path:'soft')
    assert '1 файлов' in daily_cycle.step_export({},str(tmp_path),True,lambda s:None)
