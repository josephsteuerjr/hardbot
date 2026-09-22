"""Экран «Статистика» руководителя: окно периода, данные по журналу выездов,
ручки /api/manager/stats*, Excel, карточка по прошлому выезду.

Главное свойство — ИСТОРИЯ, а не последний снимок: человек, к которому съездили
2-го и 9-го, виден в оба дня со своим результатом. Старый period_data смотрел на
bot_visit_at и «переносил» человека в день последнего выезда целиком.
"""
import asyncio
import io
import json
from datetime import date, timedelta

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import digest_data as dd
import web
from config import ADMIN_ID

MGR = 558001
STRANGER = 558002
EMP_A, EMP_B, EMP_FOREIGN = "Стапиев А.", "Стбпиев Б.", "Стчужой Ч."
TODAY = date.today()
T = TODAY.isoformat()
D2 = (TODAY - timedelta(days=2)).isoformat()
D9 = (TODAY - timedelta(days=9)).isoformat()


def _payload(status, agreed="", comment=""):
    return f"Выезд (миниапп). Статус: {status}; Договорённость: {agreed}; {comment}"


def _visit(cid, day, status, agreed=""):
    with db.connect() as conn:
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES (?,?,?,?)", (cid, 1, day, _payload(status, agreed)))
        conn.commit()


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM managers WHERE telegram_id IN (?,?)", (MGR, STRANGER))
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'ST-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'ST-%'")
        conn.commit()
    rows = [("ST-0", "Клиент Ноль", EMP_A, "1990-01-01", "500 000"),
            ("ST-1", "Клиент Ноль", EMP_A, "1990-01-01", "100 000"),   # второй КД того же человека
            ("ST-2", "Клиент Два", EMP_A, "1991-01-01", "300 000"),
            ("ST-3", "Клиент Три", EMP_B, "1992-01-01", "700 000"),
            ("ST-4", "Клиент Чужой", EMP_FOREIGN, "1993-01-01", "900 000")]
    with db.connect() as conn:
        for cid, client, emp, dob, debt in rows:
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "partner, debt_today, overall_result, country) VALUES (?,?,?,?,?,?,?,?)",
                (cid, client, emp, dob, "ЗК", debt, "Не обработан", "Таджикистан"))
        conn.commit()
    # Клиент Ноль: выезд 9 дней назад «Нет контакта», сегодня «Оплачен» (снимок сегодня)
    _visit("ST-0", D9, "Нет контакта")
    _visit("ST-0", T, "Оплачен", "да")
    _visit("ST-0", T, "Оплачен", "да")            # повторный тап — тот же выезд
    db.record_bot_visit("ST-0", {"call_status": "Оплачен"}, T)
    # Клиент Два: один выезд 2 дня назад, обещает
    _visit("ST-2", D2, "Обещает оплатить", "до конца недели")
    db.record_bot_visit("ST-2", {"call_status": "Обещает оплатить"}, D2)
    # Клиент Три (у Б): выезд сегодня без статуса в журнале — берётся снимок
    _visit("ST-3", T, "")
    db.record_bot_visit("ST-3", {"call_status": "Рассрочка"}, T)
    db.set_manager(MGR, "Рук", [], [EMP_A, EMP_B])
    db.reset_role_caches()


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    """Посев со страной «Таджикистан» иначе утекал бы в чужие тесты: пустой
    направленец из test_manager_digest_api получал бы команду из этих строк."""
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'ST-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'ST-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id IN (?,?)", (MGR, STRANGER))
        conn.commit()
    db.reset_role_caches()


def _rng(**kw):
    return dd.stats_range(kw.pop("preset", "30d"), kw.pop("shift", 0),
                          kw.pop("frm", None), kw.pop("to", None), today=TODAY)


# --- окно периода ------------------------------------------------------------
def test_range_presets_are_anchored_to_server_today():
    t = date(2026, 9, 8)
    r = dd.stats_range("today", today=t)
    assert (r["from"], r["to"], r["days"], r["next"]) == ("2026-09-08", "2026-09-08", 1, None)
    assert r["label"] == "8 сентября" and r["is_today"]
    y = dd.stats_range("yesterday", today=t)
    assert (y["preset"], y["shift"], y["from"]) == ("today", -1, "2026-09-07")
    assert y["next"]["to"] == "2026-09-08"
    w = dd.stats_range("7d", -1, today=t)
    assert (w["from"], w["to"], w["label"]) == ("2026-08-26", "2026-09-01", "26 авг – 1 сен")
    m = dd.stats_range("month", -1, today=t)
    assert (m["from"], m["to"], m["days"]) == ("2026-08-01", "2026-08-31", 31)
    assert m["prev"] == {"from": "2026-07-01", "to": "2026-07-31"}


def test_range_never_goes_into_the_future_for_presets():
    t = date(2026, 9, 8)
    assert dd.stats_range("7d", 5, today=t)["to"] == "2026-09-08"
    assert dd.stats_range("month", 3, today=t)["from"] == "2026-09-01"


def test_custom_range_is_normalised_and_shifted_on_the_server():
    t = date(2026, 9, 8)
    r = dd.stats_range(None, 0, "05.09.2026", "01.09.2026", today=t)   # перепутали концы
    assert (r["preset"], r["from"], r["to"]) == ("custom", "2026-09-01", "2026-09-05")
    back = dd.stats_range("custom", -1, "2026-09-01", "2026-09-05", today=t)
    assert (back["from"], back["to"], back["shift"]) == ("2026-08-27", "2026-08-31", 0)
    long = dd.stats_range("custom", 0, "2020-01-01", "2026-09-08", today=t)
    assert long["days"] == dd.STATS_MAX_DAYS and "2025" in long["label"]
    bare = dd.stats_range("custom", 0, today=t)          # «Даты…» без дат — сегодня
    assert (bare["from"], bare["to"]) == ("2026-09-08", "2026-09-08")


# --- данные ------------------------------------------------------------------
def test_history_counts_every_visit_day_with_its_own_outcome():
    _seed()
    data = dd.stats_data(db.get_manager(MGR), _rng(), employees=[EMP_A, EMP_B])
    a = next(r for r in data["employees"] if r["employee"] == EMP_A)
    assert a["visits"] == 3 and a["persons"] == 2, "Ноль дважды (разные дни) + Два; дубль тапа не считается"
    assert a["contracts"] == 3            # у Ноля два КД
    assert a["paid"] == 1 and a["promised"] == 1
    assert {o["status"]: o["n"] for o in a["outcomes"]} == {"Оплачен": 1, "Обещает оплатить": 1}
    days = {x["date"]: x["visits"] for x in data["by_day"]}
    assert days[D9] == 1 and days[D2] == 1 and days[T] == 2
    assert len(data["by_day"]) == 30 and data["employees"][0]["by_day"][-1] >= 1
    t = data["totals"]
    assert (t["visits"], t["persons"], t["paid"], t["active"], t["employees"]) == (4, 3, 1, 2, 2)
    assert t["money_paid"] == [{"unit": "₽", "sum": 600000}]     # оба КД Ноля


def test_journal_status_beats_current_snapshot_for_past_days():
    _seed()
    emp = dd.stats_employee_data(db.get_manager(MGR), _rng(), EMP_A, employees=[EMP_A, EMP_B])
    by_day = {(c["date"], c["rep"]): c for c in emp["cases"]}
    assert by_day[(D9, "ST-0")]["outcome"] == "Нет контакта"    # из журнала, не «Оплачен»
    assert by_day[(T, "ST-0")]["outcome"] == "Оплачен" and by_day[(T, "ST-0")]["agreed"] == "да"
    assert emp["cases"][0]["date"] == T, "новые сверху"
    assert emp["screen"] == "emp" and emp["persons"] == 2


def test_empty_journal_status_falls_back_to_snapshot():
    _seed()
    emp = dd.stats_employee_data(db.get_manager(MGR), _rng(), EMP_B, employees=[EMP_A, EMP_B])
    assert emp["cases"][0]["outcome"] == "Рассрочка"


def test_window_excludes_visits_outside_it():
    _seed()
    r = _rng(preset="custom", frm=D2, to=D2)
    data = dd.stats_data(db.get_manager(MGR), r, employees=[EMP_A, EMP_B])
    assert data["totals"]["visits"] == 1 and data["totals"]["persons"] == 1
    assert data["by_day"] == [{"date": D2, "day": int(D2[-2:]), "label": data["by_day"][0]["label"],
                               "weekend": date.fromisoformat(D2).weekday() >= 5, "visits": 1}]


def test_foreign_employee_is_none_and_country_index_is_live():
    _seed()
    assert dd.stats_employee_data(db.get_manager(MGR), _rng(), EMP_FOREIGN,
                                  employees=[EMP_A, EMP_B]) is None
    data = dd.stats_data(db.get_manager(MGR), _rng(), employees=[EMP_A, EMP_B])
    assert all(r["country"] == "tj" for r in data["employees"])
    keys = ("code", "name", "short", "flag", "employees")
    assert [{k: c[k] for k in keys} for c in data["countries"]] == [
        {"code": "tj", "name": "Таджикистан", "short": "Тадж",
         "flag": "🇹🇯", "employees": 2}]
    # У страны теперь свои итоги — плашки сводки слушают фильтр «Тадж/Узб».
    # Команда целиком таджикская, поэтому её свод равен общему.
    assert data["countries"][0]["totals"]["visits"] == data["totals"]["visits"]


def test_person_card_opens_for_a_past_visit_day():
    """Из статистики открывают карточку по выезду 9 дней назад, хотя с тех пор
    к человеку съездили ещё раз: persons_processed того дня его уже не знает."""
    _seed()
    p = dd.person_data(db.get_manager(MGR), D9, EMP_A, "ST-0", employees=[EMP_A, EMP_B])
    assert p is not None and p["client"] == "Клиент Ноль" and p["outcome"] == "Оплачен"
    assert dd.person_data(db.get_manager(MGR), D9, EMP_A, "ST-3", employees=[EMP_A, EMP_B]) is None


def test_public_payload_has_no_rows():
    _seed()
    data = dd._public(dd.stats_data(db.get_manager(MGR), _rng(), employees=[EMP_A, EMP_B]))
    json.dumps(data)                       # sqlite3.Row наружу не утекли
    assert "_entries" not in data["employees"][0]


# --- ручки -------------------------------------------------------------------
def _call(handler, uid, qs="", monkeypatch=None):
    monkeypatch.setattr(web, "_uid", lambda _r: uid)
    req = make_mocked_request("GET", f"/api/manager/x{qs}")
    resp = asyncio.run(handler(req))
    return resp


def _json(handler, uid, qs="", monkeypatch=None):
    return json.loads(_call(handler, uid, qs, monkeypatch).body.decode())


def test_endpoints_need_auth_and_role(monkeypatch):
    _seed()
    for h in (web._manager_stats, web._manager_stats_employee, web._manager_stats_xlsx):
        with pytest.raises(web.web.HTTPUnauthorized):
            _call(h, None, monkeypatch=monkeypatch)
        with pytest.raises(web.web.HTTPForbidden):
            _call(h, STRANGER, monkeypatch=monkeypatch)


def test_stats_endpoint_returns_window_and_envelope(monkeypatch):
    _seed()
    j = _json(web._manager_stats, MGR, "?preset=7d", monkeypatch)
    assert j["ok"] and j["screen"] == "stats" and j["range"]["preset"] == "7d"
    assert j["range"]["to"] == T and j["date"] == T and j["next_date"] is None
    assert set(j["team"]) == {EMP_A, EMP_B}
    assert j["totals"]["visits"] == 3                    # выезд 9 дней назад — за окном
    j2 = _json(web._manager_stats, MGR, f"?from={D9}&to={D9}", monkeypatch)
    assert j2["totals"]["visits"] == 1 and j2["range"]["preset"] == "custom"


def test_bad_dates_are_400(monkeypatch):
    _seed()
    with pytest.raises(web.web.HTTPBadRequest):
        _call(web._manager_stats, MGR, "?from=мусор", monkeypatch=monkeypatch)
    with pytest.raises(web.web.HTTPBadRequest):
        _call(web._manager_stats, MGR, "?preset=7d&shift=x", monkeypatch=monkeypatch)


def test_employee_endpoint_scopes_and_audits(monkeypatch):
    _seed()
    with pytest.raises(web.web.HTTPNotFound):
        _call(web._manager_stats_employee, MGR, f"?employee={EMP_FOREIGN}",
              monkeypatch=monkeypatch)
    j = _json(web._manager_stats_employee, MGR, f"?preset=30d&employee={EMP_A}", monkeypatch)
    assert j["employee"] == EMP_A and len(j["cases"]) == 3
    tail = [dict(r) for r in db.audit_tail(20)]
    row = next(r for r in tail if (r["detail"] or "").startswith("stats_employee "))
    assert "ST-0" in row["detail"] and row["target"] == EMP_A


def test_xlsx_is_a_workbook_with_four_sheets(monkeypatch):
    _seed()
    resp = _call(web._manager_stats_xlsx, MGR, "?preset=30d", monkeypatch=monkeypatch)
    assert resp.body[:2] == b"PK"
    assert "attachment" in resp.headers["Content-Disposition"]
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(resp.body))
    # Листы воронки Collection приехали 15.09.2026 и стоят В КОНЦЕ: руководитель
    # открывает книгу на «Сотрудниках», и порядок прежних листов менять нельзя.
    assert wb.sheetnames == ["Сотрудники", "Республики", "Выезды", "По дням",
                             "Воронка", "Воронка — республики", "Воронка — итого",
                             "Обещания", "Ранжирование"]
    ws = wb["Сотрудники"]
    names = [ws.cell(row=r, column=1).value for r in range(4, ws.max_row + 1)]
    assert EMP_A in names and names[-1] == "Итого"
    visits = wb["Выезды"]
    assert visits.max_row == 1 + 4                          # человек-день × 4
    actions = {r["action"] for r in (dict(x) for x in db.audit_tail(10))}
    assert "digest_export" in actions


def test_owner_sees_everyone(monkeypatch):
    _seed()
    if not ADMIN_ID:
        pytest.skip("ADMIN_ID не задан")
    j = _json(web._manager_stats, ADMIN_ID, "?preset=30d", monkeypatch)
    assert EMP_FOREIGN in j["team"]
