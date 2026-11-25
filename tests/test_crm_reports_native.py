"""Windows Excel only; outputs and inputs are synthetic, in pytest's own folder."""
import copy
from datetime import datetime,timedelta,timezone
import os
from pathlib import Path
import pytest
from crm import three_hour,shifts
from pult.core import crm_reports
from test_crm_core import crm_world

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Native Windows Excel required')

def test_administrator_table_preserves_literal_text_and_numeric_values(tmp_path):
    from python_calamine import CalamineWorkbook
    report={'headers':['Сотрудник','Комментарий','Количество'], 'rows':[['Тест','=1+1',3],['Другой','00125',0]]}
    output=tmp_path/'admin-table.xlsx'
    assert crm_reports.write(report,output,'table')['verified']
    rows=CalamineWorkbook.from_path(str(output)).get_sheet_by_name('Данные').to_python()
    assert rows[1]==['Тест','=1+1',3] and rows[2]==['Другой','00125',0]

def test_mail_summary_reads_native_excel_bytes(tmp_path):
    from pult.core.mailer import summary_from_book
    output=tmp_path/'mail-summary.xlsx'
    with crm_reports.excel() as app:
        book=app.books.add();sheet=book.sheets[0];sheet.name='Сводка'
        sheet.range('A3').value=[['Сотрудник','Посещено сегодня','Оплачен сегодня'],['Тест',3,2],['Итого',3,2]]
        book.save(str(output));book.close()
    summary=dict(summary_from_book(output.read_bytes()))
    assert summary['Оплатили']==2

def test_legacy_administrator_exports_use_native_excel(tmp_path):
    from datetime import datetime
    from python_calamine import CalamineWorkbook
    from pult.core.export import write_registry_xlsx,write_markers_xlsx
    fields=[dict(key='client',title='Клиент',block='Клиент',source='Общий'),dict(key='contract_id',title='КД',block='Договор',source='Общий'),dict(key='debt',title='Долг',block='Договор',source='Общий',num=True)]
    output=tmp_path/'legacy-register.xlsx'
    write_registry_xlsx(str(output),fields,[dict(client='=1+1',contract_id='00123',debt='125.50')])
    row=CalamineWorkbook.from_path(str(output)).get_sheet_by_name('Портфели').to_python()[3]
    assert row==['=1+1','00123',125.5]
    markers=tmp_path/'markers.xlsx'
    write_markers_xlsx(str(markers),[dict(created_at='2026-11-15T12:13:14',kind='office',telegram_id=123,rep='0001',client='=2+2',address='Тестовый адрес',label='Тест',lat=1.5,lon=2.25)])
    row=CalamineWorkbook.from_path(str(markers)).get_sheet_by_name('Метки').to_python()[1]
    assert row[0]==datetime(2026,11,15,12,13,14) and row[3:5]==['0001','=2+2'] and row[7:]==[1.5,2.25]


def test_manager_month_and_dynamics_exports(crm_world,tmp_path):
    from crm import manager_reports,assignments
    from test_crm_assignments import allocation_world
    from python_calamine import CalamineWorkbook
    w=allocation_world(crm_world);p=w['managers'][0]
    report=manager_reports.read(p)
    for kind,data in [('manager-dynamics',report),('manager-month',assignments.statistics(p))]:
        output=tmp_path/(kind+'.xlsx');receipt=crm_reports.write(data,output,kind)
        assert receipt['verified']
        book=CalamineWorkbook.from_path(str(output))
        assert len(book.sheet_names)==3
        if kind=='manager-month':
            rows=book.get_sheet_by_name('Портфель').to_python()
            assert rows[1][:2]==['RUB',246.90]
        else:
            rows=book.get_sheet_by_name('Динамика звонков').to_python()
            assert len(rows[0])==21 and rows[1][2]==''


def test_three_hour_native_calculation_readback_and_unknown_acknowledgement(crm_world,tmp_path):
    report=three_hour.report(crm_world['owner'])
    for sheet in report['sheets']:
        sheet['rows'][0]['yesterday']=[1,2,3,'40.10',5,'60.20',7,'80.30',9,'100.40']
        sheet['rows'][0]['current']=[2,4,6,'80.20',10,'120.40',14,'160.60',18,'200.80']
    output=tmp_path/'three-hour.xlsx'
    first=crm_reports.write(report,output,'three-hour')
    assert first['verified'] and len(first['sha256'])==64
    assert crm_reports.write(report,output,'three-hour')==first
    other=copy.deepcopy(report);other['sheets'][0]['rows'][0]['current'][0]+=1
    with pytest.raises(ValueError):crm_reports.write(other,output,'three-hour')
    from python_calamine import CalamineWorkbook
    book=CalamineWorkbook.from_path(str(output));data=book.get_sheet_by_name('Статистика звонки').to_python()
    assert len(data[0])==31 and data[2][3]==2
    # A saved data edit cannot be accepted as recovery of the captured report.
    with crm_reports.excel() as app:
        workbook=app.books.open(str(output));workbook.sheets[0].range('C3').value=99;workbook.save();workbook.close()
    with pytest.raises(ValueError):crm_reports.write(report,output,'three-hour')


def test_shift_native_saved_totals_match_report(crm_world,tmp_path):
    report=shifts.period_report(crm_world['owner'],'2026-01-01','2026-11-12')
    report['employees'][0].update(shifts=3,declared_seconds=27_000,break_seconds=1800,unclosed=1)
    output=tmp_path/'shifts.xlsx';receipt=crm_reports.write(report,output,'shifts')
    assert receipt['verified'] and crm_reports.verify(output,report,'shifts')


def test_common_workbook_uses_xlwings_and_preserves_template_formulas(crm_world,tmp_path):
    import uuid
    from crm import exchange
    from pult.core import crm_workbook as books
    snapshot=exchange.export(crm_world['owner'],dict(operation_id=str(uuid.uuid4()),period='2026-11',target_id='general'))
    for row in snapshot['rows']:
        row['values'].update({field['key']:row['values'].get(field['key']) for field in snapshot['fields']})
    first=tmp_path/'common.xlsx';books.write(snapshot,first)
    assert len(books.read(first)['rows'])==2
    with crm_reports.excel() as app:
        book=app.books.open(str(first));sheet=book.sheets.add('Witness');sheet.range('A1').formula='=SUM(2,3)'
        sheet.range('B2').value='Preserve unknown data';book.save();book.close()
    before=books.sha(first);second=tmp_path/'common-copy.xlsx'
    books.write(snapshot,second,first)
    assert books.sha(first)==before and len(books.read(second)['rows'])==2
    with crm_reports.excel() as app:
        book=app.books.open(str(second),update_links=False,read_only=True)
        assert book.sheets['Witness'].range('A1').formula=='=SUM(2,3)'
        assert book.sheets['Witness'].range('B2').value=='Preserve unknown data';book.close()


def test_empty_three_hour_scope_has_no_circular_total(crm_world,tmp_path):
    report=three_hour.report(crm_world['owner'])
    for sheet in report['sheets']:sheet['rows']=[]
    path=tmp_path/'empty-scope.xlsx';crm_reports.write(report,path,'three-hour')
    with crm_reports.excel() as app:
        book=app.books.open(str(path),update_links=False,read_only=True)
        assert book.sheets[0].range('B3:AE3').api.HasFormula is False
        assert book.sheets[0].range('B3:D3').value==[0,0,0];book.close()
