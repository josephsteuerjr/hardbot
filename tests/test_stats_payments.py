"""«Оплаты» в сводке руководителя — по реестру (v65).

Владелец 01.10.2026: «обещания — это хорошо, но что насчёт оплат?». Реестр знает
«Дату/Сумму последнего платежа» договора; сводка считает, кто из портфеля
заплатил за окно и сколько, и сколько из них — после выезда (к должнику ездили
не раньше чем за 30 дней до платежа). Здесь закреплены: границы окна, правило
«после выезда», деньги по договорам человека, итоги команды и провал в список.
"""
from datetime import date, timedelta

import pytest

import db
import digest_data as dd

MGR = 565001
EMP = "Оплатов О."
TODAY = date.today()


def _d(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


# (КД, клиент, дата платежа, сумма, день выезда или None)
ROWS = [
    ("PY-1", "Платил После", _d(2), "5 000", _d(4)),       # после выезда
    ("PY-2", "Платил Сам", _d(1), "3000", None),            # без выезда
    ("PY-3", "Давно Ездили", _d(3), "1 000", _d(40)),      # выезд старше 30 дней
    ("PY-4", "Вне Окна", _d(20), "9 999", _d(21)),         # платёж до окна «7 дней»
    ("PY-5", "Не Платил", "", "", _d(2)),
]


@pytest.fixture(autouse=True)
def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'PY-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'PY-%'")
        for cid, client, pay_day, pay_sum, visit_day in ROWS:
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, partner, "
                "debt_today, overall_result, country, last_payment_date, last_payment_sum) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (cid, client, EMP, "1990-01-0" + cid[-1], "ЗК", "100 000", "Не обработан",
                 "Таджикистан", pay_day, pay_sum))
            if visit_day:
                conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                             "VALUES (?,?,?,?)", (cid, 1, visit_day,
                                                  "Выезд (миниапп). Статус: В работе; "))
        conn.commit()
    db.set_manager(MGR, "Рук оплат", [], [EMP])
    db.reset_role_caches()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'PY-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'PY-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    db.reset_role_caches()


def _rng():
    return dd.stats_range("7d", 0, today=TODAY)


def test_payments_in_window_and_after_visit():
    j = dd.stats_data(db.get_manager(MGR), _rng(), employees=[EMP])
    t = j["totals"]
    assert t["pay_persons"] == 3, "платёж до окна не считается"
    assert t["pay_after"] == 1, "только выезд за 30 дней до платежа делает его «после выезда»"
    assert t["pay_money"] == [{"unit": "₽", "sum": 9000}]
    assert t["pay_after_money"] == [{"unit": "₽", "sum": 5000}]
    assert t["pay_after_money_text"] == "5 000 ₽"
    assert j["pay_asof"] >= _d(1), "по какой день реестр знает об оплатах"


def test_payments_drill_down_lists_the_same_people():
    rng = _rng()
    allp = dd.cases_data(db.get_manager(MGR), rng, "pay", employees=[EMP])
    after = dd.cases_data(db.get_manager(MGR), rng, "pay_after", employees=[EMP])
    assert allp["total"] == 3 and after["total"] == 1
    c = after["cases"][0]
    assert c["client"] == "Платил После" and c["date"] == _d(2)
    assert c["money_text"] == "5 000 ₽" and c["agreed"].startswith("после выезда")
    assert {x["agreed"] for x in allp["cases"]} >= {"без выезда"}


def test_shell_shows_payments_block():
    src = (__import__("pathlib").Path(__file__).resolve().parent.parent
           / "server" / "miniapp" / "index.html").read_text("utf-8")
    assert 'sec("Оплаты"' in src and "dgCases('pay')" not in src  # тап — через c("pay")
    assert 'c("pay")' in src and 'c("pay_after")' in src
    assert 'class="facts"' not in src, "сплошная строка фактов вернулась"
