"""Карта колонок реестра на стороне пульта.

Зачем модуль вообще существует. Общий реестр — чужой файл: офис переименовывает
колонки и добавляет свои, ни у кого не спрашивая. Раньше любое такое изменение
было аварией: обязательная колонка исчезала по имени, импорт вставал целиком, и
починить его мог только программист правкой кода и выкладкой сервера.

Карта колонок переносит это решение оператору. Она живёт в рабочей базе (сервер,
`/admin/registry_columns.json`), а здесь — её рабочая копия и разбор шапки книги
теми же правилами, что применит сервер при импорте. Пульт обязан показывать
ровно то, что потом произойдёт, поэтому нормализация заголовков и порядок
решений здесь совпадают с серверными (`db._norm_title`, `db.import_registry`).

Три вида решений:
    alias  — «эта колонка файла и есть вот это поле реестра» (переименование);
    ignore — «колонка не нужна, не показывай её как незнакомую»;
    extra  — «читай как дополнительное поле» (новая колонка → БД и карточка).
"""

from __future__ import annotations

import re

import enrich

norm_title = enrich.norm_title

# Пустая карта в том же виде, в каком её отдаёт и принимает сервер.
EMPTY_MAP: dict = {"version": 1, "aliases": {}, "ignored": [], "extras": [],
                   "updated_at": "", "updated_by": ""}

# Карта и справочники сервера. Заполняются `remember(...)` после успешного
# запроса; до первого ответа сервера пульт ведёт себя ровно как раньше — по
# одному контракту, без операторских решений.
_state: dict = {"map": dict(EMPTY_MAP), "schema": [], "contract_aliases": {},
                "contract_ignored": [], "contract_ignored_patterns": [],
                "required_keys": [], "loaded": False}


def remember(payload: dict) -> dict:
    """Запомнить ответ `/admin/registry_columns.json`. Возвращает карту."""
    payload = payload or {}
    _state["map"] = normalize_map(payload.get("map"))
    _state["schema"] = list(payload.get("schema") or [])
    _state["contract_aliases"] = dict(payload.get("contract_aliases") or {})
    _state["contract_ignored"] = list(payload.get("contract_ignored") or [])
    _state["contract_ignored_patterns"] = list(
        payload.get("contract_ignored_patterns") or [])
    _state["required_keys"] = list(payload.get("required_keys") or [])
    _state["loaded"] = bool(payload.get("schema"))
    return _state["map"]


def update_map(value) -> dict:
    """Заменить только карту, оставив справочники сервера. Нужно после
    сохранения: сервер отвечает картой, а схема и контракт не менялись."""
    _state["map"] = normalize_map(value)
    return _state["map"]


def forget() -> None:
    """Сбросить состояние (смена сервера — карта другой базы не подходит)."""
    _state.update({"map": dict(EMPTY_MAP), "schema": [], "contract_aliases": {},
                   "contract_ignored": [], "contract_ignored_patterns": [],
                   "required_keys": [], "loaded": False})


def current_map() -> dict:
    return _state["map"]


def schema() -> list[dict]:
    """Схема полей реестра с сервера; пусто — если ответа ещё не было."""
    return _state["schema"]


def loaded() -> bool:
    return bool(_state["loaded"])


def normalize_map(value) -> dict:
    """Привести карту к ожидаемой форме, не падая на чужом/старом ответе."""
    out = {"version": 1, "aliases": {}, "ignored": [], "extras": [],
           "updated_at": "", "updated_by": ""}
    if not isinstance(value, dict):
        return out
    aliases = value.get("aliases")
    if isinstance(aliases, dict):
        for key, titles in aliases.items():
            if isinstance(titles, str):
                titles = [titles]
            if not isinstance(titles, list):
                continue
            cleaned = [" ".join(str(t).split()) for t in titles
                       if " ".join(str(t).split())]
            if cleaned:
                out["aliases"][str(key)] = cleaned
    ignored = value.get("ignored")
    if isinstance(ignored, list):
        out["ignored"] = [" ".join(str(t).split()) for t in ignored
                          if " ".join(str(t).split())]
    extras = value.get("extras")
    if isinstance(extras, list):
        for item in extras:
            if isinstance(item, str):
                item = {"title": item}
            if not isinstance(item, dict):
                continue
            title = " ".join(str(item.get("title") or "").split())
            if not title:
                continue
            out["extras"].append({
                "title": title,
                "label": " ".join(str(item.get("label") or "").split()) or title,
                "show_in_card": bool(item.get("show_in_card", True)),
            })
    out["updated_at"] = str(value.get("updated_at") or "")
    out["updated_by"] = str(value.get("updated_by") or "")
    return out


# --- сопоставление заголовков ------------------------------------------------
def contract_title_to_key() -> dict[str, str]:
    """{нормализованный заголовок: ключ} по одному контракту, без карты.

    Схему берём с сервера, а если ответа ещё не было — из локального модуля
    полей: он лежит в том же checkout и совпадает с серверным по построению.
    """
    fields = _state["schema"]
    if not fields:
        import fields as local_fields
        fields = [{"key": f.key, "title": f.title, "block": f.block,
                   "source": f.source} for f in local_fields.FIELDS]
    out = {norm_title(f["title"]): f["key"] for f in fields}
    aliases = _state["contract_aliases"]
    if not aliases:
        import exchange_contract as contract
        aliases = contract.WORKBOOK_HEADER_ALIASES
    for key, titles in aliases.items():
        for title in titles:
            out[norm_title(title)] = key
    return out


def title_to_key() -> dict[str, str]:
    """Контракт + решения оператора — ровно как их сложит сервер при импорте."""
    out = contract_title_to_key()
    for key, titles in current_map()["aliases"].items():
        for title in titles:
            out[norm_title(title)] = key
    return out


def _ignored_titles() -> set[str]:
    titles = list(_state["contract_ignored"])
    if not titles:
        import exchange_contract as contract
        titles = list(contract.WORKBOOK_ALLOWED_IGNORED_HEADERS)
    return {norm_title(t) for t in titles} | {
        norm_title(t) for t in current_map()["ignored"]}


def _ignored_patterns() -> list[re.Pattern]:
    patterns = list(_state["contract_ignored_patterns"])
    if not patterns:
        import exchange_contract as contract
        patterns = list(contract.WORKBOOK_ALLOWED_IGNORED_HEADER_PATTERNS)
    out = []
    for pattern in patterns:
        try:
            out.append(re.compile(pattern))
        except re.error:
            continue
    return out


def extras_by_slot() -> dict[str, dict]:
    return {norm_title(item["title"]): item for item in current_map()["extras"]}


def analyze(grid: list[list]) -> dict:
    """Разбор шапки книги для экрана «Колонки реестра».

    Возвращает всё, что нужно показать человеку и по чему он принимает решение:
    какая строка признана шапкой, что распознано, каких обязательных полей нет,
    какие колонки файла бот не читает и на какое поле каждая из них похожа.
    """
    import exchange_contract as contract

    mapping = title_to_key()
    rows = grid or []
    best_h, best_keys, best_n, best_has_id = 0, [], -1, False
    for i, row in enumerate(rows[:contract.WORKBOOK_HEADER_SEARCH_ROWS]):
        keys = [mapping.get(norm_title(c)) for c in row]
        n = sum(1 for k in keys if k)
        has_id = contract.WORKBOOK_CONTRACT_KEY in keys
        if (has_id, n) > (best_has_id, best_n):
            best_has_id, best_n, best_h, best_keys = has_id, n, i, keys

    fields = _state["schema"]
    if not fields:
        import fields as local_fields
        fields = [{"key": f.key, "title": f.title, "block": f.block,
                   "source": f.source} for f in local_fields.FIELDS]
    by_key = {f["key"]: f for f in fields}

    header = rows[best_h] if rows else []
    import edit_tracking
    edit_columns = edit_tracking.columns(header)
    present = {k for k in best_keys if k}
    ignored_titles, patterns = _ignored_titles(), _ignored_patterns()
    extras = extras_by_slot()
    operator_alias_of = {norm_title(t): key
                         for key, titles in current_map()["aliases"].items()
                         for t in titles}

    recognized: list[dict] = []
    unknown: list[dict] = []
    ignored: list[dict] = []
    extra_columns: list[dict] = []
    for index, cell in enumerate(header):
        title = " ".join(str(cell or "").split())
        if not title:
            continue
        slot = norm_title(title)
        key = edit_columns.get(index) or (best_keys[index] if index < len(best_keys) else None)
        if key:
            recognized.append({
                "column": index + 1, "title": title, "key": key,
                "field_title": edit_tracking.LOGICAL_TITLES.get(key, by_key.get(key, {}).get("title", key)),
                "by_operator": operator_alias_of.get(slot) == key,
            })
        elif slot in extras:
            extra_columns.append({"column": index + 1, "title": title,
                                  "label": extras[slot]["label"],
                                  "show_in_card": extras[slot]["show_in_card"]})
        elif slot in ignored_titles or any(p.fullmatch(title) for p in patterns):
            ignored.append({"column": index + 1, "title": title})
        else:
            unknown.append({"column": index + 1, "title": title,
                            "suggest": suggest_key(title, by_key, present)})

    required = list(_state["required_keys"]) or list(
        contract.WORKBOOK_TEST_SUBSET_REQUIRED_KEYS)
    missing = [{"key": k, "title": by_key.get(k, {}).get("title", k),
                "required": k in required}
               for k in (f["key"] for f in fields) if k not in present]
    return {
        "header_row": best_h + 1,
        "has_contract_key": best_has_id,
        "recognized": recognized,
        "missing": missing,
        "missing_required": [m for m in missing if m["required"]],
        "unknown": unknown,
        "ignored": ignored,
        "extras": extra_columns,
    }


def read_header_grid(path: str, rows: int = 12) -> list[list]:
    """Первые строки листа «Портфели» — только чтобы разобрать шапку.

    Отдельный путь от `read_registry`: тот сначала требует колонку
    ответственного и падает раньше, чем человек успевает увидеть, что именно
    в книге поменялось. Экран «Колонки реестра» обязан открываться и на книге,
    которую импорт принять пока не может.
    """
    import exchange_contract as contract

    from io import BytesIO
    from pathlib import Path
    from python_calamine import CalamineWorkbook
    from .workbook import cell_json

    # Sequential SMB read once; ZIP seeks and sheet parsing stay in memory.
    # Profile discovery needs only this header, not every cell on every sheet.
    with CalamineWorkbook.from_filelike(BytesIO(Path(path).read_bytes())) as book:
        if contract.WORKBOOK_SHEET_NAME not in book.sheet_names:
            raise ValueError(f"в книге нет листа «{contract.WORKBOOK_SHEET_NAME}»")
        grid=book.get_sheet_by_name(contract.WORKBOOK_SHEET_NAME).to_python(nrows=max(0,rows))
        return [[cell_json(cell) for cell in row] for row in grid]


# Решения оператора в таблице экрана. Ключ «field:<key>» строится на лету.
DECISION_SKIP = "skip"
DECISION_EXTRA = "extra"
DECISION_HIDE = "hide"
DECISION_FIELD_PREFIX = "field:"


def decision_options(missing: list[dict]) -> list[tuple[str, str]]:
    """Варианты для выпадашки: сначала безопасные, потом связывание с полем.

    В список полей попадают только те, которых в книге сейчас нет: связать
    колонку с уже найденным полем — значит получить два источника одного
    ключа, и импорт честно отобьётся как дубль. Поэтому такой выбор просто
    не предлагается.
    """
    out = [(DECISION_SKIP, "— не читать"),
           (DECISION_EXTRA, "Дополнительное поле"),
           (DECISION_HIDE, "Скрыть из отчёта")]
    for item in missing:
        mark = " (обязательное)" if item.get("required") else ""
        out.append((DECISION_FIELD_PREFIX + item["key"],
                    f"Поле: {item['title']}{mark}"))
    return out


def apply_decisions(base: dict, decisions: list[dict]) -> dict:
    """Карта + решения экрана → новая карта для отправки на сервер.

    Решения касаются ТОЛЬКО перечисленных заголовков: всё, что оператор в этот
    раз не видел (колонки других книг), остаётся в карте нетронутым.
    """
    out = normalize_map(base)
    touched = {norm_title(item["title"]) for item in decisions}

    out["aliases"] = {
        key: [t for t in titles if norm_title(t) not in touched]
        for key, titles in out["aliases"].items()
    }
    out["aliases"] = {k: v for k, v in out["aliases"].items() if v}
    out["ignored"] = [t for t in out["ignored"] if norm_title(t) not in touched]
    out["extras"] = [item for item in out["extras"]
                     if norm_title(item["title"]) not in touched]

    for item in decisions:
        title = " ".join(str(item.get("title") or "").split())
        decision = str(item.get("decision") or DECISION_SKIP)
        if not title or decision == DECISION_SKIP:
            continue
        if decision == DECISION_HIDE:
            out["ignored"].append(title)
        elif decision == DECISION_EXTRA:
            label = " ".join(str(item.get("label") or "").split()) or title
            out["extras"].append({"title": title, "label": label,
                                  "show_in_card": True})
        elif decision.startswith(DECISION_FIELD_PREFIX):
            key = decision[len(DECISION_FIELD_PREFIX):]
            out["aliases"].setdefault(key, []).append(title)
    return out


def decisions_from_map(unknown: list[dict]) -> list[dict]:
    """Строки таблицы для незнакомых колонок с подсказанным решением."""
    return [{"column": item["column"], "title": item["title"],
             "decision": (DECISION_FIELD_PREFIX + item["suggest"]
                          if item.get("suggest") else DECISION_SKIP),
             "label": item["title"]}
            for item in unknown]


def map_summary(value: dict) -> str:
    """Одна строка про текущую карту — для подписи под таблицей."""
    value = normalize_map(value)
    aliases = sum(len(v) for v in value["aliases"].values())
    if not (aliases or value["ignored"] or value["extras"]):
        return "Карта колонок пуста: файл читается по одному контракту."
    parts = []
    if aliases:
        parts.append(f"связано вручную: {aliases}")
    if value["extras"]:
        parts.append(f"дополнительных полей: {len(value['extras'])}")
    if value["ignored"]:
        parts.append(f"скрыто колонок: {len(value['ignored'])}")
    tail = ""
    if value["updated_at"]:
        tail = (f" · изменена {value['updated_at'][:16].replace('T', ' ')}"
                + (f", {value['updated_by']}" if value["updated_by"] else ""))
    return "Карта колонок — " + ", ".join(parts) + tail


def suggest_key(title: str, by_key: dict[str, dict], present: set[str]) -> str:
    """На какое ненайденное поле похож незнакомый заголовок файла.

    Намеренно простое правило: заголовок файла начинается с названия поля
    («ответственный Сотрудник ХАРД» → «ответственный Сотрудник»). Именно так
    офис и переименовывает — приписывает уточнение к прежнему имени. Гадать
    сильнее не нужно: подсказка только подставляет галочку, решает человек.
    """
    slot = norm_title(title)
    best, best_len = "", 0
    for key, field in by_key.items():
        if key in present:
            continue
        base = norm_title(field["title"])
        if not base or len(base) < 4:
            continue
        if slot.startswith(base) and len(base) > best_len:
            best, best_len = key, len(base)
    return best
