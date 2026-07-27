from dataclasses import replace
from pathlib import Path

from registry_cycle.config import GroupConfig, PortfolioRoot, load_config_bundle
from registry_cycle.discovery import discover


def _bundle_for(tmp_path: Path):
    bundle = load_config_bundle()
    roots = (
        PortfolioRoot(
            country="Тест",
            path=str(tmp_path),
            groups=(
                GroupConfig("Город", "hard", True, True),
                GroupConfig("Soft", "soft", True, True),
                GroupConfig("Отключён", "hard", False, False),
            ),
        ),
    )
    return replace(bundle, app=replace(bundle.app, portfolio_roots=roots))


def test_discovery_uses_only_configured_active_files(tmp_path):
    bundle = _bundle_for(tmp_path)
    active_dir = tmp_path / "Город" / "Иванов Иван"
    active_dir.mkdir(parents=True)
    (active_dir / "Иванов Иван.xlsb").write_bytes(b"active")
    (active_dir / "Иванов Иван (до разброса 27.07 12-00).xlsb").write_bytes(b"backup")
    (active_dir / "Копия Иванов Иван.xlsb").write_bytes(b"ambiguous-copy")
    (active_dir / "~$Иванов Иван.xlsb").write_bytes(b"lock")

    soft_dir = tmp_path / "Soft" / "Петров Пётр"
    soft_dir.mkdir(parents=True)
    (soft_dir / "СОФТ Петров Пётр.xlsb").write_bytes(b"soft")

    report = discover(bundle)

    assert not report.blocked
    assert {entry.path.name for entry in report.entries} == {
        "Иванов Иван.xlsb",
        "СОФТ Петров Пётр.xlsb",
    }
    assert {entry.profile for entry in report.entries} == {"hard", "soft"}


def test_unconfigured_group_with_active_file_blocks_run(tmp_path):
    bundle = _bundle_for(tmp_path)
    (tmp_path / "Город").mkdir()
    (tmp_path / "Soft").mkdir()
    unknown = tmp_path / "Новый город" / "Сотрудник"
    unknown.mkdir(parents=True)
    (unknown / "Сотрудник.xlsb").write_bytes(b"x")

    report = discover(bundle)

    assert report.blocked
    assert any(issue.code == "unconfigured_group_has_files" for issue in report.issues)


def test_disabled_group_with_file_is_visible_but_not_in_manifest(tmp_path):
    bundle = _bundle_for(tmp_path)
    (tmp_path / "Город").mkdir()
    (tmp_path / "Soft").mkdir()
    disabled = tmp_path / "Отключён" / "Сотрудник"
    disabled.mkdir(parents=True)
    (disabled / "Сотрудник.xlsb").write_bytes(b"x")

    report = discover(bundle)

    assert not report.blocked
    assert not report.entries
    assert any(issue.code == "disabled_group_has_files" for issue in report.issues)


def test_filename_rule_can_override_folder_profile(tmp_path):
    bundle = _bundle_for(tmp_path)
    city = tmp_path / "Город" / "Петров Пётр"
    city.mkdir(parents=True)
    (city / "СОФТ Петров Пётр.xlsb").write_bytes(b"x")
    (tmp_path / "Soft").mkdir()

    report = discover(bundle)

    assert not report.blocked
    assert len(report.entries) == 1
    assert report.entries[0].profile == "soft"
