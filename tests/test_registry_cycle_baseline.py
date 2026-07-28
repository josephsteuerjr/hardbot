from datetime import date
from pathlib import Path

from registry_cycle.baseline import (
    build_baseline,
    load_baseline,
    save_baseline,
)
from registry_cycle.config import FieldConfig, FieldRule
from registry_cycle.models import FileFingerprint, WorkbookSnapshot
from registry_cycle.semantics import values_equal_for_rule


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
                title="Дата выезда",
                profiles=("hard",),
                value_type="date",
            ),
        ),
        preserve_if_master_blank=(),
        aliases={},
    )


def _master() -> WorkbookSnapshot:
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
            "Дата выезда",
        ),
        type_tags=("сх", "сх", "хард"),
        rows=(
            ("1", "Иванов Иван", "27.07.2026"),
            ("2", "Иванов Иван", None),
        ),
    )


def test_baseline_roundtrip_is_normalized_and_compressed(tmp_path):
    baseline = build_baseline(
        _master(),
        _fields(),
        plan_digest="b" * 64,
    )
    path = save_baseline(tmp_path / "baseline.json.gz", baseline)
    loaded = load_baseline(path, expected_master_path=Path("master.xlsb"))

    assert loaded.plan_digest == "b" * 64
    assert loaded.lookup(1.0, "Дата выезда") == (True, "27.07.2026")
    assert loaded.lookup("2", "Дата выезда") == (False, None)


def test_literal_baseline_preserves_exact_text_and_excel_date_type(tmp_path):
    literal_rule = FieldRule(
        title="Дата выезда",
        profiles=("hard",),
        value_type="literal",
    )
    fields = _fields()
    fields = FieldConfig(
        sheet_name=fields.sheet_name,
        statistics_sheet=fields.statistics_sheet,
        status_sheet=fields.status_sheet,
        key_title=fields.key_title,
        responsible_title=fields.responsible_title,
        header_search_rows=fields.header_search_rows,
        type_row_offset=fields.type_row_offset,
        profile_tags=fields.profile_tags,
        collect=(literal_rule,),
        preserve_if_master_blank=fields.preserve_if_master_blank,
        aliases=fields.aliases,
    )
    source = _master()
    source = WorkbookSnapshot(
        path=source.path,
        sheet=source.sheet,
        sheet_names=source.sheet_names,
        fingerprint=source.fingerprint,
        header_row=source.header_row,
        type_row=source.type_row,
        data_start=source.data_start,
        headers=source.headers,
        type_tags=source.type_tags,
        rows=(
            ("1", "Иванов Иван", " 23.07..2026 хвост "),
            ("2", "Иванов Иван", date(2026, 7, 23)),
        ),
    )

    path = save_baseline(
        tmp_path / "literal.json.gz",
        build_baseline(source, fields, plan_digest="c" * 64),
    )
    loaded = load_baseline(path)

    first = loaded.lookup("1", "Дата выезда")
    second = loaded.lookup("2", "Дата выезда")
    assert first[0] and values_equal_for_rule(
        first[1],
        " 23.07..2026 хвост ",
        literal_rule,
    )
    assert second[0] and values_equal_for_rule(
        second[1],
        date(2026, 7, 23),
        literal_rule,
    )
    assert not values_equal_for_rule(
        second[1],
        "23.07.2026",
        literal_rule,
    )
