"""Поиск в списке клиентов ищет по тому, что обещает подсказка.

Поле называется «Поиск: ФИО, договор, телефон», а фильтр сравнивал запрос только
с именем. Выездник набирал номер договора, который ему назвал офис, получал
пустой экран и докладывал руководителю «не работает поиск по клиентам» — и был
прав. Номера договоров и телефоны при этом на телефоне уже лежали, но в другом
месте (полные карточки), куда фильтру списка не дотянуться.
"""

import re
from pathlib import Path

import web


ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")


def _person(contracts):
    return {"rep": contracts[0]["contract_id"], "client": "Иванов Иван",
            "n": len(contracts), "total": 0, "totals": {}, "partners": [],
            "contracts": contracts}


def _row(**kw):
    row = {"contract_id": "", "phone": None, "phone_main": None,
           "geo_lat": None, "geo_lon": None}
    row.update(kw)
    return row


def _brief(contracts, monkeypatch):
    monkeypatch.setattr(web.db, "bot_visit_snapshot", lambda _c: None)
    monkeypatch.setattr(web.debt, "unit", lambda _partners: "")
    return web._person_brief(_person(contracts))


def test_list_carries_contract_numbers_of_the_person(monkeypatch):
    brief = _brief([_row(contract_id="44527028"), _row(contract_id="044527029")],
                   monkeypatch)
    assert brief["ids"] == ["044527029", "44527028"]


def test_phones_are_reduced_to_digits(monkeypatch):
    """В реестре номер пишут как угодно, набирают его подряд."""
    brief = _brief([_row(contract_id="A", phone="+998 90 123-45-67")], monkeypatch)
    assert brief["tels"] == ["998901234567"]


def test_two_numbers_in_one_cell_stay_two_numbers(monkeypatch):
    """«987064552 - раис джамаата 918436099 - хаким улицы» — живая строка базы.

    Склеенная в одну, она не нашлась бы ни по одному из двух номеров.
    """
    brief = _brief(
        [_row(contract_id="A",
              phone_main="987064552 - раис джамаата 918436099 - хаким улицы")],
        monkeypatch)
    assert brief["tels"] == ["918436099", "987064552"]


def test_short_digit_garbage_is_not_a_phone(monkeypatch):
    """Пометки вроде «2» или «нет» телефоном не становятся."""
    brief = _brief([_row(contract_id="A", phone="2", phone_main="нет")], monkeypatch)
    assert brief["tels"] == []


def test_empty_contract_numbers_are_not_offered_to_search(monkeypatch):
    brief = _brief([_row(contract_id="  "), _row(contract_id="41367878")], monkeypatch)
    assert brief["ids"] == ["41367878"]


def _filter_body() -> str:
    start = INDEX.index("function personMatches(")
    return INDEX[start:INDEX.index("async function openP(", start)]


def test_shell_filters_by_name_contract_and_phone():
    body = _filter_body()
    assert "p.client" in body, "поиск по ФИО потерян"
    assert "p.ids" in body, "поиск по номеру договора не подключён"
    assert "p.tels" in body, "поиск по телефону не подключён"


def test_shell_compares_phones_by_digits_only():
    """Набранное «901234567» обязано совпасть с «+998 90 123-45-67»."""
    body = _filter_body()
    assert re.search(r"replace\(/\\D\+/g,\"\"\)", body), "запрос не приводится к цифрам"


def test_empty_query_shows_the_whole_portfolio():
    body = _filter_body()
    assert "if(!raw){ drawList(STATE.persons); return; }" in body
