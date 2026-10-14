"""Рассылка книг Excel почтой владельца — Outlook этой машины через COM.

Решение владельца 29.09.2026: «рассылку можно сделать просто от меня, прога
будет в фоне + COM». Пульт аналитика живёт в трее; поток пульта раз в полминуты
сверяется с расписанием и, когда пора, берёт с сервера книгу
(`/admin/stats_book.xlsx`), кладёт её во временную папку и отправляет письмо из
Outlook — от имени владельца, с его подписью и в его «Отправленных». Ни
почтового сервера, ни паролей от почты пульт не знает.

Здесь — чистые части: расписание, тема, текст письма, итоги из книги. Их
проверяют тесты без Outlook и без сети. COM и сеть — в `send_outlook` и в
`desk_backend`.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta

# Какие книги умеет рассылка. «day» — вечерняя книга за день, «week» — книга за
# семь дней по выбранному дню недели.
BOOKS = {
    "day": "Сводка за день",
    "week": "Неделя",
}
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
DIRECTIONS = {"": "Вся команда", "tj": "Таджикистан", "uz": "Узбекистан"}
HISTORY_KEEP = 30
EMAIL_RE = re.compile(r"^[^@\s;,<>]+@[^@\s;,<>]+\.[^@\s;,<>]+$")

DEFAULTS = {
    "enabled": False,
    "recipients": [],            # [{"name": "…", "email": "…"}]
    "days": [0, 1, 2, 3, 4],     # по каким дням уходит «Сводка за день»
    "time": "19:30",             # местное время этой машины
    "books": {"day": True, "week": False},
    "week_day": 4,               # «Неделя» — по пятницам
    "direction": "",
    "subject": "Выезды",         # начало темы письма
    "note": "",                  # своя строка в начале письма
    "last": {},                  # {"day": "2026-09-29", ...} — когда уходила
    "history": [],
}


def config(raw: dict | None) -> dict:
    """Настройки рассылки с заполненными умолчаниями."""
    cfg = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in DEFAULTS.items()}
    for k, v in (raw or {}).items():
        if k in cfg:
            cfg[k] = v
    cfg["books"] = {**DEFAULTS["books"], **(cfg.get("books") or {})}
    return cfg


def parse_time(text: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(text or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h, mi) if 0 <= h < 24 and 0 <= mi < 60 else None


def clean(body: dict, old: dict | None = None) -> dict:
    """Новые настройки из окна: проверить и привести. Ошибка — ValueError с
    текстом для человека."""
    cfg = config(old)
    people = []
    for r in body.get("recipients") or []:
        email = str((r or {}).get("email") or "").strip()
        name = str((r or {}).get("name") or "").strip()
        if not email and not name:
            continue
        if not EMAIL_RE.match(email):
            raise ValueError(f"Не похоже на адрес почты: «{email or name}»")
        people.append({"name": name, "email": email})
    if parse_time(body.get("time", cfg["time"])) is None:
        raise ValueError("Время — в виде ЧЧ:ММ, например 19:30")
    days = sorted({int(d) for d in body.get("days", cfg["days"]) if str(d).isdigit() and 0 <= int(d) <= 6})
    books = {k: bool((body.get("books") or {}).get(k, cfg["books"].get(k))) for k in BOOKS}
    direction = str(body.get("direction", cfg["direction"]) or "")
    if direction not in DIRECTIONS:
        raise ValueError("Непонятное направление")
    week_day = int(body.get("week_day", cfg["week_day"]))
    if not 0 <= week_day <= 6:
        raise ValueError("Непонятный день недели")
    enabled = bool(body.get("enabled", cfg["enabled"]))
    if enabled and not people:
        raise ValueError("Добавьте хотя бы одного получателя")
    if enabled and not any(books.values()):
        raise ValueError("Выберите, что отправлять")
    cfg.update(enabled=enabled, recipients=people, days=days, books=books,
               time="%02d:%02d" % parse_time(body.get("time", cfg["time"])),
               week_day=week_day, direction=direction,
               subject=str(body.get("subject", cfg["subject"]) or "Выезды").strip()[:80],
               note=str(body.get("note", cfg["note"]) or "").strip()[:1000])
    return cfg


# --- расписание -----------------------------------------------------------------
def _book_day(cfg: dict, book: str, today: date) -> bool:
    if book == "day":
        return today.weekday() in (cfg.get("days") or [])
    if book == "week":
        return today.weekday() == int(cfg.get("week_day", 4))
    return False


def due(cfg: dict, now: datetime) -> list[str]:
    """Какие книги пора отправить прямо сейчас.

    Пора — если сегодня день этой книги, время наступило, а сегодня она ещё не
    уходила. Пульт был выключен в 19:30 и включён в 21:00 — книга уйдёт в
    21:00 того же дня; на следующий день вчерашняя уже не догоняет."""
    if not cfg.get("enabled") or not cfg.get("recipients"):
        return []
    hm = parse_time(cfg.get("time") or "")
    if hm is None or (now.hour, now.minute) < hm:
        return []
    today = now.date().isoformat()
    last = cfg.get("last") or {}
    return [b for b in BOOKS if (cfg.get("books") or {}).get(b)
            and _book_day(cfg, b, now.date()) and last.get(b) != today]


def next_run(cfg: dict, now: datetime) -> str:
    """«сегодня в 19:30 — сводка за день» — для подписи в окне; пусто, если
    рассылка выключена."""
    if not cfg.get("enabled") or not cfg.get("recipients"):
        return ""
    hm = parse_time(cfg.get("time") or "")
    if hm is None:
        return ""
    for shift in range(0, 8):
        day = now.date() + timedelta(days=shift)
        books = [b for b in BOOKS if (cfg.get("books") or {}).get(b)
                 and _book_day(cfg, b, day)
                 and (cfg.get("last") or {}).get(b) != day.isoformat()]
        if not books:
            continue
        if shift == 0 and (now.hour, now.minute) >= hm:
            when = "в ближайшую минуту"
        else:
            when = ("сегодня" if shift == 0 else "завтра" if shift == 1
                    else WEEKDAYS[day.weekday()]) + f" в {cfg['time']}"
        return f"{when} — " + ", ".join(BOOKS[b].lower() for b in books)
    return ""


# --- письмо ----------------------------------------------------------------------
def query(book: str, cfg: dict, today: date) -> dict:
    """Параметры /admin/stats_book.xlsx для книги."""
    q = {"direction": cfg.get("direction") or ""}
    if book == "day":
        q.update(kind="daily", date=today.isoformat())
    else:
        q.update(preset="7d")
    return {k: v for k, v in q.items() if v}


def period_text(book: str, today: date) -> str:
    if book == "day":
        return f"{today:%d.%m.%Y}"
    start = today - timedelta(days=6)
    return f"{start:%d.%m}–{today:%d.%m.%Y}"


def file_name(book: str, cfg: dict, today: date) -> str:
    what = "Сводка выездов" if book == "day" else "Выезды за неделю"
    where = f" {DIRECTIONS[cfg.get('direction') or '']}" if cfg.get("direction") else ""
    return f"{what}{where} {period_text(book, today).replace('–', '-')}.xlsx"


def subject(book: str, cfg: dict, today: date) -> str:
    head = (cfg.get("subject") or "Выезды").strip()
    where = f" · {DIRECTIONS[cfg['direction']]}" if cfg.get("direction") else ""
    what = "сводка за" if book == "day" else "неделя"
    return f"{head}{where} · {what} {period_text(book, today)}"


def _greeting(now: datetime) -> str:
    if now.hour < 12:
        return "Доброе утро!"
    if now.hour < 18:
        return "Добрый день!"
    return "Добрый вечер!"


# Строки итогов письма: подпись → заголовки колонки «Сводки» (дневной и за
# период). Берём строку «Итого» самой книги: письмо и вложение не разойдутся.
SUMMARY_ROWS = (
    ("Выездов", ("Выездов сегодня", "Выездов за период")),
    ("Отработано должников", ("Отработано сегодня", "Отработано за период")),
    ("Оплатили", ("Оплачен сегодня", "Оплачен за период")),
    ("Обещают оплатить", ("Обещают сегодня", "Обещают за период")),
    ("Выезд подтверждён на адресе", ("На адресе подтверждено",)),
    ("У адреса не был", ("У адреса не был",)),
)


def summary_from_book(blob: bytes) -> list[tuple[str, int]]:
    """Итоги со строки «Итого» листа «Сводка»: [(подпись, число)]."""
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    if "Сводка" not in wb.sheetnames:
        return []
    rows = list(wb["Сводка"].iter_rows(min_row=3, values_only=True))
    if not rows:
        return []
    head = [str(h or "") for h in rows[0]]
    total = next((r for r in rows[1:] if r and r[0] == "Итого"), None)
    if total is None:
        return []
    out = []
    for label, titles in SUMMARY_ROWS:
        for t in titles:
            if t in head:
                v = total[head.index(t)]
                if isinstance(v, (int, float)):
                    out.append((label, int(v)))
                break
    return out


def _n(v: int) -> str:
    return f"{v:,}".replace(",", " ")


def body_html(book: str, cfg: dict, today: date, summary: list[tuple[str, int]],
              now: datetime) -> str:
    what = (f"Сводка выездов за {today:%d.%m.%Y}" if book == "day"
            else f"Выезды за неделю {period_text(book, today)}")
    if cfg.get("direction"):
        what += f" ({DIRECTIONS[cfg['direction']]})"
    parts = [f"<p>{_greeting(now)}</p>"]
    if cfg.get("note"):
        parts.append("<p>" + html.escape(cfg["note"]).replace("\n", "<br>") + "</p>")
    parts.append(f"<p>{html.escape(what)} — во вложении.</p>")
    if summary:
        parts.append("<ul>" + "".join(
            f"<li>{html.escape(label)}: <b>{_n(v)}</b></li>" for label, v in summary) + "</ul>")
    parts.append("<p style=\"color:#7a6a5a\">Пояснения к колонкам — в самой книге, "
                 "на листе «Как читать».</p>")
    return ("<div style=\"font-family:Segoe UI,Arial,sans-serif;font-size:10.5pt\">"
            + "".join(parts) + "</div>")


def remember(cfg: dict, book: str, *, ok: bool, when: datetime, to: list[str],
             error: str = "", file: str = "", manual: bool = False) -> dict:
    """Записать попытку в историю. Удачная плановая отправка отмечает день —
    второй раз сегодня книга сама не уйдёт. Ручная «Отправить сейчас» день не
    отмечает: расписание остаётся в силе."""
    cfg = dict(cfg)
    if ok and not manual:
        cfg["last"] = {**(cfg.get("last") or {}), book: when.date().isoformat()}
    item = {"at": when.isoformat(timespec="seconds"), "book": book, "ok": ok,
            "to": to, "error": error, "file": file, "manual": manual}
    cfg["history"] = ([item] + list(cfg.get("history") or []))[:HISTORY_KEEP]
    return cfg


def history_rows(cfg: dict) -> list[dict]:
    out = []
    for h in cfg.get("history") or []:
        out.append({"at": str(h.get("at") or "").replace("T", " ")[:16],
                    "book": BOOKS.get(h.get("book"), h.get("book") or ""),
                    "state": ("отправлено" if h.get("ok") else "не ушло")
                    + (" вручную" if h.get("manual") and h.get("ok") else ""),
                    "ok": bool(h.get("ok")),
                    "to": ", ".join(h.get("to") or []),
                    "error": h.get("error") or ""})
    return out


def with_signature(body: str, signature_doc: str) -> str:
    """Наш текст — внутрь <body> письма, перед подписью. Outlook отдаёт подпись
    целым HTML-документом, и текст перед <html> ложился бы за пределы письма."""
    m = re.search(r"<body[^>]*>", signature_doc or "", re.I)
    if not m:
        return body + (signature_doc or "")
    return signature_doc[:m.end()] + body + signature_doc[m.end():]


# --- Outlook ----------------------------------------------------------------------
def outlook_running() -> bool:
    """Открыт ли Outlook сейчас. Не запускает его: просто спрашивает COM."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return False
    pythoncom.CoInitialize()
    try:
        win32com.client.GetActiveObject("Outlook.Application")
        return True
    except Exception:  # noqa: BLE001 — нет запущенного Outlook
        return False
    finally:
        pythoncom.CoUninitialize()


OUTLOOK_BUSY_TEXT = ("Outlook ждёт ответа в открытом окне (вопрос, пароль, "
                     "предупреждение). Переключитесь в Outlook, закройте это окно "
                     "и нажмите ещё раз.")


def outlook_error_text(exc: BaseException) -> str:
    """Ошибка Outlook словами. COM отдаёт кортеж с описанием в excepinfo[2];
    трейсбек человеку не нужен."""
    detail = ""
    info = exc.args[2] if len(getattr(exc, "args", ())) > 2 else None
    if isinstance(info, tuple) and len(info) > 2 and info[2]:
        detail = str(info[2])
    detail = detail or (str(exc.args[1]) if len(getattr(exc, "args", ())) > 1 else str(exc))
    low = detail.lower()
    if "диалогов" in low or "dialog" in low:
        return OUTLOOK_BUSY_TEXT
    return f"Outlook не принял письмо: {detail}"


def _busy(exc: BaseException) -> bool:
    return outlook_error_text(exc) == OUTLOOK_BUSY_TEXT


def _signature(ol) -> str:
    """Подпись по умолчанию — у отдельного черновика-«донора».

    Подпись Outlook вставляет, когда у письма появляется окно (GetInspector).
    Но письмо, у которого такое окно открывали, Outlook потом отказывается
    ОТПРАВЛЯТЬ: «Параметр задан неверно» (проверено 29.09.2026; показать его
    можно). Поэтому окно открываем у донора, берём его HTML и закрываем донора
    без сохранения, а настоящее письмо окна не видит вовсе."""
    donor = None
    try:
        donor = ol.CreateItem(0)
        donor.GetInspector
        return donor.HTMLBody or ""
    except Exception:  # noqa: BLE001 — без подписи письмо всё равно уйдёт
        return ""
    finally:
        if donor is not None:
            try:
                donor.Close(1)
            except Exception:  # noqa: BLE001
                pass


def send_outlook(to: list[str], subject_text: str, html_body: str, attachment: str, *,
                 display: bool = False, tries: int = 3, pause: float = 4.0, operation_id=None) -> None:
    """Письмо из Outlook этой машины. display=True — открыть черновик и не
    отправлять (проверить, как выглядит).

    Подпись владельца берётся у черновика-донора (см. `_signature`): текст
    ставим ПЕРЕД ней, и письмо выглядит так, будто его написали руками.

    Display при открытом диалоге Outlook можно повторить (tries раз). Send
    вызывается единожды: ошибка может скрывать состоявшуюся отправку.
    При operation_id черновик сохраняется с HardOperationId до Send, чтобы
    оператор мог найти его и сверить результат после неопределённого ответа.
    Несохранённый черновик без номера закрывается без сохранения."""
    import time

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    mail = None
    try:
        try:
            ol = win32com.client.Dispatch("Outlook.Application")
            signature = _signature(ol)
            mail = ol.CreateItem(0)
            mail.To = "; ".join(to)
            mail.Subject = subject_text
            if operation_id:
                mail.Subject += " [Хард " + str(operation_id) + "]"
            mail.HTMLBody = with_signature(html_body, signature)
            mail.Attachments.Add(str(attachment))
            if operation_id:
                mail.UserProperties.Add("HardOperationId", 1).Value = str(operation_id)
                mail.Save()
            if not display:
                tries = 1
            for attempt in range(max(1, tries)):
                try:
                    if display:
                        mail.Display(False)
                    else:
                        mail.Send()
                    break
                except pythoncom.com_error as exc:
                    if not _busy(exc) or attempt + 1 >= max(1, tries):
                        raise
                    time.sleep(pause)
            mail = None                      # показано или отправлено — не трогаем
        except pythoncom.com_error as exc:
            raise RuntimeError(outlook_error_text(exc)) from exc
        if not display:
            # Если Outlook был закрыт и поднят ради письма, оно могло бы остаться
            # в «Исходящих» до следующего запуска: просим отправить сразу.
            try:
                ol.Session.SendAndReceive(False)
            except Exception:  # noqa: BLE001
                pass
    finally:
        if mail is not None:
            try:
                mail.Close(1)                # olDiscard: скрытый черновик не копится
            except Exception:  # noqa: BLE001
                pass
        pythoncom.CoUninitialize()
