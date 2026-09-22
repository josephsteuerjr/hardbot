"""Обещание принадлежит ДОЛЖНИКУ, а не выезду (решение владельца 22.09.2026).

Поломка, ради которой написан файл: к должнику съездили второй раз — и сорванное
обещание погасло. `funnel.promise_state` сравнивала дату обещания с днём КАЖДОГО
выезда, а зачёт KP/BP шёл по ПОСЛЕДНЕМУ выезду человека; повторная поездка
оказывалась позже даты обещания, и срыв уезжал в «без даты». Показатель BP
улучшался ровно оттого, что с должником продолжали работать.

Замер на сиде tests/test_funnel.py («Кл Два» обещал на _day(2), выезд _day(3)):

    было   — без повторного выезда broken 1, nodate 0, BP 33.3 %;
             с повторным выездом   broken 0, nodate 1, BP 0.0 %;
    стало  — в обоих случаях broken 1, nodate 0, BP 33.3 %.
"""
from datetime import date

import pytest

import funnel
import test_funnel as tf


# --- Чистое правило (без базы) -----------------------------------------------

def test_a_promise_between_two_visits_belongs_to_the_debtor():
    """Повторный выезд не гасит срыв: граница «чужой даты» — ПЕРВЫЙ выезд."""
    first, second = date(2026, 9, 1), date(2026, 9, 20)
    made = date(2026, 9, 10)                 # обещание дал на первом выезде
    on = date(2026, 9, 22)
    assert funnel.promise_state(made, paid=False, last_payment_day=None, on=on,
                                first_visit_day=first) == funnel.PROMISE_BROKEN
    # Именно так и гасло: сравнение со ВТОРЫМ выездом делало обещание чужим.
    assert funnel.promise_state(made, paid=False, last_payment_day=None, on=on,
                                first_visit_day=second) == funnel.PROMISE_NODATE


def test_first_visit_day_is_the_earliest_visit_to_the_debtor():
    days = [date(2026, 9, 20), date(2026, 9, 1), date(2026, 9, 10)]
    assert funnel.first_visit_day(days) == date(2026, 9, 1)
    assert funnel.first_visit_day([None, date(2026, 9, 5)]) == date(2026, 9, 5)
    assert funnel.first_visit_day([]) is None
    assert funnel.first_visit_day(None) is None


def test_the_old_per_visit_call_fails_loudly():
    """Молчаливая поломка тут дороже падения: книга уходит руководителям."""
    with pytest.raises(TypeError, match="visit_day"):
        funnel.promise_state(date(2026, 9, 10), paid=False, last_payment_day=None,
                             on=date(2026, 9, 22), visit_day=date(2026, 9, 20))


# --- Тот же замер по журналу выездов -----------------------------------------

def _repeat_visit_to_the_broken_promise():
    """Сид tests/test_funnel.py плюс повторный выезд к «Кл Два» — тому самому
    должнику, чьё обещание (_day(2)) сорвано. Выезд ставим вчера, то есть ПОЗЖЕ
    даты обещания: ровно тот случай, на котором показатель и гас."""
    tf._seed()
    tf._visit("FN-2", tf._day(1), tf._payload(
        "Обещает оплатить", "Обещает оплатить клиент",
        "Должник", "По адресу проживает клиент"))


def test_a_second_visit_does_not_extinguish_a_broken_promise():
    _repeat_visit_to_the_broken_promise()
    c = tf._counts()
    assert c["ptp"] == 4                     # обещаний взято четыре (выезды)
    assert c["ptp_persons"] == 3             # должников с обещанием трое
    assert (c["kept"], c["broken"], c["pending"], c["nodate"]) == (1, 1, 1, 0)


def test_bp_does_not_improve_because_the_debtor_was_visited_again():
    tf._seed()
    before = funnel.rates(tf._counts())
    _repeat_visit_to_the_broken_promise()
    after = funnel.rates(tf._counts())
    assert before["bp"] == 33.3 and after["bp"] == 33.3
    assert before["kp"] == 33.3 and after["kp"] == 33.3


def test_promise_outcomes_still_add_up_to_the_clients_who_promised():
    """Сумма исходов = «Клиентов с обещанием»: это единственная проверка,
    которую руководитель может сделать прямо в книге, сложив колонки."""
    _repeat_visit_to_the_broken_promise()
    c = tf._counts()
    assert c["kept"] + c["broken"] + c["pending"] + c["nodate"] == c["ptp_persons"]


def test_a_second_visit_does_not_extinguish_a_kept_promise():
    """Рядом была та же беда на ступени KP: повторный выезд к должнику, который
    ЗАПЛАТИЛ, тоже уводил его в «без даты» — сравнение с днём выезда стоит в
    правиле раньше, чем проверка платежа. Замер: было kept 0, nodate 1, KP 0 %."""
    tf._seed()
    tf._visit("FN-1", tf._day(1), tf._payload(
        "Обещает оплатить", "Обещает оплатить клиент",
        "Должник", "По адресу проживает клиент"))
    c = tf._counts()
    assert (c["kept"], c["broken"], c["pending"], c["nodate"]) == (1, 1, 1, 0)
    assert funnel.rates(c)["kp"] == 33.3


def test_both_visits_of_one_debtor_carry_the_same_outcome():
    """Исход обещания — свойство должника. Два выезда к одному человеку обязаны
    нести один и тот же исход, иначе лист «Обещания» покажет у него две разные
    судьбы, а какая из них попадёт в зачёт, будет зависеть от порядка строк."""
    import digest_data as dd

    _repeat_visit_to_the_broken_promise()
    entries = dd._employee_stats(tf.EMP, tf._day(30), tf.T)["_entries"]
    states = {e["promise"]["state"] for e in entries
              if e["person"]["client"] == "Кл Два"}
    assert states == {funnel.PROMISE_BROKEN}
