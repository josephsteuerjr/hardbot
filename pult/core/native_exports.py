"""Administrator workbook exports through native Excel, with calamine readback."""
from datetime import date, datetime
from pathlib import Path
import hashlib
import os
import uuid
from .crm_reports import excel,number_format

def _sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def _write(path,title,headers,rows,widths,*,groups=None,sources=None,date_columns=None,numeric_columns=None):
    if os.environ.get('HARD_CRM_SERVER_XLSX')=='1':
        from .portable_xlsx import export_table
        return export_table(path,title,headers,rows,widths,groups=groups,sources=sources,date_columns=date_columns,numeric_columns=numeric_columns)
    from python_calamine import CalamineWorkbook
    target=Path(path).resolve();before=_sha(target) if target.exists() else None
    temporary=target.with_name(target.stem+'.'+uuid.uuid4().hex+'.xlsx')
    start=4 if groups is not None else 2
    def cell_value(value):
        if isinstance(value,date):
            stamp=value.replace(tzinfo=None) if isinstance(value,datetime) else datetime.combine(value,datetime.min.time())
            return (stamp-datetime(1899,12,30)).total_seconds()/86400
        return value
    matrix=[[cell_value(value) for value in row] for row in rows]
    with excel() as app:
        book=app.books.add()
        try:
            book.api.Date1904=False
            sheet=book.sheets[0];sheet.name=title
            sheet.range((1,1),(max(start,len(rows)+start-1),len(headers))).number_format='@'
            if groups is not None:
                sheet.range((1,1),(1,len(headers))).value=[groups]
                left=0
                for right in range(1,len(groups)+1):
                    if right==len(groups) or groups[right]!=groups[left]:
                        if right-left>1:sheet.range((1,left+1),(1,right)).merge()
                        left=right
                sheet.range((3,1),(3,len(headers))).value=[sources]
            header=start-2 if groups is not None else 1
            sheet.range((header,1),(header,len(headers))).value=[headers]
            if rows:sheet.range((start,1),(start+len(rows)-1,len(headers))).value=matrix
            for col,width in enumerate(widths,1):sheet.range((1,col)).column_width=width
            for col,format_code in (date_columns or {}).items():number_format(sheet.range((start,col),(max(start,start+len(rows)-1),col)),format_code)
            for col,format_code in (numeric_columns or {}).items():number_format(sheet.range((start,col),(max(start,start+len(rows)-1),col)),format_code)
            sheet.range((1,1),(header,len(headers))).api.Font.Bold=True
            sheet.range((1,1),(header,len(headers))).color='#E9DACA'
            sheet.used_range.api.Font.Name='Arial';sheet.used_range.api.Font.Size=10
            sheet.range((header,1),(max(start,len(rows)+start-1),len(headers))).api.AutoFilter()
            sheet.activate();app.api.ActiveWindow.SplitRow=start-1;app.api.ActiveWindow.SplitColumn=0;app.api.ActiveWindow.FreezePanes=True
            book.save(str(temporary))
        finally:book.close()
    saved=CalamineWorkbook.from_path(str(temporary)).get_sheet_by_name(title).to_python()
    for index,row in enumerate(rows,start-1):
        for col,expected in enumerate(row):
            actual=saved[index][col] if index<len(saved) and col<len(saved[index]) else ''
            if expected in (None,'') and actual=='':continue
            if isinstance(expected,(int,float)) and isinstance(actual,(int,float)) and abs(expected-actual)<1e-7:continue
            if isinstance(expected,datetime) and isinstance(actual,datetime):
                if expected.replace(tzinfo=None)==actual.replace(tzinfo=None):continue
                raise ValueError('Не совпало сохранённое время выгрузки.')
            if isinstance(expected,date) and isinstance(actual,date):
                if (expected.date() if isinstance(expected,datetime) else expected)==(actual.date() if isinstance(actual,datetime) else actual):continue
            if actual!=expected:raise ValueError('Не совпали сохранённые данные выгрузки.')
    if (_sha(target) if target.exists() else None)!=before:raise ValueError('Файл изменился во время подготовки выгрузки; проверенная копия сохранена рядом.')
    os.replace(temporary,target)

def registry(path,fields,records):
    from .export import col_width,parse_num
    from pult import enrich
    rows=[];dates={};numbers={}
    for record in records:
        row=[]
        for index,field in enumerate(fields,1):
            value=record.get(field['key'])
            if value in (None,''):value=None
            elif field.get('date') and enrich.parse_date(value):
                value=enrich.parse_date(value);dates[index]='dd.mm.yyyy'
            elif field.get('num') and parse_num(value) is not None:
                value=parse_num(value);numbers[index]='#,##0'
            else:value=str(value)
            row.append(value)
        rows.append(row)
    _write(path,'Портфели',[f['title'] for f in fields],rows,[col_width(f['key'],f['title']) for f in fields],
        groups=[f['block'] for f in fields],sources=[f['source'] for f in fields],date_columns=dates,numeric_columns=numbers)

def markers(path,records):
    from .export import MARKER_COLS
    rows=[]
    for record in records:
        row=[]
        for key,_,_ in MARKER_COLS:
            value=record.get(key)
            if key=='created_at' and value:
                try:value=datetime.fromisoformat(str(value).replace('T',' ')).replace(tzinfo=None)
                except ValueError:value=str(value)
            row.append(value)
        rows.append(row)
    _write(path,'Метки',[f[1] for f in MARKER_COLS],rows,[f[2] for f in MARKER_COLS],date_columns={1:'dd.mm.yyyy hh:mm'},numeric_columns={8:'0.000000',9:'0.000000'})
