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
