from datetime import date
from pathlib import Path

import pytest

from pult.core.manual_edits import Store
from pult.core.manual_sync import observe_saved,flush_dates,automatic_write
from pult.core.manual_workbooks import read
from test_manual_workbooks_python import fixture


def edit(path,value):
    from openpyxl import load_workbook
    wb=load_workbook(path);wb['Портфели']['D4']=value;wb.save(path)


def test_saved_xlsx_date_end_to_end_without_macro(tmp_path):
    path=tmp_path/'portfolio.xlsx';fixture(path)
    store=Store(tmp_path/'state.sqlite3')
    _,first=observe_saved(store,path,continuous=False)
    assert first.calibrated and not store.pending(path)
    edit(path,'manual edit')
    _,observed=observe_saved(store,path,continuous=True)
    assert observed.writes and store.pending(path)
    before=read(path)
    result=flush_dates(store,path)
    after=read(path)
    assert result['writes']==1
    assert after.view.cells==before.view.cells
    assert after.view.dates['T-1','excel_visit_edited_on'][2] is not None
    assert not store.pending(path) and store.lease(path) is None
    assert Path(result['receipt']).is_file()


def test_automatic_python_writer_never_stamps_manual_date(tmp_path):
    path=tmp_path/'portfolio.xlsx';fixture(path)
    store=Store(tmp_path/'state.sqlite3')
    with automatic_write(path,store=store,operation_id='qa-auto'):
        edit(path,'known automatic write')
    _,observed=observe_saved(store,path,continuous=True)
    assert observed is None and not store.pending(path)
    assert store.lease(path) is None
    assert read(path).view.dates['T-1','excel_visit_edited_on'][2] is None


def test_failed_unchanged_automatic_write_releases_lease(tmp_path):
    path=tmp_path/'portfolio.xlsx';fixture(path)
    store=Store(tmp_path/'state.sqlite3')
    with pytest.raises(RuntimeError):
        with automatic_write(path,store=store,operation_id='qa-fail'):
            raise RuntimeError('before writing')
    assert store.lease(path) is None


def test_changed_unknown_automatic_failure_retains_receipt_gate(tmp_path):
    path=tmp_path/'portfolio.xlsx';fixture(path)
    store=Store(tmp_path/'state.sqlite3')
    with pytest.raises(RuntimeError):
        with automatic_write(path,store=store,operation_id='qa-unknown'):
            edit(path,'unknown published state')
            raise RuntimeError('lost result')
    assert store.lease(path)[0]=='qa-unknown'
    with pytest.raises(ValueError,match='автоматическая'):
        observe_saved(store,path,continuous=True)
