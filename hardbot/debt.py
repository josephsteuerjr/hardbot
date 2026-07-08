"""
Долг: статьи разбивки, контрагенты, валюта.

Блок «Разбивка по долгу» в реестре — это 13 статей плюс две итоговые колонки.
Набор статей зависит от контрагента и они НЕ пересекаются:

  * Делимобиль (каршеринг) — ДТП, аренда, штраф с камер, грязь, мусор,
    новые повреждения, эвакуация, пени, прочее;
  * ЗК и прочие кредитные контрагенты — просроченный долг, просроченные
    проценты, неустойка/штрафы, госпошлина.

Раньше это знание было размазано: `web.DEBT_KEYS` смешивал оба набора в один
список и терял пять статей Делимобиля (ДТП, грязь, мусор, новые повреждения,
эвакуация) — их не видел ни один выездник. Единица валюты (`_unit`) жила в трёх
копиях — в card.py, digest.py и web.py. Здесь — единственное место, где это
знание живёт; модуль чистый (без БД, без i18n, без побочных эффектов).
"""

from __future__ import annotations

from util import num

ZK = "ЗК"

# Статьи в порядке колонок реестра (см. fields.FIELDS, блок «Разбивка по долгу»).
ARTICLES_CREDIT: tuple[str, ...] = ("debt_overdue", "interest_overdue", "penalty", "duty")
ARTICLES_CARSHARING: tuple[str, ...] = ("dtp", "rent", "camera_fine", "dirt", "trash",
                                        "new_damage", "evacuation", "peni", "other")
ARTICLES: tuple[str, ...] = ARTICLES_CREDIT + ARTICLES_CARSHARING

# Итоговые колонки — это НЕ статьи разбивки, показывать их в одном списке со
# статьями нельзя (иначе «долг всего» читается как ещё одно начисление).
TOTALS: tuple[str, ...] = ("debt_total", "debt_today")

# Колонки, которые целиком задаёт источник (сх): если такая колонка есть в файле,
# её пустая ячейка означает «статья погашена», а не «значение не пришло». См. db.import_grid.
SOURCE_OWNED: frozenset[str] = frozenset(ARTICLES + TOTALS)


def _cell(row, key):
    """Значение поля у sqlite3.Row (IndexError) или dict (KeyError) — без ветвления у вызывающих."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def is_carsharing(partner) -> bool:
    """Делимобиль и ему подобные. Реестр пишет партнёра свободным текстом,
    поэтому сверяем по подстроке, а не по точному равенству."""
    p = str(partner or "").strip().lower()
    return "делимоб" in p or p in ("дм", "delimobil", "delimobil.ru")


def articles_for(partner) -> tuple[str, ...]:
    """Статьи, ожидаемые у этого контрагента, в порядке реестра."""
    return ARTICLES_CARSHARING if is_carsharing(partner) else ARTICLES_CREDIT


def breakdown(row) -> list[tuple[str, int]]:
    """[(ключ статьи, сумма)] по договору: только ненулевые, в порядке своего контрагента.

    Статьи «чужого» набора (грязные данные — например, аренда у ЗК) не прячем:
    деньги должны быть видны, поэтому они уходят в конец списка.
    """
    own = articles_for(_cell(row, "partner"))
    alien = tuple(k for k in ARTICLES if k not in own)
    out = []
    for key in own + alien:
        value = num(_cell(row, key))
        if value:
            out.append((key, value))
    return out


def contract_debt(row) -> int:
    """Долг по одному договору: «Долг на сегодня», а если он не пришёл — «долг всего».
    Явный ноль в «Долг на сегодня» — это погашено, а не «нет данных» (поэтому `or`
    работает по разобранному числу только для пустой ячейки, см. num(None) == 0)."""
    today = _cell(row, "debt_today")
    return num(today) if today not in (None, "") else num(_cell(row, "debt_total"))


def unit(partners: list[str]) -> str:
    """Валюта показа: портфель ЗК — в сумах, российские контрагенты — в рублях.
    У человека с договорами обоих типов общей валюты нет (см. totals_by_unit)."""
    ps = [p for p in partners if p]
    if not ps:
        return ""
    if all(p == ZK for p in ps):
        return "сум"
    if all(p != ZK for p in ps):
        return "₽"
    return ""


def totals_by_unit(rows) -> list[dict]:
    """[{unit, sum}] — долг человека по валютам. Складывать сумы с рублями в одно
    число нельзя, а именно это и получалось у людей с договорами ЗК + Делимобиль."""
    acc: dict[str, int] = {}
    for r in rows:
        u = unit([_cell(r, "partner")])
        acc[u] = acc.get(u, 0) + contract_debt(r)
    return [{"unit": u, "sum": s} for u, s in acc.items() if s]


def fmt(amount: int, unit_: str = "") -> str:
    """«10 000 000 сум» — разряды пробелом, как во всём боте."""
    return f"{amount:,}".replace(",", " ") + (f" {unit_}" if unit_ else "")
