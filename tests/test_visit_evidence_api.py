"""Следы выезда доезжают до сервера и превращаются в вердикт на визите.

Оболочка v54 шлёт вместе с «Сохранить выезд» сессию карточки, точки за время
работы с ней, точку открытия и время на экране; черновик раз в 20 с несёт
свежую точку (у неё есть серверное время приёма — свидетельство связи).
Проверяем путь целиком: /api/save → visit_presence → visits.evidence →
сводка руководителя, случаи, книга, карточка, строка в рассылке.
"""
import asyncio
import io
from datetime import date

import pytest
from aiohttp import web as aiohttp_web
from aiohttp.test_utils import make_mocked_request
from openpyxl import load_workbook

import db
import digest_data as dd
import evidence
import web as webmod

EMP = "Следов Сотрудник"
UID = 7301
MGR = 573001
CID_NEAR, CID_FAR, CID_CITY = "EVD-1", "EVD-2", "EVD-3"
ADDR = (40.2800, 69.6200)
T = date.today().isoformat()


class FakeBot:
    def __init__(self):
        self.msgs = []

    async def send_message(self, chat_id, text):
        self.msgs.append((chat_id, text))

    async def send_media_group(self, chat_id, media):
        self.msgs.append((chat_id, "album"))

    async def send_photo(self, chat_id, photo, caption=None):
        self.msgs.append((chat_id, caption or "photo"))


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'EVD-%'")
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'EVD-%'")
        conn.execute("DELETE FROM visit_presence WHERE contract_id LIKE 'EVD-%'")
        conn.execute("DELETE FROM addr_candidates WHERE contract_id LIKE 'EVD-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM agent_bind WHERE telegram_id=?", (UID,))
        for cid, client, prec in ((CID_NEAR, "Ближний Б.", "street"),
                                  (CID_FAR, "Дальний Д.", "street"),
                                  (CID_CITY, "Городской Г.", "city")):
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, partner, "
                "debt_today, overall_result, country, geo_lat, geo_lon, geo_src, "
                "geo_precision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, client, EMP, "1990-01-01", "ЗК", "10 000", "Не обработан",
                 "Таджикистан", ADDR[0], ADDR[1], "osm", prec))
        conn.execute("INSERT INTO agent_bind (telegram_id, employee, kind, approved, "
                     "created_at) VALUES (?,?,?,?,?)", (UID, EMP, "agent", 1, T))
        conn.commit()
    db.set_manager(MGR, "Рук", [], [EMP])
    db.reset_role_caches()


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    _seed()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'EVD-%'")
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'EVD-%'")
        conn.execute("DELETE FROM visit_presence WHERE contract_id LIKE 'EVD-%'")
        conn.execute("DELETE FROM addr_candidates WHERE contract_id LIKE 'EVD-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM agent_bind WHERE telegram_id=?", (UID,))
        conn.commit()
    db.reset_role_caches()


def _post(body, monkeypatch, bot=None):
    monkeypatch.setattr(webmod, "_auth", lambda _r: (UID, EMP))
    app = aiohttp_web.Application()
    if bot is not None:
        app["bot"] = bot
    request = make_mocked_request("POST", "/api/save", app=app)

    async def _json():
        return body
    request.json = _json

    async def _run():
        result = await webmod._save(request)
        await webmod.wait_visit_notify()
        return result
    return asyncio.run(_run())


def _now_ms():
    return webmod._now_ms()


def _finish_body(cid, op_id, presence, **extra):
    now = _now_ms()
    body = {"rep": cid, "finish": True, "op_id": op_id,
            "values": {"call_status": "В работе", "who_at_address": "По адресу проживает клиент"},
            "form_version": webmod.FORM_PROTOCOL_VERSION,
            "input_keys": ["call_status", "who_at_address"], "photos_pending": 0,
            "occurred_at": now, "opened_at": now - 15 * 60_000,
            "session_id": f"sess-{op_id}", "presence": presence,
            "online_at_open": 1, "foreground_ms": 9 * 60_000}
    body.update(extra)
    return body


def _near(dt_min, lat=ADDR[0], lon=ADDR[1], acc=15, k="sample"):
    return {"ts": _now_ms() - dt_min * 60_000, "lat": lat, "lon": lon, "acc": acc,
            "on": 1, "vis": 1, "k": k}


def _visit(cid):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM visits WHERE contract_id=? ORDER BY id DESC "
                            "LIMIT 1", (cid,)).fetchone()


# --- приём ---------------------------------------------------------------------
def test_presence_and_verdict_are_stored_with_the_visit(monkeypatch):
    bot = FakeBot()
    body = _finish_body(CID_NEAR, "ev-near-1",
                        [_near(14, k="open"), _near(10), _near(5)],
                        geo={"lat": ADDR[0], "lon": ADDR[1], "acc": 12})
    _post(body, monkeypatch, bot=bot)
    row = _visit(CID_NEAR)
    assert row["session_id"] == "sess-ev-near-1"
    assert row["evidence_level"] == "confirmed"
    ev = evidence.parse(row["evidence"])
    assert ev["near_min"] >= 14 and ev["online_save"] is True
    with db.connect() as conn:
        kinds = sorted(r[0] for r in conn.execute(
            "SELECT kind FROM visit_presence WHERE session_id='sess-ev-near-1'"))
    assert kinds == ["open", "sample", "sample", "save"], kinds
    # руководителю — одной строкой, по всем следам
    texts = [t for _cid, t in bot.msgs if isinstance(t, str)]
    assert any("🛡" in t and "На адресе" in t for t in texts), texts


def test_repeat_from_queue_does_not_duplicate_presence(monkeypatch):
    # Очередь повторяет ТО ЖЕ тело — с теми же метками времени. Тело собирается
    # один раз: если собрать его заново, метки сдвинутся на секунду (на медленной
    # машине между тестами проходит больше секунды) и дубли будут честными.
    body = _finish_body(CID_NEAR, "ev-near-repeat",
                        [_near(14, k="open"), _near(10), _near(5)],
                        geo={"lat": ADDR[0], "lon": ADDR[1], "acc": 12})
    _post(body, monkeypatch)
    _post(body, monkeypatch)
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM visit_presence WHERE session_id="
                         "'sess-ev-near-repeat'").fetchone()[0]
    assert n == 4


def test_far_visit_gets_far_verdict(monkeypatch):
    far = (40.35, 69.70)
    body = _finish_body(CID_FAR, "ev-far-1",
                        [_near(10, lat=far[0], lon=far[1], k="open"),
                         _near(2, lat=far[0], lon=far[1])],
                        geo={"lat": far[0], "lon": far[1], "acc": 10})
    _post(body, monkeypatch)
    row = _visit(CID_FAR)
    assert row["evidence_level"] == "far"
    assert row["geo_far"] == 1, "старый гео-контроль и новый вердикт согласны"
    # адрес по такому выезду НЕ переучивается
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM addr_candidates WHERE contract_id=?",
                            (CID_FAR,)).fetchone()[0] == 0


def test_saved_elsewhere_after_a_real_visit_is_counted_separately(monkeypatch):
    """Был у адреса 10 минут, карточку сохранил в 4 км — в сводке это своя
    строка «заполнено не на адресе», а не «подтверждён» без оговорок."""
    away = (40.31, 69.65)
    body = _finish_body(CID_NEAR, "ev-away-1",
                        [_near(30, k="open"), _near(25), _near(20)],
                        geo={"lat": away[0], "lon": away[1], "acc": 10})
    _post(body, monkeypatch)
    row = _visit(CID_NEAR)
    ev = evidence.parse(row["evidence"])
    assert row["evidence_level"] == "confirmed" and ev["saved_away"] is True
    mgr = db.get_manager(MGR)
    cases = dd.cases_data(mgr, dd.stats_range("today", 0), "saved_away", "", employees=[EMP])
    assert [c["rep"] for c in cases["cases"]] == [CID_NEAR]
    assert cases["cases"][0]["saved_away"] is True
    stats = dd.stats_data(mgr, dd.stats_range("today", 0), employees=[EMP])
    assert stats["totals"]["saved_away"] == 1
    # книга: колонка «Где заполнен»
    blob = dd.stats_xlsx(mgr, dd.stats_range("today", 0), employees=[EMP])
    ws2 = load_workbook(io.BytesIO(blob))["Выезды"]
    head2 = {ws2.cell(row=1, column=c).value: c for c in range(1, ws2.max_column + 1)}
    where = {ws2.cell(row=r, column=head2["Клиент"]).value:
             ws2.cell(row=r, column=head2["Где заполнен"]).value
             for r in range(2, ws2.max_row + 1)}
    assert where["Ближний Б."] == "не на адресе"


def test_city_level_address_is_reported_as_unverifiable(monkeypatch):
    body = _finish_body(CID_CITY, "ev-city-1", [_near(3)],
                        geo={"lat": ADDR[0], "lon": ADDR[1], "acc": 10})
    _post(body, monkeypatch)
    row = _visit(CID_CITY)
    assert row["evidence_level"] == "coarse"
    assert "до города" in evidence.parse(row["evidence"])["note"]


def test_old_shell_without_traces_still_gets_a_verdict(monkeypatch):
    """Оболочка до v54: только точка закрытия — по ней и судим."""
    now = _now_ms()
    body = {"rep": CID_NEAR, "finish": True, "op_id": "ev-old-1",
            "values": {"call_status": "Нет контакта"},
            "form_version": webmod.FORM_PROTOCOL_VERSION, "input_keys": ["call_status"],
            "occurred_at": now, "opened_at": now - 60_000,
            "geo": {"lat": ADDR[0], "lon": ADDR[1], "acc": 20}}
    _post(body, monkeypatch)
    row = _visit(CID_NEAR)
    assert row["session_id"] == "srv-ev-old-1"
    assert row["evidence_level"] == "confirmed"


def test_no_geo_at_all_is_nogeo(monkeypatch):
    now = _now_ms()
    body = {"rep": CID_NEAR, "finish": True, "op_id": "ev-nogeo-1",
            "values": {"call_status": "Нет контакта"},
            "form_version": webmod.FORM_PROTOCOL_VERSION, "input_keys": ["call_status"],
            "occurred_at": now, "session_id": "sess-nogeo",
            "presence": [{"ts": now - 5000, "on": 0}]}
    response = _post(body, monkeypatch)
    assert response.status == 200
    row = _visit(CID_NEAR)
    assert row["evidence_level"] == "nogeo"
    assert row["visit_lat"] is None and row["visit_lon"] is None
    assert row["dist_m"] is None and row["geo_far"] is None


def test_draft_point_is_served_with_server_time(monkeypatch):
    body = {"rep": CID_NEAR, "finish": False, "values": {"visit_comment": "иду"},
            "form_version": webmod.FORM_PROTOCOL_VERSION, "input_keys": ["visit_comment"],
            "session_id": "sess-draft-1",
            "geo": {"lat": ADDR[0], "lon": ADDR[1], "acc": 9, "ts": _now_ms()}}
    _post(body, monkeypatch)
    rows = db.presence_for_session("sess-draft-1")
    assert len(rows) == 1 and rows[0]["k"] == "draft" and rows[0]["served"]
    # и этот след попадает в вердикт как «сервер видел телефон на месте»
    fin = _finish_body(CID_NEAR, "ev-draft-fin", [], session_id="sess-draft-1")
    _post(fin, monkeypatch)
    ev = evidence.parse(_visit(CID_NEAR)["evidence"])
    assert ev["served"] == 1 and ev["level"] == "confirmed"


# --- сводка, случаи, книга, карточка ---------------------------------------------
def _rng():
    return dd.stats_range("today", 0)


def test_stats_count_verdicts_per_employee_and_in_totals():
    data = dd.stats_data(db.get_manager(MGR), _rng(), employees=[EMP])
    row = next(r for r in data["employees"] if r["employee"] == EMP)
    # человеко-дни: три должника → три случая с ПОСЛЕДНИМ вердиктом каждого
    assert row["confirmed"] >= 1 and row["far"] == 1 and row["unverified"] >= 1
    assert data["totals"]["far"] == 1
    assert data["totals"]["confirmed"] == row["confirmed"]


def test_cases_can_be_filtered_by_verdict():
    mgr = db.get_manager(MGR)
    far = dd.cases_data(mgr, _rng(), "far", "", employees=[EMP])
    assert [c["rep"] for c in far["cases"]] == [CID_FAR]
    assert far["cases"][0]["evidence_level"] == "far"
    assert "не был" in far["cases"][0]["evidence_note"]
    unverified = dd.cases_data(mgr, _rng(), "unverified", "", employees=[EMP])
    assert CID_CITY in [c["rep"] for c in unverified["cases"]]


def test_series_are_flagged_from_the_days_facts():
    """Три сохранения за десять минут — «пачка», и все три помечены."""
    mgr = db.get_manager(MGR)
    series = dd.cases_data(mgr, _rng(), "series", "", employees=[EMP])
    assert series["total"] >= 3
    assert all("пачкой" in c["series"] for c in series["cases"])


def test_book_carries_verdict_columns():
    blob = dd.stats_xlsx(db.get_manager(MGR), _rng(), employees=[EMP])
    wb = load_workbook(io.BytesIO(blob))
    ws = wb["Сотрудники"]
    head = {ws.cell(row=4, column=c).value: c for c in range(1, ws.max_column + 1)}
    assert "Подтверждено на адресе" in head and "У адреса не был" in head
    assert ws.cell(row=5, column=head["У адреса не был"]).value == 1
    ws2 = wb["Выезды"]
    head2 = {ws2.cell(row=1, column=c).value: c for c in range(1, ws2.max_column + 1)}
    verdicts = {ws2.cell(row=r, column=head2["Клиент"]).value:
                ws2.cell(row=r, column=head2["Подтверждение"]).value
                for r in range(2, ws2.max_row + 1)}
    assert verdicts["Дальний Д."] == "у адреса не был"
    assert verdicts["Городской Г."].startswith("адрес неточный")


def test_person_card_explains_the_verdict():
    card = dd.person_data(db.get_manager(MGR), T, EMP, CID_FAR, employees=[EMP])
    ev = card["evidence"]
    assert ev["level"] == "far" and ev["emoji"] == "⚠️"
    assert any("ближе всего" in r for r in ev["rows"])


def test_new_tables_are_declared_for_the_limited_reset():
    import limited_test_reset as ltr
    assert {"visit_presence", "addr_candidates"} <= ltr.RESET_TABLES
    assert "visit_presence" in ltr.DELETE_ORDER and "addr_candidates" in ltr.DELETE_ORDER
