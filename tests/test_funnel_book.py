"""Книга воронки: складывается, считает задание на рабочие дни и не мешает валюты.

Жалоба владельца 22.09.2026: «в экселе сейчас не делается воронка либо делается
криво». Разбор нашёл четыре поломки, каждая тихая — книга открывалась, цифры
стояли, и неправдой они становились только при сложении:

  * лист «Воронка» держал четыре уровня среза в одной таблице, и каждый выезд
    лежал в ней четырежды: SUM по «Выездов» давал 20 при пяти выездах;
  * задание считалось на КАЛЕНДАРНЫЕ дни окна, включая выходные и дни, которые
    ещё не наступили, — Penetration занижался вдвое;
  * лист «Обещания» печатал строку на каждый выезд, и должник, к которому
    съездили дважды, удваивал сумму обещаний;
  * «Ранжирование» складывало суммы разных валют в одну колонку «Обещано, ₽».

Здесь всё это проверяется ЯЧЕЙКАМИ собранной книги, а не кодом: книга каждый
вечер уходит живым руководителям, и «поправлено» без числа ничего не значит.
"""
import io
from datetime import date, timedelta

from openpyxl import load_workbook

import db
import digest_data as dd
import funnel

MGR = 562011
EMP = "Фбкнижкин В."
EMP2 = "Фбкнижкин Г."
TODAY = date.today()
T = TODAY.isoformat()
CARD = ("Выезд (миниапп). Статус: Обещает оплатить; Договорённость: "
        "Обещает оплатить клиент; Контакт: Должник; "
        "На адресе: По адресу проживает клиент; ")


def _day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def _visit(cid, day, payload=CARD):
    with db.connect() as conn:
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES (?,?,?,?)", (cid, 1, day, payload))
        conn.commit()


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


def _wipe():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'FB-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'FB-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    db.reset_role_caches()


def _seed():
    """Пять выездов к четырём должникам, два офиса, две республики, две валюты.

    К «Кл Два» ездили ДВАЖДЫ — на нём и ловится задвоение листа «Обещания».
    У «Кл Четыре» нет контрагента, а значит нет и единицы долга (debt.unit):
    его обещание нельзя складывать с рублёвыми, и это второй замер.
    """
    _wipe()
    _client("FB-1", "Кл Один", EMP, "100 000", promise_date=_day(2),
            last_payment_date=_day(1), last_payment_sum="100 000",
            forecast_sum="100000")
    _visit("FB-1", _day(3))
    _client("FB-2", "Кл Два", EMP, "200 000", promise_date=_day(2),
            forecast_sum="200000")
    _visit("FB-2", _day(3))
    _visit("FB-2", _day(1))                       # повторный выезд к тому же
    _client("FB-3", "Кл Три", EMP, "300 000",
            promise_date=(TODAY + timedelta(days=5)).isoformat(),
            forecast_sum="600000")
    _visit("FB-3", T)
    _client("FB-4", "Кл Четыре", EMP2, "9 000 000", country="Узбекистан",
            partner="", promise_date=_day(2), forecast_sum="9000000")
    _visit("FB-4", _day(2))
    db.set_employee_city(EMP, "Худжанд")
    db.set_employee_city(EMP2, "Ташкент")
    db.set_manager(MGR, "Рук", [], [EMP, EMP2])
    db.reset_role_caches()


def _mgr():
    return {"name": "Рук", "employees": [EMP, EMP2], "cities": [], "direction": ""}


def _book(rng=None):
    rng = rng or dd.stats_range("30d")
    return load_workbook(io.BytesIO(
        dd.stats_xlsx(_mgr(), rng, employees=[EMP, EMP2])))


def _head(ws, at=3):
    return [c.value for c in ws[at]]


def _rows(ws, at=3):
    head = _head(ws, at)
    return [dict(zip(head, [c.value for c in ws[r]]))
            for r in range(at + 1, ws.max_row + 1)]


def _sum(ws, title, at=3, upto=None):
    pos = _head(ws, at).index(title) + 1
    return sum(v for v in (ws.cell(row=r, column=pos).value
                           for r in range(at + 1, (upto or ws.max_row) + 1))
               if isinstance(v, (int, float)))


def _workdays_by_hand(rng) -> int:
    """Рабочие наступившие дни окна — счётом, а не функцией из digest_data:
    тест, который зовёт проверяемый код, проверяет только его самого."""
    lo, hi = db._as_date(rng["from"]), min(db._as_date(rng["to"]), TODAY)
    out, d = 0, lo
    while d <= hi:
        if d.weekday() < 5:
            out += 1
        d += timedelta(days=1)
    return out


FUNNEL_SHEETS = ("Воронка", "Воронка — республики", "Воронка — офисы",
                 "Воронка — итого")


# --- Лист «Воронка» складывается ---------------------------------------------

def test_every_funnel_sheet_sums_to_the_real_number_of_visits():
    """Уровень среза на лист. Раньше четыре уровня лежали одной таблицей, и
    сумма колонки «Выездов» была вчетверо больше правды."""
    _seed()
    try:
        wb = _book()
        assert set(FUNNEL_SHEETS) <= set(wb.sheetnames)
        real = dd.stats_data(_mgr(), dd.stats_range("30d"),
                             employees=[EMP, EMP2])["totals"]["funnel"]["visits"]
        assert real == 5
        for name in FUNNEL_SHEETS:
            ws = wb[name]
            assert _sum(ws, "Выездов") == real, name
            levels = {r["Срез"] for r in _rows(ws)}
            assert len(levels) == 1, f"{name}: на листе один уровень среза"
    finally:
        _wipe()


def test_the_task_counts_only_working_days_that_have_already_come():
    """Задание = личная норма × РАБОЧИЕ наступившие дни (решение владельца).

    Проверяем на пресете «Месяц»: у него окно — месяц целиком, то есть с
    выходными и с днями, которых ещё не было. Раньше знаменателем шли все
    календарные дни месяца, и задание оказывалось почти вдвое больше.
    """
    _seed()
    try:
        rng = dd.stats_range("month")
        work = _workdays_by_hand(rng)
        assert work < rng["days"], "в месяце есть выходные или будущие дни"
        ws = _book(rng)["Воронка"]
        rows = {r["Имя"]: r for r in _rows(ws)}
        norm = db.visit_plan_for(EMP)
        assert rows[EMP]["Рабочих дней"] == work
        assert rows[EMP]["Задание"] == norm * work
        # Penetration считается от него же — это и была цена ошибки.
        assert round(rows[EMP]["Penetration, %"] * 100, 1) == funnel.rate(
            rows[EMP]["Выездов"], norm * work)
        итого = _rows(_book(rng)["Воронка — итого"])[0]
        assert итого["Задание"] == (db.visit_plan_for(EMP)
                                    + db.visit_plan_for(EMP2)) * work
        assert итого["Рабочих дней"] == work
    finally:
        _wipe()


def test_office_rows_name_their_republic():
    """Пустая «Республика» у офисных строк выбрасывала их из фильтра по
    республике — руководитель направления переставал видеть свои офисы."""
    _seed()
    try:
        rows = {r["Имя"]: r for r in _rows(_book()["Воронка — офисы"])}
        assert rows["Худжанд"]["Республика"] == "Таджикистан"
        assert rows["Ташкент"]["Республика"] == "Узбекистан"
    finally:
        _wipe()


# --- Лист «Обещания»: строка на должника --------------------------------------

def test_promise_sheet_keeps_one_row_per_debtor():
    """Строка = должник, а не выезд (решение владельца 22.09.2026). У «Кл Два»
    два выезда, и раньше он давал две строки и двойную сумму обещания."""
    _seed()
    try:
        ws = _book()["Обещания"]
        rows = _rows(ws)
        assert len(rows) == 4, "четыре должника с обещанием клиента"
        assert sorted(r["Клиент"] for r in rows) == [
            "Кл Два", "Кл Один", "Кл Три", "Кл Четыре"]
        two = next(r for r in rows if r["Клиент"] == "Кл Два")
        assert two["Выездов с обещанием"] == 2, "оба выезда названы числом"
        # Дата — настоящей датой Excel (книга на xlsxwriter, 24.09.2026): по
        # строке «2026-09-23» фильтр «за неделю» и сортировка не работают.
        assert two["Дата выезда"].date().isoformat() == _day(1),             "результат даёт последний выезд"
        assert _sum(ws, "Сумма обещания") == 100_000 + 200_000 + 600_000 + 9_000_000
    finally:
        _wipe()


def test_ranging_never_adds_two_currencies_into_one_cell():
    """Сомони под знаком ₽ — это выдуманные деньги. Строки разложены по
    валютам, и сумма каждой колонки честна внутри своей валюты."""
    _seed()
    try:
        ws = _book()["Ранжирование"]
        rows = _rows(ws)
        units = {r["Валюта"] for r in rows}
        assert units == {"₽", dd.UNIT_UNKNOWN}
        totals = {r["Валюта"]: r for r in rows
                  if r["Группа по сумме обещания"] == "Итого"}
        assert totals["₽"]["Обещано"] == 100_000 + 200_000 + 600_000
        assert totals[dd.UNIT_UNKNOWN]["Обещано"] == 9_000_000
        # Ни одной ячейки, где валюты сложены: 9 900 000 — та самая цифра,
        # которую книга печатала под знаком ₽.
        for r in rows:
            for col in ("Обещано", "Сорвано обещаний", "Долг сорвавших"):
                assert r[col] != 9_900_000, col
        # Знак валюты — в формате ячейки, а не в подписи колонки.
        pos = _head(ws).index("Обещано") + 1
        formats = {ws.cell(row=4 + i, column=pos).number_format
                   for i in range(len(rows))}
        assert '#,##0" ₽"' in formats and "#,##0" in formats
        assert all("₽" not in (h or "") for h in _head(ws))
    finally:
        _wipe()


def test_the_book_says_out_loud_why_the_two_sheets_differ():
    """Расхождение «Ранжирования» и «Обещаний» названо в самой книге и
    числами: молча разные числа читаются как ошибка книги."""
    _seed()
    try:
        wb = _book()
        promises = wb["Обещания"]["A2"].value
        ranging = wb["Ранжирование"]["A2"].value
        for note in (promises, ranging):
            assert note and "зачёт KP/BP" in note
            assert "4" in note, "числа строк названы, а не описаны словами"
        assert "должник" in promises and "последнее обещание" in ranging
    finally:
        _wipe()


# --- Вечерняя книга ------------------------------------------------------------

def test_the_evening_book_carries_the_funnel_too():
    """На контуре компании Telegram выключен, и «⬇️ Excel» в приложении — не
    дорога: воронку просили в той книге, которую присылают вечером."""
    _seed()
    try:
        wb = load_workbook(io.BytesIO(dd.daily_report_xlsx(
            db.get_manager(MGR), T, employees=[EMP, EMP2])))
        assert wb.sheetnames[:2] == ["Сводка", "Исходы за день"]
        assert set(FUNNEL_SHEETS) | {"Обещания", "Ранжирование"} <= set(wb.sheetnames)
        # Окно вечерней книги — её день: сегодня выезд один (к «Кл Три»).
        assert _sum(wb["Воронка"], "Выездов") == 1
        assert _rows(wb["Обещания"])[0]["Клиент"] == "Кл Три"
    finally:
        _wipe()


# --- Соседние листы не тронуты -------------------------------------------------

def test_the_other_sheets_stay_exactly_as_they_were():
    """Листы, которых починка не касалась, обязаны остаться прежними — включая
    колонку «План», считающуюся по КАЛЕНДАРНЫМ дням окна: её же показывают
    плашки приложения, и разъехаться книге с экраном нельзя.

    Первым встала «Сводка» (24.09.2026): на контуре компании вечерняя книга
    не рассылается, и её таблица должна быть в книге, которую берут кнопкой.
    Порядок прежних листов за ней — прежний."""
    _seed()
    try:
        rng = dd.stats_range("30d")
        wb = _book(rng)
        assert wb.sheetnames[:6] == ["Сводка", "Сотрудники", "Республики", "Офисы",
                                     "Выезды", "По дням"]
        emp = wb["Сотрудники"]
        assert _head(emp, 4)[:8] == ["Сотрудник", "Страна", "Офис", "Выездов",
                                     "План", "% плана", "Должников", "Договоров"]
        plan = _head(emp, 4).index("План") + 1
        by_calendar = db.visit_plan_for(EMP) * rng["days"]
        assert emp.cell(row=5, column=plan).value == by_calendar
        assert wb["Республики"].cell(row=5, column=plan).value == by_calendar
        assert _head(wb["Выезды"], 1)[:6] == ["Дата", "Республика", "Офис",
                                              "Сотрудник", "Клиент", "Договоров"]
        assert wb["Выезды"].max_row == 1 + 5, "пять выездов пятью строками"
        assert _head(wb["По дням"], 1) == ["Дата", "Выездов", EMP, EMP2]
        assert wb["По дням"].max_row == 1 + rng["days"]
        daily = load_workbook(io.BytesIO(dd.daily_report_xlsx(
            db.get_manager(MGR), T, employees=[EMP, EMP2])))
        assert [t for _k, t, _w in dd.DAILY_REPORT_COLUMNS] == _head(daily["Сводка"])
        assert daily["Сводка"].max_row == 3 + 2 + 1      # два человека и «Итого»
        assert _head(daily["Исходы за день"], 1) == ["Сотрудник", "Исход",
                                                     "Должников"]
    finally:
        _wipe()
