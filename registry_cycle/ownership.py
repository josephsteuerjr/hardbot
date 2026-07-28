"""Проверка, что книга физически лежит у указанного в ней сотрудника."""

from __future__ import annotations

import re
from typing import Any

from .config import ConfigBundle
from .models import ManifestEntry, WorkbookSnapshot
from .normalize import is_blank, norm_name


def canonical_responsible_name(
    value: Any,
    aliases: dict[str, str] | None = None,
) -> str:
    """Нормализовать ФИО и применить направленные aliases без циклов."""
    current = norm_name(value)
    normalized_aliases = {
        norm_name(source): norm_name(target)
        for source, target in (aliases or {}).items()
        if norm_name(source) and norm_name(target)
    }
    seen: set[str] = set()
    while current in normalized_aliases and current not in seen:
        seen.add(current)
        current = normalized_aliases[current]
    return current


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
    aliases = getattr(app, "responsible_aliases", {})
    return canonical_responsible_name(
        text.rstrip(" -–—"),
        aliases,
    )


def workbook_data_is_empty(book: WorkbookSnapshot) -> bool:
    """True, если после служебных строк в книге нет ни одного значения."""
    return not any(
        not is_blank(value)
        for row in book.rows
        for value in row
    )


def resolve_book_responsible(
    entry: ManifestEntry,
    book: WorkbookSnapshot,
    responsible_title: str,
) -> str | None:
    """Определить владельца книги, не угадывая его для непустых данных.

    В новой пустой книге ещё нет строк, откуда можно прочитать ответственного.
    Тогда единственным допустимым источником становится manifest: имя файла.
    Для книги хотя бы с одной непустой ячейкой такое допущение запрещено.
    """
    responsibles = book.unique_responsibles(responsible_title)
    if len(responsibles) == 1:
        return responsibles[0]
    if responsibles or not workbook_data_is_empty(book):
        return None
    hint = str(entry.responsible_hint or "").strip()
    return hint or None


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
