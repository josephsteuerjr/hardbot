"""Книга статистики: исходы по столбцам, блоки успешных и неуспешных выездов.

Просьба офиса 11.09.2026: «эти статусы хотели разбить по столбцам с количеством
и суммой денег; сверху блоки — успешный выезд и не успешный». Раньше исходы
ехали одной ячейкой текста («Передана инф 3 лицу 8 · Нет контакта 3»): её
нельзя ни сложить, ни отсортировать, ни сравнить два дня между собой.
"""
import io
from datetime import date, timedelta

import pytest
from openpyxl import load_workbook

import db
import digest_data as dd

MGR = 561001
EMP_A, EMP_B = "Кнтаджиков К.", "Кнузбеков К."
TODAY = date.today()
T = TODAY.isoformat()


def _visit(cid, day, status, agreed=""):
    with db.connect() as conn:
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES (?,?,?,?)",
                     (cid, 1, day,
                      f"Выезд (миниапп). Статус: {status}; Договорённость: {agreed}; "))
        conn.commit()


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'XL-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'XL-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    rows = [
        # КД, клиент, сотрудник, долг, исход
        ("XL-1", "Клиент Раз", EMP_A, "100 000", "Оплачен"),
        ("XL-2", "Клиент Два", EMP_A, "200 000", "Обещает оплатить"),
        ("XL-3", "Клиент Три", EMP_A, "300 000", "Нет контакта"),
        ("XL-4", "Клиент Четыре", EMP_B, "400 000", "Передана инф 3 лицу"),
        ("XL-5", "Клиент Пять", EMP_B, "500 000", "Тюрьма"),
    ]
    with db.connect() as conn:
        for cid, client, emp, money, _ in rows:
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "partner, debt_today, overall_result, country) VALUES (?,?,?,?,?,?,?,?)",
                (cid, client, emp, f"199{cid[-1]}-01-01", "ЗК", money,
                 "Не обработан", "Таджикистан"))
        conn.commit()
    for cid, _client, _emp, _money, status in rows:
        _visit(cid, T, status)
        db.record_bot_visit(cid, {"call_status": status}, T)
    db.set_manager(MGR, "Рук", [], [EMP_A, EMP_B])
    db.reset_role_caches()


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'XL-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'XL-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    db.reset_role_caches()


@pytest.fixture(scope="module")
def sheet():
    _seed()
    blob = dd.stats_xlsx(db.get_manager(MGR), dd.stats_range("today", 0, today=TODAY),
                         employees=[EMP_A, EMP_B])
    wb = load_workbook(io.BytesIO(blob))
    return wb, wb["Сотрудники"]


def _head(ws):
    return {str(c.value): c.column for c in ws[4] if c.value}


def _row_of(ws, employee):
    for r in range(5, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == employee:
            return r
    raise AssertionError(f"нет строки {employee}")


# --- группировка исходов -----------------------------------------------------
def test_office_split_of_outcomes():
    assert dd.outcome_group("Оплачен") == "success"
    assert dd.outcome_group("Отказ от оплаты") == "success", "отказ — тоже результат"
    assert dd.outcome_group("Рассрочка") == "success"
    assert dd.outcome_group("Нет контакта") == "fail"
    assert dd.outcome_group("Не обработан") == "fail", "выезда не было"
    assert dd.outcome_group("Тюрьма") == "other", "не наше решение, но и не потеря"
    assert dd.outcome_group("") == "fail"


# --- шапка -------------------------------------------------------------------
def test_blocks_sit_above_their_columns(sheet):
    _wb, ws = sheet
    titles = [c.value for c in ws[3] if c.value]
    assert titles == ["Успешный выезд", "Не успешный выезд", "Прочие исходы"]
    head = _head(ws)
    first_success = head["Успешный выезд: всего, чел."]
    assert ws.cell(row=3, column=first_success).value == "Успешный выезд"


def test_every_office_status_has_a_column_even_when_unused(sheet):
    """Колонка, то появляющаяся, то исчезающая, ломает сравнение двух выгрузок."""
    head = _head(sheet[1])
    for status in dd.OUTCOME_SUCCESS + dd.OUTCOME_FAIL:
        assert f"{status}, чел." in head, status
        assert f"{status}, сумма" in head, status


def test_unknown_status_lands_in_others(sheet):
    head = _head(sheet[1])
    assert "Тюрьма, чел." in head, "редкий статус потерялся бы из отчёта"
    assert head["Тюрьма, чел."] > head["Нет контакта, чел."], "не в блоке неуспешных"


# --- цифры -------------------------------------------------------------------
def test_counts_and_money_stand_in_their_own_cells(sheet):
    _wb, ws = sheet
    head = _head(ws)
    row = _row_of(ws, EMP_A)
    assert ws.cell(row=row, column=head["Оплачен, чел."]).value == 1
    assert "100 000" in str(ws.cell(row=row, column=head["Оплачен, сумма"]).value)
    assert ws.cell(row=row, column=head["Обещает оплатить, чел."]).value == 1
    assert ws.cell(row=row, column=head["Нет контакта, чел."]).value == 1
    assert "300 000" in str(ws.cell(row=row, column=head["Нет контакта, сумма"]).value)


def test_block_totals_sum_their_statuses(sheet):
    _wb, ws = sheet
    head = _head(ws)
    row = _row_of(ws, EMP_A)
    assert ws.cell(row=row, column=head["Успешный выезд: всего, чел."]).value == 2
    assert ws.cell(row=row, column=head["Не успешный выезд: всего, чел."]).value == 1
    money = str(ws.cell(row=row, column=head["Успешный выезд: всего, сумма"]).value)
    assert "300 000" in money, "100 000 + 200 000 по успешным"


def test_outcome_columns_add_up_to_debtors(sheet):
    """Сумма по исходам обязана сойтись с «Должников»: иначе кто-то потерян."""
    _wb, ws = sheet
    head = _head(ws)
    for emp in (EMP_A, EMP_B):
        row = _row_of(ws, emp)
        got = sum(ws.cell(row=row, column=head[f"{block}: всего, чел."]).value or 0
                  for block in ("Успешный выезд", "Не успешный выезд", "Прочие исходы"))
        assert got == ws.cell(row=row, column=head["Должников"]).value, emp


def test_total_line_keeps_the_same_split(sheet):
    _wb, ws = sheet
    head = _head(ws)
    last = ws.max_row
    assert ws.cell(row=last, column=1).value == "Итого"
    assert ws.cell(row=last, column=head["Успешный выезд: всего, чел."]).value == 3
    assert ws.cell(row=last, column=head["Не успешный выезд: всего, чел."]).value == 1
    assert ws.cell(row=last, column=head["Прочие исходы: всего, чел."]).value == 1
    assert "Тюрьма 1" in str(ws.cell(row=last, column=head["Какие исходы"]).value)


# --- маркер на листе выездов -------------------------------------------------
def test_each_visit_is_marked_successful_or_not(sheet):
    wb, _ws = sheet
    ws2 = wb["Выезды"]
    head = [c.value for c in ws2[1]]
    assert head[5] == "Выезд" and head[1] == "Республика"
    marks = {}
    for r in range(2, ws2.max_row + 1):
        marks[ws2.cell(row=r, column=7).value] = ws2.cell(row=r, column=6).value
    assert marks["Оплачен"] == "успешный"
    assert marks["Нет контакта"] == "не успешный"
    assert marks["Тюрьма"] == "прочее"


# --- разделение по республикам ------------------------------------------------
def test_countries_sheet_repeats_the_same_split(sheet):
    """Направленцу нужен свод по своей республике, а не по всему СНГ."""
    wb, _ws = sheet
    ws = wb["Республики"]
    head = {str(c.value): c.column for c in ws[4] if c.value}
    assert ws.cell(row=4, column=1).value == "Республика"
    labels = [ws.cell(row=r, column=1).value for r in range(5, ws.max_row + 1)]
    assert labels == ["Таджикистан", "Итого"], "обе строки: страна и общий итог"
    row = 5
    assert ws.cell(row=row, column=head["Выездов"]).value == 5
    assert ws.cell(row=row, column=head["Успешный выезд: всего, чел."]).value == 3
    assert ws.cell(row=row, column=head["Не успешный выезд: всего, чел."]).value == 1
    assert ws.cell(row=row, column=head["Прочие исходы: всего, чел."]).value == 1


def test_country_rows_add_up_to_the_total(sheet):
    wb, _ws = sheet
    ws = wb["Республики"]
    head = {str(c.value): c.column for c in ws[4] if c.value}
    last = ws.max_row
    for name in ("Выездов", "Должников", "Успешный выезд: всего, чел."):
        by_country = sum(ws.cell(row=r, column=head[name]).value or 0
                         for r in range(5, last))
        assert by_country == ws.cell(row=last, column=head[name]).value, name


def test_plan_travels_into_the_book(sheet):
    """«План 10 на сотрудника» — та же цифра, что в приложении."""
    _wb, ws = sheet
    head = {str(c.value): c.column for c in ws[4] if c.value}
    last = ws.max_row
    assert ws.cell(row=last, column=head["План"]).value == 20, "двое за один день"
    assert ws.cell(row=last, column=head["% плана"]).value == "25%", "5 выездов из 20"


def test_employee_sheet_can_be_filtered_by_country(sheet):
    """Фильтр стоит на шапке и НЕ захватывает строку «Итого»."""
    _wb, ws = sheet
    assert ws.auto_filter.ref is not None
    first, last = ws.auto_filter.ref.split(":")
    assert first.endswith("4"), "фильтр начинается со строки заголовков"
    assert int("".join(ch for ch in last if ch.isdigit())) == ws.max_row - 1
