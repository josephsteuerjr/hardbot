"""Привязка устройства, токен бота: отпечаток машины, тексты состояния."""

from __future__ import annotations

import re


def device_fingerprint() -> str:
    """Стабильный отпечаток машины. Хэш, а не имя: в БД сервера не нужны ПД.

    Берём то, что не меняется от перезагрузки к перезагрузке: имя машины,
    MAC-адрес и имя пользователя. Совпадение отпечатка означает «тот же
    пульт на той же машине» — этого достаточно, чтобы повторная привязка
    заменяла прежний токен, а не плодила записи.
    """
    import getpass
    import hashlib
    import platform
    import uuid
    raw = f"{platform.node()}|{uuid.getnode()}|{getpass.getuser()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def device_default_name() -> str:
    import platform
    return f"Пульт · {platform.node()}"[:60]


KEY_STATE_BOUND = (
    "Устройство привязано: пульт ходит своим токеном, мастер-ключ "
    "здесь не хранится. Поле ключа нужно только чтобы привязать "
    "новое устройство или перепривязать это.")
KEY_STATE_UNBOUND = (
    "Устройство НЕ привязано — пульт ходит мастер-ключом. Введи "
    "ключ и нажми «Привязать это устройство»: ключ уйдёт на сервер "
    "один раз, в обмен на токен, и сохранён не будет.")


def key_state_text(device_token: str) -> str:
    return KEY_STATE_BOUND if device_token else KEY_STATE_UNBOUND


def device_line(dev: dict) -> tuple[str, str]:
    """(имя, подпись «привязано … · на связи: …»)."""
    seen = dev.get("last_seen") or "ни разу"
    return (dev.get("name") or "(без имени)",
            f"  ·  привязано {dev.get('created_at') or '—'}  ·  на связи: {seen}")


UNBIND_TEXT = ("Забыть токен на этой машине? Пульт снова будет спрашивать "
               "мастер-ключ.\n\nНа сервере токен останется действующим — чтобы "
               "погасить его совсем, отзови устройство в списке ниже.")


def revoke_text(name, row_id) -> str:
    return (f"Отозвать «{name or row_id}»? Его токен перестанет работать "
            "немедленно. Для отзыва нужен мастер-ключ — введи его в поле выше, "
            "если поле пустое.")


def valid_bot_token(tok: str) -> bool:
    return bool(re.fullmatch(r"\d+:[\w-]{30,}", tok or ""))


def valid_telegram_id(value: str) -> bool:
    return bool(re.fullmatch(r"\d{3,}", value or ""))
