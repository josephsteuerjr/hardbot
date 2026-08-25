"""Определение профиля личного реестра по его каноническим столбцам."""

from __future__ import annotations

from .config import FieldConfig
from .errors import WorkbookReadError
from .models import WorkbookSnapshot
from .normalize import norm_title


WORKBOOK_PROFILE = "workbook"


def _title_variants(fields: FieldConfig, title: str) -> set[str]:
    return {
        norm_title(title),
        *(
            norm_title(alias)
            for alias in fields.aliases.get(title, ())
        ),
    }


def infer_workbook_profile(
    snapshot: WorkbookSnapshot,
    fields: FieldConfig,
) -> str:
    """Return the sole profile matching employee-owned column names.

    Folder names, employee-folder names and workbook filenames deliberately do
    not participate.  Aliases from ``fields.json`` are resolved before the
    profile sets are compared, so column order and cosmetic naming differences
    do not affect the result.
    """
    headers = {
        norm_title(value)
        for value in snapshot.headers
        if norm_title(value)
    }
    present = {
        norm_title(rule.title)
        for rule in fields.collect
        if headers & _title_variants(fields, rule.title)
    }
    expected = {
        profile: {
            norm_title(rule.title)
            for rule in fields.collect
            if profile in rule.profiles
        }
        for profile in fields.profile_tags
    }
    matches = [
        profile
        for profile, titles in expected.items()
        if present == titles
    ]
    if len(matches) == 1:
        return matches[0]

    # Реальная рабочая книга может временно потерять одну необязательную
    # колонку. Для профиля достаточно, чтобы в ней были характерные только для
    # него поля и не было характерных полей другого профиля. Полный перечень
    # колонок отдельно проверяет planner и отсутствующее поле просто пропускает.
    exclusive: dict[str, set[str]] = {}
    for profile, titles in expected.items():
        other_titles = set().union(
            *(
                candidate
                for other_profile, candidate in expected.items()
                if other_profile != profile
            )
        )
        exclusive[profile] = titles - other_titles
    profiles_with_markers = [
        profile
        for profile, titles in exclusive.items()
        if present & titles
    ]
    if len(profiles_with_markers) == 1:
        return profiles_with_markers[0]

    diagnostics = []
    for profile, titles in expected.items():
        missing = sorted(titles - present)
        extra = sorted(present - titles)
        diagnostics.append(
            f"{profile}: нет {missing or '—'}, лишние {extra or '—'}"
        )
    if not matches:
        reason = "ни один профиль не совпал"
    else:
        reason = "совпало несколько профилей: " + ", ".join(matches)
    raise WorkbookReadError(
        "профиль реестра не определён по названиям столбцов "
        f"({reason}); " + "; ".join(diagnostics)
    )
