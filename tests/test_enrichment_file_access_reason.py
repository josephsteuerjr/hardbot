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
