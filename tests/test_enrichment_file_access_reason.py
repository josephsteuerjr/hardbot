import os
from pathlib import Path
import pytest
from registry_cycle import file_access
from registry_cycle.errors import SafetyViolation

@pytest.mark.skipif(os.name!='nt',reason='Windows sharing error codes')
def test_missing_file_is_not_reported_as_open_excel(tmp_path):
    result=file_access.inspect_busy_file(tmp_path/'absent.xlsx')
    assert result is not None and result.kind=='missing'

@pytest.mark.skipif(os.name!='nt',reason='Windows access error codes')
@pytest.mark.parametrize('code,kind',[(5,'access'),(32,'locked'),(33,'locked')])
def test_access_and_sharing_failures_are_distinct(tmp_path,monkeypatch,code,kind):
    import ctypes
    def failure(path):
        try:raise ctypes.WinError(code)
        except OSError as cause:raise SafetyViolation('synthetic access check') from cause
    monkeypatch.setattr(file_access,'exclusive_file_check',failure)
    assert file_access.inspect_busy_file(tmp_path/'workbook.xlsx').kind==kind

@pytest.mark.skipif(os.name!='nt',reason='Windows exclusive handles')
def test_unheld_excel_marker_is_not_a_busy_workbook_and_is_not_deleted(tmp_path):
    path=tmp_path/'workbook.xlsx';path.write_bytes(b'synthetic')
    marker=file_access.excel_lock_path(path);marker.write_bytes(b'old marker')
    assert file_access.inspect_busy_file(path) is None
    assert marker.read_bytes()==b'old marker'

@pytest.mark.skipif(os.name!='nt',reason='Windows exclusive handles')
@pytest.mark.parametrize('held_file',['workbook','marker'])
def test_actual_workbook_or_marker_handle_still_blocks_transfer(tmp_path,held_file):
    import win32file,win32con
    path=tmp_path/'workbook.xlsx';path.write_bytes(b'synthetic')
    marker=file_access.excel_lock_path(path);marker.write_bytes(b'marker')
    held=path if held_file=='workbook' else marker
    handle=win32file.CreateFile(str(held),win32con.GENERIC_READ,
                              win32con.FILE_SHARE_READ,None,
                              win32con.OPEN_EXISTING,win32con.FILE_ATTRIBUTE_NORMAL,None)
    try:
        result=file_access.inspect_busy_file(path)
        assert result is not None and result.kind=='locked'
        assert marker.exists()
    finally:
        handle.Close()
    assert file_access.inspect_busy_file(path) is None
