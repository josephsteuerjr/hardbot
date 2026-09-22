"""Исход обещания — свойство ДОЛЖНИКА, а не окна отчёта.

Поломка, ради которой написан файл, пережила починку «обещание принадлежит
должнику»: границу «чужой даты» стали брать по первому выезду В ОКНЕ ОТЧЁТА.
У книги за 30 дней окно длинное, и там всё сходилось. А у ВЕЧЕРНЕЙ книги
(digest_data.daily_report_xlsx, 22.09.2026) окно — ОДИН день: повторный выезд к
должнику оказывался в нём первым, вчерашнее сорванное обещание — «раньше первого
выезда», и срыв снова уезжал в «без даты». То есть ровно тот же дефект, только
теперь в единственной книге, которая каждый вечер уходит живым руководителям по
29 сотрудникам.

Замер на одном должнике (обещание на 2026-09-20 не оплачено, выезды 19.09 и
22.09, судим на 22.09):

    было   — книга за 30 дней «сорвано», BP 100 %;
             вечерняя книга за 22.09 «без даты», BP 0 %;
    стало  — обе книги «сорвано», BP 100 %.

Починка: граница берётся по первому выезду к должнику ЗА ВСЮ ИСТОРИЮ
(db.first_visit_days), а не по первому выезду окна. Защита от офисной даты при
этом цела и проверяется здесь же: обещание месячной давности у должника, к
которому впервые съездили вчера, по-прежнему «без даты».
"""
from datetime import timedelta

import db
import digest_data as dd
import funnel

MGR = 562021
EMP = "Фокнин В."
TODAY = dd.db._as_date(dd.today_iso())
T = TODAY.isoformat()
CARD = ("Выезд (миниапп). Статус: Обещает оплатить; Договорённость: "
        "Обещает оплатить клиент; Контакт: Должник; "
        "На адресе: По адресу проживает клиент; ")


def _day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def _visit(cid, day):
    with db.connect() as conn:
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, "
                     "payload) VALUES (?,?,?,?)", (cid, 1, day, CARD))
        conn.commit()


def _client(cid, name, debt, **cols):
    row = {"contract_id": cid, "client": name, "responsible": EMP,
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
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'FW-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'FW-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    db.reset_role_caches()


def _seed_broken_then_revisited():
    """Обещание дано на первом выезде и сорвано; позже съездили ещё раз."""
    _wipe()
    _client("FW-1", "Кл Срыв", "200 000", promise_date=_day(2),
            forecast_sum="200000")
    _visit("FW-1", _day(3))                  # первый выезд, на нём и обещание
    _visit("FW-1", _day(0))                  # повторный выезд, уже сегодня
    db.set_employee_city(EMP, "Худжанд")
    db.set_manager(MGR, "Рук", [], [EMP])
    db.reset_role_caches()


def _funnel(d_from, d_to):
    return dd._employee_stats(EMP, d_from, d_to)["funnel"]


def test_the_outcome_is_the_same_in_every_window():
    """Одно и то же обещание в один и тот же день не может читаться по-разному
    в книге за месяц и в книге за день: судим-то мы на «сегодня»."""
    _seed_broken_then_revisited()
    windows = {"день": (T, T), "неделя": (_day(6), T), "месяц": (_day(29), T)}
    outcomes = {name: tuple(_funnel(lo, hi)[k] for k in
                            ("kept", "broken", "pending", "nodate"))
                for name, (lo, hi) in windows.items()}
    assert set(outcomes.values()) == {(0, 1, 0, 0)}, outcomes


def test_the_evening_book_still_sees_the_broken_promise():
    """Та самая книга, которую отправляют вечером: «сорвано», а не «без даты»."""
    _seed_broken_then_revisited()
    c = _funnel(T, T)
    assert (c["ptp_persons"], c["broken"], c["nodate"]) == (1, 1, 0)
    assert funnel.rates(c)["bp"] == 100.0


def test_an_office_promise_is_still_not_judged():
    """Защита от чужой даты цела: обещание месячной давности у должника, к
    которому впервые съездили вчера, судить не по чему — «без даты». Иначе
    срывом становилась бы работа офиса по телефону."""
    _wipe()
    _client("FW-2", "Кл Офисный", "100 000", promise_date=_day(40),
            forecast_sum="100000")
    _visit("FW-2", _day(1))
    db.set_employee_city(EMP, "Худжанд")
    db.set_manager(MGR, "Рук", [], [EMP])
    db.reset_role_caches()
    for lo, hi in ((_day(1), _day(1)), (_day(60), T)):
        c = _funnel(lo, hi)
        assert (c["ptp_persons"], c["broken"], c["nodate"]) == (1, 0, 1)


def test_the_office_promise_survives_a_second_visit_too():
    """И при повторном выезде: оба выезда позже даты обещания — она чужая."""
    _wipe()
    _client("FW-3", "Кл Офисный", "100 000", promise_date=_day(40),
            forecast_sum="100000")
    _visit("FW-3", _day(3))
    _visit("FW-3", _day(1))
    db.set_employee_city(EMP, "Худжанд")
    db.set_manager(MGR, "Рук", [], [EMP])
    db.reset_role_caches()
    for lo, hi in ((_day(1), _day(1)), (_day(60), T)):
        c = _funnel(lo, hi)
        assert (c["broken"], c["nodate"]) == (0, 1)


def test_first_visit_days_ignores_the_report_window():
    """Источник границы — вся история выездов к договору, без окна отчёта."""
    _seed_broken_then_revisited()
    assert db.first_visit_days(EMP) == {"FW-1": _day(3)}
    assert db.first_visit_days("нет такого сотрудника") == {}
