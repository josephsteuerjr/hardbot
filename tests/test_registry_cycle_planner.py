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
        employee_folder="Иванов Иван",
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


def test_empty_personal_value_never_clears_master():
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
    assert plan.projections[0].rows[0][-1] == "мастер"


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


def test_three_way_merge_keeps_master_only_change():
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
    assert not plan.changes
    assert plan.projections[0].rows[0][-1] == "правка мастера"


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


def test_three_way_merge_blocks_master_deletion():
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

    assert plan.blocked
    issue = next(
        issue
        for issue in plan.issues
        if issue.code == "baseline_merge_conflicts"
    )
    assert issue.context["by_kind"] == {"master_deletion": 1}


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
