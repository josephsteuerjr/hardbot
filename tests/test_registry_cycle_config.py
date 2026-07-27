from datetime import datetime

from registry_cycle.config import load_config_bundle


def test_default_configuration_is_complete_and_renderable():
    bundle = load_config_bundle()

    assert bundle.app.product_name == "Контур · Реестры"
    assert set(bundle.fields.profile_tags) == {"hard", "soft"}
    assert bundle.master_path(datetime(2026, 7, 27)).name == "ОБЩИЙ РЕЕСТР ИЮЛЬ.xlsb"
    assert bundle.workspace_path().name == "registry_cycle_v2"
    assert len(bundle.fields.collect) == 20


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
    assert rules["Дата выезда"] == ("date", "require_baseline")
    assert rules["Дата перезвона"] == ("date", "require_baseline")
    assert rules["Прогноз"] == ("text", "require_baseline")
    assert rules["Общий результат"] == ("text", "require_baseline")
    assert rules["Комментарий текущего месяца выезда"] == (
        "text",
        "accept_superset",
    )
    assert rules["комментарий звонка"] == ("text", "accept_superset")
    assert rules["Комментарий текущего месяца Софт"] == (
        "text",
        "accept_superset",
    )
