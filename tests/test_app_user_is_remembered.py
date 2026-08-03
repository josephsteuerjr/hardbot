"""Кто открыл приложение — должен быть опознаваем в пульте.

03.08.2026: `touch_guest` звали только обработчики бота, а миниапп открывается
кнопкой меню — то есть штатный путь мимо бота. Два выездника выбрали в приложении
чужие фамилии, и в списке они висели голыми числами без имени и ника: понять, кто
это, можно было только спросив людей в чате.
"""

import json

import db
import web


class _Req:
    def __init__(self, headers):
        self.headers = headers


def _init_data(uid, username="utkirov", first="Фирдавс", last="Уткиров"):
    return json.dumps({"user": {"id": uid, "username": username,
                                "first_name": first, "last_name": last}})


def _patch(monkeypatch, payload):
    monkeypatch.setattr(web, "validate_init_data",
                        lambda raw, token: json.loads(raw) if raw else None)
    return _Req({"X-Init-Data": payload})


def _guest(uid):
    return next((g for g in db.guests() if g["telegram_id"] == uid), None)


def test_app_entry_records_name_and_username(monkeypatch):
    db.init_db()
    uid = 990001
    web._remember_from_init(_patch(monkeypatch, _init_data(uid)))
    g = _guest(uid)
    assert g is not None, "открывший приложение остался неизвестным"
    assert g["username"] == "utkirov"
    assert g["name"] == "Фирдавс Уткиров"


def test_missing_or_broken_init_data_is_silent(monkeypatch):
    """Вход в приложение важнее знакомства: сбой не должен ронять авторизацию."""
    db.init_db()
    for payload in ("", "не json", json.dumps({"user": "строка"}),
                    json.dumps({"user": {"id": 0}})):
        web._remember_from_init(_patch(monkeypatch, payload))   # не должно бросить


def test_db_failure_does_not_break_app_entry(monkeypatch):
    db.init_db()

    def boom(*_a, **_k):
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr(db, "touch_guest", boom)
    web._remember_from_init(_patch(monkeypatch, _init_data(990002)))


def test_existing_name_is_not_wiped_by_a_later_visit(monkeypatch):
    """COALESCE в touch_guest: заход без ника не должен стирать известный ник."""
    db.init_db()
    uid = 990003
    web._remember_from_init(_patch(monkeypatch, _init_data(uid, username="karim")))
    web._remember_from_init(_patch(monkeypatch,
                                   _init_data(uid, username=None, first=None, last=None)))
    g = _guest(uid)
    assert g["username"] == "karim"
