"""Типизированное сравнение значений по контракту поля."""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .config import FieldRule
from .normalize import comparable_value, json_value, parse_date, parse_number

_LITERAL_TAG = "__registry_cycle_literal__"


def _comparison_type(rule: FieldRule) -> str:
    return rule.comparison_type or rule.value_type


def _unpack_literal_baseline(value: Any) -> Any:
    if (
        isinstance(value, dict)
        and isinstance(value.get(_LITERAL_TAG), str)
        and "value" in value
    ):
        return value["value"]
    return value


def _literal_comparable(value: Any) -> tuple[str, Any]:
    """Сравнить значение без нормализации строк и распознавания дат.

    Этот контракт нужен для полей, где пользовательский ввод является данными:
    ``23.07.2026`` не равен Excel-дате 23.07.2026 и не должен незаметно
    превращаться в неё при круговом переносе.
    """
    if value is None or value == "":
        return "blank", None
    if isinstance(value, bool):
        return "bool", value
    if isinstance(value, datetime):
        if value.time().isoformat() == "00:00:00":
            return "date", value.date().isoformat()
        return "datetime", value.isoformat()
    if isinstance(value, date):
        return "date", value.isoformat()
    if isinstance(value, Decimal):
        return (
            "number",
            int(value) if value == value.to_integral() else float(value),
        )
    if isinstance(value, float):
        if math.isnan(value):
            return "blank", None
        return "number", value
    if isinstance(value, int):
        return "number", value
    if isinstance(value, str):
        return "text", value
    return type(value).__name__, str(value)


def comparable_for_rule(value: Any, rule: FieldRule) -> tuple[str, Any]:
    comparison_type = _comparison_type(rule)
    if comparison_type == "literal":
        if (
            isinstance(value, dict)
            and isinstance(value.get(_LITERAL_TAG), str)
            and "value" in value
        ):
            return value[_LITERAL_TAG], value["value"]
        return _literal_comparable(value)
    value = _unpack_literal_baseline(value)
    if comparison_type == "date":
        parsed = parse_date(value)
        if parsed is not None:
            return "date", parsed.isoformat()
        # Некорректные числовые значения в date-колонках всё равно должны
        # пережить round-trip без превращения Excel-форматом в 2191 год.
        kind, normalized = comparable_value(value)
        if kind == "number":
            return kind, normalized
        number = parse_number(value)
        if number is None:
            return comparable_value(value)
        return "number", number
    if comparison_type == "number":
        kind, normalized = comparable_value(value)
        if kind == "number":
            return kind, normalized
        number = parse_number(value)
        if number is None:
            return comparable_value(value)
        return "number", number
    return comparable_value(value)


def write_value_for_rule(value: Any, rule: FieldRule) -> Any:
    if rule.value_type == "literal":
        return value
    if rule.value_type == "date":
        return value
    if rule.value_type == "number":
        return value
    return value


def baseline_value_for_rule(value: Any, rule: FieldRule) -> Any:
    """Упаковать baseline без потери типа и исходной строки literal-поля."""
    written = write_value_for_rule(value, rule)
    if rule.value_type == "literal":
        kind, literal = _literal_comparable(written)
        return {_LITERAL_TAG: kind, "value": literal}
    return json_value(written)


def values_equal_for_rule(left: Any, right: Any, rule: FieldRule) -> bool:
    return comparable_for_rule(left, rule) == comparable_for_rule(right, rule)


def same_representation(left: Any, right: Any) -> bool:
    """Сравнить тип и представление без распознавания даты/числа."""
    return _literal_comparable(left) == _literal_comparable(right)


def normalized_text(value: Any) -> str:
    return " ".join(str(json_value(value) or "").strip().split()).casefold()


def is_text_superset(candidate: Any, existing: Any) -> bool:
    """True, только если candidate целиком содержит прежнее непустое значение."""
    candidate_text = normalized_text(candidate)
    existing_text = normalized_text(existing)
    return bool(existing_text) and existing_text in candidate_text
