"""Модель ожидаемого сбора: признаки, лифты, устойчивость.

Смысл модели — считать деньги, а не баллы: E = P(заплатит) × сумма(если заплатит).
Здесь закрепляем то, на чём такие модели обычно и ломаются: расхождение признаков
между обучением и боем, утечка целевой переменной и падение без файла модели.
"""
from datetime import date

import pytest

import scoring
import scoring_model
from scoring_model import LEAK, Model, features

TODAY = date(2026, 7, 1)


def _m(lifts=None, base_p=0.08, base_a=20_000.0):
    return Model({"base_p": base_p, "base_a": base_a, "lifts": lifts or {}})


# --- признаки ------------------------------------------------------------------
def test_debt_buckets():
    f = lambda d: features({"debt": d}, TODAY)["debt"]
    assert f(1_000) == "<5к"
    assert f(15_000) == "5-20к"
    assert f(75_000) == "50-100к"
    assert f(5_000_000) == ">300к"


def test_missing_values_are_explicit():
    f = features({}, TODAY)
    assert f["debt"] == "—" and f["result"] == "—" and f["promise"] == "нет"
    assert f["visits"] == "0" and f["paid_before"] == "нет"


def test_dates_become_buckets():
    f = features({"assign_date": "2026-06-25", "kd_date": "2020-01-01"}, TODAY)
    assert f["days_work"] == "<7д"
    assert f["age_kd"] == ">3г"


def test_paid_history_bucket():
    assert features({"paid_months": 0}, TODAY)["paid_before"] == "нет"
    assert features({"paid_months": 2}, TODAY)["paid_before"] == "1-2 мес"
    assert features({"paid_months": 5}, TODAY)["paid_before"] == "3+ мес"


def test_result_after_payment_is_marked_as_leak():
    """«Оплачен»/«Рассрочка» проставляют ПОСЛЕ оплаты. Если пустить их в модель
    как обычное значение, она выучит тавтологию «заплатил ⟹ заплатит»."""
    assert features({"result": "Оплачен"}, TODAY)["result"] == LEAK
    assert features({"result": "Рассрочка 6 мес"}, TODAY)["result"] == LEAK
    assert features({"result": "Нет контакта"}, TODAY)["result"] == "нет контакта"


# --- применение лифтов ---------------------------------------------------------
def test_expected_is_probability_times_amount():
    m = _m({"location": {"клиент в рф": [2.0, 1.5, 500]}})
    got = m.expected({"location": "Клиент в РФ"}, TODAY)
    assert got == pytest.approx(0.08 * 2.0 * 20_000 * 1.5)


def test_unknown_value_is_neutral():
    """Новый статус в реестре не должен ни возносить договор, ни хоронить."""
    m = _m({"result": {"нет контакта": [0.3, 1.0, 900]}})
    base = m.expected({}, TODAY)
    assert m.expected({"result": "какой-то новый статус"}, TODAY) == pytest.approx(base)


def test_probability_is_capped():
    m = _m({"result": {"x": [50.0, 1.0, 100]}, "location": {"y": [50.0, 1.0, 100]}})
    got = m.expected({"result": "x", "location": "y"}, TODAY)
    assert got <= 0.95 * 20_000 + 1e-6


def test_untrained_model_still_returns_a_number():
    """Файла модели нет — бот обязан работать, а не остановить выезды."""
    m = _m({})
    assert not m.is_trained
    assert m.expected({"debt": 50_000}, TODAY) == pytest.approx(0.08 * 20_000)


# --- объяснение ----------------------------------------------------------------
def test_explain_names_strongest_factors():
    m = _m({"location": {"клиент в рф": [2.2, 1.0, 500]},
            "contact": {"контакт не установлен": [0.3, 1.0, 400]}})
    # ВАЖНО: признак модели зовётся `contact`, а поле договора — `contact_with`.
    # Перепутать их легко, и тогда фактор молча выпадает из объяснения.
    got = scoring_model.explain(m, {"location": "Клиент в РФ",
                                    "contact_with": "Контакт не установлен"},
                                top=2, today=TODAY)
    assert any("×0.3" in s for s in got)          # сильнейший — минусовой
    assert any("×2.2" in s for s in got)


def test_explain_is_empty_for_neutral_case():
    assert scoring_model.explain(_m({}), {"debt": 1}, today=TODAY) == []


# --- связка со скорингом -------------------------------------------------------
def test_scoring_uses_money_when_model_trained():
    m = _m({"location": {"клиент на родине": [2.0, 1.0, 900]}})
    rows = scoring.score_items(
        [{"rep": "a", "city": "Худжанд", "debt": 30_000, "visits": 0,
          "result": "В работе", "location": "Клиент на родине", "who": ""},
         {"rep": "b", "city": "Худжанд", "debt": 30_000, "visits": 0,
          "result": "В работе", "location": "Местоположение не установлено", "who": ""}],
        {"debt": 50, "cluster": 30, "fresh": 20}, "courier", today=TODAY, model=m)
    good = next(r for r in rows if r["rep"] == "a")
    bad = next(r for r in rows if r["rep"] == "b")
    assert good["expected"] > bad["expected"]
    assert good["score"] == 100.0                      # верх очереди нормируется в 100
    assert "₽" in good["reason"]


def test_killer_filters_still_win_over_money():
    """Оплаченный договор с любым ожиданием всё равно снимается из очереди."""
    m = _m({"debt": {">300к": [2.0, 3.0, 200]}})
    r = scoring.score_items(
        [{"rep": "a", "city": "Худжанд", "debt": 5_000_000, "visits": 0,
          "result": "Оплачен", "location": "", "who": ""}],
        {"debt": 50, "cluster": 30, "fresh": 20}, "courier", today=TODAY, model=m)[0]
    assert r["dropped"] and r["score"] == 0.0


def test_score_items_does_not_mutate_input():
    m = _m({"debt": {"20-50к": [1.5, 1.0, 900]}})
    src = {"rep": "a", "city": "Худжанд", "debt": 30_000, "visits": 0,
           "result": "", "location": "", "who": ""}
    before = dict(src)
    scoring.score_items([src], {"debt": 50, "cluster": 30, "fresh": 20},
                        "courier", today=TODAY, model=m)
    assert src == before


def test_cluster_still_shifts_order_at_equal_money():
    """Кластер остался множителем: при равных деньгах ближе стоящий выше."""
    m = _m({})
    items = ([{"rep": f"c{i}", "city": "Худжанд", "debt": 30_000, "visits": 0,
               "result": "", "location": "", "who": ""} for i in range(5)]
            + [{"rep": "lonely", "city": "Исфара", "debt": 30_000, "visits": 0,
                "result": "", "location": "", "who": ""}])
    rows = scoring.score_items(items, {"debt": 0, "cluster": 100, "fresh": 0},
                               "courier", today=TODAY, model=m)
    by = {r["rep"]: r["score"] for r in rows}
    assert by["c0"] > by["lonely"]
