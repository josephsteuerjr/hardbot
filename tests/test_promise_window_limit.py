"""Обещание оплаты — не дальше предела офиса (по умолчанию три дня).

Решение офиса «Худжанд» 11.09.2026: «возможность проставления даты обещания
оплаты, не более чем на три дня + к дню выезда». Смысл — в контроле: обещание
«через месяц» нельзя ни проверить на этой неделе, ни вернуть должника в маршрут
вовремя.

Важное свойство: слишком дальняя дата НЕ роняет визит. Клиент считает любой
не-2xx обрывом связи и хоронит запись очереди целиком — пропала бы вся
карточка, а не одна дата.
"""
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web

ROOT = Path(__file__).resolve().parent.parent
SHELL = (ROOT / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")

UID = 562001
EMP = "Прсрочев П."
CID = "PR-1"
TODAY = date.today()


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id=?", (CID,))
        conn.execute("DELETE FROM visits WHERE contract_id=?", (CID,))
        conn.execute("DELETE FROM settings WHERE key='promise_ahead_days'")
        conn.execute(
            "INSERT INTO clients (contract_id, client, responsible, birth_date, "
            "partner, debt_today, overall_result, country) VALUES (?,?,?,?,?,?,?,?)",
            (CID, "Клиент Обещалкин", EMP, "1990-01-01", "ЗК", "10 000",
             "Не обработан", "Таджикистан"))
        conn.commit()
    db.bind_agent(UID, EMP, approved=True) if hasattr(db, "bind_agent") else None


@pytest.fixture(autouse=True)
def _cleanup():
    _seed()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id=?", (CID,))
        conn.execute("DELETE FROM visits WHERE contract_id=?", (CID,))
        conn.execute("DELETE FROM settings WHERE key='promise_ahead_days'")
        conn.commit()


def _save(promise_iso, monkeypatch, occurred=None):
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP))
    body = {
        "rep": CID, "finish": True, "form_version": web.FORM_PROTOCOL_VERSION,
        "input_keys": ["call_status", "promise_date"],
        "values": {"call_status": "Обещает оплатить", "promise_date": promise_iso},
    }
    if occurred is not None:
        body["occurred_at"] = occurred
    req = make_mocked_request("POST", "/api/save")

    async def _json():
        return body

    req.json = _json
    resp = asyncio.run(web._save(req))
    return json.loads(resp.body.decode())


# --- настройка ---------------------------------------------------------------
def test_default_limit_is_three_days():
    assert db.promise_ahead_days() == 3


def test_office_can_change_or_switch_off_the_limit():
    db.set_setting("promise_ahead_days", "5")
    assert db.promise_ahead_days() == 5
    db.set_setting("promise_ahead_days", "0")
    assert db.promise_ahead_days() == 0, "ноль — «без предела»"
    db.set_setting("promise_ahead_days", "ерунда")
    assert db.promise_ahead_days() == 3


# --- сервер ------------------------------------------------------------------
def test_promise_within_the_window_is_saved(monkeypatch):
    good = (TODAY + timedelta(days=3)).isoformat()
    out = _save(good, monkeypatch)
    assert out["ok"] and "rejected" not in out
    assert db.get_client(CID)["promise_date"] == good


def test_promise_beyond_the_window_is_dropped_but_visit_survives(monkeypatch):
    far = (TODAY + timedelta(days=30)).isoformat()
    out = _save(far, monkeypatch)
    assert out["ok"], "визит обязан сохраниться"
    assert out["rejected"]["promise_date"] == "too_far:3"
    assert not (db.get_client(CID)["promise_date"] or ""), "дата не записана"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM visits WHERE contract_id=?",
                            (CID,)).fetchone()[0] == 1


def test_window_counts_from_the_day_of_the_visit_not_of_delivery(monkeypatch):
    """Выезд позавчерашний, доехал сегодня: предел считается от дня выезда."""
    two_days_ago = TODAY - timedelta(days=2)
    occurred = int(
        __import__("datetime").datetime.combine(
            two_days_ago, __import__("datetime").time(12, 0)).timestamp() * 1000)
    ok_then = (two_days_ago + timedelta(days=3)).isoformat()
    out = _save(ok_then, monkeypatch, occurred=occurred)
    assert "rejected" not in out, "в день выезда эта дата была в пределах"


def test_limit_off_lets_any_date_through(monkeypatch):
    db.set_setting("promise_ahead_days", "0")
    far = (TODAY + timedelta(days=60)).isoformat()
    out = _save(far, monkeypatch)
    assert "rejected" not in out and db.get_client(CID)["promise_date"] == far


# --- оболочка ----------------------------------------------------------------
def test_shell_knows_the_limit_from_meta():
    assert '"promiseMaxDays": db.promise_ahead_days()' in \
        (ROOT / "server" / "web.py").read_text(encoding="utf-8")
    assert "function promiseMaxDays()" in SHELL
    assert "function promiseMaxDate()" in SHELL


def test_wheel_stops_at_the_limit():
    body = SHELL[SHELL.index("function openDateWheel("):SHELL.index("function wheelScrollTo(")]
    assert 'WHEEL={key,months:[],max:(key==="promise_date")?promiseMaxDate():""}' in body, \
        "предел действует только на дату обещания"
    assert "Через 3 дня" in body, "быстрые кнопки тоже в пределах"
    assert "dim=Math.min(dim,+WHEEL.max.slice(8,10))" in body, "дни за пределом остались"


def test_rejected_field_is_shown_to_the_person():
    """Молчание означало бы, что человек уехал с адреса уверенным в записи."""
    assert "Дата обещания не принята" in SHELL
