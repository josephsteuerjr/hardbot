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
    body = mg.manager_body("9782002", " Тестовый руководитель ", {"Таджикистан"}, set(), "tj")
    assert body == {"telegram_id": 9782002, "name": "Тестовый руководитель",
                    "cities": ["Таджикистан"], "employees": [], "direction": "tj",
                    "office_cities": []}
    plain = mg.manager_body("1", "Имя", set(), {"Б", "А"})
    assert plain["direction"] == "" and plain["employees"] == ["А", "Б"], \
        "пустая строка снимает направление, а не оставляет прежнее"


def test_manager_body_sends_offices_explicitly():
    """Офисы уходят по тому же правилу, что направление: явно и всегда.

    Сервер понимает отсутствие ключа как «не трогать» (старый пульт его не
    шлёт), поэтому снятие последнего офиса обязано доехать пустым списком."""
    body = mg.manager_body("1", "Старший спец", set(), set(),
                           office_cities={"Душанбе"})
    assert body["office_cities"] == ["Душанбе"]
    assert mg.manager_body("1", "Имя", set(), set())["office_cities"] == []


def test_manager_row_shows_office_scope():
    """У руководителя по офису состав живой — строка не должна врать «0»."""
    row = mg.manager_row({"telegram_id": 1, "name": "Спец", "cities": [],
                          "employees": [], "direction": "",
                          "office_cities": ["Душанбе"]})
    assert row["employees"] == "офис: Душанбе"
    row = mg.manager_row({"telegram_id": 2, "name": "Спец", "cities": [],
                          "employees": ["А"], "direction": "",
                          "office_cities": ["Душанбе", "Худжанд"]})
    assert row["employees"] == "офис: Душанбе · Худжанд + 1 поимённо"


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


def test_chat_direction_options_match_the_server():
    assert [c for c, _ in mg.CHAT_DIRECTION_OPTIONS] == ["", "tj", "uz", "all"]
    assert mg.chat_direction_index("uz") == 2 and mg.chat_direction_index("нет") == 0
    assert mg.chat_direction_code(3) == "all" and mg.chat_direction_code(99) == ""


def test_chat_scope_text_prefers_direction():
    assert mg.chat_scope_text({"direction": "tj", "employees": []}) == \
        "направление: Таджикистан"
    assert mg.chat_scope_text({"direction": "uz", "employees": ["А", "Б"]}) == \
        "направление: Узбекистан + 2 вручную"
    assert mg.chat_scope_text({"direction": "", "employees": []}) == \
        "сотрудники не закреплены — посты не идут"
    assert mg.chat_scope_text({"direction": "", "employees": ["А"]}) == "А"


def test_digest_body_sends_xlsx_switch_only_to_a_server_that_knows_it():
    body = mg.digest_body(time="16:30", enabled=True, promise_time="09:30",
                          promise_enabled=True, visit_push=True, owner_known=False,
                          owner_digest=True, owner_promise=False)
    assert "digest_xlsx" not in body, "старый сервер не должен получать незнакомый ключ"
    body2 = mg.digest_body(time="16:30", enabled=True, promise_time="09:30",
                           promise_enabled=True, visit_push=True, owner_known=False,
                           owner_digest=True, owner_promise=False,
                           xlsx_known=True, digest_xlsx=False)
    assert body2["digest_xlsx"] is False
