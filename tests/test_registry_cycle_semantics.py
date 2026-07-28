from datetime import date, datetime

from registry_cycle.config import FieldRule
from registry_cycle.semantics import (
    same_representation,
    values_equal_for_rule,
)


def test_date_semantics_do_not_hide_representation_change():
    rule = FieldRule(
        title="Дата",
        profiles=("hard",),
        value_type="date",
    )

    assert values_equal_for_rule(
        "23.07.2026",
        date(2026, 7, 23),
        rule,
    )
    assert not same_representation(
        "23.07.2026",
        date(2026, 7, 23),
    )


def test_literal_semantics_preserve_whitespace_and_type():
    rule = FieldRule(
        title="Дата выезда",
        profiles=("hard",),
        value_type="literal",
    )

    assert not values_equal_for_rule(
        " 23.07.2026 ",
        "23.07.2026",
        rule,
    )
    assert not values_equal_for_rule(
        "2026-07-23",
        date(2026, 7, 23),
        rule,
    )
    assert values_equal_for_rule(None, "", rule)
    assert not values_equal_for_rule(None, " ", rule)
    assert values_equal_for_rule(
        date(2026, 7, 23),
        datetime(2026, 7, 23),
        rule,
    )
    assert values_equal_for_rule(1, 1.0, rule)
