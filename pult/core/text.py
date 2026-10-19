"""Мелкие текстовые помощники пульта: числа, даты, сравнение имён."""

from __future__ import annotations

import re
from datetime import datetime


def fmt_n(n) -> str:
    """1234567 → «1 234 567» (как toLocaleString ru-RU)."""
    if n is None:
        return "—"
    try:
        return f"{int(float(n)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def parse_num(v):
    """«10 000 000» / «12 400,50» / 100900 → float | None."""
    if isinstance(v, (int, float)):
        return float(v)
    s = "".join(ch for ch in str(v or "") if not ch.isspace()).replace(",", ".")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def to_iso(s: str) -> str:
    """Дата из поля периода: ISO или дд.мм.гггг → ISO; пусто → пусто."""
    import enrich

    s = (s or "").strip()
    if not s:
        return ""
    d = enrich.parse_date(s)
    if not d:
        raise ValueError(f"не дата: «{s}» (нужно ГГГГ-ММ-ДД или ДД.ММ.ГГГГ)")
    return d.isoformat()


def today_text() -> str:
    """Сегодня как «ДД.ММ.ГГГГ» — для кнопок «сегодня» у полей дат."""
    return datetime.now().strftime("%d.%m.%Y")


def names_look_different(typed: str, real: str) -> bool:
    """Похожи ли имена «на глаз». Точное совпадение не нужно и вредно: «Тестовый»
    против «Тестовый Сотрудник» — один человек, а «Михаил» против «Другой Сотрудник» —
    разные. Считаем похожими, если совпало хотя бы одно слово от трёх букв."""
    def words(s):
        return {w for w in re.split(r"[^\w]+", str(s or "").casefold().replace("ё", "е"))
                if len(w) >= 3}
    a, b = words(typed), words(real)
    return bool(a and b and not (a & b))
