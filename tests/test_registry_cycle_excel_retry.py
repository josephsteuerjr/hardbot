import pytest

from registry_cycle.errors import SafetyViolation
from registry_cycle.excel_retry import retry_local_excel


class FakeComError(Exception):
    pass


FakeComError.__name__ = "com_error"
FakeComError.__module__ = "pywintypes"


def test_local_excel_retry_restarts_only_after_com_loss():
    calls = []

    def action(attempt):
        calls.append(attempt)
        if attempt == 1:
            raise FakeComError("lost Excel")
        return "ok"

    assert retry_local_excel(action, attempts=2) == "ok"
    assert calls == [1, 2]


def test_local_excel_retry_does_not_repeat_business_error():
    calls = []

    def action(attempt):
        calls.append(attempt)
        raise SafetyViolation("bad workbook")

    with pytest.raises(SafetyViolation, match="bad workbook"):
        retry_local_excel(action, attempts=3)
    assert calls == [1]
