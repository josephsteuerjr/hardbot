"""Раздел «Сотрудники»: строки таблицы из /admin/activity, фильтры, статусы."""

from __future__ import annotations

from .text import fmt_n

ACTIVITY_COLUMNS = (
    ("emp", "Сотрудник", 180), ("role", "Роль", 66), ("entry", "Вход", 74),
    ("shell", "Обол.", 52), ("seen", "Последний вход", 112),
    ("persons", "Должн.", 60), ("contracts", "Догов.", 60),
    ("today", "Сегодня", 62), ("v7", "7д", 44), ("v30", "30д", 46),
    ("m7", "Метки 7д", 62), ("ph7", "Фото 7д", 60), ("idle", "Простой", 60),
    ("status", "Статус", 170),
)
NUMERIC = {"persons", "contracts", "today", "v7", "v30", "m7", "ph7", "idle", "shell"}

ENTRY_TEXT = {"pwa": "PWA", "telegram": "Telegram", "none": "не заходил"}

# Фильтры-чипы над таблицей. Ключ → (подпись, предикат). Предикат получает
# строку сервера и «свежую» версию оболочки.
FILTERS = (
    ("all", "все"),
    ("silent", "молчат ≥2 дн"),
    ("never", "ни разу не заходили"),
    ("old", "старая оболочка"),
    ("orphan", "⚠ нет в реестре"),
)


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


def fmt_seen(iso) -> str:
    s = str(iso or "")
    if len(s) >= 16:
        return s[8:10] + "." + s[5:7] + " " + s[11:16]
    return s[:10] or "—"


def latest_shell(workers: list[dict]) -> int:
    return max((int(w.get("shell_version") or 0) for w in workers), default=0)


def matches(w: dict, key: str, latest: int) -> bool:
    if key == "silent":
        idle = w.get("idle_days")
        return idle is None or idle >= 2
    if key == "never":
        return (w.get("entry") or "none") == "none"
    if key == "old":
        sv = w.get("shell_version")
        return bool(sv) and int(sv) < latest
    if key == "orphan":
        return bool(w.get("orphaned"))
    return True


def filter_counts(workers: list[dict]) -> dict[str, int]:
    latest = latest_shell(workers)
    return {key: sum(1 for w in workers if matches(w, key, latest)) for key, _t in FILTERS}


def activity_row(w: dict) -> tuple:
    return (w.get("employee", ""),
            "курьер" if w.get("kind") == "courier" else "выездник",
            ENTRY_TEXT.get(w.get("entry") or "none", "—"),
            w.get("shell_version") or "",
            fmt_seen(w.get("last_seen")),
            fmt_n(w.get("portfolio_persons")), fmt_n(w.get("portfolio_contracts")),
            fmt_n(w.get("visits_today")),
            fmt_n(w.get("visits_7d")), fmt_n(w.get("visits_30d")),
            fmt_n(w.get("markers_7d")), fmt_n(w.get("photos_7d")),
            "" if w.get("idle_days") is None else w.get("idle_days"),
            activity_status(w))


def summary_text(workers: list[dict]) -> str:
    n = len(workers)
    if not n:
        return "Привязанных сотрудников нет"
    active = sum(1 for w in workers if (w.get("visits_today") or 0))
    never = sum(1 for w in workers if (w.get("entry") or "none") == "none")
    latest = latest_shell(workers)
    old = sum(1 for w in workers if matches(w, "old", latest))
    parts = [f"привязано {n}", f"сегодня выезжали {active}"]
    if never:
        parts.append(f"ни разу не заходили {never}")
    if old:
        parts.append(f"на старой оболочке {old} (свежая v{latest})")
    return " · ".join(parts)
