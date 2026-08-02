"""Самовыбор без одобрения, но один сотрудник — одно устройство.

Правило владельца от 02.08.2026: выездник в поле не должен ждать аппрува, но
второй телефон на то же ФИО разрешает только владелец. Второй аккаунт почти
всегда означает либо разъехавшуюся по двум привязкам активность (карточка
дайджеста показывала нули отработавшему), либо постороннего на чужом ФИО.
"""
import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web


@pytest.fixture(autouse=True)
def _clean():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM agent_bind")
        conn.commit()


def _bind(uid, employee, kind="agent"):
    req = make_mocked_request("POST", "/api/bind")

    async def _j():
        return {"employee": employee, "kind": kind}

    req.json = _j
    req.app["bot"] = None
    orig = web._uid
    web._uid = lambda _r: uid
    try:
        return json.loads(asyncio.run(web._bind(req)).body.decode())
    finally:
        web._uid = orig


def test_first_device_works_immediately():
    """Аппрув больше не нужен: человек выбрал себя и сразу работает."""
    r = _bind(1001, "Джумаев Ш.")
    assert r["ok"] and not r.get("pending")
    assert db.get_binding(1001) == "Джумаев Ш."


def test_second_device_on_the_same_employee_waits_for_the_owner():
    _bind(1001, "Джумаев Ш.")
    r = _bind(2002, "Джумаев Ш.")
    assert r.get("pending") and r.get("taken")
    assert db.get_binding(2002) is None, "второе устройство получило доступ без разрешения"
    assert db.get_binding(1001) == "Джумаев Ш.", "первое устройство не должно пострадать"


def test_owner_can_allow_the_second_device():
    """«Если мной не разрешено иное» — разрешение даётся кнопкой под заявкой."""
    _bind(1001, "Джумаев Ш.")
    _bind(2002, "Джумаев Ш.")
    assert db.approve_binding(2002) == "Джумаев Ш."
    assert db.get_binding(2002) == "Джумаев Ш."


def test_rebinding_the_same_device_is_not_blocked_by_itself():
    """Свой же telegram_id не должен считаться «занявшим» ФИО."""
    _bind(1001, "Джумаев Ш.")
    r = _bind(1001, "Джумаев Ш.")
    assert not r.get("pending")


def test_a_different_employee_is_free_to_take():
    _bind(1001, "Джумаев Ш.")
    r = _bind(3003, "Хасанов А.")
    assert r["ok"] and not r.get("pending")
    assert db.get_binding(3003) == "Хасанов А."


def test_pending_request_does_not_occupy_the_employee():
    """Неодобренная заявка никого не занимает: иначе посторонний одним запросом
    заблокировал бы настоящему выезднику вход."""
    _bind(2002, "Хасанов А.")          # первый — сразу одобрен
    db.bind_agent(4004, "Каримов К.", "agent", approved=False)
    r = _bind(5005, "Каримов К.")
    assert not r.get("pending"), "чужая неодобренная заявка заблокировала вход"


def test_a_wrong_pick_can_be_corrected_while_pending():
    """Промах пальцем при онбординге запирал навсегда: INSERT OR IGNORE молча
    ничего не делал (telegram_id — PRIMARY KEY), приложение показывало «Нет
    доступа», выхода из экрана не было. Неодобренную заявку переписать можно."""
    _bind(1001, "Джумаев Ш.")                 # ФИО занято
    _bind(6006, "Джумаев Ш.")                 # промахнулся — заявка висит
    assert db.pending_binding(6006)["employee"] == "Джумаев Ш."
    r = _bind(6006, "Хасанов А.")             # выбрал правильного, тот свободен
    assert not r.get("pending")
    assert db.get_binding(6006) == "Хасанов А."


def test_an_approved_binding_cannot_be_switched_by_self_selection():
    """Одобренную привязку самовыбором не сменить — только через пульт."""
    _bind(7007, "Хасанов А.")
    assert db.get_binding(7007) == "Хасанов А."
    _bind(7007, "Джумаев Ш.")
    assert db.get_binding(7007) == "Хасанов А."


def test_reminders_go_only_to_an_approved_device():
    """ФИО, номер договора и адрес должника не должны уходить на аккаунт,
    которому система только что отказала в доступе."""
    _bind(1001, "Джумаев Ш.")
    _bind(2002, "Джумаев Ш.")                 # второй телефон, не одобрен
    assert db.telegram_for_employee("Джумаев Ш.") == 1001
    db.approve_binding(2002)
    assert db.telegram_for_employee("Джумаев Ш.") in (1001, 2002)
