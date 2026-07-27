from pathlib import Path

from registry_cycle.baseline import (
    build_baseline,
    load_baseline,
    save_baseline,
)
from registry_cycle.config import FieldConfig, FieldRule
from registry_cycle.models import FileFingerprint, WorkbookSnapshot


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
    assert loaded.lookup(1.0, "Дата выезда") == (True, "2026-07-27")
    assert loaded.lookup("2", "Дата выезда") == (False, None)
