from datetime import date
from pathlib import Path
import struct
import zipfile

from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,PatternFill
import pytest

from pult.core.manual_workbooks import read,write_xlsx_dates,records,formula_values


def fixture(path,epoch1904=False):
    from openpyxl.utils.datetime import CALENDAR_MAC_1904
    wb=Workbook();ws=wb.active;ws.title='Портфели'
    if epoch1904:wb.epoch=CALENDAR_MAC_1904
    ws.append([])
    ws.append(['Номер КД / ID','Дата ручной правки','Дата выезда','комментарий звонка','Дата ручной правки','статус звонка софт','Историчный комментарий Софт'])
    ws.append(['сх','хард','хард','хард','софт','софт','софт'])
    ws.append(['T-1',None,date(2026,10,20),'manual',None,'status','=1+2'])
    ws.column_dimensions['B'].number_format='dd.mm.yyyy'
    ws.column_dimensions['E'].number_format='dd.mm.yyyy'
    ws['D4'].font=Font(bold=True,color='884422')
    ws['D4'].fill=PatternFill('solid',fgColor='FFEEAA')
    stats=wb.create_sheet('Статистика сотрудника');stats['A1']="=COUNTA('Портфели'!A4:A100)"
    wb.save(path)


@pytest.mark.parametrize('epoch',[False,True])
def test_python_xlsx_patch_changes_only_date_cells_and_one_zip_part(tmp_path,epoch):
    source=tmp_path/'source.xlsx';target=tmp_path/'candidate.xlsx';fixture(source,epoch)
    before=read(source)
    assert before.view.header_row==1
    assert next(c for c in before.view.cells if c.column==6).formula
    write_xlsx_dates(source,target,[(3,1,date(2026,10,30),True),(3,4,date(2026,10,29),True)])
    after=read(target)
    assert after.view.cells==before.view.cells
    assert after.view.dates['T-1','excel_visit_edited_on'][2]=='2026-10-30'
    assert after.view.dates['T-1','excel_call_edited_on'][2]=='2026-10-29'
    with zipfile.ZipFile(source) as old,zipfile.ZipFile(target) as new:
        assert old.namelist()==new.namelist()
        changed=[name for name in old.namelist() if old.read(name)!=new.read(name)]
        assert changed==[before.portfolio_part]
    wb=load_workbook(target,data_only=False)
    assert wb['Портфели']['D4'].font.bold and wb['Портфели']['D4'].fill.fgColor.rgb=='00FFEEAA'
    assert wb['Портфели']['G4'].value=='=1+2'
    assert wb['Статистика сотрудника']['A1'].value=="=COUNTA('Портфели'!A4:A100)"


def test_in_place_write_is_rejected(tmp_path):
    path=tmp_path/'a.xlsx';fixture(path)
    with pytest.raises(ValueError,match='кандидат'):write_xlsx_dates(path,path,[])


def test_bad_biff_is_rejected():
    with pytest.raises(ValueError):list(records(b'\x09\x05\x01'))


def record(kind,payload):
    size=len(payload);length=[]
    while size>127:length.append((size&127)|128);size>>=7
    length.append(size)
    return bytes([kind,*length])+payload


def test_xlsb_cached_result_does_not_change_formula_identity(tmp_path):
    formula=struct.pack('<I',1)+b'\x1e'+struct.pack('<I',0)
    def blob(cached):
        return record(0,struct.pack('<I',3))+record(9,struct.pack('<II',2,0)+struct.pack('<d',cached)+b'\0\0'+formula)
    def inspect(value):
        path=tmp_path/(str(value)+'.zip')
        with zipfile.ZipFile(path,'w') as z:z.writestr('sheet.bin',blob(value))
        with zipfile.ZipFile(path) as z:return formula_values(z,'sheet.bin')
    assert inspect(1)==inspect(2)
