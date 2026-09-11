"""Контроль выездов: внесён позже, без геолокации, закрыл руководитель.

Решения рабочей встречи офиса «Худжанд» 11.09.2026: исключить возможность
вносить выезды в офисный день «потому что не было связи», отличать отработку
через бот от закрытой за сотрудника и видеть контроль геолокации. Всё три —
пометки на самом выезде, а не догадки по журналу.

`visits.created_at` хранит ДЕНЬ ВЫЕЗДА, поэтому отличить работу в поле от
занесённой назавтра по нему нельзя; для этого и появился `received_at`.
"""
import io
from datetime import date, datetime, timedelta

import pytest
from openpyxl import load_workbook

import db
import digest_data as dd

MGR = 563001
EMP = "Ктконтролев К."
TODAY = date.today()
T = TODAY.isoformat()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()


def _payload(status):
    return f"Выезд (миниапп). Статус: {status}; Договорённость: ; "


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'KT-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'KT-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM agent_bind WHERE employee=?", (EMP,))
        for cid, client in (("KT-1", "Клиент Вовремя"), ("KT-2", "Клиент Задним"),
                            ("KT-3", "Клиент Безгео")):
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "partner, debt_today, overall_result, country) VALUES (?,?,?,?,?,?,?,?)",
                (cid, client, EMP, "1990-01-01", "ЗК", "10 000",
                 "Не обработан", "Таджикистан"))
        conn.execute("INSERT INTO agent_bind (telegram_id, employee, kind, approved, "
                     "created_at) VALUES (?,?,?,?,?)", (777001, EMP, "agent", 1, T))
        # 1) выезд сегодня, принят сегодня, с точкой, закрыл сам
        conn.execute(
            "INSERT INTO visits (contract_id, agent_id, created_at, payload, "
            "visit_lat, visit_lon, received_at) VALUES (?,?,?,?,?,?,?)",
            ("KT-1", 777001, T, _payload("Оплачен"), 38.5, 68.7,
             datetime.now().isoformat(timespec="seconds")))
        # 2) выезд вчерашний, принят сегодня — «внесён позже»
        conn.execute(
            "INSERT INTO visits (contract_id, agent_id, created_at, payload, "
            "visit_lat, visit_lon, received_at) VALUES (?,?,?,?,?,?,?)",
            ("KT-2", 777001, YESTERDAY, _payload("Нет контакта"), 38.5, 68.7,
             datetime.now().isoformat(timespec="seconds")))
        # 3) выезд сегодня, без точки телефона, закрыл ЧУЖОЙ аккаунт (руководитель)
        conn.execute(
            "INSERT INTO visits (contract_id, agent_id, created_at, payload, received_at) "
            "VALUES (?,?,?,?,?)",
            ("KT-3", MGR, T, _payload("В работе"),
             datetime.now().isoformat(timespec="seconds")))
        conn.commit()
    db.set_manager(MGR, "Рук", [], [EMP])
    db.reset_role_caches()


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    _seed()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'KT-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'KT-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM agent_bind WHERE employee=?", (EMP,))
        conn.commit()
    db.reset_role_caches()


def _rng():
    return dd.stats_range("7d", 0, today=TODAY)


def _stats():
    return dd.stats_data(db.get_manager(MGR), _rng(), employees=[EMP])


# --- сама отметка ------------------------------------------------------------
def test_fresh_visit_carries_the_moment_it_reached_the_server():
    """Новая запись обязана нести приём: без него контроль слепой."""
    db.log_visit("KT-1", 777001, _payload("Оплачен"), op_id="kt-op-1", on_date=T)
    with db.connect() as conn:
        got = conn.execute("SELECT received_at FROM visits WHERE op_id='kt-op-1'"
                           ).fetchone()[0]
    assert got and got[:10] == date.today().isoformat()
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE op_id='kt-op-1'")
        conn.commit()


def test_same_day_delivery_is_zero_not_missing():
    assert dd._late_days(T, datetime.now().isoformat()) == 0


def test_old_rows_without_the_mark_are_not_judged():
    """У строк до этой правки приёма нет — «неизвестно», а не «внесено позже»."""
    assert dd._late_days(T, None) is None


# --- счётчики ----------------------------------------------------------------
def test_employee_row_counts_late_and_blind_visits():
    row = next(r for r in _stats()["employees"] if r["employee"] == EMP)
    assert row["late"] == 1, "вчерашний выезд доехал сегодня"
    assert row["no_geo"] == 1, "один выезд закрыт без точки телефона"
    assert row["visits"] == 3


def test_totals_carry_the_same_counters():
    t = _stats()["totals"]
    assert (t["late"], t["no_geo"]) == (1, 1)


def test_cases_screen_shows_the_marks():
    mgr = db.get_manager(MGR)
    cases = dd.cases_data(mgr, _rng(), "visits", "", employees=[EMP])
    by_rep = {c["rep"]: c for c in cases["cases"]}
    assert by_rep["KT-2"]["late_days"] == 1
    assert by_rep["KT-3"]["geo"] is False
    assert by_rep["KT-1"]["late_days"] == 0 and by_rep["KT-1"]["geo"] is True


# --- книга -------------------------------------------------------------------
@pytest.fixture(scope="module")
def book():
    blob = dd.stats_xlsx(db.get_manager(MGR), dd.stats_range("7d", 0, today=TODAY),
                         employees=[EMP])
    return load_workbook(io.BytesIO(blob))


def test_visits_sheet_marks_each_visit(book):
    ws = book["Выезды"]
    head = [c.value for c in ws[1]]
    assert head[9] == "На карточке"
    assert head[10:13] == ["Внесён", "Гео", "Закрыл"]
    rows = {ws.cell(row=r, column=5).value: [ws.cell(row=r, column=c).value
                                             for c in (11, 12, 13)]
            for r in range(2, ws.max_row + 1)}
    assert rows["Клиент Вовремя"] == ["в день выезда", "есть", "сам"]
    assert rows["Клиент Задним"] == ["позже на 1 дн", "есть", "сам"]
    assert rows["Клиент Безгео"] == ["в день выезда", "нет", "руководитель"]


def test_employee_sheet_counts_them_too(book):
    ws = book["Сотрудники"]
    head = {str(c.value): c.column for c in ws[4] if c.value}
    assert ws.cell(row=5, column=head["Внесено позже"]).value == 1
    assert ws.cell(row=5, column=head["Без гео"]).value == 1


# --- время на карточке --------------------------------------------------------
def test_time_on_card_is_measured_between_two_phone_marks():
    """Часы телефона врут, поэтому берём РАЗНИЦУ двух его же отметок."""
    import web
    opened = 1_700_000_000_000
    assert web._fill_seconds({"opened_at": opened,
                              "occurred_at": opened + 240_000}) == 240
    assert web._fill_seconds({"opened_at": opened}) == 0, "без occurred_at — момент"
    assert web._fill_seconds({}) is None
    assert web._fill_seconds({"opened_at": opened,
                              "occurred_at": opened - 5_000}) is None, "назад не идём"
    assert web._fill_seconds({"opened_at": opened,
                              "occurred_at": opened + 13 * 3600 * 1000}) is None, \
        "полсуток на карточке — забытое открытым приложение, а не выезд"


def test_fill_text_reads_like_a_human():
    assert dd._fill_text(45) == "45 с"
    assert dd._fill_text(240) == "4 мин"
    assert dd._fill_text(4200) == "1 ч 10 мин"
    assert dd._fill_text(None) == "" and dd._fill_text(-5) == ""


def test_average_skips_unmeasured_visits():
    assert dd._avg_fill([120, 240, None]) == 180
    assert dd._avg_fill([None, None]) is None


def test_book_shows_time_on_card():
    with db.connect() as conn:
        conn.execute("UPDATE visits SET fill_seconds=300 WHERE contract_id='KT-1'")
        conn.commit()
    blob = dd.stats_xlsx(db.get_manager(MGR), dd.stats_range("7d", 0, today=TODAY),
                         employees=[EMP])
    wb = load_workbook(io.BytesIO(blob))
    ws = wb["Выезды"]
    by_client = {ws.cell(row=r, column=5).value: ws.cell(row=r, column=10).value
                 for r in range(2, ws.max_row + 1)}
    assert by_client["Клиент Вовремя"] == "5 мин"
    people = wb["Сотрудники"]
    head = {str(c.value): c.column for c in people[4] if c.value}
    assert people.cell(row=5, column=head["На карточке, среднее"]).value == "5 мин"
