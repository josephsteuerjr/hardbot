"""Modify only the explicitly isolated native QA export, through xlwings."""
from pathlib import Path
import json,sys,os
from python_calamine import CalamineWorkbook
import pythoncom
import xlwings as xw
path=Path(sys.argv[1]).resolve()
qa=Path(os.environ['HARD_CRM_QA_ROOT']).resolve()
assert path.parent==qa and path.suffix=='.xlsx' and path.name.startswith('analyst-report-')
assert (qa/'profile/synthetic').is_dir(), 'An isolated CRM test profile is required'
book=CalamineWorkbook.from_path(str(path))
meta=json.loads(''.join(str(r[0]) for r in book.get_sheet_by_name('_CRM').to_python() if r and r[0]))
column=int(meta['columns']['phone'])
sheet_name=next(name for name in book.sheet_names if not name.startswith('_'))
pythoncom.CoInitialize();app=None
try:
    app=xw.App(visible=False,add_book=False);app.display_alerts=False;app.api.AutomationSecurity=3;app.api.EnableEvents=False
    workbook=app.books.open(str(path),update_links=False)
    try:
        sheet=workbook.sheets[sheet_name];sheet.cells(4,column).number_format='@';sheet.cells(4,column).value='1234509876';workbook.save()
    finally:workbook.close()
finally:
    if app:app.quit()
    pythoncom.CoUninitialize()
