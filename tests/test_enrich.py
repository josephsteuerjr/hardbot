"""enrich.py — планировщик обогащения реестра (чистая логика, без Excel).

Инвариант, ради которого всё затевалось: план трогает ТОЛЬКО кейсы, заполненные
ботом, и ТОЛЬКО их заполненные столбцы визита. Никаких записей в чужие строки,
чужие колонки, пустыми значениями — и никакой пересборки файла.
"""
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import enrich  # noqa: E402


# --- фикстуры: мини-реестр формата «Хард» ------------------------------------
FIELDS = [
    {"key": "contract_id", "title": "Номер КД / ID", "block": "Инфо", "source": "сх",
     "date": False, "num": False},
    {"key": "client", "title": "Клиент", "block": "Инфо", "source": "сх",
     "date": False, "num": False},
    {"key": "debt_total", "title": "долг всего", "block": "Долг", "source": "сх",
     "date": False, "num": True},
    {"key": "visit_date", "title": "Дата выезда", "block": "Выезд", "source": "хард",
     "date": True, "num": False},
    {"key": "call_status", "title": "Статус звонка", "block": "Выезд", "source": "хард",
     "date": False, "num": False},
    {"key": "call_comment", "title": "комментарий звонка", "block": "Выезд",
     "source": "хард", "date": False, "num": False},
    {"key": "overall_result", "title": "Общий результат", "block": "Выезд",
     "source": "сх", "date": False, "num": False},
]

# Заголовок «Cтатус звонка» — с ЛАТИНСКОЙ C, как в реальном Хард.xlsb
HEADERS = ["Номер КД / ID", "Клиент", "долг всего", "Дата выезда",
           "Cтатус звонка", "комментарий звонка", "Общий результат"]


def make_grid(data_rows):
    return [
        ["Инфо", None, None, "Выезд", None, None, None],   # блоки
        HEADERS,                                            # заголовки
        ["сх", "сх", "сх", "хард", "хард", "хард", "сх"],   # строка-источник
        *data_rows,
    ]


def srv_row(cid, **kw):
    row = {f["key"]: None for f in FIELDS}
    row["contract_id"] = cid
    row.update(kw)
    return row


# --- шапка --------------------------------------------------------------------
def test_find_header_and_homoglyph():
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    h, cols = enrich.find_header(grid, FIELDS)
    assert h == 1
    assert cols["contract_id"] == 0
    assert cols["call_status"] == 4          # латинская C в файле не мешает


def test_no_key_column_is_loud():
    grid = [["Что-то", "Другое"], ["1", "2"]]
    try:
        enrich.find_header(grid, FIELDS)
        raise AssertionError("ожидали ValueError")
    except ValueError as e:
        assert "Номер КД" in str(e)


# --- ядро: только заполненные кейсы, только их столбцы -------------------------
def test_only_filled_cases_and_columns():
    grid = make_grid([
        ["T-1", "Иванов", "100", None, None, None, None],
        ["T-2", "Петров", "200", None, None, None, None],
    ])
    rows = [
        srv_row("T-1", call_status="Оплачен", visit_date="2026-07-10"),
        srv_row("T-2"),                                   # бот ничего не заполнял
    ]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["filled_cases"] == 1
    assert plan["matched_cases"] == 1
    assert plan["cells_written"] == 2
    # обе записи — в строку T-1 (индекс 3), в колонки визита
    assert {(r, c) for r, c, *_ in plan["writes"]} == {(3, 4), (3, 3)}
    # дата пишется настоящей датой
    dates = [v for _r, c, v, is_d in plan["writes"] if is_d]
    assert dates == [date(2026, 7, 10)]


def test_never_writes_empty_or_foreign_columns():
    """Пустые значения бота и не-визитные колонки (клиент, долг) не пишутся никогда —
    даже если в БД они отличаются от файла (это защита от «старой выгрузки»)."""
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    rows = [srv_row("T-1", client="СТАРОЕ ИМЯ", debt_total="9999",
                    call_comment="взял трубку")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    cols = {c for _r, c, *_ in plan["writes"]}
    assert cols == {5}, "только комментарий звонка; клиент и долг — не наши колонки"


def test_equal_values_not_rewritten():
    grid = make_grid([
        ["T-1", "Иванов", "100", datetime(2026, 7, 10), "Оплачен", None, None],
    ])
    rows = [srv_row("T-1", visit_date="2026-07-10", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["cells_written"] == 0
    assert plan["cells_already"] == 2
    assert plan["changed_cases"] == 0


def test_overwrite_of_nonempty_is_counted():
    grid = make_grid([["T-1", "Иванов", "100", None, "В работе", None, None]])
    rows = [srv_row("T-1", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["cells_written"] == 1
    assert plan["cells_over_nonempty"] == 1


def test_missing_case_reported_not_silent():
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    rows = [srv_row("T-9", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["missing"] == ["T-9"]
    assert plan["cells_written"] == 0


def test_float_contract_id_matches():
    """Excel отдаёт номер КД как 12345.0 — матчится с «12345» сервера."""
    grid = make_grid([[12345.0, "Иванов", "100", None, None, None, None]])
    rows = [srv_row("12345", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["matched_cases"] == 1
    assert plan["cells_written"] == 1


def test_duplicate_rows_in_file_all_enriched():
    """Один КД дважды в файле (бывает после ручных правок) — пишем в обе строки."""
    grid = make_grid([
        ["T-1", "Иванов", "100", None, None, None, None],
        ["T-1", "Иванов", "100", None, None, None, None],
    ])
    rows = [srv_row("T-1", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert {(r, c) for r, c, *_ in plan["writes"]} == {(3, 4), (4, 4)}


def test_source_row_skipped_as_data():
    """Строка «сх/хард» под заголовками — не данные (и не кейс с cid='сх')."""
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    plan = enrich.build_plan(grid, FIELDS, [srv_row("T-1", call_status="Оплачен")])
    assert plan["data_start"] == 3


def test_absent_visit_column_is_reported():
    headers = HEADERS[:5]   # в файле нет колонок комментария и общего результата
    grid = [
        ["Инфо", None, None, "Выезд", None],
        headers,
        ["сх", "сх", "сх", "хард", "хард"],
        ["T-1", "Иванов", "100", None, None],
    ]
    rows = [srv_row("T-1", call_status="Оплачен", call_comment="взял трубку")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert "call_comment" in plan["visit_cols_absent"]
    # статус записали, комментарий — некуда, но кейс не потерян
    assert plan["cells_written"] == 1
    assert plan["matched_cases"] == 1


# --- вспомогательное ----------------------------------------------------------
def test_dates_equivalence():
    assert enrich._values_equal(datetime(2026, 7, 1), "2026-07-01", True)
    assert enrich._values_equal("01.07.2026", "2026-07-01", True)
    assert not enrich._values_equal("02.07.2026", "2026-07-01", True)


def test_cell_text():
    assert enrich.cell_text(12345.0) == "12345"
    assert enrich.cell_text(None) == ""
    assert enrich.cell_text("  x ") == "x"
    assert enrich.cell_text(datetime(2026, 7, 1)) == "2026-07-01"
