from pathlib import Path

from registry_cycle.config import load_config_bundle
from registry_cycle.models import FileFingerprint, ManifestEntry, WorkbookSnapshot
from registry_cycle.ownership import (
    manifest_owner_matches,
    resolve_book_responsible,
)


def _entry(folder: str, hint: str) -> ManifestEntry:
    return ManifestEntry(
        entry_id="test",
        path=Path(folder) / f"{hint}.xlsb",
        country="Тест",
        group="Город",
        profile="soft",
        employee_folder=folder,
        responsible_hint=hint,
    )


def test_soft_prefix_and_service_hyphen_do_not_change_owner():
    bundle = load_config_bundle()

    assert manifest_owner_matches(
        bundle,
        _entry("Турсунов Улугбек-", "СОФТ Турсунов Улугбек"),
        "СОФТ Турсунов Улугбек",
    )


def test_book_in_another_employee_folder_is_rejected():
    bundle = load_config_bundle()

    assert not manifest_owner_matches(
        bundle,
        _entry("Курбаниязов Ильхомбек", "СОФТ Курбаниязов"),
        "СОФТ Турсунов Улугбек",
    )


def test_configured_temporary_delegation_is_allowed():
    bundle = load_config_bundle()

    assert manifest_owner_matches(
        bundle,
        ManifestEntry(
            entry_id="delegated",
            path=Path("Курбаниязов Ильхомбек")
            / "СОФТ Турсунов Улугбек.xlsb",
            country="Узбекистан",
            group="Soft",
            profile="soft",
            employee_folder="Курбаниязов Ильхомбек",
            responsible_hint="СОФТ Турсунов Улугбек",
        ),
        "СОФТ Турсунов Улугбек",
    )


def test_delegation_does_not_allow_misnamed_book():
    bundle = load_config_bundle()

    assert not manifest_owner_matches(
        bundle,
        ManifestEntry(
            entry_id="misnamed-delegated",
            path=Path("Курбаниязов Ильхомбек")
            / "СОФТ Курбаниязов.xlsb",
            country="Узбекистан",
            group="Soft",
            profile="soft",
            employee_folder="Курбаниязов Ильхомбек",
            responsible_hint="СОФТ Курбаниязов",
        ),
        "СОФТ Турсунов Улугбек",
    )


def _book(rows) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        path=Path("Иванов Иван.xlsb"),
        sheet="Портфели",
        sheet_names=("Портфели",),
        fingerprint=FileFingerprint(1, 1, "a" * 64),
        header_row=0,
        type_row=1,
        data_start=2,
        headers=("Номер КД / ID", "ответственный Сотрудник"),
        type_tags=("сх", "сх"),
        rows=tuple(tuple(row) for row in rows),
    )


def test_empty_book_owner_is_inferred_from_manifest_hint():
    entry = _entry("Иванов Иван", "Иванов Иван")

    assert (
        resolve_book_responsible(
            entry,
            _book(()),
            "ответственный Сотрудник",
        )
        == "Иванов Иван"
    )


def test_nonempty_book_without_owner_is_never_inferred():
    entry = _entry("Иванов Иван", "Иванов Иван")

    assert (
        resolve_book_responsible(
            entry,
            _book((("123", None),)),
            "ответственный Сотрудник",
        )
        is None
    )


def test_configured_responsible_alias_is_valid_for_manifest_owner():
    bundle = load_config_bundle()
    entry = _entry("Тургунов Музаффар", "Тургунов Музаффар")

    assert manifest_owner_matches(
        bundle,
        entry,
        "Тургунов Музафар",
    )
