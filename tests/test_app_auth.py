"""Авторизация автономного PWA: ссылка входа → девайс-токен (скользящие 30 дн) → отзыв.

Схема: пульт (или бот) выдаёт код ссылкой (app_links, окно активации 7 дн, обменов
столько, сколько записано в самой ссылке — по умолчанию APP_LINK_DEFAULT_USES:
браузер и отдельное хранилище ярлыка iOS); каждый обмен даёт свой девайс-токен
(app_tokens), срок которого продлевается при использовании.
Ключевые гарантии UX: выдача НОВОЙ ссылки по слову «ссылка» не отзывает живой токен;
отзыв — только revoke/unbind.
"""
from datetime import datetime, timedelta

import db


def test_link_redeem_multi_use():
    """Ссылка многоразовая в окне активации: Телега-браузер + Safari = два обмена,
    оба токена живы. Это и есть «преемственность» без ручного ввода кода."""
    db.init_db()
    code = db.create_app_link(501)
    tok1 = db.redeem_app_link(code)
    tok2 = db.redeem_app_link(code)                  # тот же код в другом браузере
    assert tok1 and tok2 and tok1 != tok2
    assert db.uid_for_app_token(tok1) == 501
    assert db.uid_for_app_token(tok2) == 501


def test_link_uses_capped():
    """Лимит обменов: страховка от бесконечно живой пересланной ссылки. Число
    берётся из самой ссылки, а не из общей константы."""
    db.init_db()
    code = db.create_app_link(509)
    for _ in range(db.APP_LINK_DEFAULT_USES):
        assert db.redeem_app_link(code)
    assert db.redeem_app_link(code) is None          # исчерпан


def test_unknown_and_empty_code():
    db.init_db()
    assert db.redeem_app_link("nope") is None
    assert db.redeem_app_link("") is None
    assert db.uid_for_app_token("") is None


def test_new_link_does_not_kill_token():
    """Запрос новой ссылки НЕ разлогинивает уже вошедшее устройство."""
    db.init_db()
    tok = db.redeem_app_link(db.create_app_link(502))
    db.create_app_link(502)                          # юзер снова запросил «ссылка»
    assert db.uid_for_app_token(tok) == 502          # телефон продолжает работать


def test_new_link_invalidates_previous_unopened():
    """Из НЕоткрытых жива только последняя ссылка; уже открытая доживает своё окно
    (по ней могли ещё не доустановить ярлык во втором браузере)."""
    db.init_db()
    old_unopened = db.create_app_link(503)
    old_opened = db.create_app_link(503)
    assert db.redeem_app_link(old_unopened) is None  # погашена выпуском old_opened
    assert db.redeem_app_link(old_opened)            # открыли…
    db.create_app_link(503)                          # …и попросили ещё ссылку
    assert db.redeem_app_link(old_opened)            # открытая продолжает работать


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
        conn.execute("UPDATE app_tokens SET expires_at = ? WHERE token = ?",
                     (past, db.app_token_key(tok)))
        conn.commit()
    assert db.uid_for_app_token(tok) is None


def test_touch_extends_living_token():
    """Скользящее продление: использование отодвигает expires_at; пока приложением
    пользуются хотя бы раз в APP_TOKEN_DAYS — вход не протухает."""
    db.init_db()
    tok = db.redeem_app_link(db.create_app_link(510))
    soon = (datetime.now() + timedelta(minutes=5)).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("UPDATE app_tokens SET expires_at = ? WHERE token = ?",
                     (soon, db.app_token_key(tok)))
        conn.commit()
    db.touch_app_token(tok)
    with db.connect() as conn:
        exp = conn.execute("SELECT expires_at FROM app_tokens WHERE token = ?",
                           (db.app_token_key(tok),)).fetchone()["expires_at"]
    assert exp > (datetime.now() + timedelta(days=db.APP_TOKEN_DAYS - 1)) \
        .isoformat(timespec="seconds")


def test_touch_does_not_resurrect_expired():
    """Продление только живого токена: просроченный не воскресает от касания."""
    db.init_db()
    tok = db.redeem_app_link(db.create_app_link(511))
    past = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("UPDATE app_tokens SET expires_at = ? WHERE token = ?",
                     (past, db.app_token_key(tok)))
        conn.commit()
    db.touch_app_token(tok)
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


def test_manifest_personalized_only_for_live_token():
    """Персональный манифест: живой токен уезжает в start_url (вход «переезжает» в
    iOS-ярлык, у которого хранилище отдельное от Safari); чужой t — обычный манифест."""
    import asyncio
    import json

    from aiohttp.test_utils import make_mocked_request

    import web as webmod
    db.init_db()
    tok = db.redeem_app_link(db.create_app_link(512))
    resp = asyncio.run(webmod._manifest(
        make_mocked_request("GET", f"/manifest.webmanifest?t={tok}")))
    assert json.loads(resp.text)["start_url"] == f"/#t={tok}"
    resp = asyncio.run(webmod._manifest(
        make_mocked_request("GET", "/manifest.webmanifest?t=chuzhoy")))
    assert json.loads(resp.text)["start_url"] == "/"
