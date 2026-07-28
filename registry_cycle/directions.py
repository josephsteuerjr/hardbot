"""Проверка визуального контракта направлений полей."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import FieldConfig
from .excel_host import ExcelHost, sheet_by_name
from .models import Issue, WorkbookSnapshot
from .normalize import norm_title


def _rgb(value: Any) -> str:
    integer = int(value)
    red = integer & 0xFF
    green = (integer >> 8) & 0xFF
    blue = (integer >> 16) & 0xFF
    return f"#{red:02X}{green:02X}{blue:02X}"


def _canonical_title(fields: FieldConfig, value: Any) -> str:
    normalized = norm_title(value)
    for canonical, aliases in fields.aliases.items():
        if normalized in {
            norm_title(canonical),
            *(norm_title(alias) for alias in aliases),
        }:
            return canonical
    return str(value or "").strip()


def _column_profiles(
    snapshot: WorkbookSnapshot,
    fields: FieldConfig,
) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for index, raw_tag in enumerate(snapshot.type_tags):
        tag = str(raw_tag or "").strip().casefold()
        profiles = {
            profile
            for profile, allowed_tags in fields.profile_tags.items()
            if tag in set(allowed_tags)
        }
        if profiles:
            out[index] = profiles
    return out


def validate_direction_contract(
    snapshot: WorkbookSnapshot,
    fields: FieldConfig,
    *,
    header_rgb: dict[int, str] | None = None,
) -> list[Issue]:
    """Сверить collect с профильными столбцами и жёлтой заливкой."""
    issues: list[Issue] = []
    profile_columns = _column_profiles(snapshot, fields)
    rules = {
        norm_title(rule.title): rule
        for rule in fields.collect
    }
    header_columns = {
        norm_title(_canonical_title(fields, snapshot.headers[index])): index
        for index in profile_columns
    }
    missing = [
        rule.title
        for normalized, rule in rules.items()
        if normalized not in header_columns
    ]
    if missing:
        issues.append(
            Issue(
                "error",
                "employee_owned_columns_missing",
                "в общем реестре нет настроенных полей сотрудника",
                {"titles": missing},
            )
        )

    profile_mismatches = []
    for normalized, rule in rules.items():
        column = header_columns.get(normalized)
        if column is None:
            continue
        actual_profiles = profile_columns[column]
        expected_profiles = set(rule.profiles)
        if actual_profiles != expected_profiles:
            profile_mismatches.append(
                {
                    "title": rule.title,
                    "configured": sorted(expected_profiles),
                    "from_type_row": sorted(actual_profiles),
                }
            )
    if profile_mismatches:
        issues.append(
            Issue(
                "error",
                "employee_owned_profile_mismatch",
                "профили жёлтых полей не совпали со строкой типов",
                {"fields": profile_mismatches},
            )
        )

    if header_rgb is None:
        return issues
    employee_colors = set(fields.employee_header_fill_rgb)
    yellow_columns = {
        index
        for index in profile_columns
        if header_rgb.get(index, "").upper() in employee_colors
    }
    yellow_titles = {
        norm_title(_canonical_title(fields, snapshot.headers[index]))
        for index in yellow_columns
    }
    configured_titles = set(rules)
    missing_yellow = [
        rules[title].title
        for title in sorted(configured_titles - yellow_titles)
    ]
    unexpected_yellow = [
        str(snapshot.headers[index] or "").strip()
        for index in sorted(yellow_columns)
        if norm_title(_canonical_title(fields, snapshot.headers[index]))
        not in configured_titles
    ]
    if missing_yellow or unexpected_yellow:
        issues.append(
            Issue(
                "error",
                "employee_header_color_mismatch",
                "жёлтая разметка общего реестра не совпала с матрицей "
                "полей сотрудника",
                {
                    "configured_but_not_yellow": missing_yellow,
                    "yellow_but_not_configured": unexpected_yellow,
                    "employee_colors": sorted(employee_colors),
                },
            )
        )
    return issues


def validate_master_header_directions(
    path: str | Path,
    snapshot: WorkbookSnapshot,
    fields: FieldConfig,
) -> list[Issue]:
    profile_columns = _column_profiles(snapshot, fields)
    if not fields.strict_header_color_validation:
        return validate_direction_contract(snapshot, fields)
    colors: dict[int, str] = {}
    try:
        with ExcelHost() as host:
            with host.open_book(path, read_only=True) as book:
                sheet = sheet_by_name(book, fields.sheet_name)
                for index in profile_columns:
                    raw = sheet.range(
                        (snapshot.header_row + 1, index + 1)
                    ).api.Interior.Color
                    colors[index] = _rgb(raw)
    except Exception as exc:  # noqa: BLE001 — проверка обязана fail closed
        return [
            Issue(
                "error",
                "employee_header_color_read_failed",
                "не удалось проверить цвета заголовков на локальной копии",
                {"path": str(path), "error": str(exc)},
            )
        ]
    return validate_direction_contract(
        snapshot,
        fields,
        header_rgb=colors,
    )
