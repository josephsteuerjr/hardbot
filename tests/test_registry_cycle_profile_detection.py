from pathlib import Path

import pytest

from registry_cycle.config import load_config_bundle
from registry_cycle.errors import WorkbookReadError
from registry_cycle.models import FileFingerprint, WorkbookSnapshot
from registry_cycle.profile_detection import infer_workbook_profile


def _book(headers: list[str]) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        path=Path("personal.xlsb"),
        sheet="Портфели",
        sheet_names=("Портфели",),
        fingerprint=FileFingerprint(1, 1, "a" * 64),
        header_row=0,
        type_row=1,
        data_start=2,
        headers=tuple(headers),
        type_tags=tuple(None for _ in headers),
        rows=(),
    )


@pytest.mark.parametrize("profile", ["hard", "soft"])
def test_profile_is_inferred_from_mapped_columns_only(profile: str):
    fields = load_config_bundle().fields
    headers = [
        rule.title
        for rule in fields.collect
        if profile in rule.profiles
    ]
    headers[0] = "Блок оплат и обещаний"
    headers = ["Произвольный служебный столбец", *reversed(headers)]

    assert infer_workbook_profile(_book(headers), fields) == profile


def test_profile_detection_fails_closed_for_incomplete_schema():
    fields = load_config_bundle().fields
    common_only = [
        rule.title
        for rule in fields.collect
        if set(rule.profiles) == {"hard", "soft"}
    ]

    with pytest.raises(WorkbookReadError, match="ни один профиль"):
        infer_workbook_profile(_book(common_only), fields)
