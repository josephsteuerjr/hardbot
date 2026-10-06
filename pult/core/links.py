"""Раздел «Вход»: состояния персональных ссылок PWA — арифметика без Qt.

Человек попадает в приложение по короткой персональной ссылке: открыл — и
внутри, без бота и без ввода имени. Ссылка живёт несколько открытий (по
умолчанию два: второе — запас на ярлык «Домой», у которого своё хранилище, и
если ярлык родился без вшитого токена, лечится это только новым открытием
ссылки в браузере). Поэтому оператору важно не «выдал / не выдал», а сколько
открытий осталось и вошёл ли человек на самом деле — причём «вошёл» здесь
значит ровно «есть живой токен приложения на ЭТОМ сервере», а не «когда-то
числился активным»: базу на машину компании привозят с прода вместе с полем
users.last_seen, и верить ему нельзя.

Всё это считается здесь, а не в окне: состояния — самая хрупкая часть экрана,
и проверять их надо тестами без экрана. Окно остаётся тонким.
"""

from __future__ import annotations

from datetime import datetime

# Колонки таблицы: ((ключ, заголовок, ширина), …) — как ждёт widgets.Table.
LINK_COLUMNS = (
    ("emp", "Сотрудник", 210),
    ("kind", "Тип", 84),
    ("state", "Состояние", 172),
    ("uses", "Откр.", 62),
    ("issued", "Выдана", 96),
    ("seen", "Последний вход", 116),
    ("agent", "Устройство", 108),
)
# «1 из 2» — не число: выравнивать вправо нечего.
LINK_NUMERIC: frozenset[str] = frozenset()

# Ключ состояния едет скрытым хвостом строки: колонок в таблице семь, восьмой
# элемент модель не показывает и не ищет по нему — зато по нему красится
# строка, и окну не приходится разбирать обратно русский текст состояния.
STATE_IN_ROW = len(LINK_COLUMNS)

# Фильтры-чипы над таблицей: ключ → подпись.
LINK_FILTERS = (
    ("all", "все"),
    ("none", "не выдавали"),
    ("issued", "выдана"),
    ("opened", "открыта"),
    ("entered", "вошли"),
    ("revoked", "отозвана"),
)

# Цвет состояния — ролью темы, своих цветов у раздела нет.
STATE_ROLE = {
    "none": "muted",        # вход не выдавали — это не беда, это работа
    "issued": "muted",      # выдана, ждём
    "opened": "warn",       # ссылку открыли, но в приложении человека ещё нет
    "entered": "success",   # человек внутри — то, ради чего всё
    "expired": "warn",      # надо выдать заново
    "revoked": "danger",    # доступ закрыт руками оператора
}

# Сколько открытий разрешаем при выдаче. Два — норма (Safari + ярлык iOS),
# одно — «строго на один телефон», пять — «человек сидит далеко и путается».
USES_CHOICES = (1, 2, 5)
# Подписи — голые числа: рядом с комбобоксом стоит слово «открытий», и
# «открытий: 2 открытия» читалось бы как оговорка.
USES_LABELS = ("1", "2", "5")
DEFAULT_USES = 2
DEFAULT_USES_INDEX = USES_CHOICES.index(DEFAULT_USES)

# Класс устройства последнего обмена — как его называет сервер.
AGENT_TEXT = {
    "iphone": "iPhone", "ipad": "iPad", "android": "Android",
    "desktop": "компьютер", "desk": "ПК", "inapp": "в мессенджере", "other": "—",
}


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def link_alive(link: dict) -> bool:
    """Жива ли ссылка. Обычно признак приходит от сервера готовым; если поля
    нет (сервер чуть другой версии), считаем сами по сроку и открытиям — иначе
    только что выданная ссылка покажется просроченной, и оператор пойдёт
    выдавать её ещё раз, недоумевая."""
    link = link or {}
    if link.get("desktop_available"):
        return True
    if "alive" in link:
        return bool(link["alive"])
    if not link.get("code"):
        return False
    mx, uses = _int(link.get("max_uses")), _int(link.get("uses"))
    if mx and uses >= mx:
        return False
    expires = str(link.get("expires_at") or "")
    return not expires or expires >= datetime.now().isoformat(timespec="seconds")


def _later(a, b) -> str:
    """Позднее из двух ISO-времён; пустое — не в счёт (ISO сравнимы строками)."""
    return max(str(a or ""), str(b or ""))


def fmt_when(iso) -> str:
    """«2026-09-21T22:10:05» → «21.09 22:10»; дата без времени → «21.09»."""
    s = str(iso or "")
    if len(s) >= 16:
        return f"{s[8:10]}.{s[5:7]} {s[11:16]}"
    if len(s) >= 10:
        return f"{s[8:10]}.{s[5:7]}"
    return "—"


def fmt_day(iso) -> str:
    """«2026-09-28T…» → «28.09»; пусто → пусто."""
    s = str(iso or "")
    return f"{s[8:10]}.{s[5:7]}" if len(s) >= 10 else ""


def kind_text(row: dict) -> str:
    return "курьер" if (row or {}).get("kind") == "courier" else "выездник"


def agent_text(row: dict) -> str:
    return AGENT_TEXT.get(str((row or {}).get("agent") or ""), "—")


# --- сборка строк ------------------------------------------------------------
def pick_link(links: list[dict]) -> dict | None:
    """Из нескольких ссылок человека показываем одну — живую и самую свежую.

    Мёртвые берём, только если живых нет: иначе экран будет рассказывать про
    вчерашнюю просрочку вместо сегодняшней выдачи.
    """
    if not links:
        return None
    return sorted(links,
                  key=lambda x: (link_alive(x), str(x.get("created_at") or ""),
                                 str(x.get("code") or "")))[-1]


def merge_rows(employees=(), links=(), workers=(), revoked=()) -> list[dict]:
    """Одна строка на человека: реестр + выданные ссылки + факт входа.

    Идём от реестра, а не от привязок: сотрудник, которому вход ещё НЕ
    выдавали, обязан быть виден — иначе экран пуст ровно тогда, когда нужен
    больше всего (первый день, когда не выдавали никому). Кого в реестре нет,
    но ссылка или вход у него есть (курьер ГПХ), дописывается снизу: потерять
    человека с выданным доступом нельзя.

    `revoked` — кого отозвали в этом сеансе: telegram_id, ФИО или и то и
    другое. Сервер отозванную ссылку удаляет, и без этой памяти строка сразу
    после отзыва выглядела бы как «не выдавали» — оператор решил бы, что
    промахнулся кнопкой. Имя тут равноправно с id: после отзыва у человека
    может не остаться ни ссылки, ни привязки, и узнать строку больше не по чему.
    """
    revoked_ids = {str(t) for t in (revoked or ()) if str(t or "").strip()}
    people: dict[str, dict] = {}
    order: list[str] = []

    def touch(name, in_registry: bool = False) -> dict | None:
        name = str(name or "").strip()
        if not name:
            return None
        row = people.get(name)
        if row is None:
            row = {"employee": name, "kind": "agent", "telegram_id": None,
                   "contracts": None, "in_registry": False, "links": [],
                   "entered": False, "devices": 0, "last_seen": ""}
            people[name] = row
            order.append(name)
        if in_registry:
            row["in_registry"] = True
        return row

    for e in employees or ():
        row = touch(e.get("employee"), in_registry=True)
        if row is None:
            continue
        row["contracts"] = e.get("contracts")
        if e.get("kind"):
            row["kind"] = e["kind"]
        if e.get("telegram_id"):
            row["telegram_id"] = e["telegram_id"]

    for w in workers or ():
        row = touch(w.get("employee"))
        if row is None:
            continue
        if w.get("kind"):
            row["kind"] = w["kind"]
        if w.get("telegram_id"):
            row["telegram_id"] = w["telegram_id"]
        # «Вошёл» — только по живому токену приложения (entry == "pwa").
        # Ни users.last_seen, ни entry == "telegram" (это то же поле, вид
        # сбоку) доказательством не годятся: пишет last_seen одна лишь
        # Телеграм-ветка, а в контуре компании бота нет — вход по ссылке его
        # не трогает. Зато поле приезжает вместе с портфелями с прода, и по
        # нему ВСЕ перенесённые сотрудники выглядели бы вошедшими ещё до
        # выдачи ссылки: оператор не увидел бы ни кому выдавать, ни что стало
        # с выданным. Колонка «Последний вход» по той же причине считается
        # только по обменам ссылок на ЭТОМ сервере (last_use_at ниже).
        if w.get("entry") == "pwa":
            row["entered"] = True

    for link in links or ():
        row = touch(link.get("employee"))
        if row is None:
            continue
        if link.get("kind"):
            row["kind"] = link["kind"]
        if link.get("telegram_id"):
            row["telegram_id"] = link["telegram_id"]
        row["links"].append(link)
        if link.get("entered"):
            row["entered"] = True
        row["devices"] = max(row["devices"], _int(link.get("devices")))
        row["last_seen"] = _later(row["last_seen"], link.get("last_use_at"))

    out = []
    for name in order:
        row = people[name]
        best = pick_link(row["links"]) or {}
        row["code"] = str(best.get("code") or "")
        row["url"] = str(best.get("url") or "")
        row["uses"] = _int(best.get("uses"))
        row["max_uses"] = _int(best.get("max_uses"))
        row["alive"] = link_alive(best)
        row["created_at"] = str(best.get("created_at") or "")
        row["expires_at"] = str(best.get("expires_at") or "")
        row["agent"] = str(best.get("last_agent") or "")
        row["bonus_ios"] = bool(best.get("bonus_ios"))
        row["bonus_desktop"] = bool(best.get("bonus_desktop"))
        row["desktop_available"] = bool(best.get("desktop_available"))
        # Отзыв гасит ссылки, поэтому ссылка старше отзыва быть не может:
        # появилась новая — память об отзыве больше не нужна.
        row["revoked"] = not row["code"] and bool(
            revoked_ids & {str(row.get("telegram_id")), row["employee"]})
        out.append(row)
    return out


# --- состояния ---------------------------------------------------------------
def link_state(row: dict) -> str:
    """Ключ состояния строки: none · issued · opened · entered · expired · revoked.

    Порядок вопросов рабочий, а не логический: первой спрашиваем про ЖИВУЮ
    ещё не открытую ссылку. Её выдали только что, и оператор ждёт ровно
    открытия — даже если человек уже сидит в приложении со старого телефона
    (перевыдали на новый, ярлык слетел). Иначе факт входа навсегда закрывал
    бы собой состояние свежей ссылки, и «выдал — открыл?» было бы не увидеть.
    Факт входа сам никуда не девается: его считают плашки и чип «вошли».
    """
    row = row or {}
    if row.get("revoked"):
        return "revoked"
    if row.get("code") and row.get("alive") and _int(row.get("uses")) == 0:
        return "issued"
    if row.get("entered"):
        return "entered"
    if not row.get("code"):
        return "none"
    if not row.get("alive"):
        return "expired"
    return "opened"


def state_text(row: dict) -> str:
    """Состояние словами — так, как оператор пересказал бы его вслух."""
    row = row or {}
    state = link_state(row)
    uses, mx = _int(row.get("uses")), _int(row.get("max_uses"))
    if state == "none":
        return "не выдавали"
    if state == "issued":
        # «заново» — потому что человек в системе уже есть: так оператор видит
        # обе половины правды сразу, не теряя вошедшего из виду.
        return "выдана заново, не открыта" if row.get("entered") else "выдана, не открыта"
    if state == "opened":
        return f"открыта {uses} из {mx}" if mx else f"открыта {uses}"
    if state == "entered":
        devices = _int(row.get("devices"))
        return f"вошёл · устройств {devices}" if devices > 1 else "вошёл"
    if state == "expired":
        return "открытий не осталось" if mx and uses >= mx else "просрочена"
    return "отозвана"


def state_role(row: dict) -> str:
    return STATE_ROLE.get(link_state(row), "muted")


def uses_text(row: dict) -> str:
    """Колонка «Откр.»: израсходовано из выданного."""
    row = row or {}
    if not row.get("code"):
        return ""
    mx = _int(row.get("max_uses"))
    uses = _int(row.get("uses"))
    value = f"{uses} из {mx}" if mx else str(uses)
    return value + (" + ПК" if row.get("desktop_available") else "")


def uses_left(row: dict) -> int:
    """Сколько открытий осталось у ссылки (не меньше нуля)."""
    row = row or {}
    return max(_int(row.get("max_uses")) - _int(row.get("uses")), 0)


def matches(row: dict, key: str) -> bool:
    row = row or {}
    state = link_state(row)
    if key == "none":
        # Чип «не выдавали» отвечает на вопрос «кому надо выдать», поэтому сюда
        # же просроченные и исчерпанные: оператору важно действие, а не
        # бухгалтерия состояний.
        return state in ("none", "expired")
    if key == "entered":
        # «Вошли» — про людей, а не про состояние ссылки: тому, кто уже внутри,
        # могли только что выдать новую ссылку, и из системы он не делся.
        # Поэтому чип считает так же, как плашка, — по факту входа.
        return bool(row.get("entered"))
    if key in ("issued", "opened", "revoked"):
        return state == key
    return True


def filter_counts(rows) -> dict[str, int]:
    return {key: sum(1 for r in rows if matches(r, key)) for key, _title in LINK_FILTERS}


def link_row(row: dict) -> tuple:
    """Строка таблицы. Восьмой элемент — скрытый ключ состояния (STATE_IN_ROW)."""
    row = row or {}
    return (row.get("employee", ""),
            kind_text(row),
            state_text(row),
            uses_text(row),
            fmt_when(row.get("created_at")),
            fmt_when(row.get("last_seen")),
            agent_text(row),
            link_state(row))


def row_state(row: tuple) -> str:
    """Ключ состояния из строки таблицы (скрытый хвост) — для раскраски."""
    if row is None or len(row) <= STATE_IN_ROW:
        return ""
    return str(row[STATE_IN_ROW] or "")


def summary_tiles(rows) -> list[tuple[str, int]]:
    """Плашки карточки «Кто входит».

    Вошедших считаем по факту (живой токен), а не по состоянию строки: у
    вошедшего может лежать свежая невскрытая ссылка, и в плашке «вошли» ему
    место всё равно — плашки отвечают на «кто уже в системе».
    """
    total = len(rows)
    entered = sum(1 for r in rows if r.get("entered"))
    alive = sum(1 for r in rows if r.get("alive") and not r.get("revoked"))
    return [("в реестре", sum(1 for r in rows if r.get("in_registry"))),
            ("ссылок живо", alive),
            ("вошли", entered),
            ("ждут", total - entered)]


def link_summary(rows) -> str:
    """Одна строка под плашками: что происходит и что делать дальше."""
    total = len(rows)
    if not total:
        return "Реестр пуст — импортируй xlsx в разделе «Реестр»"
    entered = sum(1 for r in rows if r.get("entered"))
    need = sum(1 for r in rows if matches(r, "none"))
    opened = sum(1 for r in rows if link_state(r) == "opened")
    parts = [f"вошли {entered} из {total}"]
    if need:
        parts.append(f"вход не выдан {need}")
    if opened:
        parts.append(f"ссылку открыли, но в приложении ещё не были {opened}")
    if entered == total:
        parts.append("все на месте")
    return " · ".join(parts)


# --- адрес ссылки ------------------------------------------------------------
def base_of(url: str) -> str:
    """Публичный адрес приложения из выданной ссылки.

    Нужен, чтобы собрать адрес по коду из списка: сервер отдаёт в списке код,
    а не ссылку. Адрес админки сюда не годится — пульт ходит на сервер по
    служебному адресу, а телефон открывает публичный.
    """
    u = str(url or "").strip()
    for sep in ("/i/", "/#link=", "/#t="):
        i = u.find(sep)
        if i > 0:
            return u[:i]
    return u.rstrip("/")


def link_url(row: dict, base: str = "") -> str:
    """Короткий адрес ссылки строки: свой, либо собранный из кода и базы."""
    row = row or {}
    url = str(row.get("url") or "").strip()
    if url:
        return url
    code = str(row.get("code") or "").strip()
    base = base_of(base)
    return f"{base}/i/{code}" if base and code else ""


def fresh_row(d: dict, employee: str = "", kind: str = "agent") -> dict:
    """Ответ выдачи → строка того же вида, что в таблице.

    У свежей ссылки израсходовано ноль открытий — что бы сервер ни вернул в
    `uses`: там может стоять и «израсходовано», и эхо запроса «выдать на N
    открытий». Панель под таблицей считает остаток по одной формуле с
    таблицей, и путаницы «осталось 0» на только что выданной ссылке не будет.
    """
    d = d or {}
    mx = _int(d.get("max_uses")) or _int(d.get("uses")) or DEFAULT_USES
    return {"employee": str(d.get("employee") or employee or ""),
            "kind": kind,
            "telegram_id": d.get("telegram_id"),
            "code": str(d.get("code") or ""),
            "url": str(d.get("url") or ""),
            "uses": 0, "max_uses": mx, "alive": True, "entered": False,
            "devices": 0, "revoked": False,
            "created_at": "", "expires_at": str(d.get("expires_at") or ""),
            "days": _int(d.get("days")),
            "local_identity": bool(d.get("local_identity")),
            "agent": "", "bonus_ios": False, "links": []}


# --- тексты для человека -----------------------------------------------------
def issue_lines(row: dict) -> list[tuple[str, str]]:
    """Подписи под QR: (роль темы, текст). Роли — только из темы пульта."""
    row = row or {}
    who = row.get("employee") or "—"
    left = uses_left(row)
    lines: list[tuple[str, str]] = [("small", f"Кому: {who}")]

    until = fmt_day(row.get("expires_at"))
    days = _int(row.get("days"))
    when = f"действует до {until}" if until else (f"действует {days} дн." if days else "")
    left_text = (f"осталось открытий: {left}" if left else
                 "открытий не осталось — выдай заново")
    lines.append(("small", " · ".join(x for x in (when, left_text) if x)))

    lines.append(("small", "Покажи QR с экрана — сотрудник наведёт камеру телефона. "
                           "Открылось — он уже внутри: ссылка персональная, "
                           "имя вводить не надо."))
    # Ярлык уносит вход с собой сам: оболочка держит токен и в адресе (#t=), и
    # в персональном манифесте, из которого iOS берёт start_url. Внутри ярлыка
    # адресной строки нет — «вставить ссылку ещё раз» там физически некуда, и
    # просить об этом нельзя. Просим ровно одно действие — и говорим его всегда,
    # а не только при двух открытиях: помощь с ярлыком нужна как раз тогда,
    # когда одно открытие уже израсходовано.
    lines.append(("warn", "На iPhone: пусть добавит «На экран „Домой“» прямо с "
                          "открывшейся страницы — ярлык запомнит вход, вводить "
                          "в нём ничего не надо."))
    if left > 1:
        # Запасное открытие — не шаг инструкции, а лечение редкой беды: ярлык,
        # поставленный из старой вкладки, рождается без токена, и помогает
        # только новое открытие ссылки в браузере с повторной установкой.
        lines.append(("small", "Если ярлык всё-таки откроется без доступа — пусть "
                               "наведёт камеру на этот QR ещё раз и поставит ярлык "
                               "заново: запасное открытие оставлено на это."))
    elif left == 1:
        lines.append(("small", "Открытие последнее: если ярлык откроется без "
                               "доступа, выдай вход заново."))
    if row.get("local_identity"):
        lines.append(("small", "Вход по ссылке: у сотрудника служебная личность, "
                               "для работы нужен только телефон со ссылкой."))
    return lines


def issue_note(row: dict) -> str:
    """То же самое одним текстом — для модального окна ссылки."""
    return "\n".join(text for _role, text in issue_lines(row))


def reissue_text(row: dict) -> str:
    """Вопрос перед перевыдачей: что именно умрёт, а что доживёт."""
    who = (row or {}).get("employee") or "—"
    return (f"Выдать «{who}» новую ссылку?\n\n"
            "Прежняя НЕОТКРЫТАЯ ссылка перестанет работать. Уже открытую сервер "
            "оставляет дожить её срок — по ней могли ещё не поставить ярлык на "
            "телефон. Тот, кто уже вошёл, из приложения не вылетит.")


def revoke_text(row: dict) -> str:
    """Вопрос перед отзывом: отзыв разлогинивает телефон, это видно человеку."""
    who = (row or {}).get("employee") or "—"
    return (f"Отозвать вход у «{who}»?\n\n"
            "Ссылки перестанут работать, а телефон разлогинится: сотрудник "
            "увидит «Нет доступа» и вернётся в систему только по новой ссылке. "
            "Выезды, фото и метки остаются в базе.")


# --- массовая выдача ---------------------------------------------------------
# Старт пробы — это «выдать всем и разослать», а не шестьдесят кликов подряд.
# Сервер выдаёт одним запросом (/admin/app_links_bulk.json) по тем же правилам,
# что одиночную ссылку; здесь — что ему отправить и как показать ответ списком.

BULK_HEADERS = ("Сотрудник", "Кто", "Ссылка", "Открытий", "Действует до", "Ошибка")
MANAGER_WHO = "руководитель"
WORKER_WHO = "сотрудник"


def bulk_items(people_by_name: dict, names) -> list[dict]:
    """Строки для массовой выдачи из отмеченных ФИО раздела «Вход».

    Привязанному шлём его id — ровно как одиночная выдача: иначе сервер
    заведёт вторую служебную личность на то же ФИО, и портфель разъедется на
    два аккаунта. Имени, которого в списке нет, в выдачу не берём.
    """
    items = []
    for name in names or ():
        row = (people_by_name or {}).get(name)
        if not row:
            continue
        item = {"employee": name, "kind": row.get("kind") or "agent"}
        if row.get("telegram_id"):
            item["telegram_id"] = row["telegram_id"]
        items.append(item)
    return items


def manager_has_team(m: dict) -> bool:
    """Есть ли руководителю кого вести: направление, офис или люди поимённо.
    Города — только подпись сводки, охвата они не дают."""
    m = m or {}
    offices = [c for c in (m.get("office_cities") or []) if str(c).strip()]
    return bool((m.get("direction") or "").strip() or offices or (m.get("employees") or []))


def manager_items(managers) -> tuple[list[dict], list[str]]:
    """Руководители → строки выдачи; кому вести некого — в пропущенные.

    Руководителю шлём ТОЛЬКО id: с ФИО в запросе сервер привязал бы его к
    сотруднику и сделал выездником. Руководитель без команды увидит пустой
    пульт — выдавать ему вход на старте незачем, оператор увидит его имя в
    вопросе и решит сам.
    """
    items, skipped = [], []
    for m in managers or ():
        name = str(m.get("name") or m.get("telegram_id") or "—")
        if manager_has_team(m):
            items.append({"telegram_id": m.get("telegram_id")})
        else:
            skipped.append(name)
    return items, skipped


def bulk_rows(results, manager_ids=()) -> list[dict]:
    """Ответ сервера → строки итогового списка (удачные сверху, по алфавиту)."""
    mids = {str(t) for t in manager_ids or ()}
    rows = []
    for r in results or ():
        r = r or {}
        rows.append({
            "employee": str(r.get("employee") or "—"),
            "who": MANAGER_WHO if str(r.get("telegram_id")) in mids else WORKER_WHO,
            "url": str(r.get("url") or ""),
            "uses": _int(r.get("max_uses")),
            "expires": fmt_day(r.get("expires_at")),
            "error": str(r.get("error") or ""),
        })
    rows.sort(key=lambda x: (not x["url"], x["who"] != MANAGER_WHO, x["employee"]))
    return rows


def bulk_table(rows) -> list[tuple]:
    """Строки таблицы итогового окна — в порядке BULK_HEADERS."""
    return [(r["employee"], r["who"], r["url"], r["uses"] or "", r["expires"], r["error"])
            for r in rows or ()]


def bulk_summary(rows) -> str:
    rows = list(rows or ())
    ok = sum(1 for r in rows if r["url"])
    text = f"Выдано ссылок: {ok} из {len(rows)}."
    if ok < len(rows):
        text += " У остальных причина — в колонке «Ошибка»."
    if ok:
        text += (" Каждая ссылка персональная: перешлите её лично человеку "
                 "(не в общий чат).")
    return text


def bulk_text(rows) -> str:
    """Для буфера обмена: строка на человека «ФИО — ссылка». Так список читается
    и в мессенджере, и в заметке; для таблицы есть отдельная выгрузка в Excel."""
    return "\n".join(f"{r['employee']} — {r['url']}" for r in rows or () if r["url"])


def _uses_word(n: int) -> str:
    n = abs(int(n or 0))
    if n % 10 == 1 and n % 100 != 11:
        return "открытию"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "открытия"
    return "открытий"


def bulk_confirm_text(count: int, uses: int, entered: int = 0,
                      who: str = "отмеченным") -> str:
    """Вопрос перед массовой выдачей: что будет со старыми ссылками и входами."""
    text = (f"Выдать вход {who} — {count} чел., по {uses} {_uses_word(uses)}?\n\n"
            "У кого ссылку уже выдавали, но её не открыли, старая перестанет "
            "работать — вместо неё будет новая.")
    if entered:
        text += (f" Кто уже вошёл ({entered}), из приложения не вылетит: новая "
                 "ссылка будет запасной.")
    text += ("\n\nПосле выдачи откроется список «ФИО — ссылка»: его можно "
             "скопировать целиком или сохранить в Excel.")
    return text


def manager_bulk_text(count: int, skipped=()) -> str:
    """Вопрос перед выдачей входа всем руководителям."""
    text = (f"Выдать вход руководителям — {count} чел.?\n\n"
            "Ссылку можно открыть два раза: в пульте руководителя на компьютере и "
            "на его телефоне. Дальше входы своей команде руководитель выдаёт сам.")
    if skipped:
        text += ("\n\nБез команды — пропущены (им вести некого): "
                 + ", ".join(skipped) + ".")
    return text


def write_bulk_xlsx(path, rows) -> None:
    """Список выданных ссылок в Excel: шапка, ширины, кликабельные адреса."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Ссылки входа"
    ws.append(list(BULK_HEADERS))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows or ():
        ws.append([r["employee"], r["who"], r["url"], r["uses"] or None,
                   r["expires"], r["error"]])
        if r["url"]:
            link = ws.cell(row=ws.max_row, column=3)
            link.hyperlink = r["url"]
            link.font = Font(color="0563C1", underline="single")
    for col, width in zip("ABCDEF", (34, 14, 48, 10, 14, 40)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(path)
