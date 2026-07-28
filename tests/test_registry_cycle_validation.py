from datetime import date, datetime

from registry_cycle.config import FieldRule
from registry_cycle.validation import _master_change_value_matches


def _rule(value_type: str) -> FieldRule:
    return FieldRule(
        title="Дата",
        profiles=("hard",),
        value_type=value_type,
    )


def test_master_validation_accepts_exact_excel_date_serial_roundtrip():
    assert _master_change_value_matches(
        46230,
        date(2026, 7, 27),
        _rule("date"),
    )
    assert _master_change_value_matches(
        207.2,
        datetime(1900, 7, 25, 4, 48),
        _rule("date"),
    )


def test_master_validation_does_not_confuse_literal_text_and_serial():
    assert not _master_change_value_matches(
        46230,
        "2026-07-27",
        _rule("literal"),
    )
    assert not _master_change_value_matches(
        46229,
        date(2026, 7, 27),
        _rule("date"),
    )
