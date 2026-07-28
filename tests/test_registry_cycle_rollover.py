from dataclasses import replace
from datetime import datetime
from pathlib import Path

from registry_cycle.config import FieldConfig, FieldRule, MonthRolloverConfig
from registry_cycle.models import FileFingerprint, WorkbookSnapshot
from registry_cycle.planner import build_sync_plan
from registry_cycle.rollover import (
    automatic_rollover_month,
    build_month_rollover_plan,
)


def _fingerprint() -> FileFingerprint:
    return FileFingerprint(size=1, mtime_ns=1, sha256="a" * 64)


def _snapshot(headers, types, rows) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        path=Path("master.xlsb"),
        sheet="Портфели",
        sheet_names=("Портфели",),
        fingerprint=_fingerprint(),
        header_row=0,
        type_row=1,
        data_start=2,
        headers=tuple(headers),
        type_tags=tuple(types),
        rows=tuple(tuple(row) for row in rows),
    )


def _fields() -> FieldConfig:
    return FieldConfig(
        sheet_name="Портфели",
        statistics_sheet="Статистика",
        status_sheet="Статусы",
        key_title="Номер КД / ID",
        responsible_title="ответственный Сотрудник",
        header_search_rows=10,
        type_row_offset=1,
        profile_tags={"hard": ("сх",)},
        collect=(
            FieldRule(
                title="Сумма последнего платежа",
                profiles=("hard",),
                value_type="number",
                invalid="preserve",
            ),
            FieldRule(
                title="Дата последнего платежа",
                profiles=("hard",),
                value_type="date",
                invalid="preserve",
            ),
        ),
        preserve_if_master_blank=(),
        aliases={},
        month_rollover=MonthRolloverConfig(
            enabled=True,
            trigger="last_calendar_day",
            current_payments_title="Текущие платежи",
            history_title_template="Оплаты за {month:02d}.{year2:02d}",
            employee_clear_titles=(
                "Сумма последнего платежа",
                "Дата последнего платежа",
            ),
        ),
    )


def test_automatic_rollover_only_fires_on_last_calendar_day():
    config = _fields().month_rollover

    assert automatic_rollover_month(
        config,
        datetime(2026, 7, 30, 23, 40),
    ) is None
    assert automatic_rollover_month(
        config,
        datetime(2026, 7, 31, 23, 40),
    ) == "2026-07"


def test_rollover_inserts_history_immediately_before_current_payments():
    master = _snapshot(
        (
            "Номер КД / ID",
            "ответственный Сотрудник",
            "Текущие платежи",
        ),
        ("сх", "сх", ""),
        (("1", "Иванов Иван", 1250), ("2", "Иванов Иван", None)),
    )

    rollover, issues = build_month_rollover_plan(
        master,
        _fields(),
        "2026-07",
    )

    assert not issues
    assert rollover is not None
    assert rollover.mode == "insert"
    assert rollover.history_title == "Оплаты за 07.26"
    assert rollover.history_column == 2
    assert rollover.current_column == 2
    assert [(cell.row, cell.value) for cell in rollover.archive_cells] == [
        (2, 1250)
    ]


def test_rollover_retry_is_idempotent_when_history_already_complete():
    master = _snapshot(
        (
            "Номер КД / ID",
            "ответственный Сотрудник",
            "Оплаты за 07.26",
            "Текущие платежи",
        ),
        ("сх", "сх", "", ""),
        (("1", "Иванов Иван", 1250, None),),
    )

    rollover, issues = build_month_rollover_plan(
        master,
        _fields(),
        "2026-07",
    )

    assert not issues
    assert rollover is not None
    assert rollover.mode == "already_complete"
    assert not rollover.insert_history_column
    assert rollover.archive_cells == ()


def test_rollover_resume_accepts_same_value_and_blocks_different_value():
    fields = _fields()
    matching = _snapshot(
        (
            "Номер КД / ID",
            "ответственный Сотрудник",
            "Оплаты за 07.26",
            "Текущие платежи",
        ),
        ("сх", "сх", "", ""),
        (("1", "Иванов Иван", 1250, 1250),),
    )
    conflicting = replace(
        matching,
        rows=(("1", "Иванов Иван", 700, 1250),),
    )

    resumed, resume_issues = build_month_rollover_plan(
        matching,
        fields,
        "2026-07",
    )
    blocked, conflict_issues = build_month_rollover_plan(
        conflicting,
        fields,
        "2026-07",
    )

    assert resumed is not None and resumed.mode == "resume"
    assert not resume_issues
    assert blocked is not None
    assert any(
        issue.code == "payment_history_conflict"
        for issue in conflict_issues
    )


def test_rollover_clears_only_two_employee_payment_fields():
    fields = _fields()
    master = _snapshot(
        (
            "Номер КД / ID",
            "ответственный Сотрудник",
            "Сумма последнего платежа",
            "Дата последнего платежа",
            "Текущие платежи",
        ),
        ("сх", "сх", "сх", "сх", ""),
        (("1", "Иванов Иван", 500, "27.07.2026", 1250),),
    )
    personal = replace(
        master,
        path=Path("Иванов Иван.xlsb"),
        headers=master.headers[:-1],
        type_tags=master.type_tags[:-1],
        rows=(("1", "Иванов Иван", 500, "27.07.2026"),),
    )
    from registry_cycle.models import ManifestEntry

    entry = ManifestEntry(
        entry_id="ivanov",
        path=personal.path,
        country="Тест",
        group="Город",
        profile="hard",
        employee_folder="Иванов Иван",
        responsible_hint="Иванов Иван",
    )

    plan = build_sync_plan(
        master,
        [(entry, personal)],
        fields,
        rollover_month="2026-07",
    )

    assert not plan.blocked
    assert plan.month_rollover is not None
    assert plan.projections[0].rows == (
        ("1", "Иванов Иван", None, None),
    )
    assert plan.projections[0].rollover_cleared_cells == 2
