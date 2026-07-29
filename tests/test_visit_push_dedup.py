"""Дедуп УВЕДОМЛЕНИЙ по закрытому выезду (жалоба Василия 21.07: «почему 3 отбивки
приходит по одному клиенту? Иногда 2 иногда 3»).

op_id гасит только повтор из оффлайн-очереди — тот же ключ. Повторное нажатие
«Сохранить выезд» рождает НОВЫЙ op_id, поэтому раньше каждое нажатие давало отдельную
отбивку руководителю: в живой базе так набралось 149 лишних записей из 516, рекорд —
34 по одному договору за день.

Контракт: история визитов пишется как раньше (данные не режем), а уведомления уходят
только на ПЕРВОЕ закрытие визита по договору за день.
"""
import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

from aiohttp import web as aiohttp_web
from aiohttp.test_utils import make_mocked_request

import db
import web as webmod

TODAY = date.today().isoformat()
EMP = "Дедупов Сотрудник"
MGR = 770101


class FakeBot:
    """Считаем отдельно личку руководителю: параллельно уходит ещё пост в общую
    группу (_notify_group), и он к дедупу отбивок отношения не имеет."""

    def __init__(self):
        self.msgs = []

    def to_manager(self):
        return [m for m in self.msgs if m[0] == MGR]

    async def send_message(self, chat_id, text):
        self.msgs.append((chat_id, text))

    async def send_media_group(self, chat_id, media):
        self.msgs.append((chat_id, "album"))

    async def send_photo(self, chat_id, photo, caption=None):
        self.msgs.append((chat_id, caption or "photo"))


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'DD-%'")
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'DD-%'")
        conn.commit()
    for cid, name in (("DD-1", "Первый Д."), ("DD-2", "Второй Д.")):
        with db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO clients (contract_id, client, responsible, partner) "
                "VALUES (?,?,?,?)", (cid, name, EMP, "ЗК"))
            conn.commit()
    db.set_manager(MGR, "Дедуп", [], [EMP])
    db.set_setting("visit_push", "1")


def _finish(rep, op_id, bot, monkeypatch, values=None):
    monkeypatch.setattr(webmod, "_auth", lambda _r: (5501, EMP))
    app = aiohttp_web.Application()
    app["bot"] = bot
    request = make_mocked_request("POST", "/api/save", app=app)

    async def _json():
        submitted = values or {"call_status": "В работе"}
        return {
            "rep": rep,
            "finish": True,
            "op_id": op_id,
            "values": submitted,
            "form_version": webmod.FORM_PROTOCOL_VERSION,
            "input_keys": list(submitted),
        }

    request.json = _json
    return asyncio.run(webmod._save(request))


def test_repeat_taps_write_history_but_notify_once(monkeypatch):
    _seed()
    bot = FakeBot()
    for op in ("dd-tap-1", "dd-tap-2", "dd-tap-3"):     # три нажатия, три разных op_id
        _finish("DD-1", op, bot, monkeypatch)
    sent = bot.to_manager()
    assert len(sent) == 1, f"ожидали одну отбивку, пришло {len(sent)}"
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM visits WHERE contract_id = 'DD-1'").fetchone()[0]
    assert rows == 3, "историю визитов не режем — глушим только рассылку"
    db.delete_manager(MGR)


def test_offline_queue_retry_still_deduped(monkeypatch):
    """Прежний контракт не сломан: повтор с ТЕМ ЖЕ op_id тоже даёт одну отбивку."""
    _seed()
    bot = FakeBot()
    _finish("DD-1", "dd-same", bot, monkeypatch)
    _finish("DD-1", "dd-same", bot, monkeypatch)
    assert len(bot.to_manager()) == 1
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM visits WHERE contract_id = 'DD-1'").fetchone()[0]
    assert rows == 1, "тот же op_id не должен плодить строки"
    db.delete_manager(MGR)


def test_other_debtor_same_day_still_notifies(monkeypatch):
    """Глушим повтор по ОДНОМУ должнику, а не рабочий день целиком."""
    _seed()
    bot = FakeBot()
    _finish("DD-1", "dd-a", bot, monkeypatch)
    _finish("DD-2", "dd-b", bot, monkeypatch)
    assert len(bot.to_manager()) == 2
    db.delete_manager(MGR)


def test_yesterday_visit_does_not_mute_today(monkeypatch):
    """Повторный выезд к тому же должнику в другой день — отбивка уходит."""
    _seed()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO visits (contract_id, agent_id, created_at, payload, op_id) "
            "VALUES (?,?,?,?,?)", ("DD-1", 5501, yesterday, "вчерашний", "dd-yesterday"))
        conn.commit()
    assert db.same_visit_logged_today("DD-1", 5501, "что угодно") is False
    bot = FakeBot()
    _finish("DD-1", "dd-today", bot, monkeypatch)
    assert len(bot.to_manager()) == 1
    db.delete_manager(MGR)


def test_second_real_visit_same_day_still_notifies(monkeypatch):
    """Утром «Нет контакта», вечером «Обещает оплатить» — это ДВА выезда, и второй
    руководитель обязан увидеть. Дедуп по самому факту строки за день его глушил,
    а в беседу отчётов повторов не бывает вовсе."""
    _seed()
    bot = FakeBot()
    _finish("DD-1", "dd-morning", bot, monkeypatch, values={"call_status": "Нет контакта"})
    _finish("DD-1", "dd-evening", bot, monkeypatch,
            values={"call_status": "В работе", "visit_comment": "застал дома"})
    sent = bot.to_manager()
    assert len(sent) == 2, f"второй выезд с другим исходом не дошёл: {len(sent)}"
    assert "Нет контакта" in sent[0][1] and "В работе" in sent[1][1]
    db.delete_manager(MGR)


def test_reset_visits_does_not_mute_next_visit(monkeypatch):
    """Сброс визитов из пульта — новая сессия. Строки visits переживают сброс, но
    первый выезд после него не повтор и должен дать отбивку."""
    _seed()
    bot = FakeBot()
    _finish("DD-1", "dd-before-reset", bot, monkeypatch)
    assert len(bot.to_manager()) == 1
    db.reset_visits()
    bot2 = FakeBot()
    _finish("DD-1", "dd-after-reset", bot2, monkeypatch)
    assert len(bot2.to_manager()) == 1, "после сброса отбивка пропала"
    db.delete_manager(MGR)
