"""Раздел «Руководители»: дайджест, доставка, координаты фото, беседы, форма."""

from __future__ import annotations

import re

from .text import fmt_n


# --- дайджест ------------------------------------------------------------------
def valid_time(value: str) -> bool:
    """ЧЧ:ММ; пустое значение допустимо (сервер оставит своё)."""
    return not value or bool(re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", value))


def digest_body(*, time: str, enabled: bool, promise_time: str, promise_enabled: bool,
                visit_push: bool, owner_known: bool, owner_digest: bool,
                owner_promise: bool) -> dict:
    body = {"time": time, "enabled": enabled,
            "promise_time": promise_time, "promise_enabled": promise_enabled,
            "visit_push": visit_push}
    # Как и visit_push: ключ уходит только если сервер уже умеет его читать,
    # иначе «Сохранить» на старом сервере ничего не значит, но и не врёт.
    if owner_known:
        body["owner_digest"] = owner_digest
        body["owner_promise"] = owner_promise
    return body


def digest_state_text(d: dict) -> str:
    last_d = d.get("digest_last_run") or "—"
    last_p = d.get("promise_last_run") or "—"
    return (f"Дайджест {'вкл' if d.get('enabled') else 'выкл'} · {d.get('time')} МСК"
            f" · последняя рассылка {last_d}"
            f"   ·   Обещания {'вкл' if d.get('promise_enabled') else 'выкл'}"
            f" · {d.get('promise_time')} МСК · последняя {last_p}")


# --- доставка --------------------------------------------------------------------
KIND_NAMES = {"digest": "дайджест", "promise": "обещания",
              "visit": "визиты", "group": "группа",
              "report_chat": "беседы", "test": "проверки"}


def delivery_pairs(d: dict) -> list[tuple[str, str]]:
    delivery = d.get("delivery") or {}
    by_kind = delivery.get("by_kind") or {}
    pairs = []
    for kind, name in KIND_NAMES.items():
        st = by_kind.get(kind) or {}
        if not st:
            continue
        sent = st.get("sent", 0)
        blocked = st.get("blocked", 0)
        failed = st.get("failed", 0)
        skipped = sum(v for k, v in st.items() if str(k).startswith("skipped"))
        pairs.append((name, f"ушло {fmt_n(sent)} · заблок. "
                            f"{fmt_n(blocked)} · ошибок {fmt_n(failed)}"
                            f" · пропущено {fmt_n(skipped)}"))
    if not pairs:
        pairs = [("за сегодня", "записей доставки нет")]
    return pairs


def delivery_lines(d: dict) -> list[str]:
    delivery = d.get("delivery") or {}
    lines = []
    blocked = delivery.get("blocked") or []
    if blocked:
        who = ", ".join(f"{b.get('target_name') or b.get('target_id')}"
                        for b in blocked[:10])
        lines.append(f"Заблокированы (нужен /start в личке боту): {who}")
    last = delivery.get("last_error")
    if last:
        lines.append(f"Последняя ошибка: {last.get('kind')} → "
                     f"{last.get('target_id')}: {last.get('error')}")
    return lines


def push_result_text(d: dict) -> str:
    return (f"Не дошло ({d.get('status')}):\n{d.get('error') or ''}\n\n"
            "«blocked» = руководитель не нажал /start в личке боту.")


# --- координаты фото -------------------------------------------------------------
def geo_scope_text(emp: str, cid: str, before: str) -> str:
    return ", ".join(x for x in (
        f"сотрудник {emp}" if emp else "",
        f"КД {cid}" if cid else "",
        f"загруженные до {before}" if before else "") if x) or "ВСЕ снимки"


def geo_confirm_text(emp: str, cid: str, before: str) -> str:
    return (f"Стереть координаты: {geo_scope_text(emp, cid, before)}?"
            "\n\n"
            "Сами фото, даты и хэши останутся. Вернуть точки будет нельзя.")


def geo_state_text(d: dict) -> str:
    days = d.get("retention_days")
    term = "автоуборка выключена" if days == 0 else f"хранение {days} дн"
    parts = [f"снимков в индексе: {d.get('photos', 0)}",
             f"с координатами: {d.get('with_geo', 0)}",
             f"стёрто: {d.get('forgotten', 0)}", term]
    if d.get("oldest"):
        parts.append(f"самая старая точка: {str(d['oldest'])[:10]}")
    if d.get("cleared") is not None:
        parts.insert(0, f"стёрто сейчас: {d['cleared']}")
    return " · ".join(parts)


# --- беседы ------------------------------------------------------------------------
def chat_label(c: dict, duplicate: bool) -> str:
    title = c.get("title") or "(без названия)"
    label = f"{title}  ·  id {c.get('chat_id')}"
    if c.get("added_at"):
        label += f" · с {str(c['added_at'])[:10]}"
    if duplicate:
        label += " · ⚠ есть тёзка!"
    if c.get("removed"):
        label += " · бот удалён из беседы"
    return label


def chat_duplicates(chats: list[dict]) -> set[str]:
    """Title подделывает кто угодно (любой может создать группу-двойника и
    добавить бота) — дубли имён подсвечиваем, чтобы админ не включил чужую беседу."""
    titles = [(c.get("title") or "").strip().lower() for c in chats]
    return {t for t in titles if titles.count(t) > 1}


def chat_employees_text(emps: list[str]) -> str:
    if not emps:
        return "сотрудники не закреплены — посты не идут"
    return ", ".join(emps[:3]) + ("…" if len(emps) > 3 else "")


# --- руководители ------------------------------------------------------------------
# Направления руководителей — те же коды, что у сервера (db.DIRECTIONS).
# Пустой код — обычный руководитель со списком сотрудников.
DIRECTION_OPTIONS = (
    ("", "— по списку сотрудников —"),
    ("tj", "Таджикистан"),
    ("uz", "Узбекистан"),
    ("all", "СНГ (все сотрудники)"),
)
DIRECTION_TITLES = {code: title for code, title in DIRECTION_OPTIONS if code}
DIRECTION_HINT = ("Состав сотрудников направления сервер считает живьём по стране "
                  "реестра; отмечать их вручную не нужно.")


def direction_index(code: str) -> int:
    codes = [c for c, _t in DIRECTION_OPTIONS]
    return codes.index(code) if code in codes else 0


def direction_code(index: int) -> str:
    try:
        return DIRECTION_OPTIONS[int(index)][0]
    except (IndexError, ValueError, TypeError):
        return ""


def direction_employees_note(code: str) -> str:
    if not code:
        return ""
    return (f"направление «{DIRECTION_TITLES.get(code, code)}»: сотрудники считаются "
            "автоматически, галочки ниже не нужны")


def manager_row(m: dict) -> dict:
    direction = (m.get("direction") or "").strip()
    if direction:
        employees = f"направление: {DIRECTION_TITLES.get(direction, direction)}"
    else:
        employees = f"сотрудников: {len(m.get('employees') or [])}"
    return {
        "name": m.get("name") or "без имени",
        "cities": " · ".join(m.get("cities") or []) or "—",
        "employees": employees,
        "tid": str(m["telegram_id"]),
        "mismatch": bool(int(m.get("spelling_mismatch") or 0)),
    }


def city_chip_text(city: str, cities: list[dict]) -> str:
    cnt = next((x.get("count") for x in cities if x["name"] == city), None)
    return f"{city} {cnt}" if cnt else city


def emp_chip_text(emp: str, employees: list[dict]) -> str:
    cnt = next((x.get("contracts") for x in employees if x["employee"] == emp), None)
    return f"{emp} {cnt}" if cnt else emp


def manager_body(tid: str, name: str, cities: set[str], employees: set[str],
                 direction: str = "") -> dict:
    """Тело POST /admin/manager. direction всегда уходит явно: пустая строка
    снимает направление, иначе сервер сохранил бы прежнее (он подставляет
    старое значение только когда ключа нет вовсе)."""
    return {"telegram_id": int(tid), "name": (name or "").strip(),
            "cities": sorted(cities), "employees": sorted(employees),
            "direction": (direction or "").strip()}


def unknown_account_text(tid: str) -> str:
    return (f"Аккаунт {tid} боту ещё не писал, поэтому проверить, кто это, нельзя.\n\n"
            "Руководителю уходит полная сводка по его сотрудникам: ФИО должников, "
            "суммы, просроченные обещания, фото выездов.\n\nВсё равно выдать доступ?")


def name_mismatch_text(typed: str, real: str, tid: str) -> str:
    return (f"Ты вписал «{typed}», а телеграм-аккаунт {tid} принадлежит "
            f"«{real}».\n\nДоступ выдан ВЛАДЕЛЬЦУ аккаунта. Если это не тот "
            "человек — удали руководителя и заведи заново с верным id.")


def delete_text(tid) -> str:
    return f"Удалить руководителя {tid}? Он перестанет получать дайджест."
