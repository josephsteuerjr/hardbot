"""Read saved Excel values/formulas; patch XLSX date cells without a full rewrite.

XLSB formula cache layout follows Microsoft's MS-XLSB BrtFmlaNum/String.
https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xlsb/a4d1d5aa-dfd9-4955-b4b0-c3a2ed1a3d63
https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xlsb/006f9076-8b12-4e70-bf70-cdba08aea2e6
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date,datetime
import hashlib
import io
import tempfile
import posixpath
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile

from .manual_edits import BookView, view

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG='http://schemas.openxmlformats.org/package/2006/relationships'


def _raw_records(data: bytes):
    stream=io.BytesIO(data)
    while stream.tell()<len(data):
        start=stream.tell()
        kind=0
        for i in range(4):
            raw=stream.read(1)
            if not raw:raise ValueError('Оборванный XLSB record id')
            kind|=raw[0]<<(8*i)
            if not raw[0]&128:break
        else:raise ValueError('Некорректный XLSB record id')
        length=0
        for i in range(4):
            raw=stream.read(1)
            if not raw:raise ValueError('Оборванная длина XLSB record')
            length|=(raw[0]&127)<<(7*i)
            if not raw[0]&128:break
        else:raise ValueError('Некорректная длина XLSB record')
        payload=stream.read(length)
        if len(payload)!=length:raise ValueError('Оборванный XLSB record')
        yield kind,payload,data[start:stream.tell()]


def records(data: bytes):
    for kind,payload,_raw in _raw_records(data):yield kind,payload


def wide(data: bytes,offset: int):
    count=struct.unpack_from('<I',data,offset)[0]
    end=offset+4+2*count
    if end>len(data):raise ValueError('Оборванная XLSB строка')
    return data[offset+4:end].decode('utf-16-le'),end


def portfolio_part(archive: zipfile.ZipFile):
    rels=ET.fromstring(archive.read('xl/_rels/workbook.'+('bin' if 'xl/workbook.bin' in archive.namelist() else 'xml')+'.rels'))
    targets={r.attrib['Id']:r.attrib['Target'] for r in rels if r.attrib.get('TargetMode')!='External'}
    if 'xl/workbook.bin' in archive.namelist():
        sheets=[]
        for kind,payload in records(archive.read('xl/workbook.bin')):
            if kind==0x019C:
                rid,offset=wide(payload,8);name,_=wide(payload,offset)
                sheets.append((name,rid))
    else:
        wb=ET.fromstring(archive.read('xl/workbook.xml'))
        sheets=[(s.attrib['name'],s.attrib['{'+REL+'}id']) for s in wb.findall('./{'+NS+'}sheets/{'+NS+'}sheet')]
    ids=[rid for name,rid in sheets if name=='Портфели']
    if len(ids)!=1:raise ValueError('Неоднозначный лист Портфели')
    target=targets[ids[0]]
    part=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl',target))
    if not part.startswith('xl/worksheets/') or '..' in part.split('/'):
        raise ValueError('Некорректный путь листа')
    return part


def formula_values(archive: zipfile.ZipFile,part: str):
    result={}
    if part.endswith('.bin'):
        row=None
        for kind,payload in records(archive.read(part)):
            if kind==0:
                row=struct.unpack_from('<I',payload)[0]
            elif kind in (8,9,10,11):
                if row is None:raise ValueError('XLSB формула без строки')
                column=struct.unpack_from('<I',payload)[0]
                if kind==8:
                    _,offset=wide(payload,8)
                else:
                    offset=16 if kind==9 else 9
                formula=payload[offset+2:]  # Skip cached value and GrbitFmla.
                if len(formula)<8:raise ValueError('Оборванная XLSB формула')
                result[row,column]='=XLSB:'+hashlib.sha256(formula).hexdigest()
    else:
        root=ET.fromstring(archive.read(part))
        from openpyxl.utils.cell import coordinate_to_tuple
        for cell in root.iter('{'+NS+'}c'):
            formula=cell.find('{'+NS+'}f')
            if formula is not None:
                row,column=coordinate_to_tuple(cell.attrib['r'])
                result[row-1,column-1]='=XML:'+ET.tostring(formula,encoding='unicode')
    return result


@dataclass(frozen=True)
class SavedBook:
    path: Path
    sha256: str
    mtime_ns: int
    grid: list[list]
    view: BookView
    portfolio_part: str
    source_stat: tuple[int,int,int]


def _native_grid(blob,suffix,nrows=None):
    """Compatibility reader for an older analyst Python, without VBA access."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app=None;book=None;dummy=None
    folder=tempfile.TemporaryDirectory(prefix='manual-read-')
    try:
        path=Path(folder.name)/('portfolio'+suffix);path.write_bytes(blob)
        app=win32com.client.DispatchEx('Excel.Application')
        app.Visible=False;app.DisplayAlerts=False;app.EnableEvents=False;app.AutomationSecurity=3
        dummy=app.Workbooks.Add();app.Calculation=-4135
        book=app.Workbooks.Open(str(path),UpdateLinks=0,ReadOnly=True,AddToMru=False,CorruptLoad=0)
        dummy.Close(False);dummy=None
        ws=book.Worksheets('Портфели')
        # Find skips rows hidden by an active autofilter. Saved package cells
        # give an exact extent, including filtered records and excluding a
        # whole-sheet formatting tail.
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            part=portfolio_part(archive);height=width=0
            if part.endswith('.bin'):
                row=0
                for kind,payload in records(archive.read(part)):
                    if kind==0:row=struct.unpack_from('<I',payload)[0]
                    elif kind in range(1,12):
                        height=max(height,row+1);width=max(width,struct.unpack_from('<I',payload)[0]+1)
            else:
                from openpyxl.utils.cell import coordinate_to_tuple
                for cell in ET.fromstring(archive.read(part)).iter('{'+NS+'}c'):
                    row,column=coordinate_to_tuple(cell.attrib['r']);height=max(height,row);width=max(width,column)
        if not height:return []
        height=min(height,nrows) if nrows is not None else height
        area=ws.Range(ws.Cells(1,1),ws.Cells(height,width))
        typed=area.Value;raw=area.Value2
        if not isinstance(raw,tuple):raw=((raw,),);typed=((typed,),)
        # Value2 preserves full numeric precision (Value rounds Currency) and
        # the actual Excel serial (COM's OLE date epoch differs before March
        # 1900). Value is used solely to recognize a date-formatted cell.
        from openpyxl.utils.datetime import from_excel,CALENDAR_MAC_1904,CALENDAR_WINDOWS_1900
        epoch=CALENDAR_MAC_1904 if book.Date1904 else CALENDAR_WINDOWS_1900
        return [[from_excel(value,epoch) if isinstance(kind,datetime) and isinstance(value,(int,float)) else value
                 for value,kind in zip(row,types)] for row,types in zip(raw,typed)]
    finally:
        if book is not None:
            try:book.Close(False)
            except Exception:pass
        if dummy is not None:
            try:dummy.Close(False)
            except Exception:pass
        if app is not None:
            try:app.Quit()
            except Exception:pass
        pythoncom.CoUninitialize()
        folder.cleanup()


def _grid(blob,suffix,*,force_native=False,nrows=None):
    if force_native:return _native_grid(blob,suffix,nrows)
    try:
        from python_calamine import CalamineWorkbook
        with CalamineWorkbook.from_filelike(io.BytesIO(blob)) as workbook:
            return workbook.get_sheet_by_name('Портфели').to_python(skip_empty_area=False,nrows=nrows)
    except Exception:
        if suffix=='.xlsx':
            from openpyxl import load_workbook
            wb=load_workbook(io.BytesIO(blob),read_only=True,data_only=True)
            try:return [list(row) for row in wb['Портфели'].iter_rows(values_only=True,max_row=nrows)]
            finally:wb.close()
        return _native_grid(blob,suffix,nrows)


def read_rows(path,*,nrows=None):
    """Cached values for legacy pandas callers; no dependence on calamine 0.7."""
    path=Path(path);before=path.stat();blob=path.read_bytes();after=path.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):raise ValueError('Книга меняется во время чтения')
    return _grid(blob,path.suffix.casefold(),nrows=nrows)


def read(path: str|Path,*,force_native=False) -> SavedBook:
    path=Path(path)
    if path.is_symlink() or path.suffix.casefold() not in ('.xlsx','.xlsb'):
        raise ValueError('Нужен обычный XLSX или XLSB')
    before=path.stat()
    blob=path.read_bytes()
    after=path.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino) or len(blob)!=before.st_size:
        raise ValueError('Книга меняется во время чтения')
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        part=portfolio_part(archive)
        formulas=formula_values(archive,part)
    grid=_grid(blob,path.suffix.casefold(),force_native=force_native)
    for (row,column),expression in formulas.items():
        while len(grid)<=row:grid.append([])
        if len(grid[row])<=column:grid[row].extend([None]*(column+1-len(grid[row])))
        grid[row][column]=expression
    return SavedBook(path,hashlib.sha256(blob).hexdigest(),before.st_mtime_ns,grid,view(grid,formula_cells=formulas),part,(before.st_size,before.st_mtime_ns,before.st_ino))


def write_xlsx_dates(source: str|Path,target: str|Path,writes):
    """Modify only requested numeric cells. Other ZIP members are byte-identical.

    Columns and number formats are prepared by the separate migration. No
    row/column insertion, formula translation, macro editing or trust changes.
    """
    from lxml import etree
    from openpyxl.utils.cell import get_column_letter
    source,target=Path(source),Path(target)
    if source.resolve()==target.resolve():raise ValueError('Запись только в отдельный кандидат')
    if source.suffix.casefold()!='.xlsx':raise ValueError('Этот writer работает с XLSX')
    with zipfile.ZipFile(source) as archive:
        part=portfolio_part(archive)
        wb=ET.fromstring(archive.read('xl/workbook.xml'))
        props=wb.find('{'+NS+'}workbookPr')
        epoch1904=props is not None and props.attrib.get('date1904','0') in ('1','true')
        root=etree.fromstring(archive.read(part))
        data=root.find('{'+NS+'}sheetData')
        if data is None:raise ValueError('Нет sheetData')
        row_map={int(r.attrib['r']):r for r in data}
        column_styles={}
        for col in root.findall('./{'+NS+'}cols/{'+NS+'}col'):
            for c in range(int(col.attrib['min']),int(col.attrib['max'])+1):
                if 'style' in col.attrib:column_styles[c]=col.attrib['style']
        for row,column,value,_ in writes:
            row+=1;column+=1
            if not isinstance(value,date):raise ValueError('Записывается только настоящая дата')
            parent=row_map.get(row)
            if parent is None:raise ValueError('Нельзя выдумывать строку реестра')
            address=get_column_letter(column)+str(row)
            cells={c.attrib.get('r'):c for c in parent if c.tag=='{'+NS+'}c'}
            cell=cells.get(address)
            if cell is None:
                cell=etree.Element('{'+NS+'}c',r=address)
                if column in column_styles:cell.attrib['s']=column_styles[column]
                from openpyxl.utils.cell import coordinate_to_tuple
                later=next((c for c in parent if c.tag=='{'+NS+'}c' and coordinate_to_tuple(c.attrib['r'])[1]>column),None)
                if later is None:parent.append(cell)
                else:parent.insert(parent.index(later),cell)
            if cell.find('{'+NS+'}f') is not None:raise ValueError('Колонка даты содержит формулу')
            for child in list(cell):cell.remove(child)
            cell.attrib['t']='n'
            serial=(value-date(1899,12,30)).days-(1462 if epoch1904 else 0)
            etree.SubElement(cell,'{'+NS+'}v').text=str(serial)
        output=etree.tostring(root,encoding='UTF-8',xml_declaration=True,standalone=True)
        with zipfile.ZipFile(target,'w') as destination:
            for member in archive.infolist():
                destination.writestr(member,output if member.filename==part else archive.read(member.filename))


def calculation_metadata(path):
    """Exact original calculation flags; independent of the worker's app mode.

    MS-XLSB BrtCalcProp: calculation mode, iteration, precision and threading.
    https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xlsb/e520db93-8eea-4e09-928d-ff33daba65b9
    """
    with zipfile.ZipFile(path) as archive:
        if 'xl/workbook.bin' in archive.namelist():
            found=[raw for kind,_payload,raw in _raw_records(archive.read('xl/workbook.bin')) if kind==0x019D]
            if len(found)>1:raise ValueError('Неоднозначные настройки вычисления XLSB')
            return ('xl/workbook.bin',found[0] if found else None)
        root=ET.fromstring(archive.read('xl/workbook.xml'))
        calc=root.find('{'+NS+'}calcPr')
        return ('xl/workbook.xml',dict(calc.attrib) if calc is not None else None)


def preserve_calculation_metadata(source,target):
    """Restore only calculation metadata after a native staged save."""
    import os
    source,target=Path(source),Path(target)
    part,original=calculation_metadata(source)
    if calculation_metadata(target)==(part,original):return
    with zipfile.ZipFile(target) as archive:
        data=archive.read(part)
        if part.endswith('.bin'):
            pieces=[];replaced=False
            for kind,_payload,raw in _raw_records(data):
                if kind==0x019D:
                    if replaced:raise ValueError('Неоднозначные настройки вычисления кандидата')
                    replaced=True
                    if original is not None:pieces.append(original)
                else:pieces.append(raw)
            if original is not None and not replaced:raise ValueError('Кандидат потерял настройки вычисления')
            output=b''.join(pieces)
        else:
            from lxml import etree
            root=etree.fromstring(data);calc=root.find('{'+NS+'}calcPr')
            if original is None:
                if calc is not None:root.remove(calc)
            else:
                if calc is None:raise ValueError('Кандидат потерял настройки вычисления')
                calc.attrib.clear();calc.attrib.update(original)
            output=etree.tostring(root,encoding='UTF-8',xml_declaration=True,standalone=True)
        temporary=target.with_name(target.name+'.calc-state')
        try:
            with zipfile.ZipFile(temporary,'w') as destination:
                for member in archive.infolist():destination.writestr(member,output if member.filename==part else archive.read(member.filename))
        except BaseException:
            if temporary.exists():temporary.unlink()
            raise
    os.replace(temporary,target)
    if calculation_metadata(target)!=(part,original):raise ValueError('Не сохранены настройки вычисления')
