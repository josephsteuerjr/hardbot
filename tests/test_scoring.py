"""Тесты единого скоринга: два режима, killer-фильтры (исходы + регуляторные), чистота."""
from datetime import date

import scoring

W = {"debt": 50, "cluster": 30, "fresh": 20}
TODAY = date(2026, 6, 26)


def _one(**kw):
    base = {"rep": "1", "city": "Худжанд", "debt": 1_000_000, "visits": 0,
            "result": "", "location": "", "who": ""}
    base.update(kw)
    return scoring.score_items([base], W, "courier", today=TODAY)[0]


# --- Два режима ------------------------------------------------------------
def test_mode_razvedka_when_no_outcome():
    assert _one(result="Не обработан")["mode"] == "разведка"


def test_mode_dozhim_when_outcome():
    assert _one(result="Обещает оплатить")["mode"] == "дожим"


# --- Killer-фильтры по исходу ----------------------------------------------
def test_killer_paid_dead_jail_nocredit_dropped():
    for res in ("Оплачен", "Смерть", "Тюрьма", "Кредит не брал"):
        r = _one(result=res)
        assert r["dropped"] and r["score"] == 0.0, res


def test_killer_address_not_exists():
    assert _one(result="Адрес не существует")["dropped"]


def test_unreachable_is_retargeted_not_dropped():
    """ПОЛИТИКА ИЗМЕНЕНА ОСОЗНАННО. Раньше «клиент в РФ» снимало договор из очереди.
    На реальных 17 075 договорах под фильтр попадали 2 177 (12.7%), и они дали
    28.4% всего сбора — по 5 966 ₽ против 2 195 ₽ у остальных. Уехавший на
    заработки платит ЛУЧШЕ, а по адресу живут родственники (лучший предиктор,
    ×2.2). Поэтому теперь не выбрасываем, а меняем цель визита."""
    r = _one(result="В работе", location="в РФ")
    assert not r["dropped"]
    assert r["purpose"] == "работа с родственниками"
    assert "родственник" in r["reason"]


def test_unreachable_purpose_only_for_courier():
    a = scoring.score_items([{"city": "Худжанд", "debt": 1_000_000, "visits": 0,
                              "result": "В работе", "location": "в РФ", "who": ""}],
                            W, "agent", today=TODAY)[0]
    assert not a["dropped"] and not a["purpose"]      # агенту ехать не надо


# --- Сигналы дожима (балльный режим — фолбэк, когда модель не обучена) ------
def _fallback(**kw):
    """Тот же вход, но принудительно старым балльным путём."""
    base = {"rep": "1", "city": "Худжанд", "debt": 1_000_000, "visits": 0,
            "result": "", "location": "", "who": ""}
    base.update(kw)
    return scoring.score_items([base], W, "courier", today=TODAY,
                               model=scoring.scoring_model.Model({}))[0]


def test_signal_homeland_only_courier():
    c = _fallback(result="В работе", location="на родине")
    assert "на родине ✓" in c["reason"] and c["adj"] >= 8


def test_signal_promise_bonus():
    assert _fallback(result="Обещает оплатить")["adj"] >= 12


def test_fallback_used_when_model_missing():
    """Модель — файл. Если он не доехал до сервера, скоринг обязан работать
    по-старому, а не остановить выезды."""
    r = _fallback(result="Не обработан")
    assert r["mode"] == "разведка" and "adj" in r and 0 <= r["score"] <= 130


# --- Регуляторные killer-фильтры (230-ФЗ) ----------------------------------
def test_killer_future_promise_deferred():
    r = _one(result="Обещает оплатить", promise_date="2026-07-15")
    assert r["dropped"] and "обещание" in r["reason"]


def test_killer_recent_visit_within_week():
    assert _one(result="В работе", visit_date="2026-06-24")["dropped"]    # 2 дня назад


def test_old_visit_not_dropped():
    assert not _one(result="В работе", visit_date="2026-06-01")["dropped"]  # 25 дней назад


# --- Корректность ----------------------------------------------------------
def test_score_within_clamp():
    r = _one(debt=99_999_999, result="Обещает оплатить", location="на родине",
             who="проживает клиент")
    assert 0.0 <= r["score"] <= 130.0


def test_compute_priority_is_pure():
    items = [{"rep": "1", "client": "A", "birth": "1980-01-01", "debt": 500_000,
              "address": "Худжанд", "city": "Худжанд", "visits": 0,
              "result": "Не обработан", "location": "", "who": ""}]
    snapshot = {k: items[0].get(k) for k in items[0]}
    scoring.compute_priority(items, W, "courier")
    assert items[0] == snapshot, "compute_priority НЕ должна мутировать вход"


def test_compute_priority_sorted_desc():
    items = [{"rep": str(i), "debt": d, "city": "Худжанд", "visits": 0, "result": ""}
             for i, d in enumerate([100, 5_000_000, 50_000])]
    res = scoring.compute_priority(items, W, "courier")
    scores = [x["score"] for x in res["items"]]
    assert scores == sorted(scores, reverse=True)


def test_weights_zero_falls_back_to_default():
    w = scoring._norm_weights({"debt": 0, "cluster": 0, "fresh": 0})
    assert w == {"debt": 0.5, "cluster": 0.3, "fresh": 0.2}


def test_engine_and_bot_share_core_scores():
    """score_items (движок) и compute_priority (бот) дают один скор при том же city."""
    item = {"rep": "1", "debt": 1_000_000, "city": "Худжанд", "visits": 1,
            "result": "Обещает оплатить", "location": "на родине", "who": "", "address": "Худжанд"}
    direct = scoring.score_items([dict(item)], W, "courier", today=TODAY)[0]["score"]
    viabot = scoring.compute_priority([dict(item)], W, "courier")["items"][0]["score"]
    assert direct == viabot
