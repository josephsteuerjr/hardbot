"""Раздельная статистика Таджикистана/Узбекистана по scope руководителей."""

import country_scope
import db


def _client(cid: str, employee: str, *, visited=False, result=None):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO clients (contract_id, client, responsible, visit_date, overall_result) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, cid, employee, "2026-07-21" if visited else None, result),
        )
        conn.commit()
    if visited:
        db.record_bot_visit(cid, {}, "2026-07-21")


def test_scope_country_is_explicit_and_never_mixed():
    assert country_scope.scope_country(["Худжанд"]) == "tj"
    assert country_scope.scope_country(["Ташкент", "Самарканд"]) == "uz"
    assert country_scope.scope_country(["Таджикистан · Согд"]) == "tj"
    assert country_scope.scope_country(["Узбекистан"]) == "uz"
    assert country_scope.scope_country(["Худжанд", "Ташкент"]) is None
    assert country_scope.scope_country([]) is None


def test_employee_in_two_countries_is_excluded_to_avoid_double_count():
    mapping = country_scope.employee_countries([
        {"cities": ["Худжанд"], "employees": ["Один", "Спорный"]},
        {"cities": ["Ташкент"], "employees": ["Два", "Спорный"]},
    ])
    assert mapping == {"Один": "tj", "Два": "uz"}


def test_stats_by_country_uses_manager_scope_not_numeric_pool():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients")
        conn.execute("DELETE FROM managers")
        conn.commit()

    db.set_manager(91001, "РТ", ["Худжанд"], ["Сотрудник ТЖ"])
    db.set_manager(91002, "УЗ", ["Ташкент", "Самарканд"], ["Сотрудник УЗ"])
    _client("TJ-1", "Сотрудник ТЖ", visited=True, result="Оплачен")
    _client("TJ-2", "Сотрудник ТЖ", result="Не обработан")
    _client("UZ-1", "Сотрудник УЗ", visited=True, result="Нет контакта")
    _client("UZ-2", "Сотрудник УЗ", visited=True, result="Нет контакта")
    _client("UZ-3", "Сотрудник УЗ", result="Не обработан")
    _client("NO-1", "Без scope", visited=True, result="Оплачен")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET pool = '13'")  # live pool — код, не страна
        conn.commit()

    rows = {row["code"]: row for row in db.stats_by_country()}
    assert (rows["tj"]["clients"], rows["tj"]["visited"], rows["tj"]["employees"]) \
        == (2, 1, 1)
    assert (rows["uz"]["clients"], rows["uz"]["visited"], rows["uz"]["employees"]) \
        == (3, 2, 1)
    assert rows["uz"]["by_result"][0] == ("Нет контакта", 2)
    assert sum(row["clients"] for row in rows.values()) == 5  # NO-1 только в общей стате
