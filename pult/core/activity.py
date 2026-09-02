"""Раздел «Активность»: строки таблицы из /admin/activity."""

from __future__ import annotations

from .text import fmt_n

ACTIVITY_COLUMNS = (
    ("emp", "Сотрудник", 190), ("role", "Роль", 80), ("v7", "Визиты 7д", 70),
    ("v30", "30д", 55), ("m7", "Метки 7д", 70), ("m30", "30д", 55),
    ("last", "Последняя активность", 150), ("status", "Статус", 120),
)
NUMERIC = {"v7", "v30", "m7", "m30"}


def activity_status(w: dict) -> str:
    idle = w.get("idle_days")
    status = ("без активности" if idle is None else
              "активен" if idle < 2 else
              f"пауза {idle} дн" if idle <= 7 else f"простой {idle} дн")
    # ФИО привязки не совпало ни с одним «ответственным» в реестре —
    # обычно сотрудника переименовали в офисе. Портфель у него при
    # этом пустеет молча: приложение отвечает 200 и «никто не
    # закреплён», и найти причину было негде.
    if w.get("orphaned"):
        status = "⚠ ФИО нет в реестре · " + status
    return status


def activity_row(w: dict) -> tuple:
    last = w.get("last_activity") or "—"
    if len(last) > 16:
        last = last[:10] + " " + last[11:16]
    return (w.get("employee", ""),
            "курьер" if w.get("kind") == "courier" else "выездник",
            fmt_n(w.get("visits_7d")), fmt_n(w.get("visits_30d")),
            fmt_n(w.get("markers_7d")), fmt_n(w.get("markers_30d")),
            last, activity_status(w))
