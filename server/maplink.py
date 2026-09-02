"""Подписанные ссылки «Карта дня» для руководителя.

Руководитель живёт в чате бота, логина в вебе у него нет. Кнопка в дайджесте даёт
URL с HMAC-подписью (ключ выводится из токена бота): страница карты и её данные
отдаются только по валидной неистёкшей подписи. Ссылка живёт 48 часов — дальше
руководитель просто берёт свежую из дайджеста.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import quote

import db

TTL = 48 * 3600


def _key() -> bytes:
    token = db.get_setting("bot_token") or os.getenv("BOT_TOKEN") or ""
    return hashlib.sha256(("mapday:" + token).encode()).digest()


def _sig(employee: str, on_date: str, exp: int) -> str:
    payload = f"{employee}|{on_date}|{exp}".encode()
    return hmac.new(_key(), payload, hashlib.sha256).hexdigest()[:32]


def sign(employee: str, on_date: str) -> str:
    """Query-string для /manager/map: e=<сотрудник>&d=<дата>&x=<expiry>&s=<hmac>."""
    exp = int(time.time()) + TTL
    return (f"e={quote(employee)}&d={quote(on_date)}&x={exp}"
            f"&s={_sig(employee, on_date, exp)}")


def verify(employee: str, on_date: str, exp: str, sig: str) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < time.time():
        return False
    # Сравниваем БАЙТАМИ. compare_digest на строках требует, чтобы обе были
    # ASCII, и на кириллическом омоглифе в подделанной ссылке бросал TypeError:
    # страница карты дня отдавала 500 вместо честного «ссылка устарела».
    return hmac.compare_digest(_sig(employee, on_date, exp_i).encode(),
                               (sig or "").encode())
