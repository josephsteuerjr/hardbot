"""Раздел «Люди»: подписи гостей, привязки, снятие клиентов."""

from __future__ import annotations

from datetime import datetime

from .text import fmt_n

KIND_LABELS = ["Выездник", "Курьер ГПХ"]
GUEST_PLACEHOLDER = "— из писавших боту —"


def fmt_seen(iso) -> str:
    """«был 16.07 12:01» — отличать живой аккаунт от брошенного тёзки: у человека
    бывает два телеграма с одним именем, роль надо давать тому, с которого пишут."""
    try:
        d = datetime.fromisoformat(str(iso or ""))
    except ValueError:
        return ""
    return f" · был {d.day:02d}.{d.month:02d} {d.hour:02d}:{d.minute:02d}"


def guest_label(g: dict) -> str:
    who = g.get("name") or (("@" + g["username"]) if g.get("username") else "без имени")
    nick = (" · @" + g["username"]) if g.get("username") else ""
    busy = ("  ⚠ уже: " + g["bound_employee"]) if g.get("bound_employee") else ""
    return f"{who}{nick} · {g['telegram_id']}{fmt_seen(g.get('last_seen'))}{busy}"


def kind_from_label(label: str) -> str:
    return "courier" if label == "Курьер ГПХ" else "agent"


def kind_index(e: dict) -> int:
    return 1 if e.get("kind") == "courier" else 0


def role_status(e: dict) -> tuple[str, bool]:
    """(текст, привязан?)"""
    bound = bool(e.get("telegram_id"))
    return ("привязан ✓" if bound else "не назначен", bound)


def kind_text(b: dict) -> str:
    return "курьер" if b.get("kind") == "courier" else "выездник"


def client_label(c: dict) -> str:
    return f'{c.get("client") or "—"} · КД {c["contract_id"]}'


def client_debt(c: dict) -> str:
    return fmt_n(c.get("debt")) + (f' {c["unit"]}' if c.get("unit") else "")


def unassign_text(n: int, emp: str) -> str:
    return (f"Снять {n} договор(ов) с «{emp}»?\n\nКлиенты останутся в "
            "реестре без ответственного и пропадут из приложения сотрудника.")
