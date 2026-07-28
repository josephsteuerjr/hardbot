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
    """Ответственный должен совпасть с книгой и владельцем либо делегатом."""
    actual = normalized_owner_label(bundle, responsible)
    if not actual:
        return False
    if normalized_owner_label(bundle, entry.responsible_hint) != actual:
        return False
    if normalized_owner_label(bundle, entry.employee_folder) == actual:
        return True
    for delegation in bundle.app.ownership_delegations:
        if not delegation.enabled:
            continue
        if (
            norm_name(delegation.country) == norm_name(entry.country)
            and norm_name(delegation.group) == norm_name(entry.group)
            and normalized_owner_label(bundle, delegation.book_owner)
            == actual
            and normalized_owner_label(
                bundle,
                delegation.delegate_folder,
            )
            == normalized_owner_label(bundle, entry.employee_folder)
        ):
            return True
    return False
