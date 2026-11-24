"""Portable CRM files: XlsxWriter writes, calamine verifies, no Office process."""
from datetime import date,datetime
from pathlib import Path
import json,os,uuid
from python_calamine import CalamineWorkbook

PAPER='#F4EFE7';HEADER='#E9DACA';INK='#43382F'


def formats(book):
    base={'font_name':'Arial','font_size':10,'font_color':INK,'valign':'vcenter'}
    return {name:book.add_format({**base,**extra}) for name,extra in {
        'text':{},'number':{'num_format':'#,##0.00'},'integer':{'num_format':'#,##0'},
        'percent':{'num_format':'0.0%'},'header':{'bold':True,'bg_color':HEADER,'text_wrap':True,'bottom':1,'bottom_color':'#BCA58E'},
        'group':{'bold':True,'bg_color':HEADER,'align':'center','valign':'vcenter'},
        'total':{'bold':True,'bg_color':PAPER,'top':1,'num_format':'#,##0.00'},
        'total-percent':{'bold':True,'bg_color':PAPER,'top':1,'num_format':'0.0%'},
        'date':{'num_format':'dd.mm.yyyy'},'datetime':{'num_format':'dd.mm.yyyy hh:mm'},
        'meta':{'num_format':'@'},'muted':{'font_color':'#77695B','font_size':9}}.items()}


def setup_sheet(sheet,width=30):
    sheet.hide_gridlines(2);sheet.set_default_row(18);sheet.set_column(0,0,width)
    sheet.set_landscape();sheet.fit_to_pages(1,0);sheet.set_paper(9)
    sheet.set_margins(.3,.3,.4,.4)


def cell(sheet,row,col,value,fmt):
    if value is None:sheet.write_blank(row,col,None,fmt)
    elif isinstance(value,str):sheet.write_string(row,col,value,fmt)
    elif isinstance(value,(date,datetime)):sheet.write_datetime(row,col,value,fmt)
    else:sheet.write(row,col,value,fmt)


def metadata(book,name,value):
    sheet=book.add_worksheet(name);sheet.very_hidden();raw=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    for offset in range(0,len(raw),30000):sheet.write_string(offset//30000,0,raw[offset:offset+30000])


def table(book,title,headers,rows,fmts):
    sheet=book.add_worksheet(title);setup_sheet(sheet);sheet.set_column(1,len(headers)-1,18)
    sheet.set_row(0,34);sheet.freeze_panes(1,1);sheet.repeat_rows(0)
    for c,value in enumerate(headers):cell(sheet,0,c,value,fmts['header'])
    for r,row in enumerate(rows,1):
        for c,value in enumerate(row):cell(sheet,r,c,value,fmts['number'] if isinstance(value,(float,int)) else fmts['text'])
    sheet.autofilter(0,0,len(rows),len(headers)-1)
    return sheet


def publish(temp,target):
    # Same directory: hard-link publication is atomic and never overwrites an
    # unrelated file that appeared while generation/readback was running.
    os.link(temp,target);temp.unlink()


def write_report(report,path,kind='shifts'):
    import xlsxwriter
    from pult.core.crm_reports import verify,receipt,manager_tables
    target=Path(path).resolve()
    if target.suffix.lower()!='.xlsx':raise ValueError('Выберите имя XLSX.')
    if target.exists():verify(target,report,kind);return receipt(target)
    target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_name('.'+target.stem+'.'+uuid.uuid4().hex+'.xlsx')
    try:
        with xlsxwriter.Workbook(str(temp),{'strings_to_formulas':False,'strings_to_urls':False}) as book:
            fmts=formats(book)
            if kind=='three-hour':
                from xlsxwriter.utility import xl_col_to_name
                for data in report['sheets']:
                    sheet=book.add_worksheet(data['name']);setup_sheet(sheet,29);sheet.set_column(1,30,13)
                    sheet.set_row(0,32);sheet.set_row(1,30);sheet.freeze_panes(2,1);sheet.repeat_rows(0,1)
                    sheet.write_string(0,0,'Сотрудник',fmts['header']);column=1
                    for title,count in report['groups']:
                        label=title+(' sms' if data['sms'] and title!='Встречи' else '')
                        sheet.merge_range(0,column,0,column+count*3-1,label,fmts['group']);column+=count*3
                    for c in range(1,31):sheet.write_string(1,c,['Вчера','Сегодня','Доля'][(c-1)%3],fmts['header'])
                    totals=[[],[]]
                    for r,item in enumerate(data['rows'],2):
                        cell(sheet,r,0,item['employee'],fmts['text'])
                        totals[0].append(item['yesterday']);totals[1].append(item['current'])
                        for metric,(before,after) in enumerate(zip(item['yesterday'],item['current'])):
                            col=1+metric*3;before=None if before is None else float(before);after=float(after)
                            fmt=fmts['integer' if metric in (0,1,2,4,6,8) else 'number']
                            cell(sheet,r,col,before,fmt);cell(sheet,r,col+1,after,fmt)
                            a,b=xl_col_to_name(col),xl_col_to_name(col+1);excel_row=r+1
                            ratio='' if before is None else after/before if before else 0
                            sheet.write_formula(r,col+2,f'=IF({a}{excel_row}="","",IF({a}{excel_row}=0,0,{b}{excel_row}/{a}{excel_row}))',fmts['percent'],ratio)
                    last=len(data['rows'])+2;sheet.write_string(last,0,'Итого',fmts['total'])
                    for metric in range(10):
                        col=1+metric*3;values=[None if any(row[metric] is None for row in group) else sum(float(row[metric]) for row in group) for group in totals]
                        for offset,value in enumerate(values):
                            letter=xl_col_to_name(col+offset)
                            formula=f'=SUM({letter}3:{letter}{last})' if last>2 else '=0'
                            if offset==0 and last>2:formula=f'=IF(COUNT({letter}3:{letter}{last})<COUNTA(A3:A{last}),"",SUM({letter}3:{letter}{last}))'
                            sheet.write_formula(last,col+offset,formula,fmts['total'],'' if value is None else value)
                        a,b=xl_col_to_name(col),xl_col_to_name(col+1);r=last+1
                        ratio='' if values[0] is None else values[1]/values[0] if values[0] else 0
                        sheet.write_formula(last,col+2,f'=IF({a}{r}="","",IF({a}{r}=0,0,{b}{r}/{a}{r}))',fmts['total-percent'],ratio)
                    caption=f"{data.get('as_of',report['as_of'])} · {data.get('timezone',report['timezone'])} · {data.get('currency',report['currency']) or 'Валюта не указана'}"
                    sheet.write_string(last+2,0,caption,fmts['muted'])
                    incomplete=data.get('yesterday_incomplete_contracts',report.get('yesterday_incomplete_contracts'))
                    if incomplete:sheet.write_string(last+3,0,f"Нет полного снимка предыдущего дня: {incomplete} договоров.",fmts['muted'])
                    sheet.autofilter(1,0,max(1,last-1),30)
            elif kind=='table':table(book,'Данные',report['headers'],report['rows'],fmts)
            elif kind in {'manager-month','manager-dynamics'}:
                for title,headers,rows in manager_tables(report,kind):
                    sheet=table(book,title,headers,rows,fmts);cell(sheet,len(rows)+3,0,report.get('as_of') or report.get('day'),fmts['muted'])
            elif kind=='shifts':
                table(book,'Свод смен',['Сотрудник','Смен','Работа, часов','Перерывы, часов','Не закрыто'],
                    [[r['employee'],r['shifts'],r['declared_seconds']/3600,r['break_seconds']/3600,r['unclosed']] for r in report['employees']],fmts)
                table(book,'Смены',['Сотрудник','Дата смены','Начало','Окончание','Часовой пояс','Работа в периоде, часов','Перерывы в периоде, часов','Состояние'],
                    [[r['employee'],r['workday'],r['starts_at'],r['ends_at'] or 'Не закрыта',r['timezone'],r['declared_seconds']/3600,r['break_seconds']/3600,r['state']] for r in report['shifts']],fmts)
            else:raise ValueError('Неизвестный отчёт.')
            metadata(book,'_CRM_REPORT',report)
        verify(temp,report,kind);publish(temp,target);return receipt(target)
    finally:temp.unlink(missing_ok=True)


def read_workbook(path):
    from pult.core.crm_workbook import sha,SHEET,META,CLEAR,IDENTITY
    path=Path(path).resolve();before=sha(path)
    with CalamineWorkbook.from_path(str(path)) as book:
        if META not in book.sheet_names:raise ValueError('Нет манифеста CRM и исходной версии. Создайте версионированную копию реестра.')
        meta=json.loads(''.join(str(row[0]) for row in book.get_sheet_by_name(META).to_python() if row and row[0]!=''))
        if meta['manifest']['schema_version']!=1:raise ValueError('Неизвестная версия CRM.')
        matrix=book.get_sheet_by_name(SHEET).to_python();columns=meta['columns'];width=max(columns.values())
        if len(matrix)*width>5_000_000:raise ValueError('Область книги слишком велика.')
        def value(row,col):return matrix[row][col] if row<len(matrix) and col<len(matrix[row]) else ''
        for col,title in meta['headers'].items():
            if value(1,int(col)-1)!=title:raise ValueError('Изменён порядок или заголовок колонок. Нужна новая сверка сопоставления.')
        rows=[];seen=set()
        for row in range(3,len(matrix)):
            cid=value(row,columns['_crm_contract_uuid']-1)
            if not cid:continue
            if cid in seen:raise ValueError('В книге повторён внутренний ID договора.')
            seen.add(cid);values={};clear=[]
            for key,col in columns.items():
                if key in IDENTITY:continue
                v=value(row,col-1)
                if v==CLEAR:v=None;clear.append(key)
                elif v=='':v=None
                elif isinstance(v,(int,float)):v=format(v,'.15g')
                values[key]=v
            rows.append(dict(contract_uuid=cid,values=values,clear=clear))
    if sha(path)!=before:raise ValueError('Файл изменился во время чтения.')
    return dict(export_id=meta['manifest']['export_id'],manifest_hash=meta['manifest']['manifest_hash'],schema_version=1,file_sha256=before,rows=rows)


def write_workbook(snapshot,path,template=None):
    import xlsxwriter
    from pult.core import crm_workbook as model
    if template:raise ValueError('Сохранение существующего шаблона выполняется в Windows-программе. Для серверной выгрузки выберите новую книгу XLSX.')
    target=Path(path).resolve()
    if target.suffix.lower()!='.xlsx' or target.exists():raise ValueError('Выберите новое имя XLSX: существующий файл сохраняется.')
    if not target.parent.is_dir():raise ValueError('Каталог назначения не найден.')
    temp=target.with_name('.'+target.stem+'.'+uuid.uuid4().hex+'.xlsx')
    keys=[f['key'] for f in snapshot['fields']]+model.IDENTITY;headers=[f['title'] for f in snapshot['fields']]+model.IDENTITY
    meta=model._meta(snapshot,{key:i+1 for i,key in enumerate(keys)},{str(i+1):v for i,v in enumerate(headers)})
    try:
        with xlsxwriter.Workbook(str(temp),{'strings_to_formulas':False,'strings_to_urls':False}) as book:
            fmts=formats(book);sheet=book.add_worksheet(model.SHEET);setup_sheet(sheet);sheet.set_column(0,len(keys)-1,18)
            sheet.write_string(0,0,'Портфель '+snapshot['manifest']['period'],fmts['header']);sheet.set_row(1,36)
            for c,value in enumerate(headers):cell(sheet,1,c,value,fmts['header'])
            for c,value in enumerate([f['source'] for f in snapshot['fields']]+['CRM']*4):cell(sheet,2,c,value,fmts['muted'])
            for r,row in enumerate(snapshot['rows'],3):
                for c,value in enumerate(model._values(row,keys)):cell(sheet,r,c,None if value is None else str(value),fmts['meta'])
            sheet.set_column(len(keys)-4,len(keys)-1,18,None,{'hidden':True});sheet.freeze_panes(3,3);sheet.repeat_rows(0,2)
            sheet.autofilter(1,0,max(2,len(snapshot['rows'])+2),len(keys)-1);metadata(book,model.META,meta)
        model.resume(snapshot,temp);publish(temp,target);return model.resume(snapshot,target)
    finally:temp.unlink(missing_ok=True)


def export_table(path,title,headers,rows,widths,*,groups=None,sources=None,date_columns=None,numeric_columns=None):
    import xlsxwriter
    from .native_exports import _sha
    target=Path(path).resolve();before=_sha(target) if target.exists() else None
    temporary=target.with_name('.'+target.stem+'.'+uuid.uuid4().hex+'.xlsx');start=3 if groups is not None else 1
    try:
        with xlsxwriter.Workbook(str(temporary),{'strings_to_formulas':False,'strings_to_urls':False}) as book:
            fmts=formats(book);sheet=book.add_worksheet(title);setup_sheet(sheet);header=start-2 if groups is not None else 0
            if groups is not None:
                left=0
                for right in range(1,len(groups)+1):
                    if right==len(groups) or groups[right]!=groups[left]:
                        if right-left>1:sheet.merge_range(0,left,0,right-1,groups[left],fmts['group'])
                        else:cell(sheet,0,left,groups[left],fmts['group'])
                        left=right
                for c,value in enumerate(sources):cell(sheet,2,c,value,fmts['muted'])
            for c,value in enumerate(headers):cell(sheet,header,c,value,fmts['header'])
            column_formats={c-1:book.add_format({'font_name':'Arial','font_size':10,'num_format':f}) for c,f in {**(date_columns or {}),**(numeric_columns or {})}.items()}
            for r,row in enumerate(rows,start):
                for c,value in enumerate(row):cell(sheet,r,c,value,column_formats.get(c,fmts['text']))
            for c,width in enumerate(widths):sheet.set_column(c,c,width)
            sheet.set_row(header,32);sheet.freeze_panes(start,0);sheet.repeat_rows(0,start-1);sheet.autofilter(header,0,max(header,start+len(rows)-1),len(headers)-1)
        with CalamineWorkbook.from_path(str(temporary)) as book:
            saved=book.get_sheet_by_name(title).to_python()
            for r,row in enumerate(rows,start):
                for c,expected in enumerate(row):
                    actual=saved[r][c] if r<len(saved) and c<len(saved[r]) else ''
                    if expected in (None,'') and actual=='':continue
                    if isinstance(expected,(int,float)) and isinstance(actual,(int,float)) and abs(expected-actual)<1e-7:continue
                    if isinstance(expected,date) and isinstance(actual,date):
                        if isinstance(expected,datetime) and isinstance(actual,datetime) and expected.replace(tzinfo=None)==actual.replace(tzinfo=None):continue
                        if not isinstance(expected,datetime) and expected==(actual.date() if isinstance(actual,datetime) else actual):continue
                    if actual!=expected:raise ValueError('Не совпали сохранённые данные выгрузки.')
        if (_sha(target) if target.exists() else None)!=before:raise ValueError('Файл изменился во время подготовки выгрузки.')
        if before is None:publish(temporary,target)
        else:os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)
