"""Руководители в пульте: направление вместо ручного списка сотрудников.

Сервер давно умеет руководителей направления (tj/uz/all — состав считается
живьём по стране реестра), но пульт про направления не знал: требовал отметить
сотрудников галочками и поле direction не слал. 03.09.2026 владелец заводил
руководителя Таджикистана и двух руководителей СНГ — форма упиралась в
«Отметь хотя бы одного сотрудника».
"""

from pult.core import managers as mg


def test_direction_options_match_server_codes():
    assert [c for c, _ in mg.DIRECTION_OPTIONS] == ["", "tj", "uz", "all"]
    assert mg.direction_index("all") == 3 and mg.direction_index("") == 0
    assert mg.direction_index("nope") == 0, "неизвестный код — обычный руководитель"
    assert mg.direction_code(1) == "tj" and mg.direction_code(0) == ""
    assert mg.direction_code(99) == ""


def test_manager_body_always_sends_direction():
    body = mg.manager_body("807070009", " Альберт ", {"Таджикистан"}, set(), "tj")
    assert body == {"telegram_id": 807070009, "name": "Альберт",
                    "cities": ["Таджикистан"], "employees": [], "direction": "tj"}
    plain = mg.manager_body("1", "Имя", set(), {"Б", "А"})
    assert plain["direction"] == "" and plain["employees"] == ["А", "Б"], \
        "пустая строка снимает направление, а не оставляет прежнее"


def test_manager_row_shows_direction_instead_of_frozen_count():
    row = mg.manager_row({"telegram_id": 1, "name": "Лисов", "cities": [],
                          "employees": [], "direction": "all"})
    assert row["employees"] == "направление: СНГ (все сотрудники)"
    row = mg.manager_row({"telegram_id": 2, "name": "Х", "cities": ["Худжанд"],
                          "employees": ["А", "Б"], "direction": ""})
    assert row["employees"] == "сотрудников: 2" and row["cities"] == "Худжанд"


def test_direction_note_explains_that_checkboxes_are_not_needed():
    assert mg.direction_employees_note("") == ""
    note = mg.direction_employees_note("tj")
    assert "Таджикистан" in note and "автоматически" in note
