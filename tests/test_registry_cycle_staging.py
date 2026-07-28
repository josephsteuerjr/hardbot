from datetime import date, datetime

from registry_cycle.staging import (
    _is_general_number_format,
    _single_column_values,
    excel_safe_value,
)


def test_excel_safe_value_forces_strings_to_remain_text():
    assert excel_safe_value("24.07") == "'24.07"
    assert excel_safe_value("=1+1") == "'=1+1"
    assert excel_safe_value("  ", "date") is None


def test_excel_safe_value_preserves_early_excel_serial():
    assert excel_safe_value(date(1900, 1, 11), "date") == 11
    assert excel_safe_value(datetime(1900, 1, 17, 12, 0), "date") == 17.5
    assert excel_safe_value(date(2026, 7, 27), "date") == 46230
    assert excel_safe_value(datetime(2026, 7, 27, 12, 0)) == 46230.5


def test_excel_safe_value_keeps_text_dates_textual():
    assert excel_safe_value(date(2026, 5, 30), "text") == "'2026-05-30"
    assert excel_safe_value(106500.0, "date") == 106500.0
    assert excel_safe_value("23.07.2026", "date") == "'23.07.2026"


def test_excel_safe_value_keeps_literal_date_input_shape():
    assert excel_safe_value("23.07.2026", "literal") == "'23.07.2026"
    assert excel_safe_value("23.07..2026", "literal") == "'23.07..2026"
    assert excel_safe_value(".", "literal") == "'."
    assert excel_safe_value(date(2026, 7, 23), "literal") == 46226


def test_xlwings_single_column_matrix_is_flattened_for_preflight():
    assert _single_column_values("one", 1) == ["one"]
    assert _single_column_values([["one"]], 1) == ["one"]
    assert _single_column_values([["one"], ["two"], [None]], 3) == [
        "one",
        "two",
        None,
    ]


def test_date_datetime_keeps_fractional_excel_serial():
    assert (
        excel_safe_value(
            datetime(1900, 7, 25, 4, 48),
            "date",
        )
        == 207.2
    )


def test_general_number_format_recognizes_russian_excel_locale():
    assert _is_general_number_format("General")
    assert _is_general_number_format("Общий")
    assert _is_general_number_format("Основной")
    assert not _is_general_number_format("ДД.ММ.ГГГГ")
