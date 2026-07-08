"""debt.py — статьи разбивки, контрагенты, валюта.

Смысл модуля: набор статей зависит от контрагента (Делимобиль ≠ кредитные),
а сумы и рубли нельзя складывать в одно число.
"""
import debt


def test_carsharing_detection_is_tolerant_to_free_text():
    """Партнёр в реестре — свободный текст, а не справочник."""
    assert debt.is_carsharing("Делимобиль")
    assert debt.is_carsharing("  делимобиль  ")
    assert debt.is_carsharing("ДМ")
    assert not debt.is_carsharing("ЗК")
    assert not debt.is_carsharing("")
    assert not debt.is_carsharing(None)


def test_articles_depend_on_partner():
    """Наборы статей не пересекаются: у ЗК нет эвакуации, у Делимобиля — госпошлины."""
    zk, dm = debt.articles_for("ЗК"), debt.articles_for("Делимобиль")
    assert set(zk) & set(dm) == set()
    assert "duty" in zk and "evacuation" not in zk
    assert "evacuation" in dm and "duty" not in dm
    assert debt.articles_for(None) == debt.ARTICLES_CREDIT   # неизвестный → кредитный набор


def test_breakdown_skips_zeros_and_keeps_registry_order():
    row = {"partner": "Делимобиль", "rent": 12000, "evacuation": "5 000",
           "dtp": 0, "trash": None, "peni": "300"}
    # dtp=0 и trash=None выпадают; порядок — как колонки в реестре, а не как в dict
    assert debt.breakdown(row) == [("rent", 12000), ("evacuation", 5000), ("peni", 300)]


def test_breakdown_shows_alien_articles_last_not_hidden():
    """Грязные данные (аренда у кредитного контрагента) не прячем — деньги должны быть
    видны выезднику, но своя разбивка идёт первой."""
    row = {"partner": "ЗК", "debt_overdue": 1000, "rent": 500}
    assert debt.breakdown(row) == [("debt_overdue", 1000), ("rent", 500)]


def test_breakdown_never_includes_totals():
    """«долг всего» / «долг на сегодня» — не статьи: иначе итог читается как начисление."""
    row = {"partner": "ЗК", "debt_overdue": 1000, "debt_total": 1000, "debt_today": 1000}
    assert [k for k, _ in debt.breakdown(row)] == ["debt_overdue"]


def test_contract_debt_prefers_today_and_respects_explicit_zero():
    assert debt.contract_debt({"debt_today": "900", "debt_total": "1000"}) == 900
    assert debt.contract_debt({"debt_today": None, "debt_total": "1000"}) == 1000
    assert debt.contract_debt({"debt_today": "", "debt_total": "1000"}) == 1000
    assert debt.contract_debt({"debt_today": 0, "debt_total": "1000"}) == 0   # погашен, не «нет данных»


def test_unit_by_partner_mix():
    assert debt.unit(["ЗК"]) == "сум"
    assert debt.unit(["ЗК", "ЗК"]) == "сум"
    assert debt.unit(["Делимобиль"]) == "₽"
    assert debt.unit(["ЗК", "Делимобиль"]) == ""      # общей валюты нет
    assert debt.unit([]) == ""


def test_totals_by_unit_does_not_add_sums_to_rubles():
    """Главный баг витрины: у человека с ЗК + Делимобилем «долг всего» был суммой
    сумов с рублями — числом, которое ничего не значит."""
    rows = [{"partner": "ЗК", "debt_today": "11 550 000", "debt_total": None},
            {"partner": "Делимобиль", "debt_today": "132 900", "debt_total": None}]
    assert debt.totals_by_unit(rows) == [{"unit": "сум", "sum": 11550000},
                                         {"unit": "₽", "sum": 132900}]


def test_totals_by_unit_merges_same_currency_and_drops_zeros():
    rows = [{"partner": "Делимобиль", "debt_today": "100", "debt_total": None},
            {"partner": "Каршеринг Руссия", "debt_today": "200", "debt_total": None},
            {"partner": "ЗК", "debt_today": "0", "debt_total": None}]
    assert debt.totals_by_unit(rows) == [{"unit": "₽", "sum": 300}]


def test_fmt_groups_thousands_with_spaces():
    assert debt.fmt(11550000, "сум") == "11 550 000 сум"
    assert debt.fmt(0) == "0"
