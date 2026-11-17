import copy,uuid,zipfile,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))  # package pult, not legacy pult/pult.py GUI
import pytest
from python_calamine import CalamineWorkbook
from pult.core import portable_xlsx as files,crm_reports,crm_workbook
from crm import assignments,manager_reports,exchange
from test_crm_core import crm_world
from test_crm_assignments import allocation_world


@pytest.mark.parametrize('kind',['three-hour','manager-month','manager-dynamics','shifts','table'])
def test_server_report_readback_and_retry_without_office(crm_world,tmp_path,kind,monkeypatch):
    monkeypatch.setattr(crm_reports,'excel',lambda:pytest.fail('Office must not be started'))
    w=allocation_world(crm_world);p=w['managers'][0]
    bundle=manager_reports.read(p)
    report={'three-hour':bundle['report'],'manager-month':assignments.statistics(p),'manager-dynamics':bundle,
            'shifts':dict(employees=[],shifts=[]),'table':dict(headers=['ID','Текст','Сумма'],rows=[['000123','=1+2',12.5],['000124','https://example.invalid',None]])}[kind]
    path=tmp_path/(kind+'.xlsx');first=files.write_report(report,path,kind)
    assert first['verified'] and files.write_report(report,path,kind)==first
    assert crm_reports.verify(path,report,kind)
    if kind=='table':
        with CalamineWorkbook.from_path(str(path)) as book:assert book.get_sheet_by_name('Данные').to_python()[1]==['000123','=1+2',12.5]
        with zipfile.ZipFile(path) as z:assert b'<f>' not in z.read('xl/worksheets/sheet1.xml')
    wrong=copy.deepcopy(report);wrong['different_snapshot']=True
    with pytest.raises(ValueError):files.write_report(wrong,path,kind)


def test_server_registry_roundtrip_and_exact_resume(crm_world,tmp_path,monkeypatch):
    monkeypatch.setattr(crm_workbook,'excel',lambda:pytest.fail('Office must not be started'))
    snapshot=exchange.export(crm_world['owner'],dict(operation_id=str(uuid.uuid4()),period='2026-11',target_id='general'))
    path=tmp_path/'portfolio.xlsx';result=files.write_workbook(snapshot,path)
    assert result['readback']['rows'] and crm_workbook.resume(snapshot,path)==result
    other=copy.deepcopy(snapshot);other['manifest']['export_id']='other-export'
    with pytest.raises(ValueError):crm_workbook.resume(other,path)
    with pytest.raises(ValueError):files.write_workbook(snapshot,tmp_path/'copy.xlsx',template=path)


def test_legacy_analyst_export_keeps_types_on_server(tmp_path,monkeypatch):
    from datetime import date
    from pult.core import native_exports
    monkeypatch.setenv('HARD_CRM_SERVER_XLSX','1')
    monkeypatch.setattr(native_exports,'excel',lambda:pytest.fail('Office must not be started'))
    path=tmp_path/'export.xlsx'
    native_exports._write(path,'Портфели',['КД','Дата','Сумма','Текст'],[['00015',date(2026,11,17),12.5,'=1+1']],
        [20]*4,groups=['Идентификация','Работа','Работа','Работа'],sources=['CRM']*4,date_columns={2:'dd.mm.yyyy'},numeric_columns={3:'0.00'})
    with CalamineWorkbook.from_path(str(path)) as book:
        row=book.get_sheet_by_name('Портфели').to_python()[3]
        assert row[0]=='00015' and row[1].year==2026 and row[2:]==[12.5,'=1+1']
