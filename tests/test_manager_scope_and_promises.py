"""Две правки по запросам Михаила Пискурева от 24.07.

1. «Статистику по республикам отдельно РТ и РУ» — у руководителя с обеими республиками
   scope раньше схлопывался в None, и заказчик разделения видел сводку вообще без
   признака страны. Теперь показываются оба блока.
2. «смотреть, обновлена информация или нет» — в просроченных обещаниях не было ни
   одного признака свежести. Теперь видно, был ли визит после даты обещания.
"""
from datetime import date, timedelta

import country_scope
import db
import digest

TODAY = date.today()
EMP = "Обещалов Сотрудник"
MGR_MIX, MGR_ONE = 880101, 880102


def test_mixed_scope_reports_both_countries():
    mix = ["Душанбе", "Самарканд", "Ташкент", "Худжанд"]
    assert country_scope.scope_countries(mix) == ("tj", "uz")
    # Одно-страновая логика не тронута: сотрудник в двух странах по-прежнему выпадает
    assert country_scope.scope_country(mix) is None
    assert country_scope.scope_country(["Худжанд"]) == "tj"

    head = digest.scope_heading({"name": "Михаил", "cities": mix})
    assert "Таджикистан" in head and "Узбекистан" in head
    one = digest.scope_heading({"name": "Илья", "cities": ["Худжанд"]})
    assert "Таджикистан" in one and "Узбекистан" not in one


def test_split_employees_puts_everyone_in_one_bucket():
    mapping = {"Таджиков Т.": "tj", "Узбеков У.": "uz"}
    groups = country_scope.split_employees(
        ["Узбеков У.", "Таджиков Т.", "Ничей Н."], mapping)
    assert [c for c, _ in groups] == ["tj", "uz", None]
    assert sum(len(e) for _, e in groups) == 3, "сотрудник не должен задваиваться"


def _seed_promise(cid, promise_days_ago, visit_days_ago=None):
    pd = (TODAY - timedelta(days=promise_days_ago)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO clients (contract_id, client, responsible, "
            "promise_date, partner) VALUES (?,?,?,?,?)",
            (cid, f"Должник {cid}", EMP, pd, "ЗК"))
        if visit_days_ago is not None:
            conn.execute("UPDATE clients SET bot_visit_at = ? WHERE contract_id = ?",
                         ((TODAY - timedelta(days=visit_days_ago)).isoformat(), cid))
        conn.commit()


def test_promise_freshness_marks_visits_after_promise():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE responsible = ?", (EMP,))
        conn.commit()
    _seed_promise("PR-AFTER", promise_days_ago=10, visit_days_ago=3)    # съездили после
    _seed_promise("PR-BEFORE", promise_days_ago=10, visit_days_ago=20)  # визит до обещания
    _seed_promise("PR-NONE", promise_days_ago=10)                       # визитов не было

    by_cid = {p["contracts"][0]["contract_id"]: p
              for p in db.overdue_promises(EMP, TODAY.isoformat())}
    assert by_cid["PR-AFTER"]["visited_after_promise"] is True
    assert by_cid["PR-BEFORE"]["visited_after_promise"] is False
    assert by_cid["PR-BEFORE"]["last_visit"] is not None
    assert by_cid["PR-NONE"]["visited_after_promise"] is False
    assert by_cid["PR-NONE"]["last_visit"] is None


def test_promises_view_shows_freshness_summary():
    """Рисовальщик показывает оба состояния — «съездили после обещания» и «не
    тронуто». Окно списка на время проверки снимаем: по умолчанию оно оставляет
    только неделю и только обещания выездника (db.promise_window), а здесь
    проверяется сам текст, а не отбор."""
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE responsible = ?", (EMP,))
        conn.commit()
    _seed_promise("PR-V1", promise_days_ago=8, visit_days_ago=2)
    _seed_promise("PR-V2", promise_days_ago=8)
    db.set_manager(MGR_ONE, "Тестовый", ["Худжанд"], [EMP])
    db.set_setting("promise_max_days", "0")
    db.set_setting("promise_agent_only", "0")
    try:
        text, _kb = digest.promises_view(db.get_manager(MGR_ONE), TODAY.isoformat())
        assert "отработано после обещания: 1" in text
        assert "не тронуто: 1" in text
        assert "🟢 визит" in text and "⚪ визитов не было" in text
    finally:
        db.set_setting("promise_max_days", "")
        db.set_setting("promise_agent_only", "")
    # А с окном по умолчанию таких в рассылке нет вовсе: обещаниям 8 дней, и
    # ни одно из них не взято выездником на выезде.
    assert digest.overdue_by_employee(db.get_manager(MGR_ONE), TODAY.isoformat()) == []
    db.delete_manager(MGR_ONE)
