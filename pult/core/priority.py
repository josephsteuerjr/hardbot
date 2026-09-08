"""Раздел «Приоритет»: запрос с весами и строки топа."""

from __future__ import annotations

from .text import fmt_n

# Долг и свежесть считает модель, и ползунки по ним ни на что не влияли — но
# крутились. Оставлен один, который влияет: кластер (стоимость объезда).
WEIGHTS = (("cluster", "Кластер", 30),)
_FIXED = {"debt": 50, "fresh": 20}

# «Ожидание» — главная колонка: сколько денег принесёт выезд. Score оставлен
# как относительная шкала 0–100 внутри текущей выборки.
PRIORITY_COLUMNS = (
    ("n", "#", 34), ("client", "Клиент", 180), ("city", "Город", 100),
    ("debt", "Долг ₽", 90), ("exp", "Ожидание ₽", 100), ("visits", "Выезд.", 56),
    ("score", "Score", 56), ("mode", "Режим", 84), ("why", "Почему", 300),
)
NUMERIC = {"debt", "exp", "visits", "score", "n"}


def priority_query(weights: dict, limit: int = 40) -> str:
    w = {**_FIXED, **weights}
    return (f"?w_debt={int(w['debt'])}"
            f"&w_cluster={int(w['cluster'])}"
            f"&w_fresh={int(w['fresh'])}&limit={int(limit)}")


def cluster_chips(cities: list[dict]) -> list[str]:
    return ([f"{c['name']}  {fmt_n(c['count'])}" for c in cities]
            or ["городов нет — реестр пуст или нет координат"])


def priority_row(i: int, it: dict) -> tuple:
    return (i, it.get("client", ""), it.get("city", ""), fmt_n(it.get("debt") or 0),
            fmt_n(round(it.get("expected") or 0)), it.get("visits", 0),
            f"{it.get('score', 0):.1f}", it.get("mode", ""), it.get("reason", ""))
