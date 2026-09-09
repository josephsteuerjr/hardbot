"""Карта колонок реестра: переименование и новая колонка перестают быть аварией.

Что здесь проверяется по существу. Общий реестр — чужой файл, и офис меняет его
без предупреждения: в сентябре 2026 «ответственный Сотрудник» превратился в
«ответственный Сотрудник ХАРД» (плюс появилась колонка СОФТ), «Дата закрепления»
разделилась так же, и добавился блок акции. Раньше это означало остановку
загрузки до правки кода и выкладки сервера.

Два разных механизма закрывают два разных случая:

* переименование, о котором уже знают все стороны, живёт в контракте обмена
  (`header_aliases`) — файл читается штатно, ничего настраивать не нужно;
* всё остальное решает оператор в пульте, а хранится в рабочей базе
  (`registry_column_map`): связать заголовок с полем, включить колонку как
  дополнительное поле или скрыть её из отчёта.

Схема полей, экспортный отпечаток и форма файла для офиса при этом не меняются
вообще — иначе пришлось бы переделывать шаблоны офиса и registry_cycle.
"""
import json

import pytest

import db
from exchange_contract import WORKBOOK_HEADER_ALIASES
from fields import BY_KEY, FIELDS


def _grid(rows, header=None, types=None, extra_headers=()):
    """Мини-книга: заголовки, строка типов, данные."""
    base = list(header or [f.title for f in FIELDS])
    head = base + list(extra_headers)
    type_row = list(types or [f.source for f in FIELDS]) + [""] * len(extra_headers)
    out = [head, type_row]
    for row in rows:
        out.append([row.get(f.key, "") for f in FIELDS]
                   + [row.get(title, "") for title in extra_headers])
    return out


@pytest.fixture(autouse=True)
def _clean():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'CM-%'")
        conn.execute("DELETE FROM client_extra WHERE contract_id LIKE 'CM-%'")
        conn.execute("DELETE FROM settings WHERE key = ?",
                     (db.REGISTRY_COLUMN_MAP_SETTING,))
        conn.commit()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?",
                     (db.REGISTRY_COLUMN_MAP_SETTING,))
        conn.commit()


# --- переименование, закреплённое контрактом ---------------------------------
def test_contract_knows_the_september_rename():
    """«…ХАРД» читается как штатная колонка, без единой настройки."""
    assert "ответственный Сотрудник ХАРД" in WORKBOOK_HEADER_ALIASES["responsible"]
    assert "Дата закрепления ХАРД" in WORKBOOK_HEADER_ALIASES["assign_date"]

    header = [f.title for f in FIELDS]
    header[header.index("ответственный Сотрудник")] = "ответственный Сотрудник ХАРД"
    header[header.index("Дата закрепления")] = "Дата закрепления ХАРД"
    report = db.import_grid(
        _grid([{"contract_id": "CM-1", "client": "Тест К.",
                "responsible": "Иванов И.", "assign_date": "2026-09-01"}],
              header=header),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not report.get("blocked"), report.get("error")
    row = db.get_client("CM-1")
    assert row["responsible"] == "Иванов И."
    assert row["assign_date"] == "2026-09-01"


def test_soft_twins_are_ignored_without_noise():
    """Парные колонки колл-центра — известный мусор, а не «незнакомая колонка».

    Иначе каждый импорт заканчивался бы предупреждением про две колонки,
    которые боту не нужны и никогда не понадобятся.
    """
    report = db.import_grid(
        _grid([{"contract_id": "CM-2", "responsible": "Иванов И."}],
              extra_headers=("ответственный Сотрудник СОФТ", "Дата закрепления СОФТ")),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not report.get("blocked"), report.get("error")
    assert report["unknown_columns"] == []
    assert "ответственный Сотрудник СОФТ" in report["ignored_columns"]


# --- карта оператора ----------------------------------------------------------
def test_operator_alias_unblocks_a_renamed_required_column():
    """Обязательная колонка исчезла по имени — оператор связывает её сам.

    Это главный сценарий: без карты такой файл отбивается целиком, и починить
    его можно было только правкой кода.
    """
    header = [f.title for f in FIELDS]
    header[header.index("ответственный Сотрудник")] = "Сотрудник выезда"
    grid = _grid([{"contract_id": "CM-3", "responsible": "Петров П."}],
                 header=header)

    blocked = db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert blocked["blocked"] and "responsible" in blocked["missing_headers"]

    db.save_registry_column_map({"aliases": {"responsible": ["Сотрудник выезда"]}},
                                author="пульт Егора")
    report = db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not report.get("blocked"), report.get("error")
    assert db.get_client("CM-3")["responsible"] == "Петров П."
    assert report["operator_aliases"] == [
        "Сотрудник выезда → ответственный Сотрудник"]


def test_new_column_becomes_an_extra_field_and_reaches_the_card():
    """Новая колонка офиса доезжает до выездника, не меняя схему реестра.

    Именно так подключён сентябрьский блок акции: значения нужны в поле, а
    трогать экспортную схему (и её отпечаток) ради этого нельзя.
    """
    before = db.export_schema()
    db.save_registry_column_map(
        {"extras": [{"title": "условия акции", "label": "Акция"},
                    {"title": "сумма акции", "label": "Сумма акции"}]},
        author="пульт Егора")
    report = db.import_grid(
        _grid([{"contract_id": "CM-4", "client": "Тест К.",
                "responsible": "Иванов И.",
                "условия акции": "Прощение до 50% процентов",
                "сумма акции": 14500.0,
                "начало акции": "2026-08-19"}],
              extra_headers=("условия акции", "сумма акции", "начало акции")),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not report.get("blocked"), report.get("error")
    assert report["extra_columns"] == ["Акция", "Сумма акции"]
    assert report["extra_cells"] == 2
    # Колонка, про которую оператор ещё не решил, остаётся видимой в отчёте —
    # но импорт не блокирует.
    assert report["unknown_columns"] == ["начало акции"]
    assert db.export_schema() == before          # схема не тронута

    values = db.registry_extra_values(["CM-4"])["CM-4"]
    assert [(item["label"], item["value"]) for item in values] == [
        ("Акция", "Прощение до 50% процентов"), ("Сумма акции", "14500")]


def test_extra_value_disappears_when_the_cell_empties():
    """Акция кончилась — карточка не должна показывать её вечно."""
    db.save_registry_column_map({"extras": [{"title": "условия акции",
                                             "label": "Акция"}]})
    db.import_grid(
        _grid([{"contract_id": "CM-5", "responsible": "Иванов И.",
                "условия акции": "Прощение"}],
              extra_headers=("условия акции",)),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert db.registry_extra_values(["CM-5"])

    db.import_grid(
        _grid([{"contract_id": "CM-5", "responsible": "Иванов И.",
                "условия акции": ""}],
              extra_headers=("условия акции",)),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert db.registry_extra_values(["CM-5"]) == {}


def test_hidden_column_stops_being_reported_as_unknown():
    db.save_registry_column_map({"ignored": ["служебная метка офиса"]})
    report = db.import_grid(
        _grid([{"contract_id": "CM-6", "responsible": "Иванов И."}],
              extra_headers=("служебная метка офиса",)),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert report["unknown_columns"] == []
    assert "служебная метка офиса" in report["ignored_columns"]


def test_column_removed_from_the_map_disappears_from_the_card():
    """Оператор убрал колонку из карты — карточка о ней сразу забывает.

    Значения остаются в базе до следующего импорта, но показывать их, когда
    решение отменено, нельзя: это выглядело бы как «настройка не сработала».
    """
    db.save_registry_column_map({"extras": [{"title": "условия акции",
                                             "label": "Акция"}]})
    db.import_grid(
        _grid([{"contract_id": "CM-7", "responsible": "Иванов И.",
                "условия акции": "Прощение"}],
              extra_headers=("условия акции",)),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert db.registry_extra_values(["CM-7"])
    db.save_registry_column_map({})
    assert db.registry_extra_values(["CM-7"]) == {}


# --- защита карты от опасных решений ------------------------------------------
def test_alias_cannot_steal_a_canonical_header_of_another_field():
    """Иначе у поля стало бы два источника, и импорт отбился бы как дубль —
    уже на боевом файле и без внятной причины."""
    with pytest.raises(ValueError, match="штатный заголовок"):
        db.save_registry_column_map({"aliases": {"city": ["Адрес"]}})


def test_one_header_cannot_get_two_decisions():
    with pytest.raises(ValueError, match="другим решением"):
        db.save_registry_column_map({
            "aliases": {"city": ["город офиса"]},
            "extras": [{"title": "город офиса", "label": "Город офиса"}],
        })


def test_alias_to_an_unknown_field_is_rejected():
    with pytest.raises(ValueError, match="нет в схеме"):
        db.save_registry_column_map({"aliases": {"выдуманное": ["что-то"]}})


def test_registry_column_cannot_be_hidden_or_duplicated_as_extra():
    with pytest.raises(ValueError, match="штатная колонка"):
        db.save_registry_column_map({"ignored": ["Клиент"]})
    with pytest.raises(ValueError, match="штатная колонка"):
        db.save_registry_column_map({"extras": [{"title": "Клиент"}]})


def test_broken_setting_does_not_break_the_import():
    """Испорченная настройка не должна останавливать загрузку реестра."""
    db.set_setting(db.REGISTRY_COLUMN_MAP_SETTING, "{это не json")
    assert db.registry_column_map()["aliases"] == {}
    report = db.import_grid(
        _grid([{"contract_id": "CM-8", "responsible": "Иванов И."}]),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not report.get("blocked"), report.get("error")


def test_saved_map_records_who_and_when():
    saved = db.save_registry_column_map({"ignored": ["метка"]}, author="ноутбук Егора")
    assert saved["updated_by"] == "ноутбук Егора" and saved["updated_at"]
    assert db.registry_column_map()["updated_by"] == "ноутбук Егора"
    stored = json.loads(db.get_setting(db.REGISTRY_COLUMN_MAP_SETTING))
    assert stored["ignored"] == ["метка"]


def test_extra_columns_have_a_ceiling():
    """Карта — инструмент оператора, а не вторая схема реестра."""
    with pytest.raises(ValueError, match="слишком много"):
        db.save_registry_column_map({
            "extras": [{"title": f"колонка {n}"}
                       for n in range(db.MAX_COLUMN_MAP_EXTRAS + 1)]})


def test_extra_values_are_trimmed():
    db.save_registry_column_map({"extras": [{"title": "длинная", "label": "Длинная"}]})
    db.import_grid(
        _grid([{"contract_id": "CM-9", "responsible": "Иванов И.",
                "длинная": "я" * (db.MAX_EXTRA_VALUE_LEN + 50)}],
              extra_headers=("длинная",)),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    value = db.registry_extra_values(["CM-9"])["CM-9"][0]["value"]
    assert len(value) == db.MAX_EXTRA_VALUE_LEN


def test_extra_field_title_is_not_a_registry_key():
    """Дополнительное поле не создаёт колонку в clients: схема БД неизменна."""
    db.save_registry_column_map({"extras": [{"title": "условия акции"}]})
    with db.connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(clients)")}
    assert "условия акции" not in columns
    assert set(BY_KEY) <= columns
