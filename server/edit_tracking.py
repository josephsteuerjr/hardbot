"""Result edit provenance, independent of fact dates and delivery timestamps.

The two identical Excel captions are resolved only at their block boundaries.
All other ambiguous headers remain errors. Legacy books have no edit metadata.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math

CAPTION = "Дата ручной правки"
EXCEL_KEYS = ("excel_visit_edited_on", "excel_call_edited_on")
APP_KEYS = ("app_visit_edited_at", "app_call_edited_at")
KEYS = EXCEL_KEYS + APP_KEYS
LOGICAL_TITLES = {
    "excel_visit_edited_on": "Дата ручной правки выезда",
    "excel_call_edited_on": "Дата ручной правки звонка",
}
BOUNDARIES = {
    "excel_visit_edited_on": ("Дата выезда",),
    "excel_call_edited_on": ("Статус звонка СОФТ",),
}


def normalized(value):
    return " ".join(str(value or "").casefold().split())


def columns(headers):
    """Map zero-based date positions; reject misplaced/repeated block dates."""
    out = {}
    for index, title in enumerate(headers):
        if normalized(title) != normalized(CAPTION):
            continue
        following = normalized(headers[index + 1]) if index + 1 < len(headers) else ""
        matches = [key for key, anchors in BOUNDARIES.items()
                   if following in {normalized(a) for a in anchors}]
        if len(matches) != 1 or matches[0] in out.values():
            raise ValueError("Колонка «Дата ручной правки» должна стоять первой в своём блоке выезда/звонка")
        out[index] = matches[0]
    return out


def logical_headers(headers):
    result = list(headers)
    for index, key in columns(headers).items():
        result[index] = LOGICAL_TITLES[key]
    return result


def physical_title(title):
    return CAPTION if title in LOGICAL_TITLES.values() else title


def canonical_day(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        if math.isfinite(value) and 1 <= value <= 2958465:
            return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
        raise ValueError("Некорректная дата ручной правки Excel")
    for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(value).strip(), pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Дата ручной правки должна иметь формат ДД.ММ.ГГГГ")


def card(rows):
    """Per-contract values avoid inventing a shared person-level edit date."""
    return [{"contract_id": row["contract_id"], **{
        key: row[key] if key in row.keys() else None for key in KEYS
    }} for row in rows]
