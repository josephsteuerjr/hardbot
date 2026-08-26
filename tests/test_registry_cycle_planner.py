from datetime import date
from dataclasses import replace
from pathlib import Path

from registry_cycle.baseline import CycleBaseline
from registry_cycle.config import FieldConfig, FieldRule
from registry_cycle.models import (
    FileFingerprint,
    ManifestEntry,
    WorkbookSnapshot,
)
from registry_cycle.planner import build_sync_plan
from registry_cycle.normalize import norm_title


HEADERS = (
    "Номер КД / ID",
    "ответственный Сотрудник",
    "Телефон",
    "Город",
    "Доп. Адрес/Доп.Номер",
)
TYPES = ("сх", "сх", "сх", "хард", "сх")


def _fingerprint(seed: str) -> FileFingerprint:
    return FileFingerprint(size=len(seed), mtime_ns=1, sha256=seed * 8)


def _book(name: str, rows, *, headers=HEADERS, types=TYPES) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        path=Path(name),
        sheet="Портфели",
        sheet_names=("Портфели", "Статистика", "Статусы"),
        fingerprint=_fingerprint(name),
        header_row=0,
        type_row=1,
        data_start=2,
        headers=tuple(headers),
        type_tags=tuple(types),
        rows=tuple(tuple(row) for row in rows),
    )


def _entry(name="Иванов Иван.xlsb") -> ManifestEntry:
    return ManifestEntry(
        entry_id=name,
        path=Path(name),
        country="Тест",
        group="Город",
        profile="hard",
        employee_folder=Path(name).stem,
        responsible_hint=Path(name).stem,
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
        profile_tags={"hard": ("сх", "хард")},
        collect=(
            FieldRule(
                title="Доп. Адрес/Доп.Номер",
                profiles=("hard",),
            ),
        ),
        preserve_if_master_blank=("Телефон", "Город"),
        aliases={},
    )


def _fields_with_rule(rule: FieldRule) -> FieldConfig:
    fields = _fields()
    return FieldConfig(
        sheet_name=fields.sheet_name,
        statistics_sheet=fields.statistics_sheet,
        status_sheet=fields.status_sheet,
        key_title=fields.key_title,
        responsible_title=fields.responsible_title,
        header_search_rows=fields.header_search_rows,
        type_row_offset=fields.type_row_offset,
        profile_tags=fields.profile_tags,
        collect=(rule,),
        preserve_if_master_blank=fields.preserve_if_master_blank,
        aliases=fields.aliases,
    )


def _baseline(value) -> CycleBaseline:
    return CycleBaseline(
        schema_version=1,
        created_at="2026-07-27T10:00:00",
        master_path=Path("master.xlsb"),
        plan_digest="a" * 64,
        values={("1", norm_title("Доп. Адрес/Доп.Номер")): value},
    )


def test_plan_collects_owned_field_and_preserves_phone_when_master_blank():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", "+998 90 123", "Ташкент", "доп. 777"]],
    )

    plan = build_sync_plan(master, [(_entry(), personal)], _fields())

    assert not plan.blocked
    assert len(plan.changes) == 1
    assert plan.changes[0].title == "Доп. Адрес/Доп.Номер"
    assert plan.changes[0].after == "доп. 777"
    projection = plan.projections[0]
    assert projection.rows == (
        ("1", "Иванов Иван", "+998 90 123", "Ташкент", "доп. 777"),
    )
    assert projection.preserved_cells == 2


def test_empty_employee_owned_value_stays_empty_but_never_clears_master():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, "мастер"]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "   "]],
    )

    plan = build_sync_plan(master, [(_entry(), personal)], _fields())

    assert not plan.blocked
    assert not plan.changes
    assert plan.projections[0].rows[0][-1] is None


def test_payment_versions_follow_source_script_column_roles():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", 7000, None]],
        headers=(
            "Номер КД / ID",
            "ответственный Сотрудник",
            "Оплаты за 07.26",
            "Сумма последнего платежа",
        ),
        types=("сх", "сх", "", "сх"),
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", 1250]],
        headers=(
            "Номер КД / ID",
            "ответственный Сотрудник",
            "Сумма последнего платежа",
        ),
        types=("сх", "сх", "сх"),
    )
    fields = FieldConfig(
        sheet_name="Портфели",
        statistics_sheet="Статистика",
        status_sheet="Статусы",
        key_title="Номер КД / ID",
        responsible_title="ответственный Сотрудник",
        header_search_rows=10,
        type_row_offset=1,
        profile_tags={"hard": ("сх", "хард")},
        collect=(
            FieldRule(
                title="Сумма последнего платежа",
                profiles=("hard",),
                value_type="number",
            ),
        ),
        preserve_if_master_blank=(),
        aliases={},
    )

    plan = build_sync_plan(master, [(_entry(), personal)], fields)

    assert not plan.blocked
    assert len(plan.changes) == 1
    assert plan.changes[0].title == "Сумма последнего платежа"
    assert plan.changes[0].after == 1250
    assert plan.projections[0].headers == (
        "Номер КД / ID",
        "ответственный Сотрудник",
        "Сумма последнего платежа",
    )
    assert plan.projections[0].rows == (
        ("1", "Иванов Иван", 1250),
    )


def test_blank_employee_last_payment_is_not_refilled_from_master():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", 1250]],
        headers=(
            "Номер КД / ID",
            "ответственный Сотрудник",
            "Сумма последнего платежа",
        ),
        types=("сх", "сх", "сх"),
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None]],
        headers=master.headers,
        types=master.type_tags,
    )
    fields = FieldConfig(
        sheet_name="Портфели",
        statistics_sheet="Статистика",
        status_sheet="Статусы",
        key_title="Номер КД / ID",
        responsible_title="ответственный Сотрудник",
        header_search_rows=10,
        type_row_offset=1,
        profile_tags={"hard": ("сх", "хард")},
        collect=(
            FieldRule(
                title="Сумма последнего платежа",
                profiles=("hard",),
                value_type="number",
            ),
        ),
        preserve_if_master_blank=(),
        aliases={},
    )

    plan = build_sync_plan(master, [(_entry(), personal)], fields)

    assert not plan.blocked
    assert not plan.changes
    assert plan.projections[0].rows == (("1", "Иванов Иван", None),)


def test_different_values_for_same_contract_block_entire_plan():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [
            ["1", "Иванов Иван", None, None, "первое"],
            ["1", "Иванов Иван", None, None, "второе"],
        ],
    )

    plan = build_sync_plan(master, [(_entry(), personal)], _fields())

    assert plan.blocked
    assert any(issue.code == "source_value_conflict" for issue in plan.issues)


def test_responsible_mismatch_blocks_transfer():
    master = _book(
        "master.xlsb",
        [["1", "Петров Пётр", None, None, None]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "данные"]],
    )

    plan = build_sync_plan(master, [(_entry(), personal)], _fields())

    assert plan.blocked
    assert any(issue.code == "responsible_mismatch" for issue in plan.issues)


def test_same_responsible_in_two_active_profiles_blocks_plan():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    hard_entry = _entry("hard/Иванов Иван.xlsb")
    soft_entry = replace(
        _entry("soft/Иванов Иван.xlsb"),
        profile="soft",
    )
    hard_book = _book(
        "hard/Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    soft_book = _book(
        "soft/Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    fields = replace(
        _fields(),
        profile_tags={
            "hard": ("сх", "хард"),
            "soft": ("сх", "хард"),
        },
    )

    plan = build_sync_plan(
        master,
        [(hard_entry, hard_book), (soft_entry, soft_book)],
        fields,
    )

    assert plan.blocked
    assert any(
        issue.code == "responsible_in_multiple_active_profiles"
        for issue in plan.issues
    )
    assert len(plan.projections) == 1


def test_nonblank_master_phone_is_not_replaced_by_old_personal_phone():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", "новый", None, None]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", "старый", None, None]],
    )

    plan = build_sync_plan(master, [(_entry(), personal)], _fields())

    assert not plan.blocked
    assert plan.projections[0].rows[0][2] == "новый"


def test_first_run_blocks_different_nonblank_owned_values_without_baseline():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, "мастер"]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "личный файл"]],
    )

    plan = build_sync_plan(master, [(_entry(), personal)], _fields())

    assert plan.blocked
    assert not plan.changes
    assert any(issue.code == "initial_master_conflicts" for issue in plan.issues)


def test_date_formats_are_compared_semantically():
    rule = FieldRule(
        title="Доп. Адрес/Доп.Номер",
        profiles=("hard",),
        value_type="date",
    )
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, "2026-07-21"]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "21.07.2026"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields_with_rule(rule),
    )

    assert not plan.blocked
    assert not plan.changes


def test_additive_comment_can_extend_master_without_baseline():
    rule = FieldRule(
        title="Доп. Адрес/Доп.Номер",
        profiles=("hard",),
        master_conflict="accept_superset",
    )
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, "старый комментарий"]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [[
            "1",
            "Иванов Иван",
            None,
            None,
            "27.07 новый факт\nстарый комментарий",
        ]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields_with_rule(rule),
    )

    assert not plan.blocked
    assert len(plan.changes) == 1
    assert plan.changes[0].after == "27.07 новый факт\nстарый комментарий"


def test_additive_comment_does_not_accept_unrelated_replacement():
    rule = FieldRule(
        title="Доп. Адрес/Доп.Номер",
        profiles=("hard",),
        master_conflict="accept_superset",
    )
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, "значение A"]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "значение B"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields_with_rule(rule),
    )

    assert plan.blocked
    assert not plan.changes
    assert any(issue.code == "initial_master_conflicts" for issue in plan.issues)


def test_three_way_merge_accepts_personal_only_change():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, "baseline"]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "правка сотрудника"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields(),
        baseline=_baseline("baseline"),
    )

    assert not plan.blocked
    assert len(plan.changes) == 1
    assert plan.changes[0].after == "правка сотрудника"


def test_three_way_merge_restores_employee_owned_field_after_master_only_change():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, "правка мастера"]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "baseline"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields(),
        baseline=_baseline("baseline"),
    )

    assert not plan.blocked
    assert plan.changes[0].after == "baseline"
    assert plan.projections[0].rows[0][-1] == "baseline"
    assert any(
        issue.code == "employee_owned_master_edits_overridden"
        for issue in plan.issues
    )


def test_three_way_merge_blocks_independent_changes():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, "правка мастера"]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "правка сотрудника"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields(),
        baseline=_baseline("baseline"),
    )

    assert plan.blocked
    assert not plan.changes
    assert any(issue.code == "baseline_merge_conflicts" for issue in plan.issues)


def test_three_way_merge_restores_employee_owned_master_deletion():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "baseline"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields(),
        baseline=_baseline("baseline"),
    )

    assert not plan.blocked
    assert plan.changes[0].after == "baseline"
    assert any(
        issue.code == "employee_owned_master_edits_overridden"
        for issue in plan.issues
    )


def test_invalid_date_value_blocks_plan_without_writing_it():
    rule = FieldRule(
        title="Доп. Адрес/Доп.Номер",
        profiles=("hard",),
        value_type="date",
    )
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, 106500]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields_with_rule(rule),
    )

    assert plan.blocked
    assert not plan.changes
    assert any(
        issue.code == "invalid_source_value_type"
        for issue in plan.issues
    )


def test_configured_historical_non_date_is_preserved_with_warning():
    rule = FieldRule(
        title="Доп. Адрес/Доп.Номер",
        profiles=("hard",),
        value_type="date",
        invalid="preserve",
    )
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "до 03.08"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields_with_rule(rule),
    )

    assert not plan.blocked
    assert plan.changes[0].after == "до 03.08"
    assert any(
        issue.code == "noncanonical_source_value_preserved"
        and issue.severity == "warning"
        for issue in plan.issues
    )


def test_literal_date_is_transferred_without_parsing_or_normalization():
    rule = FieldRule(
        title="Доп. Адрес/Доп.Номер",
        profiles=("hard",),
        value_type="literal",
    )
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "23.07..2026 хвост"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields_with_rule(rule),
    )

    assert not plan.blocked
    assert plan.changes[0].after == "23.07..2026 хвост"


def test_literal_date_string_is_not_equal_to_typed_excel_date():
    rule = FieldRule(
        title="Доп. Адрес/Доп.Номер",
        profiles=("hard",),
        value_type="literal",
    )
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, date(2026, 7, 23)]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "23.07.2026"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields_with_rule(rule),
    )

    assert plan.blocked
    assert any(issue.code == "initial_master_conflicts" for issue in plan.issues)


def test_empty_portfolio_without_owner_is_skipped_not_guessed():
    """Пустая книга не получает владельца по имени файла.

    Прежде владелец брался из подсказки манифеста, и пустой портфель уезжал
    сотруднику, чьим именем названа папка. Теперь владелец читается только
    из самой книги: пустую пропускаем и отмечаем в отчёте, но работу
    не останавливаем — новый сотрудник заводится именно так.
    """
    master = _book("master.xlsb", [])
    personal = _book("Иванов Иван.xlsb", [])

    plan = build_sync_plan(master, [(_entry(), personal)], _fields())

    assert not plan.blocked
    assert plan.projections == []
    assert any(
        issue.code == "empty_personal_without_responsible_skipped"
        and issue.severity == "info"
        for issue in plan.issues
    )


def test_nonempty_portfolio_without_owner_still_blocks():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", None, None, None, "данные"]],
    )

    plan = build_sync_plan(master, [(_entry(), personal)], _fields())

    assert plan.blocked
    assert any(
        issue.code == "personal_responsible_not_unique"
        for issue in plan.issues
    )


def test_multi_owner_case_uses_master_owner_and_retains_stale_copy():
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, None]],
    )
    canonical = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "актуальное"]],
    )
    stale = _book(
        "Петров Пётр.xlsb",
        [["1", "Петров Пётр", None, None, "старое"]],
    )

    plan = build_sync_plan(
        master,
        [
            (_entry(), canonical),
            (_entry("Петров Пётр.xlsb"), stale),
        ],
        _fields(),
        multi_owner_policy="master_responsible",
    )

    assert not plan.blocked
    assert len(plan.changes) == 1
    assert plan.changes[0].after == "актуальное"
    stale_projection = next(
        item
        for item in plan.projections
        if item.responsible == "Петров Пётр"
    )
    assert stale_projection.rows == (
        ("1", "Петров Пётр", None, None, "старое"),
    )
    assert stale_projection.retained_rows == 1
    assert any(
        issue.code == "case_in_multiple_personal_books"
        for issue in plan.issues
    )
    assert any(
        issue.code == "noncanonical_personal_rows_read_only"
        for issue in plan.issues
    )


def test_multi_owner_policy_blocks_ambiguous_owner_in_master():
    master = _book(
        "master.xlsb",
        [
            ["1", "Иванов Иван", None, None, None],
            ["1", "Петров Пётр", None, None, None],
        ],
    )
    canonical = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "значение"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), canonical)],
        _fields(),
        multi_owner_policy="master_responsible",
    )

    assert plan.blocked
    assert not plan.changes
    assert any(
        issue.code == "master_case_owner_ambiguous"
        for issue in plan.issues
    )


def test_blank_master_owner_keeps_personal_row_read_only():
    master = _book(
        "master.xlsb",
        [["1", None, None, None, None]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "не терять"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields(),
        multi_owner_policy="master_responsible",
    )

    assert not plan.blocked
    assert not plan.changes
    assert plan.projections[0].rows == (
        ("1", "Иванов Иван", None, None, "не терять"),
    )
    assert any(
        issue.code == "unassigned_master_rows_read_only"
        and issue.severity == "warning"
        for issue in plan.issues
    )


def test_blank_master_owner_removes_row_from_proved_previous_owner():
    master = _book(
        "master.xlsb",
        [["1", None, "Иванов Иван", None, None, None]],
        headers=(
            "Номер КД / ID",
            "ответственный Сотрудник",
            "пред отв",
            "Телефон",
            "Город",
            "Доп. Адрес/Доп.Номер",
        ),
        types=("сх", "сх", "", "сх", "хард", "сх"),
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "снять"]],
    )
    fields = replace(
        _fields(),
        previous_responsible_title="пред отв",
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        fields,
        multi_owner_policy="master_responsible",
    )

    assert not plan.blocked
    assert plan.projections[0].rows == ()
    assert plan.projections[0].removed_rows == 1
    assert plan.projections[0].retained_rows == 0
    assert any(
        issue.code == "personal_rows_removed_by_previous_responsible"
        and issue.severity == "info"
        for issue in plan.issues
    )
    assert not any(
        issue.code == "unassigned_master_rows_read_only"
        for issue in plan.issues
    )


def test_reassigned_case_is_removed_only_from_proved_previous_owner():
    master = _book(
        "master.xlsb",
        [["1", "Петров Пётр", "Иванов Иван", None, None, None]],
        headers=(
            "Номер КД / ID",
            "ответственный Сотрудник",
            "пред отв",
            "Телефон",
            "Город",
            "Доп. Адрес/Доп.Номер",
        ),
        types=("сх", "сх", "", "сх", "хард", "сх"),
    )
    previous = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "старое"]],
    )
    current = _book(
        "Петров Пётр.xlsb",
        [["1", "Петров Пётр", None, None, "актуальное"]],
    )
    fields = replace(
        _fields(),
        previous_responsible_title="пред отв",
    )

    plan = build_sync_plan(
        master,
        [
            (_entry(), previous),
            (_entry("Петров Пётр.xlsb"), current),
        ],
        fields,
        multi_owner_policy="master_responsible",
    )

    assert not plan.blocked
    previous_projection = next(
        item
        for item in plan.projections
        if item.responsible == "Иванов Иван"
    )
    current_projection = next(
        item
        for item in plan.projections
        if item.responsible == "Петров Пётр"
    )
    assert previous_projection.rows == ()
    assert previous_projection.removed_rows == 1
    assert current_projection.rows[0][-1] == "актуальное"


def test_reassigned_case_hands_old_yellow_value_to_blank_new_owner():
    master = _book(
        "master.xlsb",
        [["1", "Петров Пётр", "Иванов Иван", None, None, "старое"]],
        headers=(
            "Номер КД / ID",
            "ответственный Сотрудник",
            "пред отв",
            "Телефон",
            "Город",
            "Доп. Адрес/Доп.Номер",
        ),
        types=("сх", "сх", "", "сх", "хард", "сх"),
    )
    previous = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "старое"]],
    )
    current = _book(
        "Петров Пётр.xlsb",
        [["1", "Петров Пётр", None, None, None]],
    )
    fields = replace(
        _fields(),
        previous_responsible_title="пред отв",
    )

    plan = build_sync_plan(
        master,
        [
            (_entry(), previous),
            (_entry("Петров Пётр.xlsb"), current),
        ],
        fields,
        multi_owner_policy="master_responsible",
    )

    assert not plan.blocked
    previous_projection = next(
        item
        for item in plan.projections
        if item.responsible == "Иванов Иван"
    )
    current_projection = next(
        item
        for item in plan.projections
        if item.responsible == "Петров Пётр"
    )
    assert previous_projection.rows == ()
    assert previous_projection.removed_rows == 1
    assert current_projection.rows[0][-1] == "старое"
    assert current_projection.handoff_cells == 1
    assert any(
        issue.code == "employee_owned_values_handed_off"
        for issue in plan.issues
    )


def test_responsible_alias_uses_canonical_book_as_master_target():
    canonical = "Тургунов Музаффар"
    typo = "Тургунов Музафар"
    master = _book(
        "master.xlsb",
        [["1", typo, None, None, None]],
    )
    personal = _book(
        f"{canonical}.xlsb",
        [["1", canonical, None, None, "актуальное"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(f"{canonical}.xlsb"), personal)],
        _fields(),
        multi_owner_policy="master_responsible",
        responsible_aliases={typo: canonical},
    )

    assert not plan.blocked
    assert len(plan.changes) == 1
    assert plan.changes[0].after == "актуальное"
    assert plan.projections[0].rows[0][1] == canonical


def test_employee_wins_publishes_instead_of_blocking():
    """Решение владельца 26.08.2026: «побеждает сотрудник».

    Тот же вход, что в test_three_way_merge_blocks_independent_changes: ячейку
    изменили и офис, и сотрудник. При 'configured' план блокируется целиком и
    не публикует НИЧЕГО — с обещанием выездника (дата и сумма пишутся обеими
    сторонами) это стало бы обычной ночью. При 'employee_wins' план проходит,
    а перебитое значение мастера уходит в журнал, а не в тишину.
    """
    master = _book(
        "master.xlsb",
        [["1", "Иванов Иван", None, None, "правка мастера"]],
    )
    personal = _book(
        "Иванов Иван.xlsb",
        [["1", "Иванов Иван", None, None, "правка сотрудника"]],
    )

    plan = build_sync_plan(
        master,
        [(_entry(), personal)],
        _fields(),
        baseline=_baseline("baseline"),
        master_conflict_policy="employee_wins",
    )

    assert not plan.blocked
    assert plan.changes[0].after == "правка сотрудника"
    assert not any(
        issue.code == "baseline_merge_conflicts" for issue in plan.issues
    )
    assert any(
        issue.code == "employee_owned_master_edits_overridden"
        for issue in plan.issues
    )


def test_service_defaults_to_employee_wins():
    """Автоматический цикл берёт то же правило, что ручной перенос.

    Раньше 'configured' был зашит в сигнатуру, и правило зависело от того,
    каким путём запущен перенос: руками — сотрудник побеждает, ночью — план
    падает. Значение читается из safety, чтобы правило меняли конфигом.
    """
    import inspect

    from registry_cycle.service import CycleService

    sig = inspect.signature(CycleService.prepare_snapshot)
    assert sig.parameters["master_conflict_policy"].default is None
    src = inspect.getsource(CycleService.prepare_snapshot)
    assert '"master_conflict_policy", "employee_wins"' in src
