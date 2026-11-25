from datetime import date
from pathlib import Path
import json
import pytest

from pult.core.manual_shared import SharedEngine,SharedBusy,file_mutex
from pult.core.manual_workbooks import read
from test_manual_workbooks_python import fixture
from test_manual_sync_python import edit


def clients(tmp_path):
    path=tmp_path/'portfolio.xlsx';fixture(path)
    a=SharedEngine(tmp_path/'journal',tmp_path/'pc-a')
    b=SharedEngine(tmp_path/'journal',tmp_path/'pc-b')
    a.scan(path)
    return path,a,b


def test_other_computer_sees_manual_date_and_excludes_automatic_write(tmp_path):
    path,a,b=clients(tmp_path)
    edit(path,'saved manual change')
    result=b.scan(path)
    assert result['writes']==1
    assert read(path).view.dates['T-1','excel_visit_edited_on'][2] is not None
    with b.automatic(path):edit(path,'automatic on another PC')
    result=a.scan(path)
    assert result['writes']==0 and result['pending']==0


def test_pending_date_is_shared_until_workbook_closes(tmp_path,monkeypatch):
    path,a,b=clients(tmp_path)
    edit(path,'manual saved while open')
    import pult.core.manual_sync as sync
    original=sync._writable
    def busy(_path):raise ValueError('open')
    monkeypatch.setattr(sync,'_writable',busy)
    result=a.scan(path)
    assert result['writes']==0 and result['pending']==1
    monkeypatch.setattr(sync,'_writable',original)
    result=b.scan(path)
    assert result['writes']==1 and result['pending']==0


def test_unknown_result_on_other_computer_is_not_retried(tmp_path):
    path,a,b=clients(tmp_path)
    with pytest.raises(RuntimeError):
        with b.automatic(path):
            edit(path,'written before lost result')
            raise RuntimeError('result lost')
    with pytest.raises(ValueError,match='неизвестен'):
        a.scan(path)


def test_network_directory_has_only_primary_metadata_and_receipts(tmp_path):
    path,a,b=clients(tmp_path)
    edit(path,'new manual value');b.scan(path)
    files=[p for p in (tmp_path/'journal').rglob('*') if p.is_file()]
    assert files and all(p.suffix in ('.json','.lock') for p in files)
    assert list((tmp_path/'pc-b'/'backups').rglob('*.xlsx'))


def test_same_book_global_mutex_prevents_two_writers(tmp_path):
    lock=tmp_path/'ledger.lock'
    with file_mutex(lock):
        with pytest.raises(SharedBusy):
            with file_mutex(lock):pass


def test_torn_slot_recovers_known_automatic_state_from_receipt(tmp_path):
    path,a,b=clients(tmp_path)
    with b.automatic(path):edit(path,'known automatic update')
    folder=a.journal.book_directory(path)
    (folder/'ledger-0.json').write_text('{',encoding='utf-8')
    result=a.scan(path)
    assert result['writes']==0
    assert read(path).view.dates['T-1','excel_visit_edited_on'][2] is None


def test_lost_final_receipt_uses_verified_slot_without_rewriting_book(tmp_path,monkeypatch):
    path,a,b=clients(tmp_path)
    def lost(*args,**kwargs):raise RuntimeError('receipt lost')
    monkeypatch.setattr(b.journal,'finish',lost)
    with pytest.raises(RuntimeError,match='receipt'):
        with b.automatic(path):edit(path,'known automatic update')
    before=path.read_bytes()
    result=a.scan(path)
    assert result['writes']==0 and path.read_bytes()==before


def test_new_automatically_generated_file_has_no_fake_edit_history(tmp_path):
    path=tmp_path/'new.xlsx'
    engine=SharedEngine(tmp_path/'journal',tmp_path/'pc')
    with engine.automatic(path):fixture(path)
    assert engine.scan(path)['writes']==0
    assert read(path).view.dates['T-1','excel_visit_edited_on'][2] is None
