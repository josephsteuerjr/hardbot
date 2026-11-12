"""Native xlwings report files with atomic publication and saved-file readback."""
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
import hashlib,json,os,uuid


@contextmanager
def excel():
    import pythoncom
    import xlwings as xw
    pythoncom.CoInitialize();app=None
    try:
        app=xw.App(visible=False,add_book=False);app.display_alerts=False
        app.api.AutomationSecurity=3;app.api.EnableEvents=False;app.api.AskToUpdateLinks=False
        yield app
    finally:
        if app is not None:app.quit()
        pythoncom.CoUninitialize()


def table(sheet,headers,rows):
    sheet.range('A:A').number_format='@'
    sheet.range((1,1),(1,len(headers))).value=[headers]
    if rows:sheet.range((2,1),(len(rows)+1,len(headers))).value=rows
    sheet.range((1,1),(1,len(headers))).color='#E9DACA'
    sheet.range((1,1),(1,len(headers))).api.Font.Bold=True
    sheet.used_range.api.Font.Name='Arial';sheet.used_range.api.Font.Size=10
    sheet.used_range.autofit();sheet.book.app.api.ActiveWindow.DisplayGridlines=False


def number_format(area,value):
    # Excel's IDispatch LCID=0 parses formats in the Windows UI language here.
    # Set invariant format codes using the explicit en-US automation locale.
    import pythoncom
    ole=area.api._oleobj_
    ole.Invoke(ole.GetIDsOfNames('NumberFormat'),1033,pythoncom.DISPATCH_PROPERTYPUT,False,value)


def verify(path,report,kind):
    from python_calamine import CalamineWorkbook
    saved=CalamineWorkbook.from_path(str(path))
    raw=''.join(str(row[0]) for row in saved.get_sheet_by_name('_CRM_REPORT').to_python() if row and row[0]!='')
    if json.loads(raw)!=json.loads(json.dumps(report)):raise ValueError('Существующий файл относится к другому снимку отчёта.')
    if kind=='three-hour':
        for data in report['sheets']:
            actual=saved.get_sheet_by_name(data['name']).to_python()
            for r,item in enumerate(data['rows'],2):
                expected=[item['employee']]
                for before,after in zip(item['yesterday'],item['current']):
                    expected.extend([None if before is None else float(before),float(after),
                        None if before is None else float(after)/float(before) if float(before) else 0])
                for col,value in enumerate(expected):
                    got=actual[r][col]
                    if value is None and got=='':continue
                    if isinstance(value,(float,int)) and isinstance(got,(float,int)) and abs(got-value)<0.000001:continue
                    if got!=value:raise ValueError('Не совпали сохранённые данные сводки.')
    else:
        actual=saved.get_sheet_by_name('Свод смен').to_python()
        for r,item in enumerate(report['employees'],1):
            expected=[item['employee'],item['shifts'],item['declared_seconds']/3600,item['break_seconds']/3600,item['unclosed']]
            for col,value in enumerate(expected):
                got=actual[r][col]
                if isinstance(value,(float,int)) and isinstance(got,(float,int)) and abs(got-value)<0.000001:continue
                if got!=value:raise ValueError('Не совпал сохранённый отчёт смен.')
    for name in saved.sheet_names:
        if name.startswith('_'):continue
        for row in saved.get_sheet_by_name(name).to_python():
            if any(str(value) in {'#REF!','#DIV/0!','#VALUE!','#NAME?','#NUM!','#N/A','#SPILL!','#CALC!'} for value in row):
                raise ValueError('Excel сохранил ошибку расчёта отчёта.')
    return True


def receipt(path):
    with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
    return dict(path=str(path),sha256=sha,verified=True)


def write(report,path,kind='shifts'):
    target=Path(path).resolve()
    if target.suffix.lower()!='.xlsx':raise ValueError('Выберите имя XLSX.')
    if target.exists():verify(target,report,kind);return receipt(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name('.'+target.stem+'.'+uuid.uuid4().hex+'.xlsx')
    expected=[]
    with excel() as app:
        book=app.books.add()
        try:
            for sheet in list(book.sheets)[1:]:sheet.delete()
            if kind=='shifts':
                sheet=book.sheets[0];sheet.name='Свод смен'
                rows=[[r['employee'],r['shifts'],r['declared_seconds']/3600,r['break_seconds']/3600,r['unclosed']] for r in report['employees']]
                table(sheet,['Сотрудник','Смен','Работа, часов','Перерывы, часов','Не закрыто'],rows)
                number_format(sheet.range('C:D'),'0.00')
                detail=book.sheets.add('Смены',after=sheet)
                rows=[[r['employee'],r['workday'],r['starts_at'],r['ends_at'] or 'Не закрыта',r['timezone'],r['declared_seconds']/3600,r['break_seconds']/3600,r['state']] for r in report['shifts']]
                table(detail,['Сотрудник','Дата смены','Начало','Окончание','Часовой пояс','Работа в периоде, часов','Перерывы в периоде, часов','Состояние'],rows)
                number_format(detail.range('F:G'),'0.00')
            elif kind=='three-hour':
                for index,data in enumerate(report['sheets']):
                    sheet=book.sheets[0] if index==0 else book.sheets.add(after=book.sheets[-1])
                    sheet.name=data['name'];top=['Сотрудник'];headers=['']
                    for title,count in report['groups']:
                        label=title+(' sms' if data['sms'] and title!='Встречи' else '')
                        top.extend([label]+['']*(count*3-1));headers.extend(['Вчера','Сейчас','%']*count)
                    rows=[]
                    for row_index,item in enumerate(data['rows'],3):
                        row=[item['employee']]
                        for position,(before,after) in enumerate(zip(item['yesterday'],item['current'])):
                            col=position*3+2
                            row.extend([None if before is None else float(before),float(after),None])
                        rows.append(row)
                    sheet.range('A:A').number_format='@'
                    sheet.range('A1:AE2').value=[top,headers]
                    column=2
                    for _,count in report['groups']:
                        sheet.range((1,column),(1,column+count*3-1)).merge();column+=count*3
                    sheet.range('B1:AE2').api.HorizontalAlignment=-4108
                    if rows:sheet.range((3,1),(len(rows)+2,31)).value=rows
                    from xlwings.utils import col_name
                    for r in range(3,len(rows)+3):
                        for col in range(2,32,3):
                            a,b=col_name(col),col_name(col+1)
                            sheet.cells(r,col+2).formula=f'=IF({a}{r}="","",IF({a}{r}=0,0,{b}{r}/{a}{r}))'
                    last=len(rows)+3;sheet.cells(last,1).value='Итого'
                    for col in range(2,32):
                        letter=col_name(col)
                        if (col-2)%3==0:
                            formula=f'=IF(COUNT({letter}3:{letter}{last-1})<COUNTA(A3:A{last-1}),"",SUM({letter}3:{letter}{last-1}))'
                        elif (col-1)%3==0:
                            a,b=col_name(col-2),col_name(col-1)
                            formula=f'=IF({a}{last}="","",IF({a}{last}=0,0,{b}{last}/{a}{last}))'
                        else:formula=f'=SUM({letter}3:{letter}{last-1})'
                        sheet.cells(last,col).formula=formula
                    sheet.used_range.api.Font.Name='Arial';sheet.used_range.api.Font.Size=10
                    sheet.range('A1:AE2').api.Font.Bold=True;sheet.range('A1:AE2').color='#D9E1F2'
                    sheet.range((last,1),(last,31)).api.Font.Bold=True
                    sheet.range('A:A').column_width=29;sheet.range('B:AE').column_width=13
                    sheet.range('A1:AE2').api.WrapText=True;sheet.range('A1:AE2').row_height=34
                    number_format(sheet.range((3,2),(last,31)),'#,##0.00')
                    for metric in (0,1,2,4,6,8):number_format(sheet.range((3,metric*3+2),(last,metric*3+3)),'#,##0')
                    for col in range(4,32,3):number_format(sheet.range((3,col),(last,col)),'0.0%')
                    sheet.api.PageSetup.Orientation=2;sheet.api.PageSetup.Zoom=False
                    sheet.api.PageSetup.FitToPagesWide=1;sheet.api.PageSetup.FitToPagesTall=False
                    sheet.api.PageSetup.PrintTitleRows='$1:$2'
                    sheet.api.PageSetup.LeftFooter=f"{report['today']} {report['timezone']} · {report['currency'] or 'Валюта не указана'}"
                    sheet.cells(last+2,1).value=f"{report['as_of']} · {report['timezone']} · {report['currency'] or 'Валюта не указана'}"
                    if report.get('yesterday_incomplete_contracts'):
                        sheet.cells(last+3,1).value=f"Нет полного снимка предыдущего дня: {report['yesterday_incomplete_contracts']} договоров. Пустые суммы и проценты не рассчитаны."
                    expected.append((data['name'],rows))
            else:raise ValueError('Неизвестный отчёт.')
            metadata=book.sheets.add('_CRM_REPORT',after=book.sheets[-1]);raw=json.dumps(report,ensure_ascii=False,separators=(',',':'))
            chunks=[raw[i:i+30000] for i in range(0,len(raw),30000)]
            metadata.range((1,1),(len(chunks),1)).number_format='@';metadata.range((1,1),(len(chunks),1)).value=[[v] for v in chunks]
            metadata.api.Visible=2
            book.sheets[0].activate();app.calculate();book.save(str(temp))
        finally:book.close()
    verify(temp,report,kind)
    # Do not replace an unrelated file created while Excel was saving.
    if target.exists():raise ValueError('Имя отчёта уже занято; проверенный файл сохранён рядом.')
    os.rename(temp,target)
    return receipt(target)
