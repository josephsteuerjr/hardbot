"""import_grid / pick_grid — ядро импорта без openpyxl (путь пульта: calamine → JSON-грид).

Проверяем то, что раньше было доступно только через openpyxl-обёртку:
матчинг заголовков, upsert, дубли, пропуски без КД, незнакомые колонки,
выбор листа из нескольких (pick_grid).
"""
import db


def _row(**kw) -> list:
    """Строка грида в порядке FIELDS: значения по ключам, остальное None."""
    return [kw.get(f.key) for f in db.FIELDS]


def _full_grid(*data_rows) -> list[list]:
    """Грид как в реестре: строка блоков + заголовки + сх/хард + данные."""
    blocks = [None] * len(db.FIELDS)
    for s, _e, name in db._block_spans():
        blocks[s] = name
    return [blocks,
            [f.title for f in db.FIELDS],
            [f.source for f in db.FIELDS],
            *data_rows]


def test_import_grid_upsert_and_report():
    db.init_db()
    grid = _full_grid(
        _row(contract_id="IG-1", client="Тестов Тест", city="Москва",
             debt_total=100900, assign_date="2026-06-12"),
        _row(contract_id="IG-1", client="Тестов Обновлённый"),      # дубль → update
        _row(client="Без номера КД"),                               # skip_no_id
    )
    rep = db.import_grid(grid)
    assert rep["total_rows"] == 3
    assert rep["inserted"] == 1
    assert rep["updated"] == 1
    assert rep["duplicates"] == 1
    assert rep["skipped_no_id"] == 1
    assert rep["unknown_columns"] == []

    c = db.get_client("IG-1")
    assert c["client"] == "Тестов Обновлённый"          # upsert обновил поле
    assert c["city"] == "Москва"                        # но не затёр прочие
    assert c["debt_total"] == "100900"                  # JSON-число → строка без .0
    assert c["assign_date"] == "2026-06-12"             # ISO-дата от calamine как есть


def test_import_grid_header_shift_and_unknown():
    db.init_db()
    # шапка без строки блоков, со сдвигом и левой колонкой
    titles = ["Левая колонка"] + [f.title for f in db.FIELDS[:10]]
    row = ["мусор", None, None, "IG-2", "Сдвигов Сдвиг"] + [None] * 6
    rep = db.import_grid([titles, row])
    assert rep["inserted"] == 1
    assert "Левая колонка" in rep["unknown_columns"]
    assert db.get_client("IG-2")["client"] == "Сдвигов Сдвиг"


def test_import_grid_skips_source_row_and_empty():
    db.init_db()
    grid = _full_grid(
        [None] * len(db.FIELDS),                        # пустая строка
        _row(contract_id="IG-3", client="Живой"),
    )
    rep = db.import_grid(grid)
    assert rep["total_rows"] == 1                       # сх/хард и пустая не считаются
    assert rep["inserted"] == 1


def test_import_grid_empty_and_garbage():
    assert db.import_grid([])["total_rows"] == 0
    assert db.import_grid([["а", "б"], ["в", "г"]])["inserted"] == 0   # шапки нет


def test_import_grid_survives_many_unknown_columns_if_key_matches():
    """«Лишние столбцы» не должны обнулять импорт: реестр с кучей незнакомых заголовков,
    но с распознанным ключом (Номер КД / ID), — должен импортироваться. Раньше критерий
    был «совпало >=3 колонки», из-за пары опечаток в незначащих заголовках мог тихо
    вернуть нули без объяснения — теперь критерий: распознана колонка-ключ."""
    db.init_db()
    titles = [db.BY_KEY["contract_id"].title, "Незнакомая колонка 1", "Незнакомая колонка 2"]
    row = ["IG-MANY", "xxx", "xxx"]
    rep = db.import_grid([titles, row])
    assert rep["inserted"] == 1
    assert "error" not in rep
    assert set(rep["unknown_columns"]) == {"Незнакомая колонка 1", "Незнакомая колонка 2"}


def test_import_grid_no_key_column_gives_diagnostic_not_silent_zero():
    """Без колонки-ключа сопоставить строки нечем — но вместо тихого нуля отчёт
    объясняет причину и показывает, что реально было в заголовке (для диагностики)."""
    db.init_db()
    rep = db.import_grid([["Совсем левая колонка", "И эта тоже"], ["xxx", "xxx"]])
    assert rep["inserted"] == 0
    assert "error" in rep
    assert "Совсем левая колонка" in rep["header_sample"]


def test_norm_title_strips_zero_width_and_trailing_punct():
    assert db._norm_title("Дата закрепления") == db._norm_title("Дата закрепления:")
    assert db._norm_title("Дата закрепления") == db._norm_title("Дата закрепления*")
    assert db._norm_title("Дата закрепления") == db._norm_title("​Дата закрепления​")
    assert db._norm_title("Дата закрепления") == db._norm_title("Дата  закрепления")  # двойной пробел


def test_call_status_title_matches_cyrillic():
    """Раньше "Cтатус звонка" в fields.py начинался с латинской C — не совпадало ни с
    одним реальным (кириллическим) заголовком реестра."""
    db.init_db()
    titles = [db.BY_KEY["contract_id"].title, db.BY_KEY["call_status"].title]
    rep = db.import_grid([titles, ["IG-CS", "Оплачен"]])
    assert rep["inserted"] == 1
    assert db.get_client("IG-CS")["call_status"] == "Оплачен"
    assert db.BY_KEY["call_status"].title[0] == "С"   # кириллическая С (U+0421), не латинская C


def test_pick_grid():
    good = _full_grid(_row(contract_id="X"))
    assert db.pick_grid([]) == []
    # выбирает лист с узнаваемыми заголовками, а не первый попавшийся
    sheets = [{"name": "Мусор", "grid": [["привет", "мир"]]},
              {"name": "Данные", "grid": good}]
    assert db.pick_grid(sheets) is good
    # «Портфели» побеждает всегда
    sheets = [{"name": "Данные", "grid": good},
              {"name": "Портфели", "grid": [["только", "имя"]]}]
    assert db.pick_grid(sheets) == [["только", "имя"]]
