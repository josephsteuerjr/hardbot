"""Проверка, что книга физически лежит у указанного в ней сотрудника."""

from __future__ import annotations

import re
from typing import Any

from .config import ConfigBundle
from .models import ManifestEntry
from .normalize import norm_name


def normalized_owner_label(
    bundle: ConfigBundle,
    value: Any,
) -> str:
    """Убрать настроенный профильный префикс и служебный хвост папки."""
    text = str(value or "").strip()
    app = getattr(bundle, "app", None)
    for rule in getattr(app, "profile_rules", ()):
        text = re.sub(
            rule["filename_regex"],
            "",
            text,
            flags=re.IGNORECASE,
        )
    return norm_name(text.rstrip(" -–—"))


def manifest_owner_matches(
    bundle: ConfigBundle,
    entry: ManifestEntry,
    responsible: Any,
) -> bool:
    """Ответственный должен совпасть и с папкой, и с именем книги."""
    actual = normalized_owner_label(bundle, responsible)
    return bool(actual) and all(
        normalized_owner_label(bundle, candidate) == actual
        for candidate in (
            entry.employee_folder,
            entry.responsible_hint,
        )
    )
