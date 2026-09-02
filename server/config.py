"""Конфиг и доступ: окружение, доверенные лица, язык. Ролей нет — только whitelist."""

import os

from dotenv import load_dotenv

import db
from i18n import DEFAULT_LANG

load_dotenv()


def _int_env(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


ADMIN_ID = _int_env("ADMIN_ID") or 0
GROUP_CHAT_ID = _int_env("GROUP_CHAT_ID")

# ИБ: привязка выездника требует аппрува ADMIN (по умолчанию ВКЛ). Для пилота можно
# отключить (BIND_APPROVAL=0) — тогда самовыбор активируется сразу, как раньше.
_BIND_APPROVAL_ENV = (os.getenv("BIND_APPROVAL", "1") or "1").strip().lower()
BIND_APPROVAL = _BIND_APPROVAL_ENV not in ("", "0", "false", "no", "off")


def approval_required() -> bool:
    """Нужен ли аппрув. Если ADMIN_ID не задан — некому одобрять, поэтому НЕ требуем
    (иначе выездники навсегда заперты). Аппрув работает только при заданном ADMIN_ID."""
    return BIND_APPROVAL and bool(ADMIN_ID)


def is_trusted(uid: int) -> bool:
    """Доступ: владелец или тот, чья привязка ОДОБРЕНА (get_binding отдаёт только approved=1)."""
    return uid == ADMIN_ID or db.get_binding(uid) is not None


def employee_of(uid: int) -> str | None:
    """Сотрудник, за которого работает пользователь (по самовыбору)."""
    return db.get_binding(uid)


def lang_of(uid: int) -> str:
    return db.get_lang(uid) or DEFAULT_LANG
