"""Книги руководителя пишутся НАСТОЯЩИМИ типами ячеек, а не текстом экрана.

До 24.09.2026 книга собиралась из тех же строк, что показывает приложение:
дата выезда лежала строкой «2026-09-22», время на карточке — «4 мин»,
отметки — «11.09 14:32», ноль — пустой строкой. Такая колонка не сортируется
по времени, не складывается и не группируется в сводной, а книгу заказывали
«для самостоятельного анализа». Здесь проверяются сами ячейки собранной книги:
тип значения и формат, который видит Excel.
"""
import io
from datetime import date, datetime, timedelta

import pytest
from openpyxl import load_workbook

import book
import db
import digest_data as dd
import evidence

MGR = 563011
EMP = "Типкнижкин А."
EMP_IDLE = "Типкнижкин Б."
TODAY = date.today()
T = TODAY.isoformat()
Y = (TODAY - timedelta(days=1)).isoformat()
CARD = ("Выезд (миниапп). Статус: Обещает оплатить; Договорённость: "
        "Обещает оплатить клиент; Контакт: Должник; "
        "На адресе: По адресу проживает клиент; ")


def _client(cid, name, emp, debt, **cols):
    row = {"contract_id": cid, "client": name, "responsible": emp,
           "birth_date": "1990-01-01", "partner": "ЗК", "debt_today": debt,
           "overall_result": "Не обработан", "country": "Таджикистан"}
    row.update(cols)
    marks = ",".join("?" * len(row))
    with db.connect() as conn:
        conn.execute(f"INSERT INTO clients ({','.join(row)}) VALUES ({marks})",
                     list(row.values()))
        conn.commit()


def _visit(cid, day, **cols):
    row = {"contract_id": cid, "agent_id": 1, "created_at": day, "payload": CARD}
    row.update(cols)
    marks = ",".join("?" * len(row))
    with db.connect() as conn:
        conn.execute(f"INSERT INTO visits ({','.join(row)}) VALUES ({marks})",
                     list(row.values()))
        conn.commit()


def _wipe():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'BT-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'BT-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    db.reset_role_caches()


@pytest.fixture(scope="module")
def wb():
    _wipe()
    _client("BT-1", "Кл Типов", EMP, "150 000", promise_date=T,
            forecast_sum="50000", last_payment_date=Y, last_payment_sum="10 000")
    _visit("BT-1", Y, fill_seconds=300, saved_at=Y + "T18:40:00",
           received_at=T + "T09:05:00", visit_lat=38.5, visit_lon=68.7,
           evidence_level="confirmed",
           evidence=evidence.dumps({"level": "confirmed", "near_first": "14:02",
                                    "near_last": "14:19", "near_min": 17}))
    _client("BT-2", "Кл Спящий", EMP_IDLE, "70 000")
    db.set_manager(MGR, "Рук", [], [EMP, EMP_IDLE])
    db.reset_role_caches()
    try:
        blob = dd.stats_xlsx(db.get_manager(MGR), dd.stats_range("7d"),
                             employees=[EMP, EMP_IDLE])
        yield load_workbook(io.BytesIO(blob))
    finally:
        _wipe()


def _head(ws, at):
    return {c.value: c.column for c in ws[at] if c.value}


def _row(ws, at_head, first_col_value):
    for r in range(at_head + 1, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == first_col_value:
            return r
    raise AssertionError(f"нет строки «{first_col_value}» на листе {ws.title}")


def _seconds(cell) -> int:
    """Длительность ячейки в секундах. openpyxl по формату «[h]:mm:ss» сам
    отдаёт timedelta — это и есть признак, что Excel видит длительность."""
    assert isinstance(cell.value, timedelta), cell.value
    return round(cell.value.total_seconds())


def _cell(ws, at_head, label, column):
    return ws.cell(row=_row(ws, at_head, label), column=_head(ws, at_head)[column])


# --- состав книги --------------------------------------------------------------
def test_summary_sheet_opens_the_book_and_funnel_closes_it(wb):
    """«Сводка» — первой: на контуре компании вечерняя книга не рассылается, и
    руководитель берёт её сам кнопкой «Excel»."""
    assert wb.sheetnames[0] == "Сводка"
    assert {"Воронка", "Воронка — республики", "Воронка — итого", "Обещания",
            "Ранжирование"} <= set(wb.sheetnames)
    assert wb.sheetnames[-1] == "Ранжирование"


# --- даты, моменты, время ------------------------------------------------------
def test_dates_are_real_dates(wb):
    ws = wb["Выезды"]
    day = ws.cell(row=2, column=_head(ws, 1)["Дата"])
    assert isinstance(day.value, datetime) and day.value.date().isoformat() == Y
    assert day.number_format == book.DATE_FMT
    by_days = wb["По дням"]
    assert all(isinstance(by_days.cell(row=r, column=1).value, datetime)
               for r in range(2, by_days.max_row + 1))
    first = _cell(wb["Сводка"], 3, EMP, "Первый выезд")
    assert isinstance(first.value, datetime) and first.value.date().isoformat() == Y
    promises = wb["Обещания"]
    promise = promises.cell(row=4, column=_head(promises, 3)["Дата обещания"])
    assert isinstance(promise.value, datetime) and promise.value.date() == TODAY
    paid_on = promises.cell(row=4, column=_head(promises, 3)["Последний платёж"])
    assert isinstance(paid_on.value, datetime) and paid_on.value.date().isoformat() == Y


def test_stamps_are_moments_and_times_of_day(wb):
    ws = wb["Выезды"]
    head = _head(ws, 1)
    saved = ws.cell(row=2, column=head["Сохранён в приложении"])
    got = ws.cell(row=2, column=head["Принят сервером"])
    assert saved.value == datetime.fromisoformat(Y + "T18:40:00")
    assert got.value == datetime.fromisoformat(T + "T09:05:00")
    assert saved.number_format == book.DATETIME_FMT
    near = ws.cell(row=2, column=head["У адреса с"])
    # Время суток openpyxl отдаёт объектом time: в ячейке — доля суток.
    assert near.value.strftime("%H:%M") == "14:02" and near.number_format == book.TIME_FMT
    assert ws.cell(row=2, column=head["У адреса, мин"]).value == 17


def test_durations_are_numbers_with_a_duration_format(wb):
    visit = wb["Выезды"].cell(row=2, column=_head(wb["Выезды"], 1)["Заполнение карточки"])
    assert _seconds(visit) == 300
    assert visit.number_format == book.DURATION_FMT
    avg = _cell(wb["Сотрудники"], 4, EMP, "Заполнение карточки, среднее")
    assert _seconds(avg) == 300 and avg.number_format == book.DURATION_FMT
    near = _cell(wb["Сотрудники"], 4, EMP, "У адреса, среднее")
    assert _seconds(near) == 17 * 60


# --- деньги и доли -------------------------------------------------------------
def test_money_is_a_number_with_the_currency_in_the_format(wb):
    debt = _cell(wb["Сотрудники"], 4, EMP, "Долг")
    assert debt.value == 150_000 and debt.number_format == '#,##0" ₽"'
    assigned = _cell(wb["Сводка"], 3, EMP, "Долг закреплённый")
    assert assigned.value == 150_000 and "₽" in assigned.number_format
    visit_debt = wb["Выезды"].cell(row=2, column=_head(wb["Выезды"], 1)["Долг"])
    assert visit_debt.value == 150_000 and "₽" in visit_debt.number_format
    promises = wb["Обещания"]
    promised = promises.cell(row=4, column=_head(promises, 3)["Сумма обещания"])
    assert promised.value == 50_000 and "₽" in promised.number_format


def test_percent_is_an_unrounded_share(wb):
    plan = _cell(wb["Сотрудники"], 4, EMP, "План")
    done = _cell(wb["Сотрудники"], 4, EMP, "% плана")
    assert done.value == pytest.approx(1 / plan.value), "доля без округления"
    assert done.number_format == "0%"
    rate = wb["Воронка"].cell(row=4, column=_head(wb["Воронка"], 3)["Contact Rate, %"])
    assert isinstance(rate.value, (int, float)) and rate.number_format == "0.0%"


# --- ноль — это ноль -----------------------------------------------------------
def test_zero_counters_are_zeros_not_blanks(wb):
    """Пустая ячейка значит «нет данных», ноль — «было ноль»: раньше оба
    случая писались пустой строкой, и сводная их не различала."""
    assert _cell(wb["Сотрудники"], 4, EMP, "Без гео").value == 0
    assert _cell(wb["Сотрудники"], 4, EMP, "Внесено позже").value == 1
    idle = _row(wb["Сводка"], 3, EMP_IDLE)
    head = _head(wb["Сводка"], 3)
    assert wb["Сводка"].cell(row=idle, column=head["Выездов за период"]).value == 0
    assert wb["Сводка"].cell(row=idle, column=head["Закреплено должников"]).value == 1


def test_outcome_grid_hides_zeros_by_format_only(wb):
    """Сетка исходов — десятки колонок, заполнены две-три: ноль спрятан
    форматом, но ячейка остаётся числом, и SUM её видит."""
    cell = _cell(wb["Сотрудники"], 4, EMP, "Оплачен, чел.")
    assert cell.value == 0 and cell.number_format == book.HIDE_ZERO_FMT
    money = _cell(wb["Сотрудники"], 4, EMP, "Оплачен, сумма")
    assert money.value == 0 and money.number_format.endswith(";;@")
    got = _cell(wb["Сотрудники"], 4, EMP, "Обещает оплатить, чел.")
    assert got.value == 1


# --- писатель отдельно от данных -------------------------------------------------
def test_writer_keeps_text_as_text_and_survives_garbage():
    """Номер договора «007» не должен стать числом 7, «=СУММ» — формулой, а
    нечитаемая дата — пропасть молча."""
    bk = book.Book()
    ws = bk.sheet("Проба")
    ws.append(["007", "=СУММ(A1)", "вчера", None, 0, (1500, "сум"), "1 500 ₽ · 2 сум"],
              ["text", "text", "date", "date", "int", "money", "money"])
    got = load_workbook(io.BytesIO(bk.close()))["Проба"]
    assert [c.value for c in got[1]] == ["007", "=СУММ(A1)", "вчера", None, 0, 1500,
                                         "1 500 ₽ · 2 сум"]
    assert got.cell(row=1, column=6).number_format == '#,##0" сум"'
