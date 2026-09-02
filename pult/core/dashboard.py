"""Раздел «Сводка»: из ответа /admin/stats — значения для карточек."""

from __future__ import annotations

from .text import fmt_n

METRICS = (("clients", "Контрагентов"), ("visited", "С визитом"),
           ("emps", "Сотрудников"), ("binds", "Выбрали себя"), ("marks", "Меток"))
COUNTRIES = (("tj", "🇹🇯 Таджикистан"), ("uz", "🇺🇿 Узбекистан"))
COUNTRIES_DESC = ("Отдельно по закреплённым кейсам. Республика берётся из настроенного "
                  "scope руководителя, а не из числового поля «Пул».")


def metric_values(d: dict) -> dict[str, str]:
    return {
        "clients": fmt_n(d.get("clients")),
        "visited": fmt_n(d.get("visited")),
        "emps": fmt_n(len(d.get("employees", []))),
        "binds": fmt_n(len(d.get("bindings", []))),
        "marks": fmt_n(d.get("markers")),
    }


def country_pairs(c: dict) -> list[tuple[str, object]]:
    clients, visited = c.get("clients") or 0, c.get("visited") or 0
    pc = round(visited / clients * 100) if clients else 0
    return [("договоров", clients), ("с визитом", visited),
            ("покрытие", f"{pc}%"), ("сотрудников", c.get("employees") or 0)]


def outcome_chips(by_result, empty: str) -> list[str]:
    return [f"{name or 'без исхода'}  {fmt_n(n)}" for name, n in (by_result or [])] or [empty]


def coverage(d: dict) -> tuple[int, str]:
    cl, vi = d.get("clients") or 0, d.get("visited") or 0
    pc = round(vi / cl * 100) if cl else 0
    text = f"{fmt_n(vi)} из {fmt_n(cl)} контрагентов — {pc}%" if cl else "реестр пуст"
    return pc, text


def period_text(d: dict) -> str:
    return (f"Активен: {d.get('from') or '…'} — {d.get('to') or '…'}"
            if d.get("from") or d.get("to") else "Период не задан — видны все.")
