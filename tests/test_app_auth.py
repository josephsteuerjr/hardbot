"""Авторизация автономного PWA: одноразовая ссылка → девайс-токен (30 дн) → отзыв.

Схема: бот выдаёт одноразовый код (app_links, окно активации 7 дн); при первом
открытии код обменивается на девайс-токен (app_tokens). Повтор кода — отказ
(одноразовость), просроченный токен не пускает, отзыв гасит доступ. Ключевая
гарантия UX: выдача НОВОЙ ссылки (сообщение боту) не отзывает живой токен —
случайное сообщение боту не разлогинивает телефон; отзыв — только redeem новой
ссылки (одно активное устройство) или явный revoke.
"""
from datetime import datetime, timedelta

import db


def test_link_redeem_once():
    db.init_db()
    code = db.create_app_link(501)
    tok = db.redeem_app_link(code)
    assert tok and db.uid_for_app_token(tok) == 501
    assert db.redeem_app_link(code) is None          # одноразовость: второй раз — отказ
    assert db.uid_for_app_token(tok) == 501          # но выданный токен жив


def test_unknown_and_empty_code():
    db.init_db()
    assert db.redeem_app_link("nope") is None
    assert db.redeem_app_link("") is None
    assert db.uid_for_app_token("") is None


def test_new_link_does_not_kill_token():
    """Сообщение боту (новая ссылка) НЕ разлогинивает уже вошедшее устройство."""
    db.init_db()
    tok = db.redeem_app_link(db.create_app_link(502))
    db.create_app_link(502)                          # юзер написал боту ещё раз
    assert db.uid_for_app_token(tok) == 502          # телефон продолжает работать


def test_new_link_invalidates_previous_unused():
    """Живая — только последняя неиспользованная ссылка юзера."""
    db.init_db()
    old = db.create_app_link(503)
    db.create_app_link(503)
    assert db.redeem_app_link(old) is None


def test_multiple_devices_coexist():
    """Вход по новой ссылке НЕ гасит прежний токен: у выездника может быть несколько
    устройств/браузеров, случайное открытие свежей ссылки не разлогинивает рабочий телефон."""
    db.init_db()
    tok1 = db.redeem_app_link(db.create_app_link(504))
    tok2 = db.redeem_app_link(db.create_app_link(504))
    assert tok1 != tok2
    assert db.uid_for_app_token(tok1) == 504    # оба живы
    assert db.uid_for_app_token(tok2) == 504


def test_expired_link_rejected():
    db.init_db()
    code = db.create_app_link(505)
    stale = (datetime.now() - timedelta(days=db.APP_LINK_DAYS, minutes=1)) \
        .isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("UPDATE app_links SET created_at = ? WHERE code = ?", (stale, code))
        conn.commit()
    assert db.redeem_app_link(code) is None


def test_expired_token_rejected():
    db.init_db()
    tok = db.redeem_app_link(db.create_app_link(506))
    past = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("UPDATE app_tokens SET expires_at = ? WHERE token = ?", (past, tok))
        conn.commit()
    assert db.uid_for_app_token(tok) is None


def test_revoke():
    db.init_db()
    tok = db.redeem_app_link(db.create_app_link(507))
    pending = db.create_app_link(507)
    assert db.revoke_app_access(507) == 1
    assert db.uid_for_app_token(tok) is None
    assert db.redeem_app_link(pending) is None       # и неактивированная ссылка мертва


def test_unbind_revokes_app_access():
    """«Убрать работника» (unbind из пульта) закрывает и автономное приложение."""
    db.init_db()
    tok = db.redeem_app_link(db.create_app_link(508))
    pending = db.create_app_link(508)
    db.unbind(508)
    assert db.uid_for_app_token(tok) is None
    assert db.redeem_app_link(pending) is None
