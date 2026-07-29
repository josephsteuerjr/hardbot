"""import_grid / pick_grid — ядро импорта без openpyxl (путь пульта: calamine → JSON-грид).

Проверяем то, что раньше было доступно только через openpyxl-обёртку:
матчинг заголовков, upsert, дубли, пропуски без КД, незнакомые колонки,
выбор листа из нескольких (pick_grid).
"""
from datetime import date

import db
import pytest


def _import_grid(rows):
    rows = [list(row) for row in rows]
    for header in rows[:db.WORKBOOK_HEADER_SEARCH_ROWS]:
        normalized = [db._norm_title(value) for value in header]
        if db._norm_title(db.BY_KEY["contract_id"].title) not in normalized:
            continue
        if db._norm_title(db.BY_KEY["responsible"].title) not in normalized:
            header.append(db.BY_KEY["responsible"].title)
            for row in rows:
                if row is not header:
                    row.append(None)
        break
    return db.import_grid(
        rows,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )


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


def test_import_grid_conflicting_duplicate_blocks_whole_file():
    db.init_db()
    grid = _full_grid(
        _row(contract_id="IG-1", client="Тестов Тест", city="Москва",
             debt_total=100900, assign_date="2026-06-12"),
        _row(contract_id="IG-1", client="Тестов Обновлённый"),
        _row(contract_id="IG-ATOMIC", client="Не должен записаться"),
        _row(client="Без номера КД"),                               # skip_no_id
    )
    rep = _import_grid(grid)
    assert rep["total_rows"] == 4
    assert rep["inserted"] == 0
    assert rep["updated"] == 0
    assert rep["duplicates"] == 1
    assert rep["skipped_no_id"] == 1
    assert rep["unknown_columns"] == []
    assert rep["blocked"] is True
    assert rep["duplicate_conflicts"] == [
        {"contract_id": "IG-1", "rows": [4, 5], "fields": ["client"]}
    ]
    assert db.get_client("IG-1") is None
    assert db.get_client("IG-ATOMIC") is None


def test_import_grid_blocks_complementary_and_identical_duplicates():
    db.init_db()
    grid = _full_grid(
        _row(contract_id="IG-MERGE", client="Один человек", city="Москва",
             debt_total=100900),
        _row(contract_id="IG-MERGE", client="Один человек",
             responsible="Хозяев Х.", debt_overdue=50900),
    )
    rep = _import_grid(grid)
    assert rep["blocked"] is True
    assert rep["duplicates"] == 1
    assert rep["inserted"] == 0
    assert rep["updated"] == 0
    assert rep["quarantined_contracts"] == ["IG-MERGE"]
    assert db.get_client("IG-MERGE") is None


def test_import_grid_header_shift_and_unknown():
    db.init_db()
    # шапка без строки блоков, со сдвигом и левой колонкой
    titles = [None] + [f.title for f in db.FIELDS[:10]]
    row = ["мусор", None, None, "IG-2", "Сдвигов Сдвиг"] + [None] * 6
    rep = _import_grid([titles, row])
    assert rep["inserted"] == 1
    assert rep["unknown_columns"] == []
    assert db.get_client("IG-2")["client"] == "Сдвигов Сдвиг"


def test_import_grid_skips_source_row_and_empty():
    db.init_db()
    grid = _full_grid(
        [None] * len(db.FIELDS),                        # пустая строка
        _row(contract_id="IG-3", client="Живой"),
    )
    rep = _import_grid(grid)
    assert rep["total_rows"] == 1                       # сх/хард и пустая не считаются
    assert rep["inserted"] == 1


def test_import_grid_empty_and_garbage():
    assert _import_grid([])["total_rows"] == 0
    assert _import_grid([["а", "б"], ["в", "г"]])["inserted"] == 0   # шапки нет


def test_import_grid_blocks_unknown_columns_before_write():
    """Новый/переименованный заголовок не может молча исчезнуть из импорта."""
    db.init_db()
    titles = [db.BY_KEY["contract_id"].title, "Незнакомая колонка 1", "Незнакомая колонка 2"]
    row = ["IG-MANY", "xxx", "xxx"]
    rep = _import_grid([titles, row])
    assert rep["blocked"] is True
    assert rep["inserted"] == 0
    assert "незнакомые заголовки" in rep["error"]
    assert set(rep["unknown_columns"]) == {"Незнакомая колонка 1", "Незнакомая колонка 2"}
    assert db.get_client("IG-MANY") is None


def test_import_grid_accepts_only_versioned_ignored_master_columns():
    """Служебные колонки реального master допустимы только по явному контракту.

    Ежемесячные оплаты имеют узкий шаблон MM.YY; произвольная новая семантика
    по-прежнему останавливает импорт до изменения версии контракта.
    """
    db.init_db()
    titles = [
        db.BY_KEY["contract_id"].title,
        db.BY_KEY["responsible"].title,
        "Страна",
        "Оплаты за 07.26",
    ]
    accepted = db.import_grid(
        [titles, ["IG-IGNORED", "Агент", "УЗБ", 100]],
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    assert accepted["blocked"] is False
    assert accepted["unknown_columns"] == []
    assert accepted["ignored_columns"] == ["Страна", "Оплаты за 07.26"]
    assert db.get_client("IG-IGNORED") is not None

    rejected = db.import_grid(
        [
            [
                db.BY_KEY["contract_id"].title,
                db.BY_KEY["responsible"].title,
                "Оплаты за 13.26",
            ],
            ["IG-BAD-MONTH", "Агент", 100],
        ],
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    assert rejected["blocked"] is True
    assert rejected["unknown_columns"] == ["Оплаты за 13.26"]
    assert db.get_client("IG-BAD-MONTH") is None


def test_duplicate_ignored_header_also_blocks_before_write():
    """Дубли нельзя прятать в служебной части книги: геометрия неоднозначна."""
    db.init_db()
    rep = db.import_grid(
        [
            [
                db.BY_KEY["contract_id"].title,
                db.BY_KEY["responsible"].title,
                "Страна",
                " страна: ",
            ],
            ["IG-DUP-IGNORED", "Агент", "УЗБ", "УЗБ"],
        ],
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    assert rep["blocked"] is True
    assert rep["duplicate_titles"] == {"Страна": [3, 4]}
    assert db.get_client("IG-DUP-IGNORED") is None


def test_published_master_type_row_is_validated_and_never_becomes_client():
    """Реальные теги сх/хард/софт в строке 3 — metadata, не первая запись."""
    db.init_db()
    titles = [
        *[field.title for field in db.FIELDS],
        "Историчный комментарий Софт",
    ]
    type_row = [*[field.source for field in db.FIELDS], "софт"]
    assert "софт" in {db._norm_title(value) for value in type_row}
    rep = db.import_grid(
        [titles, type_row],
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    assert rep["blocked"] is False
    assert rep["type_row"] == {"row": 2, "status": "validated"}
    assert rep["total_rows"] == 0
    assert rep["inserted"] == 0
    assert db.get_client("сх") is None


def test_published_master_missing_or_malformed_type_row_blocks():
    db.init_db()
    titles = [field.title for field in db.FIELDS]
    data = [
        {
            "contract_id": "IG-NO-TYPE",
            "responsible": "Агент",
        }.get(field.key)
        for field in db.FIELDS
    ]
    missing = db.import_grid(
        [titles, data],
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    assert missing["blocked"] is True
    assert missing["type_row"]["status"] == "missing_or_malformed"
    assert db.get_client("IG-NO-TYPE") is None

    malformed_row = [field.source for field in db.FIELDS]
    malformed_row[next(
        index
        for index, field in enumerate(db.FIELDS)
        if field.key == "client"
    )] = "неизвестный профиль"
    malformed = db.import_grid(
        [titles, malformed_row, data],
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    assert malformed["blocked"] is True
    assert malformed["type_row"]["status"] == "mismatch"
    assert db.get_client("IG-NO-TYPE") is None


def test_import_grid_no_key_column_gives_diagnostic_not_silent_zero():
    """Без колонки-ключа сопоставить строки нечем — но вместо тихого нуля отчёт
    объясняет причину и показывает, что реально было в заголовке (для диагностики)."""
    db.init_db()
    rep = _import_grid([["Совсем левая колонка", "И эта тоже"], ["xxx", "xxx"]])
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
    rep = _import_grid([titles, ["IG-CS", "Оплачен"]])
    assert rep["inserted"] == 1
    assert db.get_client("IG-CS")["call_status"] == "Оплачен"
    assert db.BY_KEY["call_status"].title[0] == "С"   # кириллическая С (U+0421), не латинская C


def test_import_grid_debt_cell_empty_means_zero():
    """Ежемесячный переимпорт: статью больше не начисляют → она обнуляется.

    Раньше пустая ячейка значила «поле не пришло, не трогать», и в карточке навсегда
    оставались призраки прошлых начислений (эвакуация, ДТП), а сумма статей расходилась
    с «долгом всего». Обычные (не-денежные) поля upsert по-прежнему не затирает.
    """
    db.init_db()
    _import_grid(_full_grid(_row(contract_id="IG-D", partner="Делимобиль", city="Москва",
                                  rent=12000, evacuation=5000, debt_total=17000)))
    assert db.get_client("IG-D")["evacuation"] == "5000"
    assert db.get_client("IG-D")["debt_total"] == "17000"    # JSON-число → строка без .0

    # следующий месяц: эвакуацию погасили — колонка в файле есть, ячейка пуста
    _import_grid(_full_grid(_row(contract_id="IG-D", partner="Делимобиль",
                                  rent=12000, debt_total=12000)))
    c = db.get_client("IG-D")
    assert c["evacuation"] is None          # статья погашена, а не «данных нет»
    assert c["rent"] == "12000"
    assert c["city"] == "Москва"            # не-денежное поле пустой ячейкой не затёрто


def test_import_grid_absent_debt_column_is_not_cleared():
    """Колонки разбивки, которой в файле НЕТ вообще, обнуление не касается — иначе
    частичный экспорт (КД + аренда) стёр бы все остальные статьи."""
    db.init_db()
    _import_grid(_full_grid(_row(contract_id="IG-P", partner="Делимобиль",
                                  rent=12000, evacuation=5000)))
    rep = _import_grid([[db.BY_KEY["contract_id"].title, db.BY_KEY["rent"].title],
                        ["IG-P", 9000]])
    assert rep["updated"] == 1
    c = db.get_client("IG-P")
    assert c["rent"] == "9000"
    assert c["evacuation"] == "5000"        # колонки не было в файле → не тронули


def test_import_grid_matches_latin_homoglyph_header():
    """В самом «Хард.xlsb» заголовок «Cтатус звонка» начинается с ЛАТИНСКОЙ C — колонка
    молча уезжала в unknown_columns, и статус звонка не импортировался ни разу."""
    db.init_db()
    titles = [db.BY_KEY["contract_id"].title, "Cтатус звонка"]   # C = U+0043
    assert titles[1][0] == "C" and ord(titles[1][0]) == 0x43
    rep = _import_grid([titles, ["IG-H", "Оплачен"]])
    assert rep["unknown_columns"] == []
    assert db.get_client("IG-H")["call_status"] == "Оплачен"


def test_norm_title_homoglyphs_do_not_collide_across_fields():
    """Сворачивание латиницы к кириллице не должно склеить два разных поля реестра."""
    norms = [db._norm_title(f.title) for f in db.FIELDS]
    assert len(norms) == len(set(norms))


def test_duplicate_recognized_header_blocks_before_any_write():
    db.init_db()
    titles = [
        db.BY_KEY["contract_id"].title,
        db.BY_KEY["responsible"].title,
        db.BY_KEY["client"].title,
        "Клиент:",
    ]
    rep = db.import_grid(
        [titles, ["IG-DUP-H", "Агент", "Первый", "Второй"]],
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    assert rep["blocked"] is True
    assert rep["duplicate_headers"] == {"client": [3, 4]}
    assert db.get_client("IG-DUP-H") is None


def test_renamed_responsible_header_is_not_silently_null():
    db.init_db()
    rep = db.import_grid(
        [
            [db.BY_KEY["contract_id"].title, "Новый ответственный"],
            ["IG-OWNER-DRIFT", "Агент"],
        ],
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    assert rep["blocked"] is True
    assert rep["missing_headers"] == ["responsible"]
    assert db.get_client("IG-OWNER-DRIFT") is None


def test_published_master_requires_full_approved_shape():
    db.init_db()
    rep = db.import_grid(
        [
            [
                db.BY_KEY["contract_id"].title,
                db.BY_KEY["responsible"].title,
            ],
            ["IG-PARTIAL-MASTER", "Агент"],
        ],
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    assert rep["blocked"] is True
    assert "client" in rep["missing_headers"]
    assert db.get_client("IG-PARTIAL-MASTER") is None


def test_import_grid_reports_protected_anchor_and_isolated_call_fields():
    """Дата визита защищена, а поле звонка не становится visit overlay."""
    db.init_db()
    keys = ["contract_id", "client", "responsible", "city", "visit_date", "call_status"]
    titles = [db.BY_KEY[k].title for k in keys]
    _import_grid([titles, ["IG-PR", "Клиент", "Агент", "Ташкент", None, None]])
    today = date.today().isoformat()
    db.update_person("IG-PR", {"visit_date": today, "call_status": "Оплачен"})
    db.log_visit("IG-PR", 101, "визит", op_id="ig-pr-prot")
    db.record_bot_visit("IG-PR", {"call_status": "Оплачен"}, today)

    rep = _import_grid([titles, ["IG-PR", "Клиент", "Агент", "Самарканд",
                                 "2026-01-01", "Нет контакта"]])
    assert rep["updated"] == 1
    assert rep["protected_cells"] == 1              # visit_date выброшена
    assert rep["protected_contracts"] == 1
    assert rep["isolated_call_cells"] == 1          # call_status из master изолирован
    c = db.get_client("IG-PR")
    assert c["city"] == "Самарканд"                 # обычное поле источника обновилось
    assert c["visit_date"] == today                 # результат бота не тронут
    assert c["call_status"] == "Оплачен"

    # без завершённого визита бота счётчик молчит, поля визита из файла проходят
    rep2 = _import_grid([titles, ["IG-NP", "Новый", "Агент", "Бухара",
                                  "2026-01-01", "Нет контакта"]])
    assert rep2["protected_cells"] == 0
    assert rep2["protected_contracts"] == 0
    assert db.get_client("IG-NP")["call_status"] == "Нет контакта"


def test_pick_grid():
    good = _full_grid(_row(contract_id="X"))
    with pytest.raises(ValueError, match="обязательный лист"):
        db.pick_grid([])
    # похожий лист не является допустимым fallback
    sheets = [{"name": "Мусор", "grid": [["привет", "мир"]]},
              {"name": "Данные", "grid": good}]
    with pytest.raises(ValueError, match="обязательный лист"):
        db.pick_grid(sheets)
    # exact «Портфели» — единственный разрешённый лист
    sheets = [{"name": "Данные", "grid": good},
              {"name": "Портфели", "grid": [["только", "имя"]]}]
    assert db.pick_grid(sheets) == [["только", "имя"]]
    with pytest.raises(ValueError, match="повторяющиеся имена"):
        db.pick_grid([
            {"name": "Портфели", "grid": good},
            {"name": "Портфели", "grid": [["подменённый", "лист"]]},
        ])


# --- Ответственный: видимость строки для выездника ---------------------------
# Строка без «ответственный Сотрудник» попадает в базу, но невидима выезднику и не
# входит ни в один отчёт руководителя. На проде так молча накопилось 1970 договоров
# из 5267 — в отчёте импорта это не показывалось нигде.

def test_report_counts_rows_without_responsible():
    db.init_db()
    grid = _full_grid(
        _row(contract_id="IG-NR-1", client="Ничей", debt_total=1000),
        _row(contract_id="IG-NR-2", client="С владельцем", responsible="Хозяев Х."),
    )
    rep = _import_grid(grid)
    assert rep["no_responsible"] == 1
    assert db.get_client("IG-NR-1")["responsible"] in (None, "")
    assert db.get_client("IG-NR-2")["responsible"] == "Хозяев Х."


def test_existing_owner_counts_as_owned_when_column_absent():
    """Повторный импорт без колонки владельца не считает строку ничьей."""
    db.init_db()
    _import_grid(_full_grid(
        _row(contract_id="IG-NR-3", client="Был хозяин", responsible="Хозяев Х.")))
    rep = _import_grid(_full_grid(
        _row(contract_id="IG-NR-3", client="Обновлён", debt_total=555)))
    assert rep["no_responsible"] == 0
    assert db.get_client("IG-NR-3")["responsible"] == "Хозяев Х."


def test_blank_cell_does_not_wipe_responsible():
    """Ячейка из пробелов — не «очистить поле»: раньше она затирала владельца,
    и кейс исчезал у выездника без следа в отчёте."""
    db.init_db()
    _import_grid(_full_grid(
        _row(contract_id="IG-NR-4", client="Хозяин есть", responsible="Хозяев Х.")))
    rep = _import_grid(_full_grid(
        _row(contract_id="IG-NR-4", client="Хозяин есть", responsible="   ")))
    assert db.get_client("IG-NR-4")["responsible"] == "Хозяев Х."
    assert rep["no_responsible"] == 0


def test_real_style_two_clients_with_same_contract_is_quarantined():
    """Реальная коллизия 35854193 не может слиться в UNIQUE(contract_id)."""
    db.init_db()
    rep = _import_grid(_full_grid(
        _row(contract_id="35854193", client="Первый клиент", responsible="Хозяев Х."),
        _row(contract_id="35854193", client="Второй клиент", responsible="Хозяев Х."),
    ))
    assert rep["blocked"] is True
    assert rep["quarantined_contracts"] == ["35854193"]
    assert db.get_client("35854193") is None


def test_numeric_equivalent_contract_ids_collide_atomically():
    db.init_db()
    rep = _import_grid(_full_grid(
        _row(contract_id=12345, client="Первый"),
        _row(contract_id="12345.0", client="Второй"),
        _row(contract_id="SAFE", client="Тоже не должен записаться"),
    ))
    assert rep["blocked"] is True
    assert rep["quarantined_contracts"] == ["12345"]
    assert db.get_client("12345") is None
    assert db.get_client("SAFE") is None


def test_missing_or_unknown_source_kind_is_fail_closed():
    db.init_db()
    grid = _full_grid(_row(contract_id="NO-SOURCE", client="Не войдёт"))

    missing = db.import_grid(grid)
    unknown = db.import_grid(grid, source_kind="personal_office_overlay")

    assert missing["blocked"] is True
    assert unknown["blocked"] is True
    assert db.get_client("NO-SOURCE") is None
