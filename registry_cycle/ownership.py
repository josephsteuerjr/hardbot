"""Определение ответственного внутри книги и нормализация его подписи."""

from __future__ import annotations

from typing import Any

from .models import WorkbookSnapshot
from .normalize import is_blank, norm_name, norm_title


def responsible_title_for(book, fields, profile: str) -> str:
    """Новый master имеет две ответственности; старые книги сохраняют одну."""
    titles = ([fields.soft_responsible_title, fields.responsible_title]
              if profile == "soft" else [fields.responsible_title])
    for title in titles:
        variants = fields.title_variants(title)
        found = [str(header) for header in book.headers if norm_title(header) in variants]
        if len(found) == 1:
            return found[0]
    return titles[0]


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


def workbook_data_is_empty(book: WorkbookSnapshot) -> bool:
    """True, если после служебных строк в книге нет ни одного значения."""
    return not any(
        not is_blank(value)
        for row in book.rows
        for value in row
    )


def resolve_book_responsible(
    book: WorkbookSnapshot,
    responsible_title: str,
) -> str | None:
    """Return the sole owner written in the workbook itself.

    Folder names, filenames and manifest hints are deliberately unavailable to
    this function, so they cannot influence ownership or upward transfer.
    """
    responsibles = book.unique_responsibles(responsible_title)
    if len(responsibles) == 1:
        return responsibles[0]
    return None
