from pathlib import Path

from registry_cycle.config import load_config_bundle
from registry_cycle.models import ManifestEntry
from registry_cycle.ownership import manifest_owner_matches


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
