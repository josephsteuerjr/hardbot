from datetime import date, datetime

from registry_cycle.normalize import (
    comparable_value,
    is_blank,
    norm_key,
    norm_name,
    norm_title,
    parse_date,
    parse_number,
    values_equal,
)


def test_titles_handle_spaces_and_latin_homoglyph():
    assert norm_title("  Cтатус   звонка: ") == norm_title("Статус звонка")


def test_keys_and_names_are_stable():
    assert norm_key(123.0) == "123"
    assert norm_key(" 123 ") == "123"
    assert norm_name(" Иванов\u00a0 Иван ") == "иванов иван"


def test_blank_and_dates_are_typed():
    assert is_blank("   ")
    assert comparable_value(datetime(2026, 7, 27)) == ("text", "2026-07-27")
    assert values_equal(date(2026, 7, 27), datetime(2026, 7, 27))
    assert not values_equal("1", 1)


def test_parse_date_accepts_human_trailing_punctuation():
    assert str(parse_date("23.07.2026.")) == "2026-07-23"
    assert str(parse_date("30,06,2026")) == "2026-06-30"


def test_parse_number_accepts_safe_currency_suffixes():
    assert parse_number("16 347,80р") == 16347.8
    assert parse_number("23 000 руб.") == 23000
    assert parse_number("не число") is None
