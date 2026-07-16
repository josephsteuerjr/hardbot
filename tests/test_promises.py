"""Контроль обещаний: выборка просроченных (db.overdue_promises) и экран дайджеста.

Просрочено = promise_date в прошлом И кейс не «Оплачен» И после даты обещания
не было платежа (last_payment_date). Обещание на будущее/сдержанное — не в счёт.
"""
from datetime import date, timedelta

import db
import digest

TODAY = date.today().isoformat()
D = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
MGR = 700002
EMP = "Обещалкин Тест"


def _add(cid, client, **f):
    row = dict(contract_id=cid, client=client, responsible=EMP,
               partner="ЗК", debt_today="500 000")
    row.update(f)
    with db.connect() as conn:
        keys = ", ".join(f'"{k}"' for k in row)
        qs = ", ".join("?" for _ in row)
        conn.execute(f"INSERT OR REPLACE INTO clients ({keys}) VALUES ({qs})",
                     tuple(row.values()))
        conn.commit()


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE responsible = ?", (EMP,))
        conn.commit()
    _add("PR-1", "Просрочилов П.", promise_date=D(6))                 # просрочен, 6 дн
    _add("PR-2", "Свежов С.", promise_date=D(1))                      # просрочен, 1 дн
    _add("PR-3", "Будущев Б.", promise_date=D(-3))                    # обещание впереди
    _add("PR-4", "Оплатилов О.", promise_date=D(9),
         overall_result="Оплачен")                                    # оплачен
    _add("PR-5", "Сдержалов С.", promise_date=D(9),
         last_payment_date=D(2))                                      # платёж ПОСЛЕ обещания
    _add("PR-6", "Старый платёж", promise_date=D(5),
         last_payment_date=D(30))                                     # платёж ДО обещания
    _add("PR-7", "Беззаботный Б.")                                    # без обещаний
    db.set_manager(MGR, "Контролёр", [], [EMP])


def test_overdue_selection_and_order():
    _seed()
    ps = db.overdue_promises(EMP, TODAY)
    names = [p["client"] for p in ps]
    assert names == ["Просрочилов П.", "Старый платёж", "Свежов С."]   # тухлые сверху
    assert ps[0]["days_overdue"] == 6
    assert ps[-1]["days_overdue"] == 1
    db.delete_manager(MGR)


def test_promises_view_and_summary_button():
    _seed()
    mgr = db.get_manager(MGR)
    text, kb = digest.promises_view(mgr, TODAY)
    assert "Просроченные обещания" in text
    assert "Просрочилов П." in text and "6 дн назад" in text
    assert "Будущев" not in text and "Оплатилов" not in text
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"dg:h:{TODAY}" in cbs                     # назад к сводке

    stext, skb = digest.summary_view(mgr, TODAY)
    btns = [b.text for row in skb.inline_keyboard for b in row]
    assert any("Обещания просрочены: 3" in t for t in btns)
    db.delete_manager(MGR)


def test_no_overdue_no_button():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE responsible = ?", (EMP,))
        conn.commit()
    _add("PR-8", "Чистый Ч.", promise_date=D(-5))
    db.set_manager(MGR, "Контролёр", [], [EMP])
    mgr = db.get_manager(MGR)
    assert digest.overdue_by_employee(mgr, TODAY) == []
    text, kb = digest.promises_view(mgr, TODAY)
    assert "нет 🎉" in text
    _, skb = digest.summary_view(mgr, TODAY)
    assert not any(b.callback_data.startswith("dg:o:")
                   for row in skb.inline_keyboard for b in row)
    db.delete_manager(MGR)
