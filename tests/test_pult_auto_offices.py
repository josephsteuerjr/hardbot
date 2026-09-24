"""Офис по папкам портфелей: пульт дозаполняет пустое, но не угадывает.

16.09.2026 два человека неделю висели в сводке «Без офиса», хотя их портфели
давно лежали в папке Худжанда. Правила здесь — чтобы автозаполнение не стало
новой бедой: чужое решение не перетирается, Soft не офис, два города — не
угадываем.
"""
from pathlib import Path

from pult.core import offices


def _fmap(*items):
    """Как razbros.build_folder_map: {фио.lower: [{city, kind, ...}]}."""
    out = {}
    for name, city, kind in items:
        out.setdefault(name.strip().lower(), []).append(
            {"path": Path(city) / name, "republic": "", "city": city, "kind": kind})
    return out


def test_empty_office_is_filled_from_the_only_folder():
    by = offices.folder_offices(_fmap(("Калонов Абдухафиз", "Худжанд", "hard")))
    r = offices.suggest(["Калонов Абдухафиз"], {}, by)
    assert r["fill"] == {"Калонов Абдухафиз": "Худжанд"}


def test_human_decision_is_never_overwritten():
    by = offices.folder_offices(_fmap(("Иванов Иван", "Душанбе", "hard")))
    r = offices.suggest(["Иванов Иван"], {"Иванов Иван": "Худжанд"}, by)
    assert r["fill"] == {}
    assert r["conflicts"] == [("Иванов Иван", "Худжанд", "Душанбе")]


def test_soft_folder_is_not_an_office():
    by = offices.folder_offices(_fmap(("Петров Пётр", "Soft", "soft")))
    r = offices.suggest(["Петров Пётр"], {}, by)
    assert r["fill"] == {} and r["nofolder"] == ["Петров Пётр"]


def test_two_cities_are_shown_not_guessed():
    by = offices.folder_offices(_fmap(("Двоев Два", "Душанбе", "hard"),
                                      ("Двоев Два", "Худжанд", "hard")))
    r = offices.suggest(["Двоев Два"], {}, by)
    assert r["fill"] == {} and r["ambiguous"] == ["Двоев Два"]


def test_couriers_and_gph_are_tashkent_by_owner_decision():
    by = offices.folder_offices(_fmap(("Курьеров К", "Курьеры", "hard"),
                                      ("Агентов А", "ГПХ Агент выезд", "hard")))
    r = offices.suggest(["Курьеров К", "Агентов А"], {}, by)
    assert r["fill"] == {"Курьеров К": "Ташкент", "Агентов А": "Ташкент"}


def test_config_can_add_aliases():
    aliases = offices.load_aliases({"office_aliases": {"Нукус-склад": "Нукус"}})
    by = offices.folder_offices(_fmap(("Склад С", "Нукус-склад", "hard")), aliases)
    assert offices.suggest(["Склад С"], {}, by)["fill"] == {"Склад С": "Нукус"}


def test_folder_name_matches_registry_spelling_loosely():
    """Папку называет человек: регистр, ё/е и двойной пробел не должны мешать."""
    by = offices.folder_offices(_fmap(("фёдоров  фёдор", "Ташкент", "hard")))
    r = offices.suggest(["Федоров Федор"], {}, by)
    assert r["fill"] == {"Федоров Федор": "Ташкент"}


def test_summary_names_who_got_which_office():
    text = offices.summary_text({"fill": {"А": "Худжанд"}, "conflicts": [("Б", "x", "y")],
                                 "ambiguous": [], "nofolder": ["В"]})
    assert "А → Худжанд" in text and "расходятся с папками: 1" in text
