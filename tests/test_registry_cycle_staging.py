from datetime import date, datetime

from registry_cycle.staging import excel_safe_value


def test_excel_safe_value_forces_strings_to_remain_text():
    assert excel_safe_value("24.07") == "'24.07"
    assert excel_safe_value("=1+1") == "'=1+1"
    assert excel_safe_value("  ", "date") is None


def test_excel_safe_value_preserves_early_excel_serial():
    assert excel_safe_value(date(1900, 1, 11), "date") == 11
    assert excel_safe_value(datetime(1900, 1, 17, 12, 0), "date") == 17
    assert excel_safe_value(date(2026, 7, 27), "date") == 46230
    assert excel_safe_value(datetime(2026, 7, 27, 12, 0)) == 46230.5


def test_excel_safe_value_keeps_text_dates_textual():
    assert excel_safe_value(date(2026, 5, 30), "text") == "'2026-05-30"
    assert excel_safe_value(106500.0, "date") == "'106500"
