"""Add the two date columns from Python, preserving native workbook structure.

No VBA source, project access, named event ranges or Office trust settings.
The caller owns local staging, all-cell preservation checks and publication.
"""
import edit_tracking as tracking
import re


def date_format(cells):
    cells._oleobj_.Invoke(cells._oleobj_.GetIDsOfNames('NumberFormat'),1033,4,0,'dd"."mm"."yyyy')


def save_staged_copy(book,destination):
    """SaveCopyAs preserves legacy per-user views that Save can normalize.

    Always write a different local candidate. The caller closes the original
    without saving and checks the saved copy before any live publication.
    """
    from pathlib import Path
    if Path(book.FullName).resolve()==Path(destination).resolve():raise ValueError('Нужен отдельный локальный кандидат')
    book.SaveCopyAs(str(Path(destination).resolve()))
    from .manual_workbooks import preserve_calculation_metadata
    preserve_calculation_metadata(book.FullName,destination)


def migrate_open_book(book):
    sheet=book.Worksheets('Портфели')
    if sheet.ProtectContents:raise ValueError('Лист защищён: нужна совместимая миграция без снятия защиты')
    width=sheet.UsedRange.Column+sheet.UsedRange.Columns.Count-1
    headers=None;header_row=None
    for row in range(1,11):
        values=list(sheet.Range(sheet.Cells(row,1),sheet.Cells(row,width)).Value2[0])
        if any(tracking.normalized(v)==tracking.normalized('Номер КД / ID') for v in values):
            headers=values;header_row=row;break
    if headers is None:raise ValueError('Не найден заголовок КД')
    existing=tracking.columns(headers)
    anchors={}
    for key,titles in tracking.BOUNDARIES.items():
        found=[i+1 for i,v in enumerate(headers) if tracking.normalized(v) in {tracking.normalized(t) for t in titles}]
        if len(found)>1:raise ValueError('Неоднозначный блок дат')
        if found:anchors[key]=found[0]
    if not anchors:raise ValueError('Нет блоков выезда и звонка')
    # Legacy shared workbooks can widen a user's hidden custom-view filter
    # name to the full UsedRange on Insert. Preserve its original scope and
    # translate only the inserted columns, rather than changing that view.
    view_filters=[]
    pattern=r"=?(?:'Портфели'|Портфели)!R\d+C\d+(?::R\d+C\d+)?"
    for i in range(1,book.Names.Count+1):
        name=book.Names(i)
        if name.Name.startswith(sheet.Name+'!') and name.Name.casefold().endswith('.wvu.filterdata'):
            reference=name.RefersToR1C1
            if not re.fullmatch(pattern,reference):raise ValueError('Неизвестная ссылка фильтра пользовательского вида')
            view_filters.append((name.Name,reference,bool(name.Visible)))
    inserted=[];positions=[]
    for key,column in sorted(anchors.items(),key=lambda pair:pair[1],reverse=True):
        if key in existing.values():continue
        sheet.Columns(column).Insert(Shift=-4161)
        sheet.Cells(header_row,column).Value2=tracking.CAPTION
        sheet.Cells(header_row+1,column).Value2='хард' if key==tracking.EXCEL_KEYS[0] else 'софт'
        sheet.Columns(column).ColumnWidth=18
        date_format(sheet.Columns(column))
        sheet.Cells(header_row,column).WrapText=True
        inserted.append(key)
        positions.append(column)
    for label,reference,visible in view_filters:
        translated=re.sub(r'C(\d+)',lambda m:'C'+str(int(m[1])+sum(p<=int(m[1]) for p in positions)),reference)
        name=book.Names.Item(label)
        name._oleobj_.Invoke(name._oleobj_.GetIDsOfNames('RefersToR1C1'),1033,4,0,translated)
        name.Visible=visible
    width=sheet.UsedRange.Column+sheet.UsedRange.Columns.Count-1
    headers=list(sheet.Range(sheet.Cells(header_row,1),sheet.Cells(header_row,width)).Value2[0])
    return dict(header_row=header_row,date_columns=tracking.columns(headers),inserted=inserted,
                macros_added=False,office_settings_changed=False)
