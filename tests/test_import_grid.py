"""import_grid / pick_grid — ядро импорта без openpyxl (путь пульта: calamine → JSON-грид).

Проверяем то, что раньше было доступно только через openpyxl-обёртку:
матчинг заголовков, upsert, дубли, пропуски без КД, незнакомые колонки,
выбор листа из нескольких (pick_grid).
"""
from datetime import date

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


def test_import_grid_conflicting_duplicate_blocks_whole_file():
    db.init_db()
    grid = _full_grid(
        _row(contract_id="IG-1", client="Тестов Тест", city="Москва",
             debt_total=100900, assign_date="2026-06-12"),
        _row(contract_id="IG-1", client="Тестов Обновлённый"),
        _row(contract_id="IG-ATOMIC", client="Не должен записаться"),
        _row(client="Без номера КД"),                               # skip_no_id
    )
    rep = db.import_grid(grid)
    assert rep["total_rows"] == 4
    assert rep["inserted"] == 0
    assert rep["updated"] == 0
    assert rep["duplicates"] == 1
    assert rep["skipped_no_id"] == 1
    assert rep["unknown_columns"] == []
    assert rep["blocked"] is True
    assert rep["duplicate_conflicts"] == [
        {"contract_id": "IG-1", "fields": ["client"]}
    ]
    assert db.get_client("IG-1") is None
    assert db.get_client("IG-ATOMIC") is None


def test_import_grid_merges_complementary_and_identical_duplicates():
    db.init_db()
    grid = _full_grid(
        _row(contract_id="IG-MERGE", client="Один человек", city="Москва",
             debt_total=100900),
        _row(contract_id="IG-MERGE", client="Один человек",
             responsible="Хозяев Х.", debt_overdue=50900),
    )
    rep = db.import_grid(grid)
    assert rep["blocked"] is False
    assert rep["duplicates"] == 1
    assert rep["inserted"] == 1
    assert rep["updated"] == 0
    c = db.get_client("IG-MERGE")
    assert c["client"] == "Один человек"
    assert c["city"] == "Москва"
    assert c["responsible"] == "Хозяев Х."
    assert c["debt_total"] == "100900"
    assert c["debt_overdue"] == "50900"


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


def test_import_grid_debt_cell_empty_means_zero():
    """Ежемесячный переимпорт: статью больше не начисляют → она обнуляется.

    Раньше пустая ячейка значила «поле не пришло, не трогать», и в карточке навсегда
    оставались призраки прошлых начислений (эвакуация, ДТП), а сумма статей расходилась
    с «долгом всего». Обычные (не-денежные) поля upsert по-прежнему не затирает.
    """
    db.init_db()
    db.import_grid(_full_grid(_row(contract_id="IG-D", partner="Делимобиль", city="Москва",
                                   rent=12000, evacuation=5000, debt_total=17000)))
    assert db.get_client("IG-D")["evacuation"] == "5000"
    assert db.get_client("IG-D")["debt_total"] == "17000"    # JSON-число → строка без .0

    # следующий месяц: эвакуацию погасили — колонка в файле есть, ячейка пуста
    db.import_grid(_full_grid(_row(contract_id="IG-D", partner="Делимобиль",
                                   rent=12000, debt_total=12000)))
    c = db.get_client("IG-D")
    assert c["evacuation"] is None          # статья погашена, а не «данных нет»
    assert c["rent"] == "12000"
    assert c["city"] == "Москва"            # не-денежное поле пустой ячейкой не затёрто


def test_import_grid_absent_debt_column_is_not_cleared():
    """Колонки разбивки, которой в файле НЕТ вообще, обнуление не касается — иначе
    частичный экспорт (КД + аренда) стёр бы все остальные статьи."""
    db.init_db()
    db.import_grid(_full_grid(_row(contract_id="IG-P", partner="Делимобиль",
                                   rent=12000, evacuation=5000)))
    rep = db.import_grid([[db.BY_KEY["contract_id"].title, db.BY_KEY["rent"].title],
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
    rep = db.import_grid([titles, ["IG-H", "Оплачен"]])
    assert rep["unknown_columns"] == []
    assert db.get_client("IG-H")["call_status"] == "Оплачен"


def test_norm_title_homoglyphs_do_not_collide_across_fields():
    """Сворачивание латиницы к кириллице не должно склеить два разных поля реестра."""
    norms = [db._norm_title(f.title) for f in db.FIELDS]
    assert len(norms) == len(set(norms))


def test_import_grid_reports_protected_cells():
    """Защита визитов (BOT_IMPORT_PROTECTED) раньше срабатывала молча — руководитель не
    понимал, почему его правка колонок визита «не вошла». Теперь отчёт говорит явно,
    сколько значений и по скольким договорам выброшено."""
    db.init_db()
    keys = ["contract_id", "client", "responsible", "city", "visit_date", "call_status"]
    titles = [db.BY_KEY[k].title for k in keys]
    db.import_grid([titles, ["IG-PR", "Клиент", "Агент", "Ташкент", None, None]])
    today = date.today().isoformat()
    db.update_person("IG-PR", {"visit_date": today, "call_status": "Оплачен"})
    db.log_visit("IG-PR", 101, "визит", op_id="ig-pr-prot")
    db.record_bot_visit("IG-PR", {"call_status": "Оплачен"}, today)

    rep = db.import_grid([titles, ["IG-PR", "Клиент", "Агент", "Самарканд",
                                   "2026-01-01", "Нет контакта"]])
    assert rep["updated"] == 1
    assert rep["protected_cells"] == 2              # visit_date + call_status выброшены
    assert rep["protected_contracts"] == 1
    c = db.get_client("IG-PR")
    assert c["city"] == "Самарканд"                 # обычное поле источника обновилось
    assert c["visit_date"] == today                 # результат бота не тронут
    assert c["call_status"] == "Оплачен"

    # без завершённого визита бота счётчик молчит, поля визита из файла проходят
    rep2 = db.import_grid([titles, ["IG-NP", "Новый", "Агент", "Бухара",
                                    "2026-01-01", "Нет контакта"]])
    assert rep2["protected_cells"] == 0
    assert rep2["protected_contracts"] == 0
    assert db.get_client("IG-NP")["call_status"] == "Нет контакта"


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
    rep = db.import_grid(grid)
    assert rep["no_responsible"] == 1
    assert db.get_client("IG-NR-1")["responsible"] in (None, "")
    assert db.get_client("IG-NR-2")["responsible"] == "Хозяев Х."


def test_existing_owner_counts_as_owned_when_column_absent():
    """Повторный импорт без колонки владельца не считает строку ничьей."""
    db.init_db()
    db.import_grid(_full_grid(
        _row(contract_id="IG-NR-3", client="Был хозяин", responsible="Хозяев Х.")))
    rep = db.import_grid(_full_grid(
        _row(contract_id="IG-NR-3", client="Обновлён", debt_total=555)))
    assert rep["no_responsible"] == 0
    assert db.get_client("IG-NR-3")["responsible"] == "Хозяев Х."


def test_blank_cell_does_not_wipe_responsible():
    """Ячейка из пробелов — не «очистить поле»: раньше она затирала владельца,
    и кейс исчезал у выездника без следа в отчёте."""
    db.init_db()
    db.import_grid(_full_grid(
        _row(contract_id="IG-NR-4", client="Хозяин есть", responsible="Хозяев Х.")))
    rep = db.import_grid(_full_grid(
        _row(contract_id="IG-NR-4", client="Хозяин есть", responsible="   ")))
    assert db.get_client("IG-NR-4")["responsible"] == "Хозяев Х."
    assert rep["no_responsible"] == 0


def test_no_responsible_counts_contracts_not_rows():
    """У одного КД в реестре бывает несколько строк, и владелец может стоять только
    в одной из них — в любой. Счётчик обязан считать договоры, а не строки файла."""
    db.init_db()
    rep = db.import_grid(_full_grid(
        _row(contract_id="IG-NR-5", client="Многострочный", responsible="Хозяев Х."),
        _row(contract_id="IG-NR-5", client="Многострочный", debt_total=100),
        _row(contract_id="IG-NR-5", client="Многострочный", debt_overdue=50),
    ))
    assert rep["no_responsible"] == 0, "дубли строк не должны раздувать счётчик"
    assert db.get_client("IG-NR-5")["responsible"] == "Хозяев Х."


def test_no_responsible_counts_when_owner_arrives_in_later_row():
    """Владелец пришёл во ВТОРОЙ строке того же договора — договор не ничей."""
    db.init_db()
    rep = db.import_grid(_full_grid(
        _row(contract_id="IG-NR-6", client="Поздний хозяин", debt_total=100),
        _row(contract_id="IG-NR-6", client="Поздний хозяин", responsible="Хозяев Х."),
    ))
    assert rep["no_responsible"] == 0
    assert db.get_client("IG-NR-6")["responsible"] == "Хозяев Х."
