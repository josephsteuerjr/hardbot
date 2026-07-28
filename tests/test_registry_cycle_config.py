import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from registry_cycle.config import ConfigurationError, load_config_bundle


def test_default_configuration_is_complete_and_renderable():
    bundle = load_config_bundle()

    assert bundle.app.product_name == "Контур · Реестры"
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
    assert bundle.fields.month_rollover is not None
    assert bundle.fields.month_rollover.employee_clear_titles == (
        "Сумма последнего платежа",
        "Дата последнего платежа",
    )


def test_four_primary_cities_and_soft_are_explicit_in_config():
    bundle = load_config_bundle()
    enabled = {
        (root.country, group.name, group.profile)
        for root in bundle.app.portfolio_roots
        for group in root.groups
        if group.enabled
    }

    assert ("Таджикистан", "Душанбе", "hard") in enabled
    assert ("Таджикистан", "Худжанд", "hard") in enabled
    assert ("Узбекистан", "Ташкент", "hard") in enabled
    assert ("Узбекистан", "Самарканд", "hard") in enabled
    assert ("Таджикистан", "Soft", "soft") in enabled
    assert ("Узбекистан", "Soft", "soft") in enabled
    assert ("Узбекистан", "Андижан", "hard") not in enabled


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
