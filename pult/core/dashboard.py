"""Раздел «Сегодня»: плашки, столбики, молчащие, доставка — из /admin/today;
плюс справка о реестре из /admin/stats."""

from __future__ import annotations

from .text import fmt_n

# Плашки дня: ключ ответа → подпись.
TILES = (
    ("visits_today", "Выездов сегодня"), ("visits_yesterday", "Вчера"),
    ("visits_7d", "За 7 дней"), ("employees_active", "В работе"),
    ("paid_today", "Оплачен сегодня"), ("promised_today", "Обещают"),
    ("overdue", "Обещаний просрочено"),
)

SILENT_COLUMNS = (
    ("emp", "Сотрудник", 200), ("portfolio", "Должников", 80),
    ("entry", "Вход", 80), ("last", "Последний выезд", 120), ("idle", "Простой", 70),
)
SILENT_NUMERIC = {"portfolio", "idle"}

# Старые помощники «Сводки» — их всё ещё зовёт render_stats со /admin/stats.
METRICS = (("clients", "Контрагентов"), ("visited", "С визитом"),
           ("emps", "Сотрудников"), ("binds", "Выбрали себя"), ("marks", "Меток"))
COUNTRIES = (("tj", "🇹🇯 Таджикистан"), ("uz", "🇺🇿 Узбекистан"))
COUNTRIES_DESC = ("Отдельно по закреплённым кейсам. Республика берётся из настроенного "
                  "scope руководителя, а не из числового поля «Пул».")
REASON_TEXT = {"absent_from_registry": "нет в срезе реестра", "recalled": "отозваны",
               "manual": "сняты вручную", "(нет)": "без причины"}
ENTRY_TEXT = {"pwa": "PWA", "telegram": "Telegram", "none": "не заходил"}


def tile_values(d: dict) -> dict[str, str]:
    k = d.get("kpi") or {}
    out = {key: fmt_n(k.get(key)) for key, _t in TILES}
    if k.get("employees_total") is not None:
        out["employees_active"] = f"{fmt_n(k.get('employees_active'))} / {fmt_n(k.get('employees_total'))}"
    return out


def bars(d: dict) -> list[dict]:
    return [{"label": str(x.get("day") or ""), "value": x.get("visits") or 0,
             "weekend": bool(x.get("weekend"))} for x in (d.get("days") or [])]


def silent_row(r: dict) -> tuple:
    last = str(r.get("last_activity") or "")[:10] or "—"
    return (r.get("employee", ""), fmt_n(r.get("portfolio")),
            ENTRY_TEXT.get(r.get("entry") or "none", "—"), last,
            "" if r.get("idle_days") is None else r.get("idle_days"))


def silent_note(d: dict) -> str:
    silent = d.get("silent") or []
    k = d.get("kpi") or {}
    if not silent:
        return "Сегодня выехали все, у кого есть портфель."
    never = sum(1 for r in silent if (r.get("entry") or "none") == "none")
    text = f"{len(silent)} из {fmt_n(k.get('employees_total'))} с портфелем сегодня без выездов"
    if never:
        text += f" · {never} ни разу не открывали приложение"
    return text


def delivery_pairs(d: dict) -> list[tuple[str, object]]:
    dv = d.get("delivery") or {}

    def line(kind):
        st = dv.get(kind) or {}
        return f"ушло {fmt_n(st.get('sent'))} · упало {fmt_n(st.get('failed'))}"
    return [("карточки визитов", line("visit")), ("сводка", line("digest")),
            ("обещания", line("promise")), ("беседы", line("report_chat"))]


def delivery_note(d: dict) -> str:
    dv = d.get("delivery") or {}
    parts = []
    blocked = dv.get("blocked") or []
    if blocked:
        parts.append("Заблокированы (нужен /start боту): "
                     + ", ".join(str(b.get("target_name") or b.get("target_id")) for b in blocked[:8]))
    no_dm = (d.get("managers") or {}).get("no_dm") or []
    if no_dm:
        parts.append("Ни разу не писали боту — им не дойдёт ничего: " + ", ".join(no_dm))
    last = dv.get("last_error")
    if last:
        parts.append(f"Последняя ошибка: {last.get('kind')} → {last.get('target_id')}: "
                     f"{str(last.get('error') or '')[:90]}")
    return "\n".join(parts) or "Сбоев доставки сегодня нет."


def country_today_pairs(c: dict) -> list[tuple[str, object]]:
    return [("выездов сегодня", c.get("visits_today")),
            ("в работе", f"{fmt_n(c.get('active'))} / {fmt_n(c.get('employees'))}"),
            ("обещают", c.get("promised_today")), ("просрочено", c.get("overdue")),
            ("должников закреплено", c.get("portfolio"))]


def registry_pairs(d: dict) -> list[tuple[str, object]]:
    r = d.get("registry") or {}
    return [("договоров", r.get("clients")), ("закреплено", r.get("assigned")),
            ("без ответственного", sum((r.get("unassigned") or {}).values())),
            ("с визитом бота", r.get("visited")), ("сотрудников", r.get("employees"))]


def registry_note(d: dict) -> str:
    r = d.get("registry") or {}
    un = r.get("unassigned") or {}
    parts = []
    if un:
        parts.append("Без ответственного: " + " · ".join(
            f"{REASON_TEXT.get(k, k)} {fmt_n(v)}" for k, v in
            sorted(un.items(), key=lambda kv: -kv[1])))
    e = d.get("enrichment") or {}
    last = str(e.get("last_delivered_at") or "")
    parts.append(f"Обогащение: в реестре доставленного {fmt_n(e.get('ledger_cells'))} ячеек "
                 f"по {fmt_n(e.get('ledger_contracts'))} КД"
                 + (f" · последняя запись {last[:10]} {last[11:16]}" if last else
                    " · ещё не записывали"))
    return "\n".join(parts)


# --- старая «Сводка» (справка о реестре из /admin/stats) --------------------
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
