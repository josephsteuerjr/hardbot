"""Воронка Collection: Penetration → Contact Rate → RPC → PTP → KP/BP.

Просьба Альберта 15.09.2026: пять показателей одной лестницей, «полная
конверсия от выезда до транзакции», плюс ранжирование исполнения обещаний по
размеру обещанной суммы.

Главное свойство, которое здесь защищается, — ЕДИНИЦЫ. Первые ступени считают
выезды, исполнение обещаний считает людей, и сумма «исполнено + сорвано + ждём
срока + без даты» обязана сойтись с колонкой «Клиентов с обещанием». Показатель,
у которого знаменатель нельзя проверить сложением, руководитель считать не
станет.
"""
import io
from datetime import date, timedelta

from openpyxl import load_workbook

import db
import digest_data as dd
import funnel

MGR = 562001
EMP = "Фнворонкин В."
EMP2 = "Фнворонкин Г."
TODAY = date.today()
T = TODAY.isoformat()


def _day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def _payload(status, agreed="", contact=None, at_address="", comment=""):
    """Строка журнала выезда. contact=None — выезд старше 15.09.2026: полей
    контакта в записи ещё нет, и воронка должна уметь с этим жить."""
    head = f"Выезд (миниапп). Статус: {status}; Договорённость: {agreed}; "
    if contact is None:
        return head + comment
    return head + f"Контакт: {contact}; На адресе: {at_address}; " + comment


def _visit(cid, day, payload):
    with db.connect() as conn:
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES (?,?,?,?)", (cid, 1, day, payload))
        conn.commit()


def _client(cid, name, emp, debt, **cols):
    keys = ("contract_id", "client", "responsible", "birth_date", "partner",
            "debt_today", "overall_result", "country")
    vals = [cid, name, emp, "1990-01-01", "ЗК", debt, "Не обработан", "Таджикистан"]
    for k, v in cols.items():
        keys, vals = keys + (k,), vals + [v]
    marks = ",".join("?" * len(keys))
    with db.connect() as conn:
        conn.execute(f"INSERT INTO clients ({','.join(keys)}) VALUES ({marks})", vals)
        conn.commit()


def _wipe():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'FN-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'FN-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    db.reset_role_caches()


def _seed():
    """Семь выездов одного сотрудника — по одному на каждую ветку воронки."""
    _wipe()
    # 1. Должник, обещал, заплатил в срок → KP
    _client("FN-1", "Кл Один", EMP, "100 000",
            promise_date=_day(2), last_payment_date=_day(1),
            last_payment_sum="100 000", forecast_sum="100000")
    _visit("FN-1", _day(3), _payload("Обещает оплатить", "Обещает оплатить клиент",
                                     "Должник", "По адресу проживает клиент"))
    # 2. Должник, обещал, не заплатил → BP, просрочка 2 дня
    _client("FN-2", "Кл Два", EMP, "200 000",
            promise_date=_day(2), forecast_sum="200000")
    _visit("FN-2", _day(3), _payload("Обещает оплатить", "Обещает оплатить клиент",
                                     "Должник", "По адресу проживает клиент"))
    # 3. Должник и 3 лицо, обещание на будущее → ждём срока
    _client("FN-3", "Кл Три", EMP, "300 000",
            promise_date=(TODAY + timedelta(days=5)).isoformat(),
            forecast_sum="600000")
    _visit("FN-3", T, _payload("Обещает оплатить", "Обещает оплатить клиент и 3-е лицо",
                               "Должник и 3 лицо", "По адресу проживает клиент"))
    # 4. Должник, договориться не удалось → RPC без PTP
    _client("FN-4", "Кл Четыре", EMP, "400 000")
    _visit("FN-4", T, _payload("Отказ от оплаты", "На оплату договориться не удалось",
                               "Должник", "По адресу проживает клиент"))
    # 5. Только 3 лицо, родственники на адресе → контакт есть, RPC нет
    _client("FN-5", "Кл Пять", EMP, "500 000")
    _visit("FN-5", T, _payload("Передана инф 3 лицу", "Обещает оплатить 3-е лицо",
                               "3 лицо", "По адресу проживают родственники клиента"))
    # 6. Мёртвый адрес
    _client("FN-6", "Кл Шесть", EMP, "600 000")
    _visit("FN-6", T, _payload("Нет контакта", "",
                               "Контакт не установлен/ должника не знают",
                               "По адресу клиента не знают/ не общаются"))
    # 7. Старый выезд без полей контакта в записи журнала
    _client("FN-7", "Кл Семь", EMP, "700 000")
    _visit("FN-7", T, _payload("В работе", ""))
    db.set_manager(MGR, "Рук", [], [EMP])
    db.reset_role_caches()


def _counts(emp=EMP, days=30):
    return dd._employee_stats(emp, _day(days), T)["funnel"]


# --- Чистые правила (funnel.py, без базы) ------------------------------------

def test_contact_and_promise_classes_match_the_registry():
    assert funnel.contact_kind("Должник") == "client"
    assert funnel.contact_kind("Должник и 3 лицо") == "client"
    assert funnel.contact_kind("3 лицо") == "third"
    assert funnel.contact_kind("Контакт не установлен/ должника не знают") == "none"
    assert funnel.contact_kind("") == ""
    assert funnel.promise_kind("Обещает оплатить клиент") == "client"
    assert funnel.promise_kind("Обещает оплатить клиент и 3-е лицо") == "client"
    assert funnel.promise_kind("Обещает оплатить 3-е лицо") == "third"
    assert funnel.promise_kind("На оплату договориться не удалось") == "none"
    assert funnel.promise_kind("3-е лицо передаст информацию клиенту") == "none"
    assert funnel.address_kind("По адресу проживают родственники клиента") == "relatives"


def test_promise_state_repeats_the_rule_of_overdue_promises():
    on = date(2026, 9, 15)
    visit = date(2026, 9, 1)
    made = date(2026, 9, 10)
    # платёж не раньше даты обещания = сдержал (db.overdue_promises)
    assert funnel.promise_state(made, paid=False, last_payment_day=made, on=on,
                                visit_day=visit) == funnel.PROMISE_KEPT
    assert funnel.promise_state(made, paid=True, last_payment_day=None, on=on,
                                visit_day=visit) == funnel.PROMISE_KEPT
    assert funnel.promise_state(made, paid=False, last_payment_day=date(2026, 9, 2),
                                on=on, visit_day=visit) == funnel.PROMISE_BROKEN
    # срок ещё не наступил — это не срыв
    assert funnel.promise_state(date(2026, 9, 20), paid=False, last_payment_day=None,
                                on=on, visit_day=visit) == funnel.PROMISE_PENDING
    # срок «сегодня» тоже не срыв: день не кончился. Та же граница, что у
    # db.overdue_promises, иначе книга и «Просрочено обещаний» спорят каждое утро.
    assert funnel.promise_state(on, paid=False, last_payment_day=None,
                                on=on, visit_day=visit) == funnel.PROMISE_PENDING
    assert funnel.promise_state(date(2026, 9, 14), paid=False, last_payment_day=None,
                                on=on, visit_day=visit) == funnel.PROMISE_BROKEN
    # обещание старше самого выезда — чужое (его ставил офис по телефону)
    assert funnel.promise_state(date(2026, 8, 20), paid=False, last_payment_day=None,
                                on=on, visit_day=visit) == funnel.PROMISE_NODATE
    assert funnel.promise_state(None, paid=False, last_payment_day=None,
                                on=on) == funnel.PROMISE_NODATE


def test_rate_tells_zero_from_no_denominator():
    # 0 % — «делали и не вышло», прочерк — «не делали». Смешивать нельзя:
    # средний Contact Rate по команде занижался бы теми, кто не выезжал.
    assert funnel.rate(0, 5) == 0.0
    assert funnel.rate(3, 0) is None
    assert funnel.rate(1, 3) == 33.3


def test_amount_ranging_keeps_its_order_and_does_not_invent_zero():
    assert funnel.amount_bucket(None) == funnel.AMOUNT_UNKNOWN
    assert funnel.amount_bucket(0) == funnel.AMOUNT_UNKNOWN
    assert funnel.amount_bucket(49_999) == "до 50 тыс"
    assert funnel.amount_bucket(50_000) == "50–150 тыс"
    assert funnel.amount_bucket(5_000_000) == "1 млн и выше"
    assert funnel.AMOUNT_ORDER[0] == "до 50 тыс"
    assert funnel.AMOUNT_ORDER[-1] == funnel.AMOUNT_UNKNOWN
    assert funnel.overdue_bucket(0) == "в срок"
    assert funnel.overdue_bucket(8) == "8–30 дней"
    assert funnel.overdue_bucket(200) == "свыше 90 дней"


# --- Счётчики по журналу выездов ---------------------------------------------

def test_every_step_of_the_ladder_counts_its_own_visits():
    _seed()
    c = _counts()
    assert c["visits"] == 7
    assert c["carded"] == 6                 # седьмой выезд без анкеты контакта
    assert c["contacts"] == 5
    assert c["dead"] == 1
    assert c["rpc"] == 4                    # «Должник» и «Должник и 3 лицо»
    assert c["third"] == 1
    assert c["relatives"] == 1
    assert c["ptp"] == 3
    assert c["promise_third"] == 1
    assert (c["kept"], c["broken"], c["pending"], c["nodate"]) == (1, 1, 1, 0)


def test_promise_outcomes_add_up_to_the_clients_who_promised():
    _seed()
    c = _counts()
    assert c["kept"] + c["broken"] + c["pending"] + c["nodate"] == c["ptp_persons"]


def test_rates_are_the_ladder_albert_described():
    _seed()
    r = funnel.rates(_counts(), plan=10)
    assert r["penetration"] == 70.0         # 7 выездов из задания в 10
    assert r["contact_rate"] == 83.3        # 5 контактов из 6 заполненных анкет
    assert r["rpc"] == 80.0                 # 4 контакта с клиентом из 5
    assert r["ptp"] == 75.0                 # 3 обещания из 4 контактов с клиентом
    assert r["kp"] == 33.3 and r["bp"] == 33.3


def test_an_unfilled_card_does_not_improve_contact_rate():
    """Выезд без анкеты не попадает НИ в числитель, ни в знаменатель.

    Считать его контактом значило бы выдумать разговор, а считать мёртвым
    адресом — наказать выездника за то, что поле появилось позже его выезда.
    """
    _seed()
    _client("FN-8", "Кл Восемь", EMP, "800 000")
    _visit("FN-8", T, _payload("В работе", ""))
    c = _counts()
    assert c["visits"] == 8 and c["carded"] == 6 and c["contacts"] == 5


def test_the_card_snapshot_covers_a_visit_that_predates_the_journal_fields():
    """У выезда без полей в записи анкету берём из снимка — но только если
    снимок описывает ИМЕННО его. Снимок один на человека, и приписать его
    позапрошлому выезду значит сочинить контакт."""
    _wipe()
    _client("FN-9", "Кл Девять", EMP, "900 000")
    _visit("FN-9", _day(4), _payload("Нет контакта", ""))     # старый выезд
    _visit("FN-9", T, _payload("Обещает оплатить", ""))       # выезд дня снимка
    db.record_bot_visit("FN-9", {"call_status": "Обещает оплатить",
                                 "contact_with": "Должник",
                                 "agreed_payment": "Обещает оплатить клиент",
                                 "who_at_address": "По адресу проживает клиент"}, T)
    db.set_manager(MGR, "Рук", [], [EMP])
    db.reset_role_caches()
    c = _counts()
    assert c["visits"] == 2
    assert c["carded"] == 1                 # анкету получил только выезд снимка
    assert c["rpc"] == 1


def test_one_debtor_promising_twice_is_one_promise_in_kp():
    """Повторное обещание того же должника платят один раз — и в зачёт KP/BP
    оно идёт один раз, иначе показатель завышался бы вдвое."""
    _wipe()
    _client("FN-10", "Кл Десять", EMP, "100 000", promise_date=_day(1))
    card = _payload("Обещает оплатить", "Обещает оплатить клиент",
                    "Должник", "По адресу проживает клиент")
    _visit("FN-10", _day(5), card)
    _visit("FN-10", _day(3), card)
    db.set_manager(MGR, "Рук", [], [EMP])
    db.reset_role_caches()
    c = _counts()
    assert c["ptp"] == 2                    # обещаний взято два
    assert c["ptp_persons"] == 1            # клиент один
    assert c["broken"] == 1


def test_a_registry_promise_older_than_the_visit_is_not_judged():
    """Колонку «дата обещания» заполняет и офис по телефону. Обещание месячной
    давности не должно записываться в срыв тому, кто съездил вчера."""
    _wipe()
    _client("FN-11", "Кл Одиннадцать", EMP, "100 000", promise_date=_day(40))
    _visit("FN-11", _day(1), _payload("Обещает оплатить", "Обещает оплатить клиент",
                                      "Должник", "По адресу проживает клиент"))
    db.set_manager(MGR, "Рук", [], [EMP])
    db.reset_role_caches()
    c = _counts()
    assert c["ptp"] == 1 and c["nodate"] == 1
    assert c["broken"] == 0


# --- Своды и книга ------------------------------------------------------------

def test_team_total_sums_counters_not_percentages():
    _seed()
    _client("FN-20", "Кл Двадцать", EMP2, "100 000")
    _visit("FN-20", T, _payload("Нет контакта", "",
                                "Контакт не установлен/ должника не знают",
                                "По адресу клиента не знают/ не общаются"))
    db.set_manager(MGR, "Рук", [], [EMP, EMP2])
    db.reset_role_caches()
    rng = dd.stats_range("30d")
    data = dd.stats_data({"name": "Рук", "employees": [EMP, EMP2], "cities": [],
                          "direction": ""}, rng, employees=[EMP, EMP2])
    total = data["totals"]["funnel"]
    assert total["visits"] == 8 and total["carded"] == 7
    assert total["contacts"] == 5 and total["dead"] == 2
    # Доля команды — сумма числителей на сумму знаменателей, а не среднее долей.
    assert funnel.rates(total)["contact_rate"] == funnel.rate(5, 7)


def _book(employees):
    rng = dd.stats_range("30d")
    mgr = {"name": "Рук", "employees": employees, "cities": [], "direction": ""}
    return load_workbook(io.BytesIO(dd.stats_xlsx(mgr, rng, employees=employees)))


def test_book_carries_the_funnel_sheets():
    _seed()
    wb = _book([EMP])
    assert {"Воронка", "Обещания", "Ранжирование"} <= set(wb.sheetnames)
    ws = wb["Воронка"]
    head = [c.value for c in ws[3]]
    assert head[:2] == ["Срез", "Имя"]
    for name in ("Penetration, %", "Contact Rate, %", "RPC, %", "PTP, %",
                 "KP, %", "BP, %"):
        assert name in head
    levels = [ws.cell(row=r, column=1).value for r in range(4, ws.max_row + 1)]
    assert "Сотрудник" in levels and levels[-1] == "Итого"


def test_percent_cells_are_real_numbers_so_the_book_can_be_pivoted():
    """Процент лежит долей с форматом «0.0%», а не текстом «83,3%»: книгу
    заказывали для самостоятельного анализа, а по тексту не строится сводная."""
    _seed()
    ws = _book([EMP])["Воронка"]
    head = [c.value for c in ws[3]]
    col = head.index("Contact Rate, %") + 1
    cell = ws.cell(row=4, column=col)
    assert isinstance(cell.value, float)
    assert round(cell.value * 100, 1) == 83.3
    assert cell.number_format == "0.0%"


def test_promise_register_lists_every_promise_and_marks_the_counted_one():
    _seed()
    ws = load_workbook(io.BytesIO(dd.stats_xlsx(
        {"name": "Рук", "employees": [EMP], "cities": [], "direction": ""},
        dd.stats_range("30d"), employees=[EMP])))["Обещания"]
    head = [c.value for c in ws[3]]
    rows = [dict(zip(head, [c.value for c in ws[r]]))
            for r in range(4, ws.max_row + 1)]
    assert len(rows) == 3                            # три обещания клиента
    states = sorted(r["Исход обещания"] for r in rows)
    assert states == ["ждём срока", "исполнено", "сорвано"]
    broken = next(r for r in rows if r["Исход обещания"] == "сорвано")
    assert broken["Дней просрочки"] == 2
    assert broken["Группа по сумме"] == "150–500 тыс"
    assert all(r["В зачёт KP/BP"] == "да" for r in rows)


def test_ranging_splits_kept_and_broken_by_the_size_of_the_promise():
    _seed()
    ws = load_workbook(io.BytesIO(dd.stats_xlsx(
        {"name": "Рук", "employees": [EMP], "cities": [], "direction": ""},
        dd.stats_range("30d"), employees=[EMP])))["Ранжирование"]
    head = [c.value for c in ws[3]]
    rows = {ws.cell(row=r, column=1).value:
            dict(zip(head, [c.value for c in ws[r]]))
            for r in range(4, ws.max_row + 1)}
    # Корзины стоят все и в своём порядке, даже пустые: исчезающая строка
    # ломает сравнение двух выгрузок.
    assert list(rows)[:-1] == list(funnel.AMOUNT_ORDER)
    assert rows["50–150 тыс"]["Исполнено (KP)"] == 1
    assert rows["150–500 тыс"]["Сорвано (BP)"] == 1
    assert rows["150–500 тыс"]["Средняя просрочка, дн"] == 2
    assert rows["500 тыс – 1 млн"]["Ждём срока"] == 1
    assert rows["Итого"]["Клиентов с обещанием"] == 3


def test_every_sheet_row_is_as_wide_as_its_header():
    """Шапка и строка расходятся молча: колонки просто съезжают на одну, и
    «Сорвано» читается как «Исполнено». Ширину держим тестом."""
    _seed()
    assert len(dd._funnel_line("Срез", "Имя", "", "",
                               funnel.empty_counts(), 10)) == len(dd.FUNNEL_COLUMNS)
    assert len(dd._range_line("Итого", [])) == len(dd.RANGE_COLUMNS)
    wb = _book([EMP])
    for sheet, columns in (("Воронка", dd.FUNNEL_COLUMNS),
                           ("Обещания", dd.PROMISE_COLUMNS),
                           ("Ранжирование", dd.RANGE_COLUMNS)):
        ws = wb[sheet]
        assert [c[0] for c in columns] == [c.value for c in ws[3]]
        for r in range(4, ws.max_row + 1):
            assert len(ws[r]) == len(columns), f"{sheet}, строка {r}"
