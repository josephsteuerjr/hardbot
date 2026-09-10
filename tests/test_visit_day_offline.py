"""День ВЫЕЗДА, а не день досыла.

Вопрос Альберта 10.09.2026: сотрудник из Худжанда откатал адреса во вторник
8.09, уехал на выходные в горы на границе с Киргизией, где глушат интернет, и в
четверг визиты подгрузились ЧЕТВЕРГОМ. Для руководителя это выезды, которых во
вторник не было, и всплеск в четверг.

Телефон присылает время события (occurred_at). Проверяем:
  • визит ложится в свой день — и строка visits, и visit_date, и снимок;
  • клиенту доверяем ограниченно: будущее и глубокая старина → сегодня;
  • старая оболочка без occurred_at работает как раньше;
  • доставка ПОЗАВЧЕРАШНЕГО визита не откатывает более свежий;
  • карточка честно говорит, что выезд не сегодняшний.
"""
import asyncio
from datetime import date, datetime, timedelta

from aiohttp import web as aiohttp_web
from aiohttp.test_utils import make_mocked_request

import db
import notify
import web as webmod

EMP = "Опоздалов Сотрудник"
TODAY = date.today()
CID = "OFF-1"


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
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'OFF-%'")
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'OFF-%'")
        conn.execute(
            "INSERT OR REPLACE INTO clients (contract_id, client, responsible, partner) "
            "VALUES (?,?,?,?)", (CID, "Должников Д.", EMP, "ЗК"))
        conn.commit()


def _save(op_id, monkeypatch, occurred_at=None, status="В работе"):
    monkeypatch.setattr(webmod, "_auth", lambda _r: (7001, EMP))
    app = aiohttp_web.Application()
    app["bot"] = FakeBot()
    request = make_mocked_request("POST", "/api/save", app=app)

    async def _json():
        body = {"rep": CID, "finish": True, "op_id": op_id,
                "values": {"call_status": status},
                "form_version": webmod.FORM_PROTOCOL_VERSION,
                "input_keys": ["call_status"], "photos_pending": 0}
        if occurred_at is not None:
            body["occurred_at"] = occurred_at
        return body

    request.json = _json

    async def _run():
        result = await webmod._save(request)
        await webmod.wait_visit_notify()
        return result

    return asyncio.run(_run())


def _ms(day: date) -> float:
    """Полдень указанного дня в миллисекундах эпохи (как Date.now() телефона)."""
    return datetime.combine(day, datetime.min.time()).timestamp() * 1000 + 12 * 3600 * 1000


def _visit_days():
    with db.connect() as conn:
        return [r["created_at"] for r in conn.execute(
            "SELECT created_at FROM visits WHERE contract_id=? ORDER BY id", (CID,))]


def test_offline_visit_lands_on_its_own_day(monkeypatch):
    """Выезд, отработанный позавчера, ложится позавчерашним днём."""
    _seed()
    two_days_ago = TODAY - timedelta(days=2)
    _save("off-1", monkeypatch, occurred_at=_ms(two_days_ago))
    assert _visit_days() == [two_days_ago.isoformat()]
    row = db.get_client(CID)
    assert row["visit_date"] == two_days_ago.isoformat()
    assert row["bot_visit_at"] == two_days_ago.isoformat()


def test_online_save_is_today(monkeypatch):
    """Обычное сохранение при связи — сегодняшним днём."""
    _seed()
    _save("off-2", monkeypatch, occurred_at=_ms(TODAY))
    assert _visit_days() == [TODAY.isoformat()]


def test_old_shell_without_occurred_at(monkeypatch):
    """Оболочка до v41 поля не шлёт — поведение прежнее, день доставки."""
    _seed()
    _save("off-3", monkeypatch)
    assert _visit_days() == [TODAY.isoformat()]


def test_future_and_ancient_are_ignored(monkeypatch):
    """Сбитые часы телефона не датируют визит будущим и не уводят его в прошлый месяц."""
    _seed()
    _save("off-4", monkeypatch, occurred_at=_ms(TODAY + timedelta(days=3)))
    _save("off-5", monkeypatch, occurred_at=_ms(TODAY - timedelta(days=90)))
    assert _visit_days() == [TODAY.isoformat(), TODAY.isoformat()]


def test_late_delivery_does_not_roll_back_fresher_visit(monkeypatch):
    """Сегодняшний выезд уже записан; доставка позавчерашнего его не отменяет."""
    _seed()
    _save("off-6", monkeypatch, occurred_at=_ms(TODAY), status="Обещает оплатить")
    _save("off-7", monkeypatch,
          occurred_at=_ms(TODAY - timedelta(days=2)), status="Нет контакта")
    row = db.get_client(CID)
    assert row["visit_date"] == TODAY.isoformat(), "дата последнего выезда не откатывается"
    snap = db.bot_visit_snapshot(row) or {}
    assert snap.get("call_status") == "Обещает оплатить", "снимок остаётся от свежего выезда"
    # История при этом хранит ОБА выезда — это разные события.
    assert sorted(_visit_days()) == sorted(
        [TODAY.isoformat(), (TODAY - timedelta(days=2)).isoformat()])


def test_card_says_the_visit_is_not_from_today():
    """Карточка называет день выезда, если он не сегодняшний."""
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    line = webmod._late_visit_line(yesterday)
    assert line and (TODAY - timedelta(days=1)).strftime("%d.%m") in line
    assert webmod._late_visit_line(TODAY.isoformat()) is None
    assert webmod._late_visit_line(None) is None
    text = notify.visit_report_text({"responsible": EMP, "client": "Д.",
                                     "contract_id": CID, "call_status": "",
                                     "agreed_payment": "", "city": "", "address": "",
                                     "visit_comment": "", "call_comment": "",
                                     "tips": "", "forecast_sum": "", "promise_date": ""},
                                    late_line=line)
    assert line in text
