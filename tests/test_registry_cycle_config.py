import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from registry_cycle.config import ConfigurationError, load_config_bundle
from registry_cycle.distribution import load_distribution_config
from registry_cycle.errors import SafetyViolation
from registry_cycle.recovery import load_recovery_config


def test_default_configuration_is_complete_and_renderable():
    bundle = load_config_bundle()

    assert len(bundle.config_identity) == 64
    assert int(bundle.config_identity, 16) >= 0
    assert bundle.app.schema_version == 2
    assert bundle.app.product_name == "Выезды · Реестры"
    assert set(bundle.fields.profile_tags) == {"hard", "soft"}
    assert bundle.master_path(datetime(2026, 7, 27)).name == "ОБЩИЙ РЕЕСТР ИЮЛЬ.xlsb"
    assert bundle.workspace_path().name == "registry_cycle_v2"
    assert len(bundle.fields.collect) == 21
    assert any(
        rule.title == "Телефон глав"
        for rule in bundle.fields.collect
    )
    assert bundle.fields.previous_responsible_title == "пред отв"
    assert bundle.fields.employee_header_fill_rgb == ("#FFFF00",)
    assert bundle.fields.strict_header_color_validation
    assert (
        bundle.app.portfolio_layout
        == "root/group/employee/*.xlsb"
    )
    assert bundle.fields.month_rollover is not None
    assert bundle.fields.month_rollover.employee_clear_titles == (
        "Сумма последнего платежа",
        "Дата последнего платежа",
    )


def test_groups_are_not_configured_and_layout_rules_are_explicit():
    """Группы под корнем больше не перечисляются в настройках.

    Прежде каждая группа задавалась явно с профилем и признаком обязательности,
    и незнакомая папка останавливала работу. Теперь участвуют все папки, кроме
    архивных, а профиль книги определяется по её столбцам. Настройки должны
    описывать только корни и правила отсева — это здесь и закреплено.
    """
    app = load_config_bundle().app

    assert app.group_discovery_mode == "all_except_ignored"
    assert {root.country for root in app.portfolio_roots} == {
        "Таджикистан",
        "Узбекистан",
    }
    # Ни одна группа не перечислена: перечисление в этом режиме запрещено.
    assert all(root.groups == () for root in app.portfolio_roots)

    assert app.portfolio_layout == "root/group/employee/*.xlsb"
    assert app.active_extensions == (".xlsb",)
    assert app.ignored_directory_prefixes == ("архив", "!архив")
    assert app.backup_name_prefixes == ("~", "копия")


def test_field_semantics_are_explicit_and_safe():
    rules = {
        rule.title: (rule.value_type, rule.master_conflict)
        for rule in load_config_bundle().fields.collect
    }

    assert rules["Сумма последнего платежа"] == ("number", "require_baseline")
    assert rules["Дата последнего платежа"] == ("date", "require_baseline")
    assert rules["дата обещания"] == ("date", "require_baseline")
    assert rules["Дата встречи"] == ("date", "require_baseline")
    assert rules["Дата выезда"] == ("literal", "require_baseline")
    assert rules["Дата перезвона"] == ("date", "require_baseline")
    assert rules["Прогноз"] == ("text", "require_baseline")
    assert rules["Общий результат"] == ("text", "require_baseline")
    assert rules["Телефон глав"] == ("text", "require_baseline")
    assert rules["Комментарий текущего месяца выезда"] == (
        "text",
        "accept_superset",
    )
    assert rules["комментарий звонка"] == ("text", "accept_superset")
    assert rules["Комментарий текущего месяца Софт"] == (
        "text",
        "accept_superset",
    )


def test_full_cycle_has_shared_responsible_aliases():
    bundle = load_config_bundle()

    assert bundle.app.responsible_aliases == {
        "Тургунов Музафар": "Тургунов Музаффар"
    }


def test_distribution_aliases_must_match_app_exactly(tmp_path: Path):
    source = Path(__file__).parents[1] / "registry_cycle" / "config"
    config_dir = tmp_path / "config"
    shutil.copytree(source, config_dir)
    original_identity = load_config_bundle(config_dir).config_identity
    distribution_path = config_dir / "distribution.json"
    payload = json.loads(
        distribution_path.read_text(encoding="utf-8")
    )
    payload["responsible_aliases"]["Другой"] = "Ответственный"
    distribution_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    bundle = load_config_bundle(config_dir)
    assert bundle.config_identity != original_identity
    with pytest.raises(SafetyViolation, match="должны совпадать точно"):
        load_distribution_config(bundle)


def test_loaded_bundle_pins_distribution_and_recovery_payloads(tmp_path: Path):
    source = Path(__file__).parents[1] / "registry_cycle" / "config"
    config_dir = tmp_path / "config"
    shutil.copytree(source, config_dir)
    bundle = load_config_bundle(config_dir)
    distribution_before = load_distribution_config(bundle)
    recovery_before = load_recovery_config(bundle)

    distribution_path = config_dir / "distribution.json"
    distribution_payload = json.loads(
        distribution_path.read_text(encoding="utf-8")
    )
    distribution_payload["safety"]["max_total_rows"] = 1
    distribution_path.write_text(
        json.dumps(distribution_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    recovery_path = config_dir / "recovery.json"
    recovery_payload = json.loads(recovery_path.read_text(encoding="utf-8"))
    recovery_payload["safety"]["max_writes_per_file"] = 1
    recovery_path.write_text(
        json.dumps(recovery_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    assert load_distribution_config(bundle) == distribution_before
    assert load_recovery_config(bundle) == recovery_before
    changed = load_config_bundle(config_dir)
    assert changed.config_identity != bundle.config_identity
    assert load_distribution_config(changed).safety["max_total_rows"] == 1
    assert load_recovery_config(changed).safety["max_writes_per_file"] == 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("employee_to_master", "last_write_wins"),
        ("master_to_employee", "all_columns"),
        ("preserve_employee_values_for_existing_owner", False),
        ("transfer_employee_values_on_reassignment", False),
        ("typo_direction", True),
    ],
)
def test_direction_contract_cannot_be_silently_changed(
    tmp_path: Path,
    key: str,
    value: object,
):
    source = Path(__file__).parents[1] / "registry_cycle" / "config"
    config_dir = tmp_path / "config"
    shutil.copytree(source, config_dir)
    fields_path = config_dir / "fields.json"
    fields = json.loads(fields_path.read_text(encoding="utf-8"))
    fields["directions"][key] = value
    fields_path.write_text(
        json.dumps(fields, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="directions"):
        load_config_bundle(config_dir)


@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    [
        (
            "discovery",
            "portfolio_layout",
            "root/group/**/*.xlsb",
            "portfolio_layout",
        ),
        (
            "discovery",
            "active_extensions",
            [".xlsx"],
            "active_extensions",
        ),
        (
            "safety",
            "block_unconfigured_groups_with_files",
            False,
            "block_unconfigured_groups_with_files",
        ),
    ],
)
def test_portfolio_layout_contract_cannot_be_silently_changed(
    tmp_path: Path,
    section: str,
    key: str,
    value: object,
    message: str,
):
    source = Path(__file__).parents[1] / "registry_cycle" / "config"
    config_dir = tmp_path / "config"
    shutil.copytree(source, config_dir)
    app_path = config_dir / "app.json"
    app = json.loads(app_path.read_text(encoding="utf-8"))
    app[section][key] = value
    app_path.write_text(
        json.dumps(app, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match=message):
        load_config_bundle(config_dir)


def test_pre_layout_app_schema_is_rejected_instead_of_guessed(tmp_path: Path):
    source = Path(__file__).parents[1] / "registry_cycle" / "config"
    config_dir = tmp_path / "config"
    shutil.copytree(source, config_dir)
    app_path = config_dir / "app.json"
    app = json.loads(app_path.read_text(encoding="utf-8"))
    app["schema_version"] = 1
    app_path.write_text(
        json.dumps(app, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="schema_version=1"):
        load_config_bundle(config_dir)
