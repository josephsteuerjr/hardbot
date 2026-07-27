"""Нормализация заголовков, ФИО, КД и значений Excel."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_ZERO_WIDTH = ("\u200b", "\u200c", "\u200d", "\ufeff")
_HOMOGLYPHS = str.maketrans("acekmoptxy", "асекмортху")


def norm_title(value: Any) -> str:
    """Стабильное сравнение заголовков, включая латинские двойники."""
    if value in (None, ""):
        return ""
    text = str(value)
    for char in _ZERO_WIDTH:
        text = text.replace(char, "")
    text = re.sub(r"\s+", " ", text.replace("\u00a0", " ").strip().lower())
    return text.translate(_HOMOGLYPHS).rstrip(":;*.")


def norm_name(value: Any) -> str:
    """ФИО/ответственный без различий в пробелах и регистре."""
    return re.sub(
        r"\s+",
        " ",
        str(value or "").replace("\u00a0", " ").strip(),
    ).casefold()


def norm_key(value: Any) -> str:
    """Excel-число 123.0 и строка ``123`` означают один номер КД."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
    if isinstance(value, Decimal) and value == value.to_integral():
        return str(int(value))
    return str(value).strip()


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def json_value(value: Any) -> Any:
    """Значение calamine/Excel в устойчивый JSON-вид без потери пустоты."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return int(value) if value.is_integer() and abs(value) < 9e15 else value
    if isinstance(value, datetime):
        if value.time().isoformat() == "00:00:00":
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def comparable_value(value: Any) -> tuple[str, Any]:
    """Типизированное значение для сравнения источников и построения diff."""
    value = json_value(value)
    if value is None:
        return "blank", None
    if isinstance(value, bool):
        return "bool", value
    if isinstance(value, (int, float)):
        return "number", value
    return "text", re.sub(r"\s+", " ", str(value).strip())


def values_equal(left: Any, right: Any) -> bool:
    return comparable_value(left) == comparable_value(right)


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    core = text.split("T", 1)[0].split(" ", 1)[0]
    core = core.rstrip(".,;").replace(",", ".")
    for pattern in (
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d.%m.%y",
        "%d/%m/%Y",
        "%d/%m/%y",
    ):
        try:
            return datetime.strptime(core, pattern).date()
        except ValueError:
            continue
    return None


def parse_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return int(number) if number.is_integer() else number
    text = str(value).strip().casefold().replace("\u00a0", " ")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"(?:руб\.?|р\.?|₽)$", "", text)
    text = text.replace(",", ".")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return int(number) if number.is_integer() else number
