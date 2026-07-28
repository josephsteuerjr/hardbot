from pathlib import Path

from registry_cycle.config import FieldConfig, FieldRule
from registry_cycle.directions import validate_direction_contract
from registry_cycle.models import FileFingerprint, WorkbookSnapshot


def _snapshot() -> WorkbookSnapshot:
    return WorkbookSnapshot(
        path=Path("master.xlsb"),
        sheet="Портфели",
        sheet_names=("Портфели",),
        fingerprint=FileFingerprint(1, 1, "a" * 64),
        header_row=0,
        type_row=1,
        data_start=2,
        headers=(
            "Номер КД / ID",
            "ответственный Сотрудник",
            "Телефон",
            "Телефон глав",
            "Дата выезда",
        ),
        type_tags=("сх", "сх", "сх", "сх", "хард"),
        rows=(),
    )


def _fields(*, include_main_phone: bool = True) -> FieldConfig:
    rules = [
        FieldRule(
            "Дата выезда",
            ("hard",),
            value_type="literal",
        )
    ]
    if include_main_phone:
        rules.insert(
            0,
            FieldRule("Телефон глав", ("hard", "soft")),
        )
    return FieldConfig(
        sheet_name="Портфели",
        statistics_sheet="Статистика",
        status_sheet="Статусы",
        key_title="Номер КД / ID",
        responsible_title="ответственный Сотрудник",
        header_search_rows=10,
        type_row_offset=1,
        profile_tags={
            "hard": ("сх", "хард"),
            "soft": ("сх", "софт"),
        },
        collect=tuple(rules),
        preserve_if_master_blank=("Телефон",),
        aliases={},
    )


def test_direction_contract_matches_yellow_employee_columns_exactly():
    issues = validate_direction_contract(
        _snapshot(),
        _fields(),
        header_rgb={
            0: "#BFBFBF",
            1: "#BFBFBF",
            2: "#BFBFBF",
            3: "#FFFF00",
            4: "#FFFF00",
        },
    )

    assert not issues


def test_direction_contract_detects_old_missing_main_phone_rule():
    issues = validate_direction_contract(
        _snapshot(),
        _fields(include_main_phone=False),
        header_rgb={
            0: "#BFBFBF",
            1: "#BFBFBF",
            2: "#BFBFBF",
            3: "#FFFF00",
            4: "#FFFF00",
        },
    )

    issue = next(
        item
        for item in issues
        if item.code == "employee_header_color_mismatch"
    )
    assert issue.context["yellow_but_not_configured"] == ["Телефон глав"]
