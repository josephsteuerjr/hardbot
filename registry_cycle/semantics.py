"""Типизированное сравнение значений по контракту поля."""

from __future__ import annotations

from typing import Any

from .config import FieldRule
from .normalize import comparable_value, parse_date, parse_number


def comparable_for_rule(value: Any, rule: FieldRule) -> tuple[str, Any]:
    if rule.value_type == "date":
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
    if rule.value_type == "number":
        kind, normalized = comparable_value(value)
        if kind == "number":
            return kind, normalized
        number = parse_number(value)
        if number is None:
            return comparable_value(value)
        return "number", number
    return comparable_value(value)


def write_value_for_rule(value: Any, rule: FieldRule) -> Any:
    if rule.value_type == "date":
        return parse_date(value) or value
    if rule.value_type == "number":
        return parse_number(value) if parse_number(value) is not None else value
    return value


def values_equal_for_rule(left: Any, right: Any, rule: FieldRule) -> bool:
    return comparable_for_rule(left, rule) == comparable_for_rule(right, rule)


def normalized_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def is_text_superset(candidate: Any, existing: Any) -> bool:
    """True, только если candidate целиком содержит прежнее непустое значение."""
    candidate_text = normalized_text(candidate)
    existing_text = normalized_text(existing)
    return bool(existing_text) and existing_text in candidate_text
