"""Подписанные ссылки «Карта дня» для руководителя.

Руководитель живёт в чате бота, логина в вебе у него нет. Кнопка в дайджесте даёт
URL с HMAC-подписью: страница карты и её данные отдаются только по валидной
неистёкшей подписи. Ссылка живёт 48 часов — дальше руководитель просто берёт
свежую из дайджеста.

Ключ подписи — собственный секрет сервера (``MAPLINK_SECRET`` из окружения или
случайная строка, которую сервер сам заводит в settings при первой подписи).
Раньше ключ выводился из токена бота, и на контуре без Телеграма (``BOT_TOKEN``
пуст) он вырождался в константу из открытого кода: кто угодно подписывал ссылку
на любое ФИО и любую дату и читал адреса должников с GPS-точками выездника.
Прежнюю схему не держим даже запасной — ссылки, выданные до смены ключа, просто
устаревают, как устаревают через 48 часов и без того.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import quote

import db

TTL = 48 * 3600
# Ключ settings с секретом карты. В строке лежит сам секрет, поэтому ни одна
# ручка чтения settings его не отдаёт.
SECRET_SETTING = "maplink_secret"


def _secret() -> str:
    """Секрет подписи: окружение главнее, иначе свой в settings.

    Заводится атомарно одной вставкой: два параллельных первых запроса не должны
    получить два разных ключа, иначе ссылка, подписанная одним, не прошла бы
    проверку другим. Вставка не трогает уже заведённый секрет; пустую строку
    (руками затёртую) считает отсутствием и заменяет — иначе карта молча перестала
    бы открываться до ручной чистки базы.
    """
    env = (os.getenv("MAPLINK_SECRET") or "").strip()
    if env:
        return env
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?",
                           (SECRET_SETTING,)).fetchone()
        if row is None or not (row["value"] or "").strip():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value "
                "WHERE trim(coalesce(settings.value, '')) = ''",
                (SECRET_SETTING, secrets.token_urlsafe(32)))
            conn.commit()
            row = conn.execute("SELECT value FROM settings WHERE key = ?",
                               (SECRET_SETTING,)).fetchone()
    return ((row["value"] if row else "") or "").strip()


def _key() -> bytes:
    """Пустой секрет — пустой ключ, и verify() на нём отказывает (fail-closed).

    Приставка другая, чем у прежней схемы («mapday:»): даже совпади секрет
    с токеном бота, старая подпись под новый ключ не подойдёт.
    """
    secret = _secret()
    if not secret:
        return b""
    return hashlib.sha256(("maplink:v2:" + secret).encode()).digest()


def _sig(employee: str, on_date: str, exp: int, key: bytes | None = None) -> str:
    key = _key() if key is None else key
    if not key:
        raise RuntimeError("нет секрета подписи карты")
    payload = f"{employee}|{on_date}|{exp}".encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()[:32]


def sign(employee: str, on_date: str) -> str:
    """Query-string для /manager/map: e=<сотрудник>&d=<дата>&x=<expiry>&s=<hmac>."""
    exp = int(time.time()) + TTL
    return (f"e={quote(employee)}&d={quote(on_date)}&x={exp}"
            f"&s={_sig(employee, on_date, exp)}")


def verify(employee: str, on_date: str, exp: str, sig: str) -> bool:
    # Пустые поля — отказ сразу: подпись «ни на кого и ни на когда» не выдаётся.
    if not employee or not on_date or not sig:
        return False
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < time.time():
        return False
    key = _key()
    if not key:
        return False
    # Сравниваем БАЙТАМИ. compare_digest на строках требует, чтобы обе были
    # ASCII, и на кириллическом омоглифе в подделанной ссылке бросал TypeError:
    # страница карты дня отдавала 500 вместо честного «ссылка устарела».
    return hmac.compare_digest(_sig(employee, on_date, exp_i, key).encode(),
                               str(sig).encode())
