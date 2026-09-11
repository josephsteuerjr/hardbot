"""
Хранилище на SQLite (stdlib sqlite3 — без лишних зависимостей).

Таблица `clients` создаётся динамически из fields.FIELDS, поэтому схема БД
всегда совпадает со схемой реестра. Плюс несколько служебных колонок для бота
и таблица `visits` — история выездов (кнопка «История» в карточке).
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import country_scope
import debt
from exchange_contract import (
    ENRICHMENT_SOURCE_KEYS,
    ExchangeContractError,
    FORM_AMOUNT_KEYS,
    FORM_DATE_KEYS,
    FORM_DROPDOWN_KEYS,
    FORM_INPUT_KEYS,
    FORM_TEXT_KEYS,
    IMPORT_SOURCE_KINDS,
    IMPORT_SOURCE_PUBLISHED_MASTER,
    IMPORT_SOURCE_TEST_SUBSET,
    OFFICE_CALL_FIELDS,
    OFFICE_DIRECT_EDIT_KEYS,
    OFFICE_ECHO_ONLY_KEYS,
    OFFICE_RESULT_SNAPSHOT_KEY,
    OFFICE_RESULT_SOURCE_KEY,
    PROVENANCE_MARKER,
    SNAPSHOT_KEYS,
    WORKBOOK_ALLOWED_IGNORED_HEADERS,
    WORKBOOK_ALLOWED_IGNORED_HEADER_PATTERNS,
    WORKBOOK_CONTRACT_KEY,
    WORKBOOK_HEADER_ALIASES,
    WORKBOOK_HEADER_SEARCH_ROWS,
    WORKBOOK_RESPONSIBLE_KEY,
    WORKBOOK_SHEET_NAME,
    WORKBOOK_TEST_SUBSET_REQUIRED_KEYS,
    WORKBOOK_TYPE_ROW_ALLOWED_BLANK_KEYS,
    WORKBOOK_TYPE_ROW_OFFSET,
    WORKBOOK_TYPE_ROW_TAGS,
    validate_import_source_proof,
    validate_import_source_proof_record,
    verify_export_schema,
)
from fields import FIELDS, BY_KEY
from util import num as _money_int   # единый разбор чисел (был дубль локальной логики)

# БД лежит рядом со скриптом, либо в каталоге APP_DATA (том в Docker; старое имя HARDBOT_DATA принимается)
_data_dir = Path(os.getenv("APP_DATA") or os.getenv("HARDBOT_DATA") or Path(__file__).parent)
_data_dir.mkdir(parents=True, exist_ok=True)
DB_PATH = _data_dir / "hardbot.db"

UNASSIGNED_REASON_MANUAL = "manual"
UNASSIGNED_REASON_ABSENT_FROM_REGISTRY = "absent_from_registry"
# Отзыв офисом через колонки общего реестра («Дата отзыва» и соседние).
# Политика возврата своя: договор не возвращается НИЧЕМ, пока колонка отзыва
# в файле непуста, — ни другим ФИО, ни галочкой возврата ручных снятий.
UNASSIGNED_REASON_RECALLED = "recalled"

# Заголовок реестра → служебная колонка отзыва. Контракт эти заголовки уже
# знает (allowed_ignored_headers), в экспортную схему они НЕ входят.
RECALL_TITLES = {
    "Дата отзыва": "recall_date",
    "Причина отзыва": "recall_reason",
    "Передача на отзыв": "recall_transfer",
    "Вывод из работы": "recall_withdrawn",
}
RECALL_KEYS = tuple(RECALL_TITLES.values())
RECALL_TITLE_BY_KEY = {key: title for title, key in RECALL_TITLES.items()}

# --- Журнал доставки уведомлений (push_log) ---------------------------------
# Виды рассылок и статусы попыток. Всюду ТОЛЬКО эти константы — строковые
# литералы статусов в web.py/app.py запрещены (пинится тестом): словарь,
# скопированный по коду, разъезжается, как уже разъехались рубильники.
PUSH_DIGEST = "digest"
PUSH_PROMISE = "promise"
PUSH_VISIT = "visit"
PUSH_GROUP = "group"
PUSH_REPORT_CHAT = "report_chat"
PUSH_TEST = "test"          # пробная отправка из пульта: НЕ выдаёт себя за рассылку
PUSH_KINDS = (PUSH_DIGEST, PUSH_PROMISE, PUSH_VISIT, PUSH_GROUP,
              PUSH_REPORT_CHAT, PUSH_TEST)
PUSH_SENT = "sent"
PUSH_BLOCKED = "blocked"    # TelegramForbiddenError: «не нажал /start» — чинит человек
PUSH_FAILED = "failed"
PUSH_SKIP_NO_BOT = "skipped_no_bot"
PUSH_SKIP_DISABLED = "skipped_disabled"
PUSH_SKIP_NO_TARGETS = "skipped_no_targets"
PUSH_SKIP_NO_EMPLOYEE = "skipped_no_employee"
PUSH_SKIP_NOTHING = "skipped_nothing_to_say"
PUSH_SKIP_DEDUP = "skipped_dedup"
PUSH_STATUSES = (PUSH_SENT, PUSH_BLOCKED, PUSH_FAILED, PUSH_SKIP_NO_BOT,
                 PUSH_SKIP_DISABLED, PUSH_SKIP_NO_TARGETS,
                 PUSH_SKIP_NO_EMPLOYEE, PUSH_SKIP_NOTHING, PUSH_SKIP_DEDUP)

# Служебные колонки бота (не из реестра).
# (agent_id удалён — легаси: назначение давно идёт через responsible + agent_bind.)
BOOKKEEPING = [
    ("photos_count", "INTEGER DEFAULT 0"),
    # --- Колонки реестра, которые читаются ТОЛЬКО для скоринга ------------------
    # В экспортную схему они намеренно НЕ входят: файл, уходящий в офис, менять
    # не нужно, а значит не меняются ни его форма, ни отпечаток контракта, ни
    # строка типов. Контракт эти заголовки и так знает — они перечислены в
    # allowed_ignored_headers; здесь мы просто перестаём выбрасывать их значения.
    ("country", "TEXT"),               # «Страна» — страна портфеля (лифт ×1.5/×0.6)
    ("debt_start_month", "REAL"),      # «Долг на начало месяца» — долг ДО периода
    ("paid_current", "REAL"),          # «Текущие платежи» — фактический сбор
    # Сколько месяцев из «Оплаты за MM.YY» с ненулевым платежом. Сами колонки
    # помесячные и называются по-новому каждый месяц, поэтому в контракте они
    # заданы шаблоном, а в БД сворачиваются в одно число. Признак сильный:
    # платившие раньше платят снова (лифт ×2.4).
    ("paid_months", "INTEGER"),
    ("reminder_date", "TEXT"),      # дата повторного выезда (для «Мои планы»)
    ("geo_lat", "REAL"),
    ("geo_lon", "REAL"),
    ("geo_src", "TEXT"),            # источник точки: nominatim/geonames/manual (выездник)
    # До какого уровня адрес реально разобран: street/village/mahalla/jamoat/city/
    # district/region. Без неё центр района выглядел как адресная точка, и гео-контроль
    # честного выездника показывал руководителю «визит далеко от адреса» — при том,
    # что «адрес» был центром района за десятки километров.
    ("geo_precision", "TEXT"),
    # С КОГО договор сняли в пульте. Держим не флаг, а ИМЯ: следующий импорт
    # приносит responsible из файла и молча возвращал клиента обратно — снятие
    # жило до ближайшей загрузки реестра, хотя карточка пульта обещает обратное.
    # По имени видно, то же самое написано в файле (значит офис ничего не менял,
    # снятие в силе) или другое (значит кейс переназначили — это законно, и
    # снятие снимается вместе с пометкой).
    ("unassigned_from", "TEXT"),
    # Причины снятия имеют разную политику возврата. Ручное снятие держится,
    # пока офис не назначит другого сотрудника; автоснятие из-за отсутствия
    # строки отменяется при любом возвращении договора в реестр.
    ("unassigned_reason", "TEXT"),
    ("tips", "TEXT"),               # наводки выездника (староста, соседи, ориентиры)
    # Своей колонки под сумму обещания НЕТ: и дата, и сумма — живые поля
    # реестра (promise_date и forecast_sum, «Сумма прогноза»). Владелец
    # 26.08.2026: «суть в переносе, она изначально должна ехать и
    # редактироваться». Цена принята им же: форма заполняется на человека,
    # поэтому одна сумма ложится во все договоры должника.
    # Колонки отзыва из общего реестра (значения как их видел последний импорт).
    ("recall_date", "TEXT"),
    ("recall_reason", "TEXT"),
    ("recall_transfer", "TEXT"),
    ("recall_withdrawn", "TEXT"),
    # Причина снятия ДО отзыва: ручное снятие оператора не должно уничтожаться
    # циклом «офис отозвал → офис очистил» — после очистки колонок договор
    # возвращается не в работу, а в ручное снятие.
    ("recall_prev_reason", "TEXT"),
    ("updated_at", "TEXT"),
    # Доказательство происхождения результата визита. Обычный импорт реестра эти
    # колонки не знает и потому не может подделать завершённый визит миниаппа.
    ("bot_visit_at", "TEXT"),
    ("bot_visit_data", "TEXT"),
    # Офисные правки визита, принятые импортом через якорь по «Дате выезда»
    # (см. import_grid): сотрудники дописывают в реестрах и в офисе. Снимок
    # bot_visit_data остаётся нетронутым доказательством фактического POST.
    ("office_edit_data", "TEXT"),
    # Журнал всех значений, которые бот/шлюз когда-либо считал правдой по полям
    # визита. Нужен якорному шлюзу: файл, обогащённый между двумя визитами
    # одного дня, содержит СТАРЫЙ снимок — без журнала он выглядел бы как
    # «офисная правка» и откатывал бы результат повторного выезда.
    ("visit_value_log", "TEXT"),
]

# Семантика обмена больше не дублируется между bot/pult/clearance. Она загружается
# из version+SHA-locked data_exchange_contract.json; несовместимая правка не даст
# даже импортировать модуль. BOT_VISIT_KEYS — только физические registry-поля,
# тогда как BOT_SNAPSHOT_KEYS включает bot-only ``tips``.
BOT_SNAPSHOT_KEYS = SNAPSHOT_KEYS
BOT_VISIT_KEYS = tuple(key for key in BOT_SNAPSHOT_KEYS if key in BY_KEY)
BOT_VISIT_MARKER = PROVENANCE_MARKER
BOT_DROPDOWN_KEYS = FORM_DROPDOWN_KEYS
OFFICE_EDIT_KEYS = {OFFICE_RESULT_SNAPSHOT_KEY, *OFFICE_DIRECT_EDIT_KEYS}

# «Не обработан» — довизитный ДЕФОЛТ реестра, а не решение офиса. Тот же принцип,
# что и в журнале вытеснения: это техническая пустота. Поэтому такой результат
# никогда не принимается офисной правкой поверх доказанного визита.
UNPROCESSED_RESULT = "Не обработан"

# Типизированные поля формы (обещание выездника; контракт v3 — на v2 пусты).
FORM_TYPED_KEYS = frozenset(FORM_AMOUNT_KEYS) | frozenset(FORM_DATE_KEYS)
_bookkeeping_keys = {name for name, _ in BOOKKEEPING}
if FORM_TYPED_KEYS - set(BY_KEY) - _bookkeeping_keys:
    raise RuntimeError(
        "типизированное поле формы не существует ни в реестре, ни в служебных "
        "колонках: " + ", ".join(sorted(
            FORM_TYPED_KEYS - set(BY_KEY) - _bookkeeping_keys)))

# --- Прогноз оплаты (колонка AP реестра) -------------------------------------
# Ставили его аналитики руками, и по обещаниям выездника он молчал. Владелец
# 26.08.2026: «при наличии обещания ставится „Ждём", а если было обещание до
# выезда и исчезло — „Срыв"». Оценочные «ОК», «Нет» и «Сомнение» остаются за
# аналитиками: бот их не пишет, а их правку возвращает импорт (direct_edit).
FORECAST_KEY = "forecast"
FORECAST_WAITING = "Ждём"       # обещание есть — ждём срока
FORECAST_BROKEN = "Срыв"        # обещание было и не устояло
FORECAST_PAID = "Уже"           # платёж уже прошёл, ждать нечего
PAID_STATUS = "Оплачен"         # значение результата визита, при котором ждать нечего
_forecast_options = set(BY_KEY[FORECAST_KEY].options or [])
if not {FORECAST_WAITING, FORECAST_BROKEN, FORECAST_PAID} <= _forecast_options:
    raise RuntimeError(
        "список «Прогноза» в реестре разошёлся с ботом: "
        + ", ".join(sorted({FORECAST_WAITING, FORECAST_BROKEN, FORECAST_PAID}
                           - _forecast_options)))


def visit_forecast(*, paid: bool, promise_now: str, promise_was: str,
                   promise_dropped: bool = False) -> str | None:
    """Прогноз по итогу ЗАКРЫТОГО визита. None — бот молчит (значение аналитика
    остаётся как было).

    promise_dropped — выездник нажал «Убрать» по обещанию. Отдельный признак,
    а не «стало пусто»: чужое (офисное) обещание сервер стереть не даёт, но
    сам факт «должник больше не обещает» — это показание выездника, и терять
    его нельзя. Именно так «было обещание до выезда и исчезло» доезжает до
    офиса даже тогда, когда сама дата остаётся офисной.
    """
    if paid:
        return FORECAST_PAID
    if promise_dropped and str(promise_was or "").strip():
        return FORECAST_BROKEN
    if str(promise_now or "").strip():
        return FORECAST_WAITING
    if str(promise_was or "").strip():
        return FORECAST_BROKEN
    return None


PROMISE_YEAR_MIN, PROMISE_YEAR_MAX = 2020, 2100
MONEY_MAX = 10 ** 12
# Чем строка книги доказывает, что видела ЭТОТ визит. Только поля, которые
# заполнил сам выездник: обещание офис ведёт и до визита, а прогноз бот выводит
# сам — совпадение таких ячеек не доказывает ничего.
_ECHO_EVIDENCE_KEYS = frozenset(FORM_INPUT_KEYS) - FORM_TYPED_KEYS
_MONEY_RX = re.compile(r"^\d+(?:[.,]\d+)?$")


def canon_iso_date(value) -> str | None:
    """Дата формы → строгая ISO «ГГГГ-ММ-ДД». None — это не дата."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        iso = _excel_serial_date(value)
        d = _as_date(iso) if iso else None
    else:
        s = str(value).strip()
        d = _as_date(s)
        if d is None and s.isdigit():
            d = _as_date(_excel_serial_date(int(s)) or "")
    if d is None or not (PROMISE_YEAR_MIN <= d.year <= PROMISE_YEAR_MAX):
        return None
    return d.isoformat()


def canon_amount(value) -> str | None:
    """Денежное поле формы → канонические целые «1500000». Мусор даёт None,
    а не 0: «сто тыщ» не должно превратиться в обещание на ноль."""
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(round(value))
    else:
        s = str(value).replace("\u00a0", " ").replace("\u202f", " ").strip()
        s = s.replace(" ", "").replace(",", ".")
        if not _MONEY_RX.fullmatch(s):
            return None
        try:
            n = int(round(float(s)))
        except (ValueError, OverflowError):
            return None
    return str(n) if 0 <= n <= MONEY_MAX else None


def canon_form_value(key: str, value) -> str | None:
    if key in FORM_DATE_KEYS:
        return canon_iso_date(value)
    if key in FORM_AMOUNT_KEYS:
        return canon_amount(value)
    return None

_exchange_registry_keys = (
    set(BOT_VISIT_KEYS)
    | set(BOT_DROPDOWN_KEYS)
    | set(OFFICE_DIRECT_EDIT_KEYS)
    | set(OFFICE_ECHO_ONLY_KEYS)
    | set(OFFICE_CALL_FIELDS)
    | {OFFICE_RESULT_SOURCE_KEY, OFFICE_RESULT_SNAPSHOT_KEY}
)
_exchange_registry_missing = _exchange_registry_keys - set(BY_KEY)
if _exchange_registry_missing:
    raise RuntimeError(
        "data exchange contract references unknown registry keys: "
        + ", ".join(sorted(_exchange_registry_missing))
    )


# Версия данных: монотонный счётчик, растёт при ЛЮБОЙ записи в clients. Нужен для
# дешёвой инвалидации кэша индекса приоритета (scoring.priority_index) — пересчитываем
# только когда реестр реально изменился, а не на каждый запрос миниаппа (был N+1).
_DATA_VERSION = 0


def data_version() -> int:
    return _DATA_VERSION


def _bump_version() -> None:
    global _DATA_VERSION
    _DATA_VERSION += 1


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL: бот и Tkinter-пульт пишут в один файл; WAL переживает аварийную
    # перезагрузку и допускает одновременное чтение во время записи.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def _bot_snapshot(values, completed_at: str) -> dict | None:
    """Нормализованный снимок подтверждённого визита либо None при мусоре.

    Дата обязана быть строгой ISO, dropdown-значения — входить в каноническую
    схему. Это второй контур защиты после валидации API и фильтра пульта.
    """
    completed_at = str(completed_at or "").strip()
    try:
        if date.fromisoformat(completed_at).isoformat() != completed_at:
            return None
    except ValueError:
        return None
    out = {"visit_date": completed_at}
    for key in BOT_SNAPSHOT_KEYS:
        if key == "visit_date":
            continue
        try:
            value = values[key]
        except (KeyError, TypeError):
            continue
        if key in FORM_TYPED_KEYS and value is not None                 and str(value).strip() == "":
            # Пустая строка по обещанию — ОТВЕТ выездника «обещания больше
            # нет». Молчание — это отсутствие ключа; путать их нельзя, иначе
            # снятое обещание возвращала бы первая же копия реестра.
            out[key] = ""
            continue
        if value in (None, ""):
            continue
        value = str(value).strip()
        if not value:
            continue
        if key in BOT_DROPDOWN_KEYS and value not in (BY_KEY[key].options or []):
            return None
        if key in FORM_TYPED_KEYS:
            canon = canon_form_value(key, value)
            if canon is None:
                continue    # мусор пропускает ключ, но не рушит снимок визита
            value = canon
        out[key] = value
    return out


def _decode_bot_snapshot(row) -> dict | None:
    """Проверить служебный JSON перед использованием как доверенного источника."""
    try:
        completed_at = row["bot_visit_at"]
        data = json.loads(row["bot_visit_data"] or "")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or str(data.get("visit_date") or "") != completed_at:
        return None
    return _bot_snapshot(data, completed_at)


def _decode_office_edits(row) -> dict:
    """Принятые офисные правки визита (JSON office_edit_data) с той же строгой
    валидацией, что у снимка: только ключи OFFICE_EDIT_KEYS, dropdown — только
    канонические значения. Оверлей привязан к визиту, к которому его приняли
    (visit_at == bot_visit_at): если код откатывали и старый record_bot_visit
    записал новый снимок, не обнулив колонку, протухший оверлей не должен лечь
    поверх свежего результата. Любой мусор молча отбрасывается (fail-closed)."""
    try:
        data = json.loads(row["office_edit_data"] or "")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, IndexError):
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        if str(data.get("visit_at") or "") != str(row["bot_visit_at"] or ""):
            return {}
    except (KeyError, IndexError):
        return {}
    fields = data.get("fields")
    if not isinstance(fields, dict):
        return {}
    out = {}
    for key, value in fields.items():
        if key not in OFFICE_EDIT_KEYS:
            continue
        if value in (None, ""):
            # Пустота у типизированного ключа — принятая офисом ОЧИСТКА
            # обещания (снятие через якорный шлюз); у остальных — мусор.
            if key in FORM_TYPED_KEYS:
                out[key] = ""
            continue
        value = str(value).strip()
        if not value:
            if key in FORM_TYPED_KEYS:
                out[key] = ""
            continue
        if key in FORM_TYPED_KEYS:
            canon = canon_form_value(key, value)
            if canon is None:
                continue
            out[key] = canon
            continue
        option_key = (
            OFFICE_RESULT_SOURCE_KEY
            if key == OFFICE_RESULT_SNAPSHOT_KEY
            else key
        )
        if (
            option_key in BOT_DROPDOWN_KEYS
            or option_key == OFFICE_RESULT_SOURCE_KEY
        ) and value not in (BY_KEY[option_key].options or []):
            continue
        out[key] = value
    return out


def _decode_value_log(row) -> dict:
    """Журнал известных значений визита {ключ: [значения]} из visit_value_log."""
    try:
        data = json.loads(row["visit_value_log"] or "")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, IndexError):
        return {}
    fields = data.get("fields") if isinstance(data, dict) else None
    if not isinstance(fields, dict):
        return {}
    return {k: [str(v) for v in vs][-12:] for k, vs in fields.items()
            if k in OFFICE_EDIT_KEYS and isinstance(vs, list)}


def _log_append(log: dict, values: dict) -> dict:
    """Дописать значения в журнал (без дублей, хвост из 12 на ключ)."""
    out = {k: list(v) for k, v in log.items()}
    for key, value in values.items():
        if key not in OFFICE_EDIT_KEYS or value in (None, ""):
            continue
        s = str(value).strip()
        if s and s not in out.setdefault(key, []):
            out[key] = (out[key] + [s])[-12:]
    return out


def _log_json(log: dict) -> str:
    return json.dumps({"fields": log}, ensure_ascii=False, separators=(",", ":"))


def _canon_option(key: str, value: str) -> str | None:
    """Свести значение dropdown к каноническому варианту: регистр и хвостовая
    пунктуация (автозамены Excel) не должны превращать правку в отказ. Не
    свелось — None (мусор вроде CONSUMER не проходит)."""
    options = BY_KEY[key].options or []
    if value in options:
        return value
    norm = value.casefold().strip(" .…!,;")
    return next((o for o in options if o.casefold() == norm), None)


def _merged_visit(row) -> dict | None:
    """Подтверждённый результат визита + живой комментарий текущего месяца.

    Снимок миниаппа остаётся неизменяемым доказательством завершённого визита,
    офисные правки накладываются поверх него. ``visit_comment`` отличается по
    природе: это совместный рабочий текст текущего месяца, который сотрудник
    продолжает в боте или офисе и который аналитик может отдельно заменить при
    месячном импорте, включая явную пустоту. Поэтому его каноническое текущее
    значение берём из raw-колонки последним слоем, не переписывая снимок.
    Без валидного снимка функция по-прежнему не создаёт результат визита."""
    snapshot = _decode_bot_snapshot(row)
    if snapshot is None:
        return None
    edits = _decode_office_edits(row)
    merged = {**snapshot, **edits} if edits else dict(snapshot)
    try:
        merged["visit_comment"] = str(row["visit_comment"] or "").strip()
    except (KeyError, IndexError):
        pass
    return merged


def _backfill_bot_visit_snapshots(conn: sqlite3.Connection) -> int:
    """Однократно сохранить доказанные старые визиты после добавления колонок.

    Допуск строгий: есть строка history.visits, её последняя дата совпадает с
    clients.visit_date, все непустые dropdown-значения каноничны. Несовпадение
    означает старую сессию/последующий импорт и намеренно остаётся без маркера.
    """
    rows = conn.execute("""
        WITH latest AS (
          SELECT contract_id, MAX(id) AS visit_id
          FROM visits
          GROUP BY contract_id
        )
        SELECT c.*, v.id AS visit_id, v.created_at AS completed_at
        FROM latest x
        JOIN visits v ON v.id = x.visit_id
        JOIN clients c ON c.contract_id = v.contract_id
        ORDER BY v.id DESC
    """).fetchall()
    done: set[tuple[str, str, str]] = set()
    changed = 0
    for row in rows:
        person = (row["client"] or "", row["birth_date"] or "",
                  row["responsible"] or "")
        if person in done:
            continue
        done.add(person)
        if row["bot_visit_data"] or row["visit_date"] != row["completed_at"]:
            continue
        snapshot = _bot_snapshot(row, row["completed_at"])
        if snapshot is None:
            continue
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        cur = conn.execute(
            "UPDATE clients SET bot_visit_at=?, bot_visit_data=? "
            "WHERE client IS ? AND IFNULL(birth_date,'')=IFNULL(?, '') "
            "AND IFNULL(responsible,'')=IFNULL(?, '') AND bot_visit_data IS NULL",
            (row["completed_at"], payload, row["client"], row["birth_date"],
             row["responsible"]),
        )
        changed += cur.rowcount
    return changed


def init_db() -> None:
    cols = [f'"{f.key}" TEXT' for f in FIELDS]
    cols += [f'"{name}" {decl}' for name, decl in BOOKKEEPING]
    cols_sql = ",\n  ".join(cols)
    with connect() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS clients (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              {cols_sql},
              UNIQUE(contract_id)
            )
        """)
        # Авто-миграция: дотянуть недостающие колонки (новые поля реестра/служебные)
        # в уже существующую БД — без потери данных. SQLite ADD COLUMN безопасен.
        have = {r[1] for r in conn.execute("PRAGMA table_info(clients)")}
        for name, decl in [(f.key, "TEXT") for f in FIELDS] + BOOKKEEPING:
            if name not in have:
                conn.execute(f'ALTER TABLE clients ADD COLUMN "{name}" {decl}')
        # До появления отдельной причины все непустые unassigned_from создавались
        # только ручной кнопкой пульта. Явно закрепляем прежнюю семантику, чтобы
        # обновление схемы не вернуло снятые вручную договоры в портфель.
        conn.execute(
            "UPDATE clients SET unassigned_reason=? "
            "WHERE COALESCE(unassigned_from, '') <> '' "
            "AND COALESCE(unassigned_reason, '') = ''",
            (UNASSIGNED_REASON_MANUAL,),
        )
        # Миграция: убрать легаси-колонку clients.agent_id (назначение — через
        # responsible + agent_bind). SQLite ≥3.35 умеет DROP COLUMN; если нет — пропускаем.
        if "agent_id" in have:
            try:
                conn.execute('ALTER TABLE clients DROP COLUMN agent_id')
            except sqlite3.OperationalError:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS visits (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contract_id TEXT,
              agent_id INTEGER,
              created_at TEXT,
              payload TEXT,
              op_id TEXT
            )
        """)
        # Идемпотентный ключ выезда (uuid от клиента). Оффлайн-очередь миниаппа при
        # восстановлении связи досылает накопленные выезды; повторная доставка того же
        # выезда с тем же op_id НЕ создаёт дубль строки и НЕ шлёт повторный пост в группу.
        # Старые строки (op_id IS NULL) уникальному индексу не мешают — в SQLite NULL-ы
        # в UNIQUE-индексе считаются различными.
        vcols = {r[1] for r in conn.execute("PRAGMA table_info(visits)")}
        if "op_id" not in vcols:
            conn.execute("ALTER TABLE visits ADD COLUMN op_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_visits_op ON visits(op_id)")
        # Вкладка руководителя превращает разовое нажатие дайджеста в регулярное
        # открытие, а сводка на N сотрудников — это 2N проходов по clients.
        # Составной индекс покрывает и persons_processed/overdue_promises
        # (ведущая колонка responsible), и DISTINCT-скан направленцев
        # (employees_by_direction: responsible, country, city — COVERING).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clients_resp_country_city "
                     "ON clients(responsible, country, city)")
        # last_visit_geo — горячий запрос обеих карточек (телеграм и вкладка),
        # без индекса это полный SCAN visits на каждое открытие.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_contract "
                     "ON visits(contract_id)")
        # day_map_points фильтрует визиты по created_at (карта дня).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_created "
                     "ON visits(created_at)")
        # Гео-контроль: точка телефона в момент закрытия визита + дистанция до
        # известной точки должника (метры). NULL = точки не было (нет разрешения/сигнала).
        # geo_far — ВЕРДИКТ на момент визита, а не вычисление на лету. Точность
        # точки должника может измениться после следующего геокодинга, и тогда
        # пересчёт задним числом переписал бы историю: визит, закрытый честно,
        # завтра стал бы нарушением. NULL = не судили (точка не адресная).
        # received_at — КОГДА выезд доехал до сервера. created_at хранит ДЕНЬ
        # ВЫЕЗДА (см. log_visit), и по нему нельзя отличить работу в поле от
        # занесённой назавтра «потому что не было связи»: офис попросил такой
        # контроль прямо (Худжанд, 11.09.2026). Точное время приёма лежало
        # только в append-only audit — сопоставлять его со строкой визита
        # каждый раз пришлось бы вручную.
        for col, decl in (("visit_lat", "REAL"), ("visit_lon", "REAL"),
                          ("dist_m", "INTEGER"), ("geo_far", "INTEGER"),
                          ("received_at", "TEXT"), ("fill_seconds", "INTEGER"),
                          ("saved_at", "TEXT")):
            if col not in vcols:
                conn.execute(f"ALTER TABLE visits ADD COLUMN {col} {decl}")
        # Профиль пользователя (язык). Whitelist теперь = привязка к сотруднику.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
              telegram_id INTEGER PRIMARY KEY,
              name TEXT,
              lang TEXT DEFAULT 'ru',
              role TEXT DEFAULT 'collector',   -- историческая колонка, не используется
              created_at TEXT
            )
        """)
        ucols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        if "lang" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'ru'")
        # Гости: @username, имя и время последнего апдейта — чтобы в пульте можно было
        # выбрать нужный telegram_id при привязке (не вводить руками). Идемпотентно.
        if "username" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
        if "last_seen" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")
        # Версия оболочки на устройстве (X-Shell-Version из bootstrap): «у него
        # старая оболочка» становится проверяемым фактом, а не догадкой по
        # отсутствию заголовка. Новую таблицу под это заводить НЕЛЬЗЯ — она
        # блокирует сброс ограниченного теста целиком (EXPECTED_TABLES).
        if "shell_version" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN shell_version INTEGER")
        # Привязка «кто есть кто»: telegram_id → ответственный Сотрудник (одноразово).
        # kind: 'agent' (выездник) | 'courier' (ГПХ-разведчик). Разница — только в выгрузке.
        # approved: 1 — доступ открыт; 0 — ждёт аппрува ADMIN (ИБ: авторизация на этапе привязки).
        # DEFAULT 1 — существующие привязки и режим «без аппрува» остаются активными.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_bind (
              telegram_id INTEGER PRIMARY KEY,
              employee TEXT NOT NULL,
              kind TEXT DEFAULT 'agent',
              approved INTEGER DEFAULT 1,
              created_at TEXT
            )
        """)
        bind_cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_bind)")}
        if "kind" not in bind_cols:
            conn.execute("ALTER TABLE agent_bind ADD COLUMN kind TEXT DEFAULT 'agent'")
        if "approved" not in bind_cols:
            conn.execute("ALTER TABLE agent_bind ADD COLUMN approved INTEGER DEFAULT 1")
        # Аудит-лог доступа к карточке должника (ИБ: кто-что-когда смотрел/менял ПД).
        # Append-only: только INSERT, без UPDATE/DELETE из кода (tamper-evident-каркас).
        # Офис сотрудника. В реестре этого факта нет: город там — это где живёт
        # ДОЛЖНИК, а колонка «Подразделение» про Хард/Софт. Заводит оператор.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS employee_city (
              employee   TEXT PRIMARY KEY,
              city       TEXT NOT NULL,
              updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT,
              actor INTEGER,         -- telegram_id того, кто действовал
              action TEXT,           -- view_card / save_visit / photo / marker / approve_bind …
              target TEXT,           -- contract_id (rep) карточки
              detail TEXT
            )
        """)
        # Устройства пульта. Admin-ключ вводится ОДИН РАЗ — чтобы обменять его на
        # токен, привязанный к этой машине. Дальше ключ в пульте не хранится, и
        # утёкший ключ сам по себе ничего не грузит: нужен ещё и enroll с машины.
        # Храним ХЭШ токена, а не токен: дамп таблицы не даёт войти.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_devices (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              token_hash TEXT NOT NULL UNIQUE,
              device_id TEXT NOT NULL,     -- отпечаток машины (хэш), не ПД
              name TEXT,                   -- как назвал владелец: «Ноут дома»
              created_at TEXT,
              last_seen TEXT,
              revoked_at TEXT
            )
        """)
        # Коды привязки. Мастер-ключ перестал быть тем, чем привязывают: теперь
        # код рождается НА МЕСТЕ У СЕРВЕРА (server/admin_access.py, как и
        # регистратор импорта — без HTTP-входа) и гаснет при первом же обмене.
        # Храним хэш: дамп таблицы кода не даёт. Срока жизни у кода намеренно
        # нет — одноразовости достаточно, а лишний таймер владелец отверг.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_enroll_codes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              code_hash TEXT NOT NULL UNIQUE,
              name TEXT,                   -- для кого выдан: «Ноут дома»
              created_at TEXT,
              used_at TEXT,                -- NULL = ещё не обменян
              used_by_device TEXT          -- отпечаток машины, обменявшей код
            )
        """)
        # Роль устройства. Оператор — пульт аналитика, он видит должников. Инженер —
        # панель обслуживания сервера, она должников не видит НИКОГДА и умеет
        # только закрытый список операций. Роли не пересекаются: токен инженера
        # на ручках пульта не работает и наоборот. Новую таблицу под это заводить
        # нельзя — она блокирует сброс ограниченного теста целиком.
        dcols = {r[1] for r in conn.execute("PRAGMA table_info(admin_devices)")}
        if "role" not in dcols:
            conn.execute("ALTER TABLE admin_devices ADD COLUMN role TEXT DEFAULT 'operator'")
        ccols = {r[1] for r in conn.execute("PRAGMA table_info(admin_enroll_codes)")}
        if "role" not in ccols:
            conn.execute("ALTER TABLE admin_enroll_codes ADD COLUMN role TEXT DEFAULT 'operator'")
        # Метки: и привязанные к кейсу (client_rep), и отдельные «разведочные» (rep пуст).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS markers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              telegram_id INTEGER,
              kind TEXT,
              client_rep TEXT,
              lat REAL, lon REAL,
              label TEXT,
              created_at TEXT,
              op_id TEXT
            )
        """)
        # Тот же идемпотентный ключ для меток из оффлайн-очереди (см. visits.op_id).
        mcols = {r[1] for r in conn.execute("PRAGMA table_info(markers)")}
        if "op_id" not in mcols:
            conn.execute("ALTER TABLE markers ADD COLUMN op_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_markers_op ON markers(op_id)")
        # Заметки выездника «для себя»: в форму визита успевает влезть не всё, а
        # на ходу проще надиктовать пару строк и вернуться к ним вечером. Живут на
        # сервере, а не только в телефоне: иначе пропадут при смене устройства.
        # Привязка к кейсу необязательна — бывает мысль вообще не про кейс.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              telegram_id INTEGER,
              employee TEXT,
              client_rep TEXT,
              text TEXT,
              created_at TEXT,
              op_id TEXT,
              deleted_at TEXT
            )
        """)
        ncols = {r[1] for r in conn.execute("PRAGMA table_info(notes)")}
        if "deleted_at" not in ncols:
            conn.execute("ALTER TABLE notes ADD COLUMN deleted_at TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_op ON notes(op_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_tg ON notes(telegram_id)")
        # Настройки (например, принятая беседа group_chat_id).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT
            )
        """)
        # Дополнительные колонки реестра (карта колонок, см. registry_column_map).
        # Отдельная таблица, а не новые колонки clients: офис добавляет столбцы
        # когда захочет, а экспортная схема и её отпечаток должны оставаться
        # ровно теми, под которые сделаны шаблоны офиса и registry_cycle.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS client_extra (
              contract_id TEXT NOT NULL,
              slot TEXT NOT NULL,
              title TEXT NOT NULL,
              label TEXT NOT NULL,
              value TEXT,
              updated_at TEXT,
              PRIMARY KEY (contract_id, slot)
            )
        """)
        # Доверенная граница внешнего импорта. HTTP-клиент не может создавать
        # эти записи: exact proof заранее регистрируется локальным/root CLI.
        # Head переживает application reset и не даёт переиграть старый master.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS import_receipts (
              proof_digest TEXT PRIMARY KEY,
              source_kind TEXT NOT NULL,
              source_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              proof_json TEXT NOT NULL,
              status TEXT NOT NULL,
              registered_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              consumed_at TEXT,
              result_json TEXT,
              result_digest TEXT,
              UNIQUE(source_id, sequence)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS import_source_heads (
              source_id TEXT PRIMARY KEY,
              sequence INTEGER NOT NULL,
              proof_digest TEXT NOT NULL,
              imported_at TEXT NOT NULL
            )
        """)
        # Реестр доставленного обогащением: что писатель пульта последний раз
        # положил в личную книгу для пары (КД, поле). Без него писатель не
        # отличал правку офиса после прогона от «файл отстал» и затирал её
        # (04.08 и 28.08.2026). Трёхстороннее сравнение — pult/enrich.py.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS enrichment_ledger (
              contract_id TEXT NOT NULL,
              field_key TEXT NOT NULL,
              value TEXT NOT NULL,
              visit_date TEXT,
              responsible TEXT,
              file_path TEXT,
              delivered_at TEXT NOT NULL,
              receipt_digest TEXT,
              PRIMARY KEY (contract_id, field_key)
            )
        """)
        receipt_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(import_receipts)")
        }
        if "result_digest" not in receipt_cols:
            conn.execute(
                "ALTER TABLE import_receipts ADD COLUMN result_digest TEXT"
            )
        if "expires_at" not in receipt_cols:
            conn.execute(
                "ALTER TABLE import_receipts ADD COLUMN expires_at TEXT"
            )
        # Руководители (дайджест). Роль задаётся из пульта: telegram_id →
        # набор городов (ярлык-подпись, свободный ввод) + набор сотрудников
        # (ФИО из clients.responsible — источник правды «кого показывать»).
        # cities/employees — JSON-массивы строк. Один руководитель = 1..N городов
        # и 1..N сотрудников (Тадж «один на 2 города» — штатно, без хардкода).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS managers (
              telegram_id INTEGER PRIMARY KEY,
              name TEXT,
              cities TEXT,
              employees TEXT,
              direction TEXT,
              created_at TEXT
            )
        """)
        # direction (tj/uz/all) — руководитель НАПРАВЛЕНИЯ: список его сотрудников
        # считается живьём, чтобы не устаревал с каждым разбросом.
        mgcols = {r[1] for r in conn.execute("PRAGMA table_info(managers)")}
        if "direction" not in mgcols:
            conn.execute("ALTER TABLE managers ADD COLUMN direction TEXT")
        # can_dm — производный признак «бот может писать этому руководителю в
        # личку». NULL = не знаем, 1 = писали и дошло, 0 = Телеграм ответил
        # Forbidden («не нажал /start» / заблокировал). Обновляется ТОЛЬКО по
        # факту отправки; db.guest() для этого непригоден — он true и после
        # простого входа в миниапп, где личного чата нет вовсе.
        for col, decl in (("can_dm", "INTEGER"), ("can_dm_at", "TEXT")):
            if col not in mgcols:
                conn.execute(f"ALTER TABLE managers ADD COLUMN {col} {decl}")
        # office_cities — охват ПО ОФИСАМ сотрудников (живой состав, как у
        # направления). Отдельно от cities: та колонка означает города ДОЛЖНИКОВ
        # и на состав не влияет — «точечный список не расширяется сам» это
        # заявленный контракт, на нём стоит тест.
        if "office_cities" not in mgcols:
            conn.execute("ALTER TABLE managers ADD COLUMN office_cities TEXT")
        # Журнал доставки уведомлений: строка на КАЖДУЮ попытку и каждый ранний
        # return. Ранние return сейчас молчат — именно они и есть «не работает».
        # day = date.today() (в контейнере UTC) — та же система координат, что у
        # visit_date и digest_last_run, иначе «сводка за сегодня» спорила бы с
        # датой дайджеста. Телеметрия, не аудит: ПД-выдача пишется в audit.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT NOT NULL,
              day TEXT NOT NULL,
              kind TEXT NOT NULL,
              target_kind TEXT,
              target_id INTEGER,
              employee TEXT,
              contract_id TEXT,
              status TEXT NOT NULL,
              error TEXT,
              note TEXT,
              attempt INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_push_log_day "
                     "ON push_log(day, kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_push_log_target "
                     "ON push_log(day, target_id)")
        # Автономное приложение (PWA вне Телеги): вне Телеги нет initData, поэтому вход
        # двухступенчатый. Бот шлёт ссылку (app_links, окно активации APP_LINK_DAYS,
        # до APP_LINK_MAX_USES обменов — одну ссылку открывают и в браузере Телеги, и в
        # Safari/Chrome при установке на экран); код обменивается на девайс-токен
        # (app_tokens, скользящие APP_TOKEN_DAYS — продлевается при использовании).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_links (
              code TEXT PRIMARY KEY,
              telegram_id INTEGER,
              created_at TEXT,
              used_at TEXT,
              uses INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_tokens (
              token TEXT PRIMARY KEY,
              telegram_id INTEGER,
              created_at TEXT,
              expires_at TEXT
            )
        """)
        # Фактическая точка съёмки фото (вручение досудебки не подделаешь):
        # миниапп шлёт координаты в момент загрузки /api/photo. Ключ — путь
        # «КД/имя файла»: повторная доставка того же op из оффлайн-очереди
        # строк не плодит. Колонка в реестре пока обсуждается — здесь только сбор.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS photo_geo (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contract_id TEXT,
              file TEXT UNIQUE,
              telegram_id INTEGER,
              lat REAL, lon REAL, acc_m INTEGER,
              captured_at TEXT,
              created_at TEXT
            )
        """)
        # photo_geo из «гео фото» стала ИНДЕКСОМ ФОТО: строка есть у каждого
        # снимка, гео — опциональна. Имя таблицы не меняем: оно прибито в
        # limited_test_reset.RESET_TABLES/DELETE_ORDER и в photo_geo_for_files.
        #
        # Справочные данные должника (addr_*, assign_date_at) фиксируем СНИМКОМ
        # на момент загрузки, а не читаем текущие — то же правило и по той же
        # причине, что visits.geo_far: точка должника уезжает после каждого
        # геокодинга, и пересчёт задним числом переписывал бы историю (честное
        # фото понедельника становилось бы поводом к разбору в четверг).
        # addr_tol_m = geo_tolerance_m на момент загрузки; NULL = точка не
        # адресная, гео-сигналы не выдаются. assign_date_at = разобранная
        # _as_date дата закрепления (ISO); NULL = не распознана, правило
        # shot_before_assignment не применяется. exif_dt_norm = exif_dt,
        # приведённый к серверной шкале по exif_tz; NULL = зона неизвестна,
        # в арифметику не пускаем (в UZ/TJ разрыв со стенными часами +5 ч).
        pgcols = {r[1] for r in conn.execute("PRAGMA table_info(photo_geo)")}
        for col, decl in (("sha256", "TEXT"), ("size_bytes", "INTEGER"),
                          ("source", "TEXT"), ("shot_at_client", "TEXT"),
                          ("exif_dt", "TEXT"), ("exif_tz", "TEXT"),
                          ("exif_dt_norm", "TEXT"),
                          ("exif_lat", "REAL"), ("exif_lon", "REAL"),
                          ("exif_gps_at", "TEXT"),
                          ("exif_make", "TEXT"), ("exif_model", "TEXT"),
                          ("exif_software", "TEXT"),
                          ("meta_state", "TEXT"), ("shell_version", "INTEGER"),
                          ("meta_origin", "TEXT"), ("shrunk", "INTEGER"),
                          ("addr_lat", "REAL"), ("addr_lon", "REAL"),
                          ("addr_tol_m", "INTEGER"), ("assign_date_at", "TEXT"),
                          # Когда координаты стёрты (срок хранения или ручка
                          # оператора). Отдельная отметка, а не просто NULL:
                          # «точки не было» и «точку стёрли по сроку» — разные
                          # факты, и руководителю нельзя показывать второе как
                          # первое (иначе честный выезд выглядит фото без гео).
                          ("geo_forgotten_at", "TEXT")):
            if col not in pgcols:
                conn.execute(f"ALTER TABLE photo_geo ADD COLUMN {col} {decl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_geo_contract "
                     "ON photo_geo(contract_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_geo_sha "
                     "ON photo_geo(sha256)")
        # Трек поездки: точки, снятые НАВИГАТОРОМ по дороге к должнику, пока визит
        # ещё не закрыт. До этого от всей поездки оставались только координата
        # фотографии и точка закрытия визита — то есть две точки на весь день.
        #
        # Зачем копим: (1) фактическое время в пути против оценки OSRM — сейчас в
        # движке стоит взятый с потолка коэффициент ×1.15; (2) какие дороги реально
        # проезжаемы (в горах ТДж OSM рисует сезонные грунтовки как обычные дороги);
        # (3) сколько на самом деле длится визит — вход в скоринг.
        #
        # UNIQUE(session_id, ts): повторная доставка пачки из оффлайн-очереди
        # (сеть в поле рвётся постоянно) не должна плодить точки — тот же приём,
        # что и в photo_geo с UNIQUE(file).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS track_points (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              employee TEXT,
              telegram_id INTEGER,
              contract_id TEXT,
              ts TEXT NOT NULL,
              lat REAL NOT NULL, lon REAL NOT NULL,
              acc_m INTEGER, speed REAL, heading REAL,
              created_at TEXT,
              UNIQUE(session_id, ts)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_track_session "
                     "ON track_points(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_track_emp_ts "
                     "ON track_points(employee, ts)")
        # КОРПУС ДЛЯ ОБУЧЕНИЯ СКОРИНГА. Отдельная сущность от портфелей выездников:
        # сюда загружается ОБЩИЙ реестр целиком (17 тыс договоров), а не то, что
        # роздано на выезд. Живёт своей жизнью и переживает сброс портфелей —
        # иначе каждый сброс стирал бы всё, на чём модель училась.
        #
        # Персональных данных здесь НЕТ и быть не должно: обучению нужны признаки
        # и результат, а не имя с адресом. Номер договора хранится хэшем — его
        # хватает, чтобы не задваивать строки при повторной загрузке снимка.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS training_rows (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              snapshot TEXT NOT NULL,
              contract_key TEXT NOT NULL,
              debt REAL,
              product_type TEXT, partner TEXT, country TEXT,
              assign_date TEXT, kd_date TEXT, promise_date TEXT,
              result TEXT, contact_with TEXT, who TEXT, location TEXT,
              visits INTEGER, paid_months INTEGER,
              target REAL,
              created_at TEXT,
              UNIQUE(snapshot, contract_key)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_training_snapshot "
                     "ON training_rows(snapshot)")
        # Происхождение обещания в корпусе. До вер. 33 «дата обещания» могла быть
        # записана только офисом; после — ещё и выездником из формы. Признак модели
        # один и тот же, а смысл разный, и без метки старые и новые строки корпуса
        # стали бы неразличимы навсегда (training_rows переживает сброс намеренно).
        # Колонку заводим ДО первой записи из формы — потом окно закрывается.
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(training_rows)")}
        if "promise_src" not in tcols:
            conn.execute("ALTER TABLE training_rows ADD COLUMN promise_src TEXT")
        # Беседы для отчётов о выездах: бот, добавленный в группу, регистрирует
        # её здесь (выключенной); пульт включает и закрепляет сотрудников (ФИО
        # из реестра). Закрытый визит сотрудника постится в его беседы.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS report_chats (
              chat_id INTEGER PRIMARY KEY,
              title TEXT,
              enabled INTEGER NOT NULL DEFAULT 0,
              removed INTEGER NOT NULL DEFAULT 0,
              added_at TEXT
            )
        """)
        # Направление беседы (tj/uz/all): состав считается живьём, как у
        # руководителя направления. Поимённое закрепление остаётся и работает
        # вместе с направлением — беседа отдела плюс пара чужих сотрудников.
        rc_cols = {r[1] for r in conn.execute("PRAGMA table_info(report_chats)")}
        if "direction" not in rc_cols:
            conn.execute("ALTER TABLE report_chats ADD COLUMN direction TEXT")
        # Личные рубильники руководителя. Значение по умолчанию 1: миграция
        # существующих строк не имеет права молча выключить рассылку.
        mgr_cols = {r[1] for r in conn.execute("PRAGMA table_info(managers)")}
        for col in MANAGER_PUSH_KEYS:
            if col not in mgr_cols:
                conn.execute(f"ALTER TABLE managers ADD COLUMN {col} "
                             f"INTEGER NOT NULL DEFAULT 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS report_chat_employees (
              chat_id INTEGER,
              employee TEXT,
              UNIQUE(chat_id, employee)
            )
        """)
        # Миграция ранней дев-схемы app_tokens (без expires_at).
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(app_tokens)")}
        if "expires_at" not in tcols:
            conn.execute("ALTER TABLE app_tokens ADD COLUMN expires_at TEXT")
        # Миграция app_links времён одноразовых кодов (без счётчика uses).
        lcols = {r[1] for r in conn.execute("PRAGMA table_info(app_links)")}
        if "uses" not in lcols:
            conn.execute("ALTER TABLE app_links ADD COLUMN uses INTEGER NOT NULL DEFAULT 0")
        # Одноразовый переход без потери уже завершённых визитов: переносим только
        # текущие значения, подтверждённые history.visits и той же датой. Маркер
        # обязателен: после reset_visits история остаётся, и повторная миграция не
        # должна воскресить старую сессию из очередного импорта.
        migration_key = "migration_bot_visit_provenance_v1"
        migrated = conn.execute(
            "SELECT 1 FROM settings WHERE key=?", (migration_key,)).fetchone()
        if not migrated:
            n_backfilled = _backfill_bot_visit_snapshots(conn)
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                (migration_key, json.dumps({"contracts": n_backfilled,
                                            "at": date.today().isoformat()})),
            )
        # Сидинг журнала значений визита для строк, созданных до колонки
        # visit_value_log: без него якорный шлюз принял бы эхо текущего снимка
        # из давно обогащённого файла за офисную правку. Идемпотентно: после
        # первого прохода журнал непустой и строка больше не выбирается.
        for r in conn.execute("SELECT * FROM clients WHERE bot_visit_data IS NOT "
                              "NULL AND visit_value_log IS NULL").fetchall():
            seeded = _log_append(_log_append({}, _decode_bot_snapshot(r) or {}),
                                 _decode_office_edits(r))
            if seeded:
                conn.execute(
                    "UPDATE clients SET visit_value_log=? WHERE contract_id=?",
                    (_log_json(seeded), r["contract_id"]))
        conn.commit()
    reset_role_caches()   # схема/роли могли поменяться — индекс адресатов заново


# --- Профиль / язык ---------------------------------------------------------
def ensure_owner(telegram_id: int, name: str = "Владелец") -> None:
    with connect() as conn:
        conn.execute(
            # role — легаси-колонка; задаём явно, чтобы пережить старую схему с NOT NULL
            "INSERT INTO users (telegram_id, name, role, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(telegram_id) DO NOTHING",
            (telegram_id, name, "owner", date.today().isoformat()),
        )
        conn.commit()


def get_lang(telegram_id: int) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT lang FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    return row["lang"] if row and row["lang"] else None


def set_lang(telegram_id: int, lang: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (telegram_id, lang, role, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET lang=excluded.lang",
            (telegram_id, lang, "collector", date.today().isoformat()),
        )
        conn.commit()


# --- Гости (кто писал боту) -------------------------------------------------
def touch_guest(telegram_id: int, username: str | None = None,
                name: str | None = None) -> None:
    """Запомнить факт обращения к боту: telegram_id + @username + имя + время.
    Источник для списка выбора при привязке в пульте. Пустые значения не затирают
    уже сохранённые (COALESCE)."""
    now = datetime.now().isoformat(timespec="seconds")
    uname = (username or "").strip() or None
    nm = (name or "").strip() or None
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (telegram_id, name, username, last_seen, role, created_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET "
            "  username=COALESCE(excluded.username, users.username), "
            "  name=COALESCE(excluded.name, users.name), "
            "  last_seen=excluded.last_seen",
            (telegram_id, nm, uname, now, "collector", date.today().isoformat()),
        )
        conn.commit()


def guests() -> list[dict]:
    """Все, кто писал боту (users), с подтянутой привязкой (agent_bind):
    {telegram_id, username, name, last_seen, bound_employee|None}. Свежие сверху.
    Владелец (ADMIN) тоже попадёт сюда, если писал боту — это ок, его id обычно и нужен."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT u.telegram_id, u.username, u.name, u.last_seen, b.employee AS bound "
            "FROM users u LEFT JOIN agent_bind b ON b.telegram_id = u.telegram_id "
            "ORDER BY (u.last_seen IS NULL), u.last_seen DESC, u.telegram_id"
        ).fetchall()
    return [{"telegram_id": r["telegram_id"], "username": r["username"],
             "name": r["name"], "last_seen": r["last_seen"],
             "bound_employee": r["bound"]} for r in rows]


def guest(telegram_id: int) -> dict | None:
    """Кто сидит на этом telegram_id ПО ФАКТУ (users), или None — бот такого не видел.

    Имя руководителя в пульте печатает оператор, а права всегда получает владелец id.
    Разошлись — значит роль ушла не тому: так запись «Михаил» со всеми четырьмя
    городами оказалась на постороннем аккаунте и четыре дня получала полный дайджест
    с ФИО должников. Поэтому перед выдачей роли сверяемся с этой функцией."""
    with connect() as conn:
        r = conn.execute(
            "SELECT u.telegram_id, u.username, u.name, u.last_seen, b.employee AS bound "
            "FROM users u LEFT JOIN agent_bind b ON b.telegram_id = u.telegram_id "
            "WHERE u.telegram_id = ?", (telegram_id,)).fetchone()
    return {"telegram_id": r["telegram_id"], "username": r["username"],
            "name": r["name"], "last_seen": r["last_seen"],
            "bound_employee": r["bound"]} if r else None


# --- Привязка «кто есть кто» (telegram_id → сотрудник, одноразово) -----------
def employees_in_registry() -> list[sqlite3.Row]:
    """Список ответственных сотрудников из реестра (для самовыбора)."""
    with connect() as conn:
        return conn.execute(
            "SELECT responsible AS employee, COUNT(*) AS contracts FROM clients "
            "WHERE responsible IS NOT NULL AND TRIM(responsible) <> '' "
            "GROUP BY responsible ORDER BY responsible"
        ).fetchall()


def bind_agent(telegram_id: int, employee: str, kind: str = "agent",
               approved: bool = True) -> bool:
    """Закрепить за пользователем сотрудника + тип (agent/courier).

    Одобренную привязку менять НЕЛЬЗЯ: самовыбор одноразовый, переназначение —
    только через пульт. А вот НЕОДОБРЕННУЮ заявку человек вправе переписать: без
    этого промах пальцем при онбординге запирал навсегда. INSERT OR IGNORE молча
    ничего не делал (telegram_id — PRIMARY KEY), приложение показывало «Нет
    доступа», и выхода из этого экрана не было вообще.
    """
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO agent_bind (telegram_id, employee, kind, approved, created_at) "
            "VALUES (?,?,?,?,?)",
            (telegram_id, employee, kind, 1 if approved else 0, date.today().isoformat()),
        )
        if cur.rowcount == 0:
            cur = conn.execute(
                "UPDATE agent_bind SET employee = ?, kind = ?, approved = ?, created_at = ? "
                "WHERE telegram_id = ? AND approved = 0",
                (employee, kind, 1 if approved else 0, date.today().isoformat(), telegram_id),
            )
        conn.commit()
        return cur.rowcount > 0


def employee_taken_by(employee: str, exclude_telegram_id: int = 0) -> int | None:
    """telegram_id, за которым УЖЕ закреплён этот сотрудник (или None).

    Правило владельца: один сотрудник — одно устройство. Второй аккаунт на то же
    ФИО почти всегда означает одно из двух: человек завёл второй телеграм (и тогда
    его активность разъезжается по двум привязкам — карточка дайджеста показывала
    нули отработавшему), либо на чужое ФИО зашёл посторонний. И то и другое должен
    разрешать владелец руками, а не самовыбор.

    Считаем только ОДОБРЕННЫЕ: заявка, которую владелец ещё не подтвердил, никого
    не занимает.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT telegram_id FROM agent_bind "
            "WHERE employee = ? AND approved = 1 AND telegram_id <> ? LIMIT 1",
            (employee, exclude_telegram_id)).fetchone()
    return row["telegram_id"] if row else None


def set_bind(telegram_id: int, employee: str, kind: str = "agent") -> None:
    """Предзаданная привязка из пульта: заказчик сам вписывает telegram_id
    сотруднику (ФИО из реестра) и тип. approved=1 сразу — работник, написавший
    боту с этим id, сразу привязан к своим клиентам (без самовыбора).

    Корректный upsert (в отличие от bind_agent с INSERT OR IGNORE):
      (а) один employee = один telegram_id — старые записи этого ФИО чистим;
      (б) переназначение того же telegram_id обновляет employee/kind, а не игнорит.
    Матчинг клиентов к работнику идёт по clients.responsible == agent_bind.employee —
    его не трогаем."""
    kind = "courier" if kind == "courier" else "agent"
    employee = (employee or "").strip()
    with connect() as conn:
        # (а) убрать прежние привязки этого ФИО на другие id (переезд ФИО на новый id)
        conn.execute("DELETE FROM agent_bind WHERE employee = ? AND telegram_id <> ?",
                     (employee, telegram_id))
        # (б) upsert по telegram_id (PRIMARY KEY): создать или переназначить, approved=1
        conn.execute(
            "INSERT INTO agent_bind (telegram_id, employee, kind, approved, created_at) "
            "VALUES (?,?,?,1,?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET "
            "employee=excluded.employee, kind=excluded.kind, approved=1",
            (telegram_id, employee, kind, date.today().isoformat()),
        )
        conn.commit()


def employees_with_bindings() -> list[dict]:
    """ФИО из реестра (clients.responsible) с подтянутой привязкой из agent_bind:
    {employee, contracts, telegram_id|None, kind|None, approved|None}. Источник для
    экрана «Роли» в пульте — там заказчик вписывает telegram_id каждому ФИО."""
    with connect() as conn:
        emps = conn.execute(
            "SELECT responsible AS employee, COUNT(*) AS contracts FROM clients "
            "WHERE responsible IS NOT NULL AND TRIM(responsible) <> '' "
            "GROUP BY responsible ORDER BY responsible"
        ).fetchall()
        # Только одобренные: неодобренная заявка второго телефона на то же ФИО
        # не должна выглядеть как «сотрудник привязан» (правило «одно ФИО — одно
        # устройство» теперь такие строки создаёт штатно).
        binds = {b["employee"]: b for b in
                 conn.execute("SELECT * FROM agent_bind WHERE approved = 1"
                              " ORDER BY telegram_id").fetchall()}
    out = []
    for e in emps:
        b = binds.get(e["employee"])
        out.append({
            "employee": e["employee"], "contracts": e["contracts"],
            "telegram_id": b["telegram_id"] if b else None,
            "kind": (b["kind"] if b and b["kind"] else None) if b else None,
            "approved": (b["approved"] if b else None),
        })
    return out


def clients_of_employee(employee: str) -> list:
    """Все договоры, закреплённые за сотрудником, — для «убрать клиентов» в пульте.
    Без фильтра по периоду: снимают из ПОЛНОГО текущего портфеля, а не из витрины."""
    with connect() as conn:
        return conn.execute(
            "SELECT contract_id, client, partner, debt_total, debt_today FROM clients "
            "WHERE responsible = ? ORDER BY client, contract_id", (employee,)).fetchall()


def _name_key(name) -> str:
    """Ключ сравнения ФИО: регистр, лишние пробелы и ё/е значения не имеют.

    SQL-ный NOCASE тут бессилен — он работает только с латиницей. А в реестре
    достаточно одной опечатки регистра («михаил» вместо «Михаил»), чтобы у
    сотрудника появился двойник: отдельный «сотрудник» с одним договором, свой
    портфель, своя привязка. На экране «Роли» это видно, но искать причину
    приходится глазами.
    """
    return " ".join(str(name or "").replace("ё", "е").replace("Ё", "Е").split()).casefold()


def canonical_responsible(name: str, known: dict[str, str]) -> str:
    """Привести написание ответственного к уже известному в базе.

    known — {ключ сравнения: как записано в базе}. Совпало по ключу — берём
    написание из базы; не совпало — это действительно новый человек.
    """
    key = _name_key(name)
    return known.get(key, name) if key else name


def unassign_clients(employee: str, contract_ids: list[str]) -> int:
    """Снять договоры с сотрудника (пульт): чистим responsible — клиент ОСТАЁТСЯ в
    реестре, но уходит из миниаппа сотрудника. Снимаем только его собственные
    договоры (гонка с переимпортом/переразбросом — чужие молча пропускаются).
    Возвращает число снятых."""
    ids = [str(c).strip() for c in contract_ids if str(c or "").strip()]
    if not employee or not ids:
        return 0
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE clients SET responsible = '', unassigned_from = ?, "
            f"unassigned_reason = ? "
            f"WHERE responsible = ? "
            f"AND contract_id IN ({','.join('?' * len(ids))})",
            (employee, UNASSIGNED_REASON_MANUAL, employee, *ids))
        conn.commit()
        n = cur.rowcount
    _bump_version()          # иначе индекс приоритета держит снятых до импорта
    reset_role_caches()
    return n


def unassigned_overview(*, reason: str = UNASSIGNED_REASON_RECALLED,
                        employee: str = "", limit: int = 500) -> dict:
    """Кто снят с работы и почему — единственная выдача, где откреплённые видны.

    Все обычные выборки сидят на responsible = ?, поэтому откреплённый договор
    не показывает ни один экран. Здесь фильтр наоборот — по unassigned_reason.
    Явный список колонок (не SELECT *): blob-колонки провенанса незачем гонять
    по 500 строк на каждый клик пульта."""
    cap = max(1, min(int(limit or 500), 5000))
    cols = ("contract_id, client, partner, unassigned_from, "
            "recall_date, recall_reason, recall_transfer, recall_withdrawn, "
            "debt_total, debt_today")
    with connect() as conn:
        totals = {(r["reason"] or ""): r["n"] for r in conn.execute(
            "SELECT COALESCE(unassigned_reason,'') AS reason, COUNT(*) AS n "
            "FROM clients WHERE COALESCE(unassigned_reason,'') <> '' GROUP BY 1")}
        by_employee = {r["who"]: r["n"] for r in conn.execute(
            "SELECT COALESCE(unassigned_from,'') AS who, COUNT(*) AS n "
            "FROM clients WHERE COALESCE(unassigned_reason,'') = ? "
            "GROUP BY 1 ORDER BY 2 DESC", (reason,))}
        sql = (f"SELECT {cols} FROM clients "
               "WHERE COALESCE(unassigned_reason,'') = ?")
        params: list = [reason]
        if employee:
            sql += " AND unassigned_from = ?"
            params.append(employee)
        sql += " ORDER BY unassigned_from, client, contract_id LIMIT ?"
        params.append(cap)
        rows = conn.execute(sql, params).fetchall()
    return {"totals": totals, "by_employee": by_employee, "rows": rows,
            "shown": len(rows), "limit": cap}


def get_binding(telegram_id: int) -> str | None:
    """Сотрудник пользователя — ТОЛЬКО если привязка одобрена (approved=1).
    Привязка в ожидании аппрува доступа не даёт (ИБ: авторизация на этапе привязки)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT employee FROM agent_bind WHERE telegram_id = ? AND approved = 1",
            (telegram_id,),
        ).fetchone()
    return row["employee"] if row else None


def pending_binding(telegram_id: int) -> sqlite3.Row | None:
    """Неодобренная привязка (для статуса «ожидайте аппрува»)."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM agent_bind WHERE telegram_id = ? AND approved = 0", (telegram_id,)
        ).fetchone()


def approve_binding(telegram_id: int) -> str | None:
    """ADMIN одобряет привязку. Возвращает сотрудника (или None, если нечего одобрять)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT employee FROM agent_bind WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE agent_bind SET approved = 1 WHERE telegram_id = ?", (telegram_id,))
        conn.commit()
    return row["employee"]


def reject_binding(telegram_id: int) -> None:
    """ADMIN отклоняет привязку (удаляем — выездник сможет выбрать себя заново)."""
    with connect() as conn:
        conn.execute("DELETE FROM agent_bind WHERE telegram_id = ? AND approved = 0",
                     (telegram_id,))
        conn.commit()


def list_pending() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM agent_bind WHERE approved = 0 ORDER BY created_at"
        ).fetchall()


def get_kind(telegram_id: int) -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT kind FROM agent_bind WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    return (row["kind"] if row and row["kind"] else "agent")


def telegram_for_employee(employee: str) -> int | None:
    """Кому слать напоминание о повторном выезде — ТОЛЬКО одобренной привязке.

    Фильтра approved здесь не было, и он стал обязательным после правила «одно ФИО
    — одно устройство»: теперь неодобренные строки появляются штатно, при каждой
    попытке второго телефона. Без фильтра ФИО, номер договора и адрес должника
    каждый час уходили бы на аккаунт, которому система только что ОТКАЗАЛА в
    доступе, а настоящий выездник напоминания не видел бы вовсе.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT telegram_id FROM agent_bind WHERE employee = ? AND approved = 1 "
            "ORDER BY telegram_id LIMIT 1", (employee,)
        ).fetchone()
    return row["telegram_id"] if row else None


def list_bindings(include_pending: bool = False) -> list[sqlite3.Row]:
    """Действующие привязки. Заявка, ждущая одобрения, — НЕ привязка.

    04.08.2026: в сводке пульта у Акназарова оказалось «два устройства», и по
    списку это выглядело как выданный доступ постороннему. На деле вторая строка
    была заявкой с approved=0 — правило «одно устройство» отработало и никого не
    пустило. Но отличить это по списку было нельзя: он показывал обе одинаково.

    Тревога по такому поводу дороже пропущенной: владелец идёт отзывать доступ,
    которого нет, и теряет доверие к списку. Кому нужны заявки — просит явно.
    """
    where = "" if include_pending else " WHERE approved = 1"
    with connect() as conn:
        return conn.execute(
            f"SELECT * FROM agent_bind{where} ORDER BY employee").fetchall()


def unbind(telegram_id: int) -> None:
    """Снять привязку сотрудника. Заодно отзываем доступ автономного приложения
    (токены + неактивированные ссылки): «убрать работника» из пульта должно закрывать
    и PWA, а не оставлять живой девайс-токен до 30 дней."""
    with connect() as conn:
        conn.execute("DELETE FROM agent_bind WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM app_tokens WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM app_links WHERE telegram_id = ?", (telegram_id,))
        conn.commit()


# --- Автономное приложение (PWA вне Телеги): ссылки входа + токены -----------
APP_LINK_DAYS = 7       # окно активации ссылки входа
APP_LINK_MAX_USES = 10  # обменов на токен с одной ссылки (Телега + Safari + повторы)
APP_TOKEN_DAYS = 30     # скользящий срок токена: продлевается при каждом использовании


# Личности без Telegram. Вся идентификация в системе — целый id пользователя
# (исторически telegram_id). Если Telegram не используется, пульт выдаёт
# сотруднику служебный id из диапазона выше любого настоящего Telegram-id и
# сразу привязывает его к ФИО; ссылка входа для такого id работает как обычная.
LOCAL_UID_BASE = 900_000_000_000


def is_local_uid(uid: int) -> bool:
    return int(uid or 0) >= LOCAL_UID_BASE


def local_identity_for(employee: str, kind: str = "agent") -> tuple[int, bool]:
    """(id, создана_ли_сейчас) для ФИО. Существующая привязка ФИО — любая, в том
    числе на настоящий Telegram — переиспользуется: у сотрудника один вход."""
    employee = (employee or "").strip()
    if not employee:
        raise ValueError("пустое ФИО")
    with connect() as conn:
        row = conn.execute(
            "SELECT telegram_id FROM agent_bind WHERE employee = ? "
            "ORDER BY approved DESC, telegram_id LIMIT 1",
            (employee,),
        ).fetchone()
        if row:
            return int(row["telegram_id"]), False
        used = [
            conn.execute(
                f"SELECT MAX(telegram_id) FROM {table} WHERE telegram_id >= ?",
                (LOCAL_UID_BASE,),
            ).fetchone()[0] or 0
            for table in ("agent_bind", "users")
        ]
        uid = max(LOCAL_UID_BASE - 1, *used) + 1
        conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, name, created_at) VALUES (?,?,?)",
            (uid, employee, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    set_bind(uid, employee, kind)
    return uid, True


def create_app_link(telegram_id: int) -> str:
    """Код входа для автономного PWA (бот шлёт ссылкой). Прежние НЕОТКРЫТЫЕ коды
    юзера гасим (из неоткрытых жива только последняя ссылка; уже открытые доживают
    своё окно — по ним могли ещё не доустановить ярлык), выданный девайс-токен
    НЕ трогаем: запрос новой ссылки не разлогинивает телефон."""
    code = secrets.token_urlsafe(18)
    now = datetime.now()
    with connect() as conn:
        conn.execute("DELETE FROM app_links WHERE telegram_id = ? AND used_at IS NULL",
                     (telegram_id,))
        # заодно чистим просроченный мусор всех юзеров (лениво, без отдельного крона)
        cutoff = (now - timedelta(days=APP_LINK_DAYS)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM app_links WHERE created_at < ?", (cutoff,))
        conn.execute("INSERT INTO app_links (code, telegram_id, created_at) VALUES (?,?,?)",
                     (code, telegram_id, now.isoformat(timespec="seconds")))
        conn.commit()
    return code


def redeem_app_link(code: str) -> str | None:
    """Обменять код из ссылки бота на девайс-токен. Код МНОГОразовый в окне активации
    (APP_LINK_DAYS, максимум APP_LINK_MAX_USES обменов): одну и ту же ссылку выездник
    открывает в ин-апп браузере Телеги, потом в Safari/Chrome для установки на экран —
    каждый контекст получает свой токен, ничего не «сгорает» и код руками не вводится.
    Плата за преемственность: пересланная СВЕЖАЯ ссылка тоже пустит; защита — короткое
    окно, лимит обменов и явный revoke_app_access из пульта. Прежние токены пользователя
    НЕ трогаем: несколько устройств/браузеров — норма. None — код неизвестен, просрочен
    или исчерпан."""
    if not code:
        return None
    now = datetime.now()
    cutoff = (now - timedelta(days=APP_LINK_DAYS)).isoformat(timespec="seconds")
    with connect() as conn:
        # Атомарный расход попытки: rowcount==0 → код чужой/просрочен/исчерпан
        # (в т.ч. проигранная гонка параллельных обменов за последнюю попытку).
        cur = conn.execute(
            "UPDATE app_links SET used_at = ?, uses = uses + 1 WHERE code = ? "
            "AND created_at >= ? AND uses < ?",
            (now.isoformat(timespec="seconds"), code, cutoff, APP_LINK_MAX_USES))
        if cur.rowcount == 0:
            conn.commit()
            return None
        uid = conn.execute("SELECT telegram_id FROM app_links WHERE code = ?",
                           (code,)).fetchone()["telegram_id"]
        token = secrets.token_urlsafe(24)
        conn.execute(
            "INSERT INTO app_tokens (token, telegram_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, uid, now.isoformat(timespec="seconds"),
             (now + timedelta(days=APP_TOKEN_DAYS)).isoformat(timespec="seconds")))
        conn.commit()
    return token


def uid_for_app_token(token: str) -> int | None:
    """telegram_id по девайс-токену; просроченный (или легаси без expires_at) не пускаем."""
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT telegram_id FROM app_tokens WHERE token = ? AND expires_at >= ?",
            (token, datetime.now().isoformat(timespec="seconds"))).fetchone()
    return row["telegram_id"] if row else None


def touch_app_token(token: str) -> None:
    """Скользящее продление: живой токен при использовании отодвигает expires_at ещё
    на APP_TOKEN_DAYS. Пока приложение открывают хотя бы раз в месяц — вход вечный
    («поставил на экран — это его телефон»); умирает только у брошенных устройств.
    Просроченный токен НЕ воскрешаем (только живой), отзыв из пульта работает как раньше."""
    now = datetime.now()
    with connect() as conn:
        conn.execute(
            "UPDATE app_tokens SET expires_at = ? WHERE token = ? AND expires_at >= ?",
            ((now + timedelta(days=APP_TOKEN_DAYS)).isoformat(timespec="seconds"),
             token, now.isoformat(timespec="seconds")))
        conn.commit()


def revoke_app_access(telegram_id: int) -> int:
    """Отзыв доступа автономного приложения: гасим и токены, и неактивированные ссылки.
    Возвращает число отозванных токенов (для ответа пульту)."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM app_tokens WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM app_links WHERE telegram_id = ?", (telegram_id,))
        conn.commit()
        return cur.rowcount


# --- Настройки --------------------------------------------------------------
def get_setting(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()


# --- Руководители (роль дайджеста) ------------------------------------------
def _json_list(s) -> list[str]:
    """Разобрать JSON-массив строк из БД в list[str]; мусор → []."""
    try:
        v = json.loads(s) if s else []
    except (ValueError, TypeError):
        return []
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


# Персональные рубильники руководителя. Раньше рубильник был ОДИН на всех
# (settings.visit_push), и «гендиру не нужны 225 карточек в день» решалось
# только тем, что он уносил чат в архив. Значение по умолчанию — 1: включение
# роли не должно молча отбирать уже работающие уведомления.
MANAGER_PUSH_KEYS = ("push_visits", "push_digest", "push_promises")

DIRECTIONS = {
    "tj": ("Таджикистан", ("Таджикистан",)),
    "uz": ("Узбекистан", ("Узбекистан",)),
    "all": ("СНГ", ("Таджикистан", "Узбекистан", "Россия")),
}


# --- Город СОТРУДНИКА (офис), а не города его клиентов -----------------------
# Руководителю в Душанбе нужны отбивки по своему штату. Вывести это из данных
# нельзя: город в clients — это где живёт ДОЛЖНИК, и душанбинский выездник ездит
# в Ёвон, Хисор и Вахдат, а не «в Душанбе» (в самом Душанбе всего 35 кейсов на
# всю базу). Принадлежность человека к офису — отдельный факт, его заводит
# оператор; в реестре его нет вовсе (колонка «Подразделение» там про Хард/Софт).
def set_employee_city(employee: str, city: str) -> None:
    """Закрепить сотрудника за офисом. Пустой город — снять закрепление."""
    employee = (employee or "").strip()
    if not employee:
        return
    city = (city or "").strip()
    with connect() as conn:
        if city:
            conn.execute(
                "INSERT INTO employee_city (employee, city, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(employee) DO UPDATE SET city=excluded.city, "
                "updated_at=excluded.updated_at",
                (employee, city, date.today().isoformat()))
        else:
            conn.execute("DELETE FROM employee_city WHERE employee = ?", (employee,))
        conn.commit()
    reset_role_caches()


def employee_cities() -> dict[str, str]:
    """{ФИО сотрудника: город}. Только заполненные."""
    with connect() as conn:
        return {r["employee"]: r["city"] for r in conn.execute(
            "SELECT employee, city FROM employee_city WHERE TRIM(city) <> ''")}


def employees_by_cities(cities) -> list[str]:
    """Сотрудники перечисленных офисов — состав ЖИВОЙ, как у направления.

    Сверяем по _name_key: в роли и в реестре ФИО пишут по-разному, и точное
    сравнение строк уже однажды оставило руководителей направлений без пушей."""
    wanted = {(c or "").strip().casefold() for c in (cities or []) if str(c).strip()}
    if not wanted:
        return []
    mapping = employee_cities()
    known = {_name_key(r["employee"]): r["employee"]
             for r in employees_in_registry()}
    out: dict[str, None] = {}
    for employee, city in mapping.items():
        if (city or "").strip().casefold() not in wanted:
            continue
        # Отдаём написание РЕЕСТРА: под ним живут портфели, визиты и пуши.
        out.setdefault(known.get(_name_key(employee), employee), None)
    return sorted(out)


def employees_by_direction(direction: str) -> list[str]:
    """Сотрудники направления — СЧИТАЕМ ЖИВЬЁМ, а не замораживаем списком.

    У руководителя направления состав меняется с каждым разбросом. Замороженный
    список устаревал бы молча: новый сотрудник не появлялся бы в сводке, а
    уволенный оставался бы в ней навсегда. Страну берём из колонки «Страна»
    реестра, а если она пуста — из города, теми же токенами, что и статистика.
    """
    import country_scope
    out: dict[str, None] = {}
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT responsible, country, city FROM clients "
            "WHERE responsible IS NOT NULL AND TRIM(responsible) <> ''").fetchall()
    if direction == "all":
        # СНГ — это ВСЕ, а не «Таджикистан плюс Узбекистан». Пока страна умела быть
        # только этими двумя, разницы не было; с портфелем по России сотрудник, у
        # которого нет ни одного таджикского или узбекского кейса, выпадал бы из
        # сводки руководителя СНГ молча — как будто он не работает.
        for r in rows:
            out.setdefault(r["responsible"], None)
        return sorted(out)
    for r in rows:
        code = (country_scope.scope_country([r["country"] or ""])
                or country_scope.scope_country([r["city"] or ""]))
        if code == direction:
            out.setdefault(r["responsible"], None)
    return sorted(out)


def _manager_dict(r: sqlite3.Row) -> dict:
    keys = r.keys()
    out = {"telegram_id": r["telegram_id"], "name": r["name"] or "",
           "cities": _json_list(r["cities"]), "employees": _json_list(r["employees"]),
           "direction": (r["direction"] or "") if "direction" in keys else "",
           "office_cities": (_json_list(r["office_cities"])
                             if "office_cities" in keys else []),
           "can_dm": r["can_dm"] if "can_dm" in keys else None}
    for key in MANAGER_PUSH_KEYS:
        out[key] = bool(r[key]) if key in keys and r[key] is not None else True
    return out


def set_manager_pushes(telegram_id: int, pushes: dict) -> None:
    """Личные рубильники руководителя: {push_visits|push_digest|push_promises: bool}.
    Ключи, которых нет в словаре, не трогаются."""
    fields = {k: 1 if pushes[k] else 0 for k in MANAGER_PUSH_KEYS if k in pushes}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE managers SET {sets} WHERE telegram_id=?",
                     (*fields.values(), telegram_id))
        conn.commit()
    reset_role_caches()


def set_manager(telegram_id: int, name: str, cities: list[str],
                employees: list[str], direction: str | None = None,
                pushes: dict | None = None,
                office_cities: list[str] | None = None) -> None:
    """Создать/обновить руководителя (upsert по telegram_id). cities — ярлык-подпись
    (свободный ввод), employees — ФИО из реестра (кого он видит в дайджесте).
    office_cities — офисы, чей штат он ведёт: состав живой (db.employee_city),
    складывается с employees. None — «не трогать» (старый пульт поля не шлёт).

    direction (tj/uz/all) — руководитель НАПРАВЛЕНИЯ: сотрудники считаются живьём.
    pushes — личные рубильники; None означает «не трогать» (у новой записи
    остаются значения по умолчанию — всё включено).
    """
    with connect() as conn:
        # office_cities=None означает «не трогать»: старый пульт этого поля не
        # шлёт, и сохранение руководителя не должно молча снимать ему охват.
        keep_offices = office_cities is None
        conn.execute(
            "INSERT INTO managers "
            "(telegram_id, name, cities, employees, direction, office_cities, created_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET "
            "name=excluded.name, cities=excluded.cities, employees=excluded.employees, "
            "direction=excluded.direction, "
            "office_cities=CASE WHEN ? THEN managers.office_cities "
            "ELSE excluded.office_cities END",
            (telegram_id, (name or "").strip(),
             json.dumps(list(cities or []), ensure_ascii=False),
             json.dumps(list(employees or []), ensure_ascii=False),
             (direction or "").strip() or None,
             json.dumps(list(office_cities or []), ensure_ascii=False),
             date.today().isoformat(), 1 if keep_offices else 0),
        )
        conn.commit()
    if pushes:
        set_manager_pushes(telegram_id, pushes)
    reset_role_caches()


def get_manager(telegram_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM managers WHERE telegram_id = ?",
                         (telegram_id,)).fetchone()
    return _manager_dict(r) if r else None


def list_managers() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM managers ORDER BY (name IS NULL OR name=''), name, telegram_id"
        ).fetchall()
    return [_manager_dict(r) for r in rows]


def delete_manager(telegram_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM managers WHERE telegram_id = ?", (telegram_id,))
        conn.commit()
    reset_role_caches()


def is_manager(telegram_id: int) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM managers WHERE telegram_id = ?",
                            (telegram_id,)).fetchone() is not None


# --- Руководитель в приложении: чей портфель он сейчас смотрит ----------------
# Руководителя НЕ привязывают к сотруднику как выездника — у него своя роль. Чтобы
# он мог открыть приложение, ему разрешено «действовать от имени» одного из СВОИХ
# сотрудников и переключаться между ними. Выбор хранится в settings по uid: он
# личный, переживает перезаход и не мешает другим.
_ACTING_KEY = "acting_employee:{}"


def manager_employees(telegram_id: int, *, is_owner: bool = False) -> list[str]:
    """Сотрудники, портфели которых руководитель вправе открыть.

    Владелец без явной роли видит всех — это его контур. Обычный руководитель —
    только тех, кто закреплён за ним в пульте; пустой список у него означает
    «никого», а не «всех», иначе роль не ограничивала бы ничего."""
    mgr = get_manager(telegram_id)
    if mgr is not None:
        direction = (mgr.get("direction") or "").strip()
        if direction:                      # руководитель направления — список живой
            return employees_by_direction(direction)
        allowed = [e for e in mgr.get("employees") or [] if str(e).strip()]
        # Офис — живой состав, а не поимённый: в портфеле выездника из другого
        # города ничего нет, а руководителю офиса сотрудник добавляется в тот
        # же день, когда его заводит оператор.
        allowed += employees_by_cities(mgr.get("office_cities"))
        return sorted(dict.fromkeys(allowed))
    if is_owner:
        return [r["employee"] for r in employees_in_registry()]
    return []


def acting_employee(telegram_id: int, allowed: list[str]) -> str | None:
    """Текущий выбор руководителя, сверенный со списком доступных.

    Сверка обязательна: сотрудника могли открепить или переименовать в реестре,
    и сохранённый выбор не должен становиться дырой в доступе."""
    if not allowed:
        return None
    chosen = get_setting(_ACTING_KEY.format(telegram_id))
    if chosen in allowed:
        return chosen
    # Фолбэк ЗАПИСЫВАЕМ. Иначе личность руководителя дрейфует сама: allowed[0]
    # зависит от текущего состава списка, а пульт перезаписывает его целиком —
    # добавили сотрудника с фамилией на «А», и человек молча оказался в другом
    # портфеле, а приложение сочло это сменой сотрудника и стёрло локальные данные.
    set_setting(_ACTING_KEY.format(telegram_id), allowed[0])
    return allowed[0]


def set_acting_employee(telegram_id: int, employee: str, allowed: list[str]) -> bool:
    """Переключить руководителя на портфель сотрудника. False — не разрешён."""
    if employee not in allowed:
        return False
    set_setting(_ACTING_KEY.format(telegram_id), employee)
    return True


def cities_in_registry() -> list[dict]:
    """Города из реестра (clients.city) с числом клиентов — для мультивыбора в пульте.
    Часто пуст (city — HARD-поле, его заполняет выездник), поэтому пульт даёт и свободный ввод."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT TRIM(city) c, COUNT(*) n FROM clients "
            "WHERE city IS NOT NULL AND TRIM(city) <> '' GROUP BY TRIM(city) ORDER BY n DESC, c"
        ).fetchall()
    return [{"name": r["c"], "count": r["n"]} for r in rows]


def city_employees() -> dict[str, list[str]]:
    """Карта город → сотрудники, у кого есть клиенты в этом городе (подсказка в пульте:
    отметил город — предложили его команду). Работает только там, где city заполнен."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT TRIM(city) c, responsible FROM clients "
            "WHERE city IS NOT NULL AND TRIM(city) <> '' "
            "AND responsible IS NOT NULL AND TRIM(responsible) <> '' "
            "GROUP BY TRIM(city), responsible"
        ).fetchall()
    out: dict[str, set] = {}
    for r in rows:
        out.setdefault(r["c"], set()).add(r["responsible"])
    return {c: sorted(v) for c, v in out.items()}


def employee_activity(employee: str) -> dict | None:
    """Метрики активности сотрудника (визиты/метки 7/30д, простой) — если он привязан
    к telegram (agent_bind). Не привязан → None (телеметрии нет).

    СУММИРУЕМ ПО ВСЕМ привязкам этого ФИО, а не берём первую попавшуюся. У людей
    бывает два телеграм-аккаунта (реальный случай: роль выдали одному, стучится с
    другого), самовыбор в онбординге заводит вторую строку, а сортировка ставит
    аккаунт БЕЗ активности первым — и карточка показывала «Визиты: 7д 0 · 30д 0»
    человеку, который отработал весь день. Заметить неоткуда: экран «Роли» в
    пульте дубли схлопывает по ФИО.
    """
    rows = [w for w in activity_stats() if w["employee"] == employee]
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    # Ключи здесь ДОЛЖНЫ совпадать с теми, что кладёт activity_stats. Первая
    # версия суммировала по выдуманным именам («visits7» вместо «visits_7d»),
    # молча ничего не делала и оставляла карточку с нулями — то есть чинила баг
    # только на бумаге.
    merged = dict(rows[0])
    for key in ("visits_7d", "visits_30d", "markers_7d", "markers_30d"):
        merged[key] = sum(int(r.get(key) or 0) for r in rows)
    lasts = [r.get("last_activity") for r in rows if r.get("last_activity")]
    merged["last_activity"] = max(lasts) if lasts else None
    idles = [r["idle_days"] for r in rows if r.get("idle_days") is not None]
    merged["idle_days"] = min(idles) if idles else None
    merged["orphaned"] = all(r.get("orphaned") for r in rows)
    return merged


def persons_processed(employee: str, on_date: str) -> list[dict]:
    """Люди сотрудника, у кого выезд состоялся в дату on_date (ISO). Сигнал —
    служебный bot_visit_at (его ставит только завершение в миниаппе). Возвращает
    сгруппированных людей (как persons_for_employee), но только обработанных в этот день."""
    target = _as_date(on_date)
    if target is None:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE responsible = ? "
            "AND bot_visit_at IS NOT NULL AND TRIM(bot_visit_at) <> ''",
            (employee,),
        ).fetchall()
    rows = [r for r in rows if _as_date(r["bot_visit_at"]) == target
            and _decode_bot_snapshot(r) is not None]
    return _group_persons(rows)


def persons_processed_range(employee: str, date_from: str, date_to: str) -> list[dict]:
    """Как persons_processed, но за диапазон дат [date_from..date_to] включительно —
    для недельной/месячной сводки руководителя."""
    lo, hi = _as_date(date_from), _as_date(date_to)
    if lo is None or hi is None:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE responsible = ? "
            "AND bot_visit_at IS NOT NULL AND TRIM(bot_visit_at) <> ''",
            (employee,),
        ).fetchall()
    rows = [r for r in rows if _decode_bot_snapshot(r) is not None
            and (d := _as_date(r["bot_visit_at"])) and lo <= d <= hi]
    return _group_persons(rows)


def visits_for_employee(employee: str, date_from: str, date_to: str) -> list[sqlite3.Row]:
    """Журнал завершённых выездов сотрудника за [date_from..date_to] (ISO,
    включительно): строки visits, чей договор сейчас закреплён за этим ФИО.

    Это ИСТОРИЯ, а не последний снимок: человек, к которому съездили 2-го и
    9-го, виден в оба дня. persons_processed_range такого не умеет — он смотрит
    на bot_visit_at, а повторный выезд переносит человека в свой день целиком.
    created_at режется до даты: старые строки могли нести время."""
    lo, hi = _as_date(date_from), _as_date(date_to)
    if lo is None or hi is None:
        return []
    with connect() as conn:
        return conn.execute(
            "SELECT v.id id, v.contract_id contract_id, "
            "substr(v.created_at, 1, 10) created_at, v.payload payload, "
            "v.agent_id agent_id, v.received_at received_at, "
            "v.visit_lat visit_lat, v.geo_far geo_far, "
            "v.fill_seconds fill_seconds, v.saved_at saved_at "
            "FROM visits v JOIN clients c ON c.contract_id = v.contract_id "
            "WHERE c.responsible = ? AND substr(v.created_at, 1, 10) BETWEEN ? AND ? "
            "ORDER BY v.id",
            (employee, lo.isoformat(), hi.isoformat()),
        ).fetchall()


def contracts_of_responsible(employee: str) -> list[sqlite3.Row]:
    """Все договоры ответственного целиком — без окна периода витрины (оно про
    дату закрепления и к статистике выездов отношения не имеет)."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE responsible = ? ORDER BY client, contract_id",
            (employee,),
        ).fetchall()


# --- Гео фото (фактическая точка съёмки) -------------------------------------
def record_photo(contract_id: str, file: str, telegram_id: int, *,
                 geo: tuple | None = None, sha256: str | None = None,
                 size_bytes: int | None = None, source: str | None = None,
                 shot_at_client: str | None = None,
                 shell_version: int | None = None,
                 meta_state: str | None = None, exif: dict | None = None,
                 exif_dt_norm: str | None = None,
                 shrunk: int | None = None,
                 addr: tuple | None = None,
                 assign_date_at: str | None = None,
                 meta_origin: str = "upload",
                 created_at: str | None = None) -> None:
    """Индексная строка снимка. Пишется для КАЖДОГО фото (гео может не быть).

    ON CONFLICT DO NOTHING: файл неизменяем (атомарная публикация), поэтому
    первая приехавшая запись и есть запись о снимке. Амендмент запрещён
    намеренно — иначе повторная отправка того же op с подделанным EXIF
    затёрла бы исходные метаданные. Единственное исключение — бэкфилл
    (см. photo_backfill: клиент строку meta_origin='backfill' создать не может).

    captured_at достоверен как время гео-фикса ТОЛЬКО у строк meta_origin=
    'upload'; у строк, заведённых обёрткой add_photo_geo и до вер. 33, он
    равен created_at из-за фолбэка «captured_at or now» — судить по нему
    время съёмки нельзя."""
    lat = lon = acc = captured = None
    if geo:
        lat, lon, acc, captured = geo
    a_lat = a_lon = a_tol = None
    if addr:
        a_lat, a_lon, a_tol = addr
    e = exif or {}
    now = created_at or datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO photo_geo (contract_id, file, telegram_id, lat, lon, "
            "acc_m, captured_at, created_at, sha256, size_bytes, source, "
            "shot_at_client, exif_dt, exif_tz, exif_dt_norm, exif_lat, "
            "exif_lon, exif_gps_at, exif_make, exif_model, exif_software, "
            "meta_state, shell_version, meta_origin, shrunk, addr_lat, "
            "addr_lon, addr_tol_m, assign_date_at) "
            "VALUES (" + ",".join("?" * 29) + ") ON CONFLICT(file) DO UPDATE SET "
            "telegram_id=excluded.telegram_id, lat=excluded.lat, "
            "lon=excluded.lon, acc_m=excluded.acc_m, "
            "captured_at=excluded.captured_at, created_at=excluded.created_at, "
            "sha256=excluded.sha256, size_bytes=excluded.size_bytes, "
            "source=excluded.source, shot_at_client=excluded.shot_at_client, "
            "exif_dt=excluded.exif_dt, exif_tz=excluded.exif_tz, "
            "exif_dt_norm=excluded.exif_dt_norm, exif_lat=excluded.exif_lat, "
            "exif_lon=excluded.exif_lon, exif_gps_at=excluded.exif_gps_at, "
            "exif_make=excluded.exif_make, exif_model=excluded.exif_model, "
            "exif_software=excluded.exif_software, "
            "meta_state=excluded.meta_state, "
            "shell_version=excluded.shell_version, "
            "meta_origin=excluded.meta_origin, shrunk=excluded.shrunk, "
            "addr_lat=excluded.addr_lat, addr_lon=excluded.addr_lon, "
            "addr_tol_m=excluded.addr_tol_m, "
            "assign_date_at=excluded.assign_date_at "
            # Единственный разрешённый амендмент: настоящие метаданные загрузки
            # перекрывают СИНТЕТИЧЕСКУЮ строку бэкфилла (гонка «бот принял фото,
            # пока шёл бэкфилл»). Клиент строку meta_origin='backfill' создать
            # не может (сервер пишет 'upload'), так что подделанный EXIF чужую
            # запись не затрёт.
            "WHERE photo_geo.meta_origin='backfill' AND excluded.meta_origin='upload'",
            (contract_id, file, telegram_id or None, lat, lon, acc, captured,
             now, sha256, size_bytes, source, shot_at_client, e.get("dt"),
             e.get("tz"), exif_dt_norm, e.get("lat"), e.get("lon"),
             e.get("gps_at"), e.get("make"), e.get("model"), e.get("software"),
             meta_state, shell_version, meta_origin, shrunk, a_lat, a_lon,
             a_tol, assign_date_at))
        conn.commit()


def add_photo_geo(contract_id: str, file: str, telegram_id: int,
                  lat: float, lon: float, acc_m: int | None,
                  captured_at: str | None = None) -> None:
    """Запомнить точку загрузки фото. file = «КД/имя файла» (уникален);
    повторная доставка того же фото из оффлайн-очереди строк не плодит.
    Тонкая обёртка record_photo — поведение (включая фолбэк captured_at or
    now) прибито tests/test_report_chats.py."""
    now = datetime.now().isoformat(timespec="seconds")
    record_photo(contract_id, file, telegram_id,
                 geo=(lat, lon, acc_m, captured_at or now))


# Сколько живут координаты снимков. ПО УМОЛЧАНИЮ — вечно (0 = автоуборки нет):
# точка съёмки это доказательство выезда, и терять её у всей базы по расписанию
# владелец не захотел. Ручка стирания отдельных кейсов — photo_geo_forget; срок
# включается настройкой, если он однажды понадобится ИБ.
PHOTO_GEO_RETENTION_DEFAULT = 0
_GEO_COLS = ("lat", "lon", "acc_m", "exif_lat", "exif_lon", "exif_gps_at")


def photo_geo_retention_days() -> int:
    raw = (get_setting("photo_geo_retention_days") or "").strip()
    if not raw:
        return PHOTO_GEO_RETENTION_DEFAULT
    try:
        days = int(float(raw))
    except (TypeError, ValueError):
        return PHOTO_GEO_RETENTION_DEFAULT
    return days if days >= 0 else PHOTO_GEO_RETENTION_DEFAULT


def photo_geo_stats() -> dict:
    """Что сейчас лежит: сколько снимков с координатами, сколько уже стёрто и
    какая точка самая старая. Без этого срок хранения — слово без проверки."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) total,"
            " SUM(CASE WHEN lat IS NOT NULL OR exif_lat IS NOT NULL"
            "     THEN 1 ELSE 0 END) with_geo,"
            " SUM(CASE WHEN geo_forgotten_at IS NOT NULL THEN 1 ELSE 0 END) forgotten,"
            " MIN(CASE WHEN lat IS NOT NULL OR exif_lat IS NOT NULL"
            "     THEN COALESCE(created_at, captured_at) END) oldest"
            " FROM photo_geo").fetchone()
    return {"photos": row["total"] or 0, "with_geo": row["with_geo"] or 0,
            "forgotten": row["forgotten"] or 0, "oldest": row["oldest"] or "",
            "retention_days": photo_geo_retention_days()}


def photo_geo_forget(*, before: str | None = None, employee: str | None = None,
                     contract_id: str | None = None) -> int:
    """Стереть координаты снимков (сами файлы и индекс остаются).

    Стираются ТОЛЬКО координаты: точка телефона, EXIF-точка и время GPS-фикса.
    Хэш, размер, время съёмки и модель камеры не трогаем — они не говорят, где
    человек живёт, и без них разбор «то же фото по другому договору» умрёт.
    Адресная точка должника (addr_*) — данные реестра, а не о сотруднике.

    Фильтры складываются: пустые = «все». Возвращает число стёртых строк.
    """
    where, args = ["(lat IS NOT NULL OR exif_lat IS NOT NULL)"], []
    if before:
        limit = _as_date(before)
        if limit is None:
            raise ValueError("неверная граница срока хранения")
        where.append("COALESCE(created_at, captured_at, '') < ?")
        args.append(limit.isoformat())
    if contract_id:
        where.append("contract_id = ?")
        args.append(canonical_contract_id(contract_id))
    if employee:
        tids = agent_tids(employee)
        if not tids:
            return 0
        where.append("telegram_id IN (%s)" % ",".join("?" for _ in tids))
        args.extend(tids)
    sets = ", ".join(f"{c} = NULL" for c in _GEO_COLS)
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE photo_geo SET {sets}, geo_forgotten_at = ? "
            f"WHERE {' AND '.join(where)}", [now, *args])
        conn.commit()
    return cur.rowcount


def photo_geo_sweep() -> int:
    """Плановая уборка по сроку хранения. По умолчанию срока нет — функция
    молчит, пока оператор явно не задаст число дней."""
    days = photo_geo_retention_days()
    if days <= 0:
        return 0
    before = (date.today() - timedelta(days=days)).isoformat()
    return photo_geo_forget(before=before)


def photo_index_for_dirs(dirs: list[str]) -> list[sqlite3.Row]:
    """Строки индекса по каталогам КД. Идёт по idx_photo_geo_contract."""
    if not dirs:
        return []
    qs = ",".join("?" for _ in dirs)
    with connect() as conn:
        return conn.execute(
            f"SELECT * FROM photo_geo WHERE contract_id IN ({qs}) "
            "ORDER BY COALESCE(created_at,'') DESC", list(dirs)).fetchall()


def photo_rows_by_sha(shas: list[str]) -> list[sqlite3.Row]:
    """Строки индекса по хэшам содержимого (дедуп «тот же файл у разных КД»)."""
    shas = [s for s in shas if s]
    if not shas:
        return []
    out = []
    for i in range(0, len(shas), 400):        # SQLITE_MAX_VARIABLE_NUMBER
        chunk = shas[i:i + 400]
        qs = ",".join("?" for _ in chunk)
        with connect() as conn:
            out += conn.execute(
                f"SELECT * FROM photo_geo WHERE sha256 IN ({qs})",
                chunk).fetchall()
    return out


def photo_uploaders_for_dirs(dirs: list[str]) -> set[int]:
    """Кто ЗАГРУЖАЛ фото в эти каталоги — по следу audit (web._photo пишет
    audit_access(uid,'photo',rep) на каждой загрузке с самого начала проекта,
    журнал append-only). Восстанавливает доступ «по привязке того, кто снимал»
    для архива: у бэкфилленных строк telegram_id NULL, а после законного
    переназначения импорт стирает метку открепления."""
    if not dirs:
        return set()
    qs = ",".join("?" for _ in dirs)
    with connect() as conn:
        return {r["actor"] for r in conn.execute(
            f"SELECT DISTINCT actor FROM audit WHERE action='photo' "
            f"AND target IN ({qs})", list(dirs)) if r["actor"]}


def photo_contract_dirs(contract_id: str) -> dict:
    """Каталоги, под которыми могут лежать фото этого договора.

    НЕ через person_contracts: она фильтрует по responsible и после
    открепления теряет договоры — весь альбом человека исчезал, хотя файлы
    лежат на диске. Здесь группируем ТЕМ ЖЕ person_key, но без фильтра
    ответственного."""
    cid = canonical_contract_id(contract_id)
    row = get_client(cid)
    contracts, person = [], None
    if row is not None:
        key = person_key(row)
        with connect() as conn:
            cand = conn.execute("SELECT * FROM clients WHERE client IS ?",
                                (row["client"],)).fetchall()
        contracts = [c for c in cand if person_key(c) == key]
        person = row["client"]
    dirs = {cid} | {c["contract_id"] for c in contracts if c["contract_id"]}
    return {"cid": cid, "person": person, "contracts": contracts,
            "dirs": sorted(d for d in dirs if d)}


def visits_for_contracts(contract_ids: list[str]) -> list[sqlite3.Row]:
    """Все визиты по набору договоров, новые сверху (без LIMIT — visit_history
    режет до 20, а якорь фото ищется по всей истории). Идёт по idx_visits_contract."""
    ids = [c for c in contract_ids if c]
    if not ids:
        return []
    out = []
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        qs = ",".join("?" for _ in chunk)
        with connect() as conn:
            out += conn.execute(
                f"SELECT * FROM visits WHERE contract_id IN ({qs}) "
                "ORDER BY COALESCE(created_at,'') DESC, id DESC",
                chunk).fetchall()
    return out


def add_track_points(session_id: str, employee: str, telegram_id: int,
                     contract_id: str | None, points: list[dict]) -> int:
    """Дописать точки трека. Возвращает число ДЕЙСТВИТЕЛЬНО новых строк.

    Точки с битыми координатами молча отбрасываются: телефон в поле присылает
    и NaN, и нули, и «точность 5 км» — падать из-за этого посреди поездки нельзя.
    Дубли по (session_id, ts) игнорируются, поэтому повторная отправка одной и
    той же пачки из оффлайн-очереди безопасна."""
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for p in points or []:
        try:
            lat, lon = float(p.get("lat")), float(p.get("lon"))
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            continue
        ts = str(p.get("ts") or "").strip()
        if not ts:
            continue

        def _f(key):
            try:
                return float(p.get(key))
            except (TypeError, ValueError):
                return None

        acc = _f("acc")
        rows.append((session_id, employee, telegram_id, contract_id, ts, lat, lon,
                     int(acc) if acc is not None else None,
                     _f("speed"), _f("heading"), now))
    if not rows:
        return 0
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT INTO track_points (session_id, employee, telegram_id, contract_id, "
            "ts, lat, lon, acc_m, speed, heading, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(session_id, ts) DO NOTHING", rows)
        conn.commit()
        return conn.total_changes - before


_TRAIN_COLS = ("promise_src",
               "debt", "product_type", "partner", "country", "assign_date", "kd_date",
               "promise_date", "result", "contact_with", "who", "location",
               "visits", "paid_months", "target")


def contract_key(contract_id: str | None) -> str:
    """Хэш номера договора. Нужен только для дедупликации внутри снимка —
    самого номера в корпусе обучения не храним."""
    return hashlib.sha256(str(contract_id or "").strip().encode()).hexdigest()[:24]


def add_training_rows(snapshot: str, rows: list[dict]) -> int:
    """Загрузить снимок общего реестра в корпус обучения. Возвращает число новых строк.

    Повторная загрузка того же снимка ничего не задваивает (UNIQUE snapshot+договор),
    поэтому прерванную загрузку можно просто повторить."""
    now = datetime.now().isoformat(timespec="seconds")
    payload = []
    for r in rows or []:
        key = r.get("contract_key") or contract_key(r.get("contract_id"))
        if not key:
            continue
        payload.append((snapshot, key,
                        *[r.get(c) for c in _TRAIN_COLS], now))
    if not payload:
        return 0
    cols = ", ".join(_TRAIN_COLS)
    marks = ", ".join("?" * (len(_TRAIN_COLS) + 3))
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(
            f"INSERT INTO training_rows (snapshot, contract_key, {cols}, created_at) "
            f"VALUES ({marks}) ON CONFLICT(snapshot, contract_key) DO NOTHING", payload)
        conn.commit()
        return conn.total_changes - before


def training_rows(snapshot: str | None = None) -> list[sqlite3.Row]:
    """Корпус для обучения. Без снимка — последний загруженный."""
    with connect() as conn:
        if snapshot is None:
            got = conn.execute(
                "SELECT snapshot FROM training_rows ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not got:
                return []
            snapshot = got[0]
        return conn.execute(
            "SELECT * FROM training_rows WHERE snapshot = ?", (snapshot,)).fetchall()


def training_snapshots() -> list[sqlite3.Row]:
    """Какие снимки реестра загружены: сколько строк, когда, сколько с оплатой."""
    with connect() as conn:
        return conn.execute(
            "SELECT snapshot, COUNT(*) AS n, "
            "SUM(CASE WHEN target > 0 THEN 1 ELSE 0 END) AS paid, "
            "SUM(target) AS collected, MIN(created_at) AS loaded_at "
            "FROM training_rows GROUP BY snapshot ORDER BY loaded_at DESC").fetchall()


def track_sessions(employee: str | None = None, limit: int = 200) -> list[sqlite3.Row]:
    """Сводка по поездкам: точек, начало/конец, к кому ехали. Для пульта и анализа."""
    sql = ("SELECT session_id, employee, contract_id, COUNT(*) AS n, "
           "MIN(ts) AS started, MAX(ts) AS finished FROM track_points ")
    args: list = []
    if employee:
        sql += "WHERE employee = ? "
        args.append(employee)
    sql += "GROUP BY session_id ORDER BY started DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return conn.execute(sql, args).fetchall()


def track_points(session_id: str) -> list[sqlite3.Row]:
    """Точки одной поездки по возрастанию времени."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM track_points WHERE session_id = ? ORDER BY ts", (session_id,)
        ).fetchall()


def photo_geo_for_files(files: list[str]) -> dict[str, sqlite3.Row]:
    """{«КД/имя файла»: строка гео} для набора фото (отчёт в беседу, экспорт)."""
    if not files:
        return {}
    qs = ",".join("?" for _ in files)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM photo_geo WHERE file IN ({qs})", list(files)).fetchall()
    return {r["file"]: r for r in rows}


# --- Беседы для отчётов о выездах (опционально, настраивается пультом) --------
def upsert_report_chat(chat_id: int, title: str) -> None:
    """Бот в беседе → она появляется в списке пульта (выключенной). Сотрудники
    при повторном добавлении сохраняются, но ВКЛЮЧЁННОСТЬ после выгона бота не
    воскресает: кик — способ группы отписаться от отчётов, и любой участник,
    вернувший бота, не должен молча возобновить посты с ПД. Включение — только
    пульт."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO report_chats (chat_id, title, enabled, removed, added_at) "
            "VALUES (?,?,0,0,?) ON CONFLICT(chat_id) DO UPDATE SET "
            "title=excluded.title, "
            "enabled=CASE WHEN report_chats.removed=1 THEN 0 "
            "ELSE report_chats.enabled END, removed=0",
            (chat_id, title, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
    reset_role_caches()


def remove_report_chat(chat_id: int) -> None:
    """Бота выгнали из беседы: помечаем, настройки и сотрудников не теряем."""
    with connect() as conn:
        conn.execute("UPDATE report_chats SET removed=1 WHERE chat_id=?", (chat_id,))
        conn.commit()
    reset_role_caches()


def list_report_chats() -> list[dict]:
    with connect() as conn:
        chats = conn.execute(
            "SELECT * FROM report_chats ORDER BY added_at DESC, chat_id").fetchall()
        emps: dict[int, list[str]] = {}
        for r in conn.execute("SELECT chat_id, employee FROM report_chat_employees"):
            emps.setdefault(r["chat_id"], []).append(r["employee"])
    return [{"chat_id": r["chat_id"], "title": r["title"] or "",
             "enabled": bool(r["enabled"]), "removed": bool(r["removed"]),
             "added_at": r["added_at"] or "",
             "direction": (r["direction"] if "direction" in r.keys() else "") or "",
             "employees": sorted(emps.get(r["chat_id"], []))} for r in chats]


def set_report_chat(chat_id: int, enabled: bool | None = None,
                    employees: list[str] | None = None,
                    direction: str | None = None) -> None:
    """Настройка из пульта: включённость, направление и/или список ФИО
    (None = не трогать; пустая строка в direction снимает направление).

    Направление и поимённый список складываются: беседа отдела по республике
    плюс пара приглашённых из соседней."""
    with connect() as conn:
        if enabled is not None:
            conn.execute("UPDATE report_chats SET enabled=? WHERE chat_id=?",
                         (1 if enabled else 0, chat_id))
        if direction is not None:
            code = (direction or "").strip()
            conn.execute("UPDATE report_chats SET direction=? WHERE chat_id=?",
                         (code if code in DIRECTIONS else None, chat_id))
        if employees is not None:
            conn.execute("DELETE FROM report_chat_employees WHERE chat_id=?",
                         (chat_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO report_chat_employees (chat_id, employee) "
                "VALUES (?,?)",
                [(chat_id, e.strip()) for e in employees if e and e.strip()])
        conn.commit()
    reset_role_caches()   # индекс бесед живёт в том же кэше, что и роли


def migrate_report_chat(old_id: int, new_id: int) -> None:
    """Группа стала супергруппой — Telegram сменил chat_id. Переносим настройку
    и сотрудников на новый id, чтобы отчёты не умерли молча."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM report_chats WHERE chat_id=?",
                           (old_id,)).fetchone()
        if row is None:
            return
        conn.execute(
            "INSERT INTO report_chats (chat_id, title, enabled, removed, added_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET "
            "title=excluded.title, enabled=excluded.enabled, removed=0",
            (new_id, row["title"], row["enabled"], row["removed"], row["added_at"]))
        conn.execute("DELETE FROM report_chats WHERE chat_id=?", (old_id,))
        for r in conn.execute("SELECT employee FROM report_chat_employees "
                              "WHERE chat_id=?", (old_id,)).fetchall():
            conn.execute("INSERT OR IGNORE INTO report_chat_employees "
                         "(chat_id, employee) VALUES (?,?)", (new_id, r["employee"]))
        conn.execute("DELETE FROM report_chat_employees WHERE chat_id=?", (old_id,))
        conn.commit()
    reset_role_caches()


def chats_by_employee_index() -> dict[str, list[int]]:
    """Обратный индекс _name_key(ФИО) → беседы отчётов. Кэш — как у ролей:
    ручка зовётся на КАЖДОМ закрытии визита, а состав направления считается
    DISTINCT-сканом clients."""
    global _chats_index_cache
    now = time.monotonic()
    if _chats_index_cache and now - _chats_index_cache[0] < _ROLE_CACHE_TTL_S:
        return _chats_index_cache[1]
    index: dict[str, list[int]] = {}
    by_direction: dict[str, list[str]] = {}
    with connect() as conn:
        chats = conn.execute(
            "SELECT chat_id, direction FROM report_chats "
            "WHERE enabled=1 AND removed=0").fetchall()
        named = conn.execute(
            "SELECT e.chat_id, e.employee FROM report_chat_employees e "
            "JOIN report_chats c ON c.chat_id = e.chat_id "
            "WHERE c.enabled=1 AND c.removed=0").fetchall()
    for c in chats:
        direction = ((c["direction"] if "direction" in c.keys() else "") or "").strip()
        if not direction:
            continue
        if direction not in by_direction:        # направлений максимум три
            by_direction[direction] = employees_by_direction(direction)
        for spelled in by_direction[direction]:
            key = _name_key(spelled)
            if key:
                index.setdefault(key, []).append(c["chat_id"])
    for r in named:
        key = _name_key(r["employee"])
        if key:
            index.setdefault(key, []).append(r["chat_id"])
    _chats_index_cache = (now, index)
    return index


def report_chats_for(employee: str) -> list[int]:
    """Куда постить отчёт о визите сотрудника: беседа включена, бот в ней есть,
    и сотрудник в неё попадает — по НАПРАВЛЕНИЮ беседы либо поимённо.

    Сравнение по _name_key, а не SQL-равенству: одна опечатка регистра при
    закреплении ФИО в пульте — и отчёты молча не уходили. Расхождение написаний
    при этом делаем ГРОМКИМ (лог INFO), иначе завтрашнее «посыпались лишние
    уведомления» будет невосстановимо."""
    if not (employee or "").strip():
        return []
    key = _name_key(employee)
    chat_ids = chats_by_employee_index().get(key, [])
    if chat_ids:
        canonical = registry_name_index().get(key)
        if canonical and canonical != employee:
            logging.info("report_chats: сматчено по нормализованному ключу — "
                         "реестр «%s», канон «%s» (чаты %s)",
                         employee, canonical, chat_ids)
    return sorted(dict.fromkeys(chat_ids))


# --- Телеметрия доставки (push_log) и производные признаки -------------------
def log_push(kind: str, status: str, *, target_kind: str = "",
             target_id: int | None = None, employee: str | None = None,
             contract_id: str | None = None, error: str | None = None,
             note: str | None = None, attempt: int = 1) -> None:
    """Строка журнала доставки. Телеметрия НЕ имеет права уронить сохранение
    визита — весь вызов проглатывает свои ошибки (как audit_access), но потеря
    строки не немая: logging.warning."""
    try:
        if status not in PUSH_STATUSES:
            logging.warning("log_push: незнакомый статус %r (kind=%s)", status, kind)
        with connect() as conn:
            conn.execute(
                "INSERT INTO push_log (ts, day, kind, target_kind, target_id, "
                "employee, contract_id, status, error, note, attempt) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"),
                 date.today().isoformat(), kind, target_kind or "",
                 target_id, employee, contract_id,
                 status, (error or "")[:300] or None, (note or "")[:300] or None,
                 int(attempt)))
            conn.commit()
    except Exception as exc:                               # noqa: BLE001
        logging.warning("log_push потерян: kind=%s status=%s target=%s err=%s",
                        kind, status, target_id, exc)


def set_can_dm(telegram_id: int, ok: bool) -> None:
    """Обновить признак «личка доступна» по ФАКТУ отправки. rowcount 0
    (владелец без записи в managers) — не ошибка. Сетевой сбой can_dm не
    трогает: он не доказывает отсутствие лички."""
    with connect() as conn:
        conn.execute(
            "UPDATE managers SET can_dm=?, can_dm_at=? WHERE telegram_id=?",
            (1 if ok else 0, datetime.now().isoformat(timespec="seconds"),
             telegram_id))
        conn.commit()


def push_summary(day: str) -> dict:
    """Сводка доставок за день: по видам, итого, заблокированные (с именем
    ПОЛУЧАТЕЛЯ — в строках визит-пуша employee это ФИО выездника, чей визит
    вызвал отбивку, показывать его как заблокированного нельзя), последняя ошибка."""
    with connect() as conn:
        by_kind: dict[str, dict[str, int]] = {}
        for r in conn.execute(
                "SELECT kind, status, COUNT(*) n FROM push_log WHERE day=? "
                "GROUP BY kind, status", (day,)):
            by_kind.setdefault(r["kind"], {})[r["status"]] = r["n"]
        totals: dict[str, int] = {}
        for r in conn.execute(
                "SELECT status, COUNT(*) n FROM push_log WHERE day=? "
                "GROUP BY status", (day,)):
            totals[r["status"]] = r["n"]
        blocked = [dict(r) for r in conn.execute(
            "SELECT DISTINCT p.target_id, m.name AS target_name, p.kind, p.employee "
            "FROM push_log p LEFT JOIN managers m ON m.telegram_id = p.target_id "
            "WHERE p.day=? AND p.status=? AND p.target_id IS NOT NULL",
            (day, PUSH_BLOCKED))]
        last = conn.execute(
            "SELECT ts, kind, target_id, status, error FROM push_log "
            "WHERE day=? AND error IS NOT NULL ORDER BY id DESC LIMIT 1",
            (day,)).fetchone()
    return {"by_kind": by_kind, "totals": totals, "blocked": blocked,
            "last_error": dict(last) if last else None}


def push_tail(limit: int = 20, day: str | None = None,
              kind: str | None = None) -> list[dict]:
    """Последние строки журнала (карточка диагностики в пульте)."""
    q, args = "SELECT * FROM push_log", []
    conds = []
    if day:
        conds.append("day=?")
        args.append(day)
    if kind:
        conds.append("kind=?")
        args.append(kind)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with connect() as conn:
        return [dict(r) for r in conn.execute(q, args)]


def push_settled_today(kind: str, day: str) -> set[int]:
    """Кому за этот день уже не надо слать: доставлено ИЛИ Forbidden (чинит
    человек, ретрай бессмыслен). Пробные отправки (kind=test) сюда не попадают
    по построению — они пишутся своим видом и не могут заглушить рассылку."""
    with connect() as conn:
        return {r["target_id"] for r in conn.execute(
            "SELECT DISTINCT target_id FROM push_log "
            "WHERE day=? AND kind=? AND status IN (?,?) AND target_id IS NOT NULL",
            (day, kind, PUSH_SENT, PUSH_BLOCKED))}


def push_trim(keep_days: int = 60) -> int:
    """Обрезка журнала: телеметрия, не архив. ПД-выдача живёт в audit."""
    edge = (date.today() - timedelta(days=keep_days)).isoformat()
    with connect() as conn:
        cur = conn.execute("DELETE FROM push_log WHERE day < ?", (edge,))
        conn.commit()
        return cur.rowcount


def push_enabled(key: str, default: str = "1") -> bool:
    """ЕДИНЫЙ разбор рубильников рассылок. Раньше digest_enabled понимал
    ('0','false','no','off'), а visit_push — только три из них: visit_push='no'
    молча НЕ выключал пуш. Один парсер — одно поведение."""
    return (get_setting(key) or default).strip().lower() not in ("0", "false", "no", "off")


# --- Кэш ролей: обратный индекс ФИО → руководители ---------------------------
# _notify_managers зовётся на КАЖДОМ закрытии визита; прямой пересчёт состава
# (у направленца — DISTINCT-скан clients) на каждый визит недопустим. Кэш живёт
# 60 с и явно сбрасывается там, где роли/реестр меняются: init_db, set_manager,
# delete_manager, unassign_clients, конец импорта. В _bump_version его НЕ вешать:
# тот дёргается на каждом update_fields, и кэш стал бы бесполезен.
_ROLE_CACHE_TTL_S = 60.0
_registry_names_cache: tuple[float, dict] | None = None
_managers_index_cache: tuple[float, dict] | None = None
_chats_index_cache: tuple[float, dict] | None = None


def reset_role_caches() -> None:
    global _registry_names_cache, _managers_index_cache, _chats_index_cache
    _registry_names_cache = None
    _managers_index_cache = None
    _chats_index_cache = None


def registry_name_index() -> dict[str, str]:
    """{_name_key(ФИО): каноническое написание в реестре} — ТОЛЬКО для
    диагностики расхождений написаний; писать этим в clients.responsible нельзя.

    Правило победителя — «больше договоров, то и написание» (детерминированно).
    ORDER BY responsible здесь не годится: сортировка бинарная, кириллическая
    заглавная младше строчной, и наивный проход по словарю отдал бы каноном
    строчную опечатку с одним договором. От импортного known_resp (первый по
    rowid) отличается намеренно — у них разные задачи."""
    global _registry_names_cache
    now = time.monotonic()
    if _registry_names_cache and now - _registry_names_cache[0] < _ROLE_CACHE_TTL_S:
        return _registry_names_cache[1]
    with connect() as conn:
        rows = conn.execute(
            "SELECT responsible name, COUNT(*) c FROM clients "
            "WHERE responsible IS NOT NULL AND TRIM(responsible) <> '' "
            "GROUP BY responsible ORDER BY c DESC, responsible ASC").fetchall()
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(_name_key(r["name"]), r["name"])
    _registry_names_cache = (now, out)
    return out


def managers_by_employee_index() -> dict[str, list[dict]]:
    """Обратный индекс: _name_key(ФИО сотрудника) → руководители, которым идут
    его уведомления. Учитывает руководителей НАПРАВЛЕНИЙ (их явный список пуст,
    состав живой) — дыра, из-за которой четверо направленцев не получали
    визит-пуш вообще.

    В индекс кладётся КОПИЯ dict на каждый ключ: list_managers отдаёт по одному
    объекту на руководителя, и общий объект под тремя ключами оставил бы
    matched_as от последнего сотрудника — каждый пуш многосоставного
    руководителя врал бы про расхождение написаний. Значения кэша отдаются по
    ссылке — мутировать их нельзя."""
    global _managers_index_cache
    now = time.monotonic()
    if _managers_index_cache and now - _managers_index_cache[0] < _ROLE_CACHE_TTL_S:
        return _managers_index_cache[1]
    index: dict[str, list[dict]] = {}
    by_direction: dict[str, list[str]] = {}
    for mgr in list_managers():
        direction = (mgr.get("direction") or "").strip()
        if direction:
            if direction not in by_direction:    # направлений максимум три
                by_direction[direction] = employees_by_direction(direction)
            spelled_list = by_direction[direction]
        else:
            spelled_list = [e for e in (mgr.get("employees") or [])
                            if str(e).strip()]
            # Тот же принцип, что в manager_employees: иначе руководитель по
            # офису открывал бы портфели своего города, но карточек по визитам
            # не получал — ровно та дыра, что была у направленцев.
            spelled_list += employees_by_cities(mgr.get("office_cities"))
        if not mgr.get("push_visits", True):
            continue          # личный рубильник: карточки визитов ему не нужны
        for spelled in spelled_list:
            key = _name_key(spelled)
            if not key:
                continue
            index.setdefault(key, []).append({**mgr, "matched_as": spelled})
    _managers_index_cache = (now, index)
    return index


def managers_for_employee(employee: str) -> list[dict]:
    """Руководители, которым идут уведомления по этому сотруднику (каждый dict
    несёт matched_as — написание ФИО, как оно лежит у руководителя)."""
    key = _name_key(employee)
    if not key:
        return []
    return managers_by_employee_index().get(key, [])


def manager_spelling_mismatches(mgr: dict) -> list[dict]:
    """Расхождения написаний ФИО между ролью и реестром — для диагностики.
    У таких сотрудников портфель у руководителя не открывается (доступ матчит
    точно), хотя отбивки приходят (доставка матчит по _name_key)."""
    known = registry_name_index()
    exact = set(known.values())
    out = []
    for spelled in (mgr.get("employees") or []):
        s = str(spelled).strip()
        if not s or s in exact:
            continue
        canonical = known.get(_name_key(s))
        if canonical:
            out.append({"в роли": s, "в реестре": canonical})
    return out


# Путь к .env, откуда docker-compose (env_file) и load_dotenv() берут BOT_TOKEN.
# Лежит рядом с кодом; в контейнере — /data не подходит (env_file читает .env билда),
# поэтому пишем в файл рядом со скриптами. HARDBOT_ENV — переопределение пути на всякий.
_ENV_PATH = Path(os.getenv("APP_ENV_FILE") or os.getenv("HARDBOT_ENV") or Path(__file__).parent / ".env")


def set_bot_token(token: str) -> None:
    """Сменить токен бота: обновить строку BOT_TOKEN= в .env (источник для env_file
    docker-compose и load_dotenv при старте процесса) и продублировать в settings как
    аудит-след факта смены (сам токен там тоже хранится — файл .env уже приватный,
    БД лежит рядом; GET-эндпоинты его НЕ отдают). Применяется после рестарта процесса.
    Токен НЕ логируем."""
    token = (token or "").strip()
    # (1) .env — главный источник. Переписываем/добавляем строку BOT_TOKEN=, остальное как есть.
    try:
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines() if _ENV_PATH.exists() else []
    except OSError:
        lines = []
    out, replaced = [], False
    for ln in lines:
        if re.match(r"\s*BOT_TOKEN\s*=", ln) and not ln.lstrip().startswith("#"):
            out.append(f"BOT_TOKEN={token}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.insert(0, f"BOT_TOKEN={token}")
    _ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    # (2) БД — резервная копия/аудит-след. read-only эндпоинтам не отдаётся.
    set_setting("bot_token", token)
    set_setting("bot_token_changed_at", datetime.now().isoformat(timespec="seconds"))


# --- Фильтр по периоду (дата закрепления) ----------------------------------
# Сужает, каких клиентов видят ВЫЕЗДНИКИ (по assign_date). Аналитик в пульте видит всё.
def get_period() -> tuple[str | None, str | None]:
    return get_setting("period_from"), get_setting("period_to")


def set_period(frm: str | None, to: str | None) -> None:
    set_setting("period_from", (frm or "").strip())
    set_setting("period_to", (to or "").strip())


def _period_bounds():
    f, t = get_period()
    return (_as_date(f) if f else None), (_as_date(t) if t else None)


def _date_in(val, lo, hi) -> bool:
    """Попадает ли дата закрепления в окно периода.

    ДАТА НЕ РАСПОЗНАНА — кейс ПОКАЗЫВАЕМ. Раньше было наоборот, и одно нажатие
    «Применить» в панели периода прятало у выездника десятки договоров: в реестре
    дата закрепления бывает пустой или записанной в формате, которого не знает
    `_as_date`. Пульт при этом продолжал показывать полное число договоров, поиск
    по номеру КД переставал находить, и причину было не найти ни на одном экране.
    Показать лишнее — потеря минуты; спрятать нужное — сорванный выезд.
    """
    d = _as_date(val)
    if d is None:
        return True
    if lo and d < lo:
        return False
    if hi and d > hi:
        return False
    return True


# --- Клиенты и люди ---------------------------------------------------------
def get_client(contract_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE contract_id = ?", (contract_id,)
        ).fetchone()


def person_key(row) -> tuple:
    """Ключ человека: (ФИО, дата рождения) — а без даты рождения ещё и адрес.

    ПУСТАЯ ДАТА РОЖДЕНИЯ — не значение, а «неизвестно». Прежний `birth_date or ''`
    превращал её в полноценный ключ, и два РАЗНЫХ должника с одинаковым ФИО
    сливались в одного: в карточке оказывались два адреса и два чужих телефона,
    долг складывался, а закрытие визита по одному договору писало снимок и во
    второй — результат выезда уезжал в чужую строку офисного реестра.

    Но просто разделить всех без ДР нельзя: несколько займов на одного человека —
    норма. На реальном реестре (17 075 строк, 17.7% без ДР) таких групп 331 на
    1 382 договора, и у 320 из них АДРЕС СОВПАДАЕТ — это действительно один
    человек. Групп с расходящимися адресами не нашлось ни одной.

    Поэтому без ДР объединяем только при совпадающем опознавательном признаке:
    адрес, иначе телефон. Нет ни того, ни другого — человек равен договору.
    Ошибка в сторону разделения безопасна (два кейса вместо одного), в сторону
    объединения — нет (смешение ПД и визит не тому должнику)."""
    dob = (row["birth_date"] or "").strip()
    if dob:
        return (row["client"] or "", dob)
    ident = (row["address"] or "").strip().lower() or (row["phone"] or "").strip()
    if ident:
        return (row["client"] or "", "\x00адрес:" + ident)
    return (row["client"] or "", "\x00договор:" + (row["contract_id"] or ""))


def _group_persons(rows: list[sqlite3.Row]) -> list[dict]:
    """Объединить договоры в людей по (ФИО, дата рождения)."""
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault(person_key(r), []).append(r)
    persons = []
    for (name, dob), rs in groups.items():
        # Служебный суффикс ключа (номер договора вместо неизвестной ДР) наружу
        # не отдаём — снаружи это по-прежнему «дата рождения неизвестна».
        if dob.startswith("\x00"):
            dob = ""
        rep = min(rs, key=lambda x: x["contract_id"] or "")["contract_id"]
        persons.append({
            "rep": rep, "client": name, "birth_date": dob,
            "contracts": rs, "n": len(rs),
            "total": sum(debt.contract_debt(x) for x in rs),
            "totals": debt.totals_by_unit(rs),   # раздельно по валютам: сумы ≠ рубли
            "partners": sorted({x["partner"] for x in rs if x["partner"]}),
        })
    persons.sort(key=lambda p: p["client"])
    return persons


def clients_for_employee(employee: str) -> list[sqlite3.Row]:
    lo, hi = _period_bounds()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE responsible = ? ORDER BY client, contract_id",
            (employee,),
        ).fetchall()
    if lo is None and hi is None:
        return rows
    return [r for r in rows if _date_in(r["assign_date"], lo, hi)]   # период по дате закрепления


def persons_for_employee(employee: str) -> list[dict]:
    return _group_persons(clients_for_employee(employee))


def person_contracts(rep_contract_id: str) -> list[sqlite3.Row]:
    """Все договоры человека, к которому относится этот договор.

    Без даты рождения объединять НЕЛЬЗЯ (см. person_key): результат визита
    расходится по всем договорам человека, и при склейке однофамильцев он уехал бы
    в чужую строку реестра. Пустая ДР → человек = ровно этот договор."""
    row = get_client(rep_contract_id)
    if not row:
        return []
    with connect() as conn:
        candidates = conn.execute(
            "SELECT * FROM clients WHERE client IS ? "
            "AND IFNULL(responsible,'') = IFNULL(?, '') ORDER BY contract_id",
            (row["client"], row["responsible"]),
        ).fetchall()
    # Фильтруем ТЕМ ЖЕ ключом, что и группировка: иначе SQL и _group_persons
    # разойдутся, и визит разнесётся по договорам, которых карточка не показывает.
    key = person_key(row)
    return [c for c in candidates if person_key(c) == key]


def search_persons(employee: str, query: str) -> list[dict]:
    like = f"%{query}%"
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE responsible = ? "
            "AND (client LIKE ? OR contract_id LIKE ? OR phone LIKE ?) ORDER BY client",
            (employee, like, like, like),
        ).fetchall()
    lo, hi = _period_bounds()
    if lo or hi:
        rows = [r for r in rows if _date_in(r["assign_date"], lo, hi)]
    return _group_persons(rows)


def plans_for_employee(employee: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE responsible = ? AND reminder_date IS NOT NULL "
            "ORDER BY reminder_date",
            (employee,),
        ).fetchall()


def update_person(rep_contract_id: str, values: dict) -> None:
    """Записать поля во ВСЕ договоры человека (выезд = на человека, не на КД)."""
    for r in person_contracts(rep_contract_id):
        update_fields(r["contract_id"], values)


def record_bot_visit(rep_contract_id: str, values: dict,
                     completed_at: str | None = None) -> int:
    """Зафиксировать доверенный снимок завершённого визита во всех КД человека.

    ``values`` — уже отфильтрованное тело фактического POST /api/save; намеренно
    не дочитываем пропущенные поля из clients, потому что там может лежать старый
    импорт. Возвращает число помеченных договоров.
    """
    completed_at = completed_at or date.today().isoformat()
    snapshot = _bot_snapshot(values, completed_at)
    if snapshot is None:
        return 0
    contracts = person_contracts(rep_contract_id)
    if not contracts:
        return 0
    # Выезд, доехавший из оффлайна ПОЗЖЕ более свежего визита, не имеет права
    # затереть его снимок: иначе вчерашняя доставка «нет контакта» отменила бы
    # сегодняшнее «обещает оплатить». Строку в visits такой выезд пишет всегда —
    # это история, — но текущим снимком человека остаётся самый поздний выезд.
    newest = max((d for r in contracts
                  if (d := _as_date(r["bot_visit_at"])) is not None),
                 default=None)
    if newest is not None and _as_date(completed_at) and newest > _as_date(completed_at):
        return 0
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    now = date.today().isoformat()
    with connect() as conn:
        # office_edit_data=NULL: новый визит перекрывает прежние офисные правки —
        # иначе старый оверлей лёг бы поверх свежего снимка повторного выезда.
        # Журнал НЕ чистим, а дополняем: значения прежнего снимка должны остаться
        # «известными», чтобы файл, обогащённый до повторного выезда, не выдал их
        # импорту за офисные правки.
        for r in contracts:
            log = _log_append(_decode_value_log(r), snapshot)
            if "visit_comment" in snapshot:
                # Прямые in-process вызовы record_bot_visit (миграции/тесты)
                # тоже обязаны синхронизировать живой комментарий. HTTP-путь уже
                # записал его через update_person, повтор здесь идемпотентен.
                conn.execute(
                    "UPDATE clients SET visit_comment=?, bot_visit_at=?, "
                    "bot_visit_data=?, office_edit_data=NULL, visit_value_log=?, "
                    "updated_at=? WHERE contract_id=?",
                    (snapshot["visit_comment"], completed_at, payload,
                     _log_json(log), now, r["contract_id"]),
                )
            else:
                conn.execute(
                    "UPDATE clients SET bot_visit_at=?, bot_visit_data=?, "
                    "office_edit_data=NULL, visit_value_log=?, updated_at=? "
                    "WHERE contract_id=?",
                    (completed_at, payload, _log_json(log), now,
                     r["contract_id"]),
                )
        conn.commit()
    _bump_version()
    return len(contracts)


def has_bot_visit(rep_contract_id: str) -> bool:
    """Есть ли у человека валидный текущий снимок завершённого визита."""
    return any(_decode_bot_snapshot(r) is not None
               for r in person_contracts(rep_contract_id))


def bot_visit_snapshot(row) -> dict | None:
    """Публичное представление подтверждённого визита для чтения:
    неизменяемый снимок + офисные правки + живой комментарий текущего месяца."""
    return _merged_visit(row)


def person_history(rep_contract_id: str) -> list[sqlite3.Row]:
    cids = [r["contract_id"] for r in person_contracts(rep_contract_id)]
    if not cids:
        return []
    qs = ",".join("?" for _ in cids)
    with connect() as conn:
        return conn.execute(
            f"SELECT * FROM visits WHERE contract_id IN ({qs}) "
            f"ORDER BY created_at DESC LIMIT 20", cids,
        ).fetchall()


def due_reminders(on_date: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE reminder_date = ?", (on_date,)
        ).fetchall()


# Окно и источник просроченных обещаний — настройками, без переката кода.
# 10.09.2026 рассылка ушла со 211 позициями, из них 8 просрочены накануне, а 165
# старше двух недель: самому старому обещанию было 100 дней. Читалось это как
# архив, а не как список работы на день. Решение владельца: неделя и только те
# обещания, которые взял ВЫЕЗДНИК на выезде.
PROMISE_MAX_DAYS_DEFAULT = 7
PROMISE_AGENT_ONLY_DEFAULT = "1"


def promise_window() -> tuple[int | None, bool]:
    """(сколько дней просрочки показываем, только ли обещания выездника).
    max_days=None — без ограничения (settings['promise_max_days']=0)."""
    raw = (get_setting("promise_max_days") or "").strip()
    try:
        days = int(raw) if raw else PROMISE_MAX_DAYS_DEFAULT
    except ValueError:
        days = PROMISE_MAX_DAYS_DEFAULT
    agent_only = push_enabled("promise_agent_only", PROMISE_AGENT_ONLY_DEFAULT)
    return (days if days > 0 else None), agent_only


def overdue_promises(employee: str, on_date: str, *,
                     max_days: int | None = None,
                     agent_only: bool = False) -> list[dict]:
    """Люди сотрудника с ПРОСРОЧЕННЫМ обещанием: promise_date (реестр) в прошлом,
    кейс не «Оплачен» и после даты обещания платежа не было (last_payment_date).
    Группировка по людям; в каждом — promise_date (самая ранняя просроченная)
    и days_overdue. Самые «тухлые» сверху.

    max_days — не показывать просрочку старше стольких дней. agent_only —
    только обещания, зафиксированные выездником на выезде (`by_agent`);
    остальные приходят из колонки реестра «дата обещания», её заполняет офис по
    телефону, и к таким должникам выездник чаще всего вообще не приезжал.
    Оба фильтра по умолчанию выключены: вызывающий решает сам (см.
    promise_window)."""
    target = _as_date(on_date)
    if target is None:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE responsible = ? "
            "AND promise_date IS NOT NULL AND TRIM(promise_date) <> ''",
            (employee,),
        ).fetchall()
    bad = []
    for r in rows:
        pd = _as_date(r["promise_date"])
        if pd is None or pd >= target:
            continue                       # обещание ещё не наступило
        if "Оплачен" in ((r["overall_result"] or "") + (r["call_status"] or "")):
            continue                       # уже оплачен
        lp = _as_date(r["last_payment_date"])
        if lp and lp >= pd:
            continue                       # платёж после даты обещания = сдержал
        bad.append(r)
    persons = _group_persons(bad)
    for p in persons:
        pd = min(d for d in (_as_date(x["promise_date"]) for x in p["contracts"]) if d)
        p["promise_date"] = pd.isoformat()
        p["days_overdue"] = (target - pd).days
        # Свежесть: ходил ли к нему выездник ПОСЛЕ того, как он обещал. Без этого
        # признака список выглядит одинаково и для «обещал, съездили, снова тянет»,
        # и для «обещал месяц назад, и с тех пор к нему никто не заходил».
        visits = [_as_date(x["bot_visit_at"]) for x in p["contracts"]]
        last = max([d for d in visits if d], default=None)
        p["last_visit"] = last.isoformat() if last else None
        p["visited_after_promise"] = bool(last and last >= pd)
        # Сумму обещания даёт ТОЛЬКО доказанный снимок визита (в нём же живёт
        # офисная правка этой ячейки). Сырую «Сумму прогноза» из clients брать
        # нельзя: она поконтрактная и до первого визита целиком офисная — ни
        # max, ни сумма по КД там не значат ничего, а 🤝 рядом с офисным
        # прогнозом читался бы как обещание должника. Одно значение на
        # человека, не сумма по договорам. Ключ выдачи остаётся promised_sum:
        # это «сколько обещал должник», а не колонка реестра.
        # Кто взял обещание. Снимок визита — единственное доказательство, что
        # дату назвал должник ВЫЕЗДНИКУ: колонку реестра «дата обещания»
        # заполняет ещё и офис по телефону, и такие обещания выглядели в
        # рассылке точно так же, хотя выездник к человеку не приезжал.
        p["by_agent"] = any(
            _as_date((_merged_visit(x) or {}).get("promise_date")) == pd
            for x in p["contracts"])
        p["promised_sum"] = None
        for x in p["contracts"]:
            snap = _merged_visit(x) or {}
            if not snap.get("forecast_sum"):
                continue
            snap_pd = _as_date(snap.get("promise_date"))
            if snap_pd is not None and snap_pd != pd:
                continue
            money = canon_amount(snap["forecast_sum"])
            if money:
                p["promised_sum"] = int(money)
                break
    if max_days is not None:
        persons = [p for p in persons if p["days_overdue"] <= max_days]
    if agent_only:
        persons = [p for p in persons if p["by_agent"]]
    persons.sort(key=lambda p: -p["days_overdue"])
    return persons


def deferred_promises_count(employee: str, on_date: str) -> int:
    """Должники сотрудника с обещанием В БУДУЩЕМ. Скоринг убирает их из
    маршрута сразу (киллер-фильтр), а видимыми для руководителя они
    становились только после срыва — с момента, когда обещание ставит
    выездник, эта дыра означала «маршрут можно тихо расчистить на месяц»."""
    target = _as_date(on_date)
    if target is None:
        return 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE responsible = ? "
            "AND promise_date IS NOT NULL AND TRIM(promise_date) <> ''",
            (employee,)).fetchall()
    future = [r for r in rows
              if (d := _as_date(r["promise_date"])) is not None and d > target]
    return len(_group_persons(future))


def visit_history(contract_id: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM visits WHERE contract_id = ? ORDER BY created_at DESC LIMIT 20",
            (contract_id,),
        ).fetchall()


# --- Чтение для аналитика/админа (весь реестр) ------------------------------
def all_clients(limit: int = 100) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM clients ORDER BY client LIMIT ?", (limit,)
        ).fetchall()


def search_all(query: str) -> list[sqlite3.Row]:
    like = f"%{query}%"
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE client LIKE ? OR contract_id LIKE ? OR phone LIKE ? "
            "ORDER BY client LIMIT 100",
            (like, like, like),
        ).fetchall()


def stats_all() -> dict:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        visited = conn.execute(
            "SELECT COUNT(*) FROM clients WHERE bot_visit_at IS NOT NULL "
            "AND TRIM(bot_visit_at) <> '' AND bot_visit_data IS NOT NULL"
        ).fetchone()[0]
        by_result = conn.execute(
            "SELECT COALESCE(overall_result,'(нет)') r, COUNT(*) c FROM clients "
            "GROUP BY r ORDER BY c DESC"
        ).fetchall()
    return {
        "total": total, "visited": visited,
        "by_result": [(r["r"], r["c"]) for r in by_result],
    }


def stats_by_country() -> list[dict]:
    """Раздельная статистика Таджикистана/Узбекистана по явным manager scopes.

    ``pool`` на живом реестре — числовой код и страной не является. Поэтому договор
    относится к республике только через ``responsible`` сотрудника, включённого в
    однозначный country-scope руководителя. Нераспределённые и неоднозначные строки
    остаются в общей статистике ``stats_all``, но не угадываются здесь.
    """
    employee_map = country_scope.employee_countries(list_managers())
    with connect() as conn:
        registry_employees = {r["responsible"] for r in conn.execute(
            "SELECT DISTINCT responsible FROM clients "
            "WHERE responsible IS NOT NULL AND TRIM(responsible) <> ''")}
        result = []
        for spec in country_scope.COUNTRIES:
            employees = sorted(emp for emp, code in employee_map.items()
                               if code == spec["code"] and emp in registry_employees)
            if employees:
                marks = ",".join("?" for _ in employees)
                total, visited = conn.execute(
                    f"SELECT COUNT(*), SUM(CASE WHEN bot_visit_at IS NOT NULL "
                    f"AND TRIM(bot_visit_at) <> '' AND bot_visit_data IS NOT NULL "
                    f"THEN 1 ELSE 0 END) FROM clients "
                    f"WHERE responsible IN ({marks})", employees).fetchone()
                by_result = conn.execute(
                    f"SELECT COALESCE(overall_result,'(нет)') r, COUNT(*) c "
                    f"FROM clients WHERE responsible IN ({marks}) "
                    f"GROUP BY r ORDER BY c DESC, r", employees).fetchall()
            else:
                total, visited, by_result = 0, 0, []
            result.append({**country_scope.info(spec["code"]),
                           "clients": total or 0, "visited": visited or 0,
                           "employees": len(employees),
                           "by_result": [(r["r"], r["c"]) for r in by_result]})
    return result


def activity_stats() -> list[dict]:
    """Активность каждого привязанного работника (agent_bind): визиты
    (visits.agent_id) и метки (markers.telegram_id) за 7/30 дней по created_at,
    последняя активность и простой. Пруф: активность пишется в БД, тут только
    агрегируется. photos_total — сумма photos_count по клиентам сотрудника
    (только если он matched в реестре по responsible, иначе None — данных нет).

    Сортировка «проблемные сверху»: сначала вовсе без активности, затем по
    убыванию idle_days (кто дольше простаивает), при равенстве — по имени.
    """
    today = date.today()
    cut7 = (today - timedelta(days=7)).isoformat()    # окна включают границу
    cut30 = (today - timedelta(days=30)).isoformat()
    with connect() as conn:
        vis = {r["agent_id"]: r for r in conn.execute(
            "SELECT agent_id, SUM(created_at >= ?) d7, SUM(created_at >= ?) d30, "
            "MAX(created_at) last FROM visits GROUP BY agent_id", (cut7, cut30))}
        mrk = {r["telegram_id"]: r for r in conn.execute(
            "SELECT telegram_id, SUM(created_at >= ?) d7, SUM(created_at >= ?) d30, "
            "MAX(created_at) last FROM markers GROUP BY telegram_id", (cut7, cut30))}
        photos = {r["responsible"]: r["ph"] for r in conn.execute(
            "SELECT responsible, SUM(COALESCE(photos_count,0)) ph FROM clients "
            "WHERE responsible IS NOT NULL AND TRIM(responsible) <> '' GROUP BY responsible")}
        binds = conn.execute("SELECT * FROM agent_bind ORDER BY employee").fetchall()
        # Чем и когда человек заходил — то, что 08.09.2026 пришлось доставать
        # из базы по SSH, чтобы ответить на «у Хайдарова тормозит телефон».
        today_iso = today.isoformat()
        users = {r["telegram_id"]: r for r in conn.execute(
            "SELECT telegram_id, last_seen, shell_version FROM users")}
        pwa = {r["telegram_id"] for r in conn.execute(
            "SELECT DISTINCT telegram_id FROM app_tokens WHERE expires_at >= ?",
            (datetime.now().isoformat(timespec="seconds"),))}
        vis_today = {r["agent_id"]: r["n"] for r in conn.execute(
            "SELECT agent_id, COUNT(DISTINCT contract_id) n FROM visits "
            "WHERE substr(created_at,1,10)=? GROUP BY agent_id", (today_iso,))}
        photos7 = {r["telegram_id"]: r["n"] for r in conn.execute(
            "SELECT telegram_id, COUNT(*) n FROM photo_geo "
            "WHERE substr(created_at,1,10) >= ? GROUP BY telegram_id", (cut7,))}
        portfolio = {r["responsible"]: (r["contracts"], r["persons"]) for r in conn.execute(
            "SELECT responsible, COUNT(*) contracts, "
            "COUNT(DISTINCT client || '|' || IFNULL(birth_date,'') || '|' || "
            "CASE WHEN IFNULL(birth_date,'')='' THEN IFNULL(address,'') ELSE '' END) persons "
            "FROM clients WHERE responsible IS NOT NULL AND TRIM(responsible) <> '' "
            "GROUP BY responsible")}
    # ФИО, которые реально встречаются в реестре как ответственные. Привязка на
    # имя вне этого списка — осиротевшая: сотрудника переименовали, и портфель
    # обнулился молча.
    known = {r["employee"] for r in employees_in_registry()}
    out = []
    for b in binds:
        v, m = vis.get(b["telegram_id"]), mrk.get(b["telegram_id"])
        # visits.created_at — дата, markers.created_at — datetime: ISO сравнимы лексикографически
        last = max((x["last"] for x in (v, m) if x and x["last"]), default=None)
        d = _as_date((last or "")[:10])
        out.append({
            "employee": b["employee"], "kind": b["kind"] or "agent",
            "telegram_id": b["telegram_id"],
            "visits_7d": (v["d7"] if v else 0) or 0,
            "visits_30d": (v["d30"] if v else 0) or 0,
            "markers_7d": (m["d7"] if m else 0) or 0,
            "markers_30d": (m["d30"] if m else 0) or 0,
            "photos_total": photos.get(b["employee"]),
            "last_activity": last,
            "idle_days": max((today - d).days, 0) if d else None,
            # ФИО привязки не совпадает ни с одним «ответственным» в реестре.
            # Обычно это переименование сотрудника в офисе: портфель обнуляется
            # бесшумно — приложение отвечает 200 и «за вами никто не закреплён»,
            # осиротевшей привязки не видно ни на одном экране. Отдаём признак,
            # чтобы пульт мог показать её человеку.
            "orphaned": b["employee"] not in known,
            # --- вход и портфель (для экрана «Сотрудники» в пульте) ---
            "last_seen": (users[b["telegram_id"]]["last_seen"]
                          if b["telegram_id"] in users else None),
            "shell_version": (users[b["telegram_id"]]["shell_version"]
                              if b["telegram_id"] in users else None),
            # «pwa» — есть живой токен приложения; «telegram» — заходил, но
            # только через Телеграм; «none» — ни разу не открывал.
            "entry": ("pwa" if b["telegram_id"] in pwa
                      else "telegram" if b["telegram_id"] in users
                      and users[b["telegram_id"]]["last_seen"] else "none"),
            "visits_today": vis_today.get(b["telegram_id"], 0),
            "photos_7d": photos7.get(b["telegram_id"], 0),
            "portfolio_contracts": portfolio.get(b["employee"], (0, 0))[0],
            "portfolio_persons": portfolio.get(b["employee"], (0, 0))[1],
        })
    out.sort(key=lambda w: (w["idle_days"] is not None, -(w["idle_days"] or 0), w["employee"]))
    return out


# --- Запись -----------------------------------------------------------------
def update_fields(contract_id: str, values: dict[str, str]) -> None:
    """Перезаписать поля карточки (только существующие ключи)."""
    safe = {k: v for k, v in values.items() if k in BY_KEY or k in dict(BOOKKEEPING)}
    if not safe:
        return
    safe["updated_at"] = date.today().isoformat()
    sets = ", ".join(f'"{k}" = ?' for k in safe)
    with connect() as conn:
        conn.execute(
            f"UPDATE clients SET {sets} WHERE contract_id = ?",
            (*safe.values(), contract_id),
        )
        conn.commit()
    _bump_version()   # данные изменились → кэш приоритета протухает


# --- Устройства пульта -------------------------------------------------------
def _device_token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").strip().encode("utf-8")).hexdigest()


ADMIN_BOOTSTRAP_KEY = "admin_bootstrap_done"


def admin_bootstrap_done() -> bool:
    """Была ли на этом сервере хоть одна привязка устройства.

    Пока не было — мастер-ключ работает как обычный доступ: иначе первый пульт
    неоткуда взять, а данных в свежей базе всё равно нет. Как только устройство
    привязано хоть раз, ключ теряет это право НАВСЕГДА и умеет только привязать
    и отозвать.

    Флаг намеренно односторонний. Если бы он снимался вместе с последним
    устройством, обладатель ключа отозвал бы все устройства и вернул себе полный
    доступ — то есть «один ключ открывает всё» вернулось бы через два шага.

    На УЖЕ РАБОТАЮЩЕМ сервере флага в настройках нет: устройства там привязаны
    давно, а строка появилась только сейчас. Поэтому если записей об устройствах
    хоть одна — считаем бутстрап закрытым и запоминаем это. Без такой сверки
    обновление молча оставило бы боевому серверу прежнее поведение, и вся правка
    подействовала бы только на новые установки.
    """
    if get_setting(ADMIN_BOOTSTRAP_KEY) == "1":
        return True
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM admin_devices LIMIT 1").fetchone()
    if row is None:
        return False
    set_setting(ADMIN_BOOTSTRAP_KEY, "1")
    return True


def enroll_admin_device(device_id: str, name: str = "", role: str = "operator") -> dict:
    """Привязать устройство и выдать ему токен. Токен возвращается ОДИН раз.

    Повторный enroll с той же машины не плодит записи, а отзывает прежний токен и
    выдаёт новый: иначе список устройств зарастал бы дублями после каждой
    переустановки пульта, и «отозвать это устройство» перестало бы что-то значить.
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now().isoformat(timespec="seconds")
    device_id = str(device_id or "").strip()[:128]
    role = _role(role)
    if not device_id:
        raise ValueError("device_id обязателен")
    with connect() as conn:
        # Гасим прежний токен ТОЙ ЖЕ роли с той же машины. Одна машина может быть
        # одновременно и пультом аналитика, и панелью инженера — это разные
        # доступы, и отзывать их надо порознь.
        conn.execute(
            "UPDATE admin_devices SET revoked_at=? "
            "WHERE device_id=? AND role=? AND revoked_at IS NULL",
            (now, device_id, role))
        conn.execute(
            "INSERT INTO admin_devices (token_hash, device_id, name, created_at, role) "
            "VALUES (?,?,?,?,?)",
            (_device_token_hash(token), device_id, str(name or "").strip()[:80], now, role))
        row = conn.execute("SELECT id FROM admin_devices WHERE token_hash=?",
                           (_device_token_hash(token),)).fetchone()
        conn.commit()
    if not admin_bootstrap_done():
        set_setting(ADMIN_BOOTSTRAP_KEY, "1")
        audit_access(0, "admin_bootstrap_closed", str(row["id"]))
    audit_access(0, "device_enroll", str(row["id"]), f"{role}: {name or device_id[:16]}")
    return {"id": row["id"], "token": token, "created_at": now, "role": role}


def admin_device_by_token(token: str, device_id: str | None = None) -> dict | None:
    """Живое устройство по токену; заодно отмечаем, что оно на связи.

    ``device_id`` — отпечаток машины, который пульт присылает КАЖДЫМ запросом.
    Раньше отпечаток проверялся только при выдаче токена, а дальше токен был
    предъявительским: скопировал файл настроек на другую машину — и работает.
    Теперь несовпадение отпечатка означает отказ.

    ``None`` (а не пустая строка) отключает сверку — так ходят внутренние
    вызовы и старые тесты. Веб-слой всегда передаёт строку, поэтому запрос без
    заголовка отпечатка получает пустую строку и не проходит.
    """
    if not str(token or "").strip():
        return None
    digest = _device_token_hash(token)
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        row = conn.execute(
            "SELECT id, device_id, name, created_at, last_seen, role FROM admin_devices "
            "WHERE token_hash=? AND revoked_at IS NULL", (digest,)).fetchone()
        if row is None:
            return None
        if device_id is not None and not hmac.compare_digest(
                str(row["device_id"] or "").encode("utf-8"),
                str(device_id or "").strip()[:128].encode("utf-8")):
            # Сравниваем БАЙТЫ, а не строки: compare_digest на str падает, если
            # в них есть не-ASCII, — и присланный кем угодно заголовок с
            # кириллицей ронял бы ручку пятисоткой вместо честного отказа.
            return None
        conn.execute("UPDATE admin_devices SET last_seen=? WHERE id=?", (now, row["id"]))
        conn.commit()
    return {"id": row["id"], "device_id": row["device_id"], "name": row["name"] or "",
            "created_at": row["created_at"], "last_seen": row["last_seen"] or "",
            "role": row["role"] or "operator"}


def list_admin_devices(include_revoked: bool = False) -> list[dict]:
    where = "" if include_revoked else " WHERE revoked_at IS NULL"
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, device_id, name, created_at, last_seen, revoked_at, role "
            f"FROM admin_devices{where} ORDER BY id DESC").fetchall()
    return [{"id": r["id"], "device_id": (r["device_id"] or "")[:12],
             "name": r["name"] or "", "created_at": r["created_at"] or "",
             "last_seen": r["last_seen"] or "", "revoked_at": r["revoked_at"] or "",
             "role": r["role"] or "operator"}
            for r in rows]


def revoke_admin_device(device_row_id: int) -> bool:
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        changed = conn.execute(
            "UPDATE admin_devices SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (now, int(device_row_id))).rowcount
        conn.commit()
    if changed:
        audit_access(0, "device_revoke", str(device_row_id))
    return bool(changed)


# --- Коды привязки -----------------------------------------------------------
# Смысл: мастер-ключ перестаёт быть тем, чем каждый день привязывают устройства.
# Код рождается на месте у сервера, живёт до первого обмена и гаснет. Утёкший код
# либо уже использован (и бесполезен), либо виден в списке как неиспользованный.

def _enroll_code_hash(code: str) -> str:
    return hashlib.sha256(("enroll:" + str(code or "").strip()).encode("utf-8")).hexdigest()


DEVICE_ROLES = ("operator", "engineer")


def _role(value: str | None) -> str:
    role = str(value or "operator").strip().lower()
    if role not in DEVICE_ROLES:
        raise ValueError(f"роль должна быть одной из {', '.join(DEVICE_ROLES)}")
    return role


def issue_enroll_code(name: str = "", role: str = "operator") -> dict:
    """Выдать одноразовый код привязки. Сам код возвращается ОДИН раз.

    Роль вписана в КОД, а не выбирается при обмене: иначе тот, кому выдали код
    для пульта, привязал бы себе панель обслуживания сервера.
    """
    role = _role(role)
    code = secrets.token_urlsafe(24)
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO admin_enroll_codes (code_hash, name, created_at, role) "
            "VALUES (?,?,?,?)",
            (_enroll_code_hash(code), str(name or "").strip()[:80], now, role))
        row = conn.execute("SELECT id FROM admin_enroll_codes WHERE code_hash=?",
                           (_enroll_code_hash(code),)).fetchone()
        conn.commit()
    audit_access(0, "enroll_code_issued", str(row["id"]), f"{role}: {str(name or '')[:60]}")
    return {"id": row["id"], "code": code, "created_at": now, "role": role}


def redeem_enroll_code(code: str, device_id: str, name: str = "") -> dict:
    """Обменять код на токен устройства. Код гаснет в той же транзакции.

    Гашение и выдача идут вместе намеренно: иначе два одновременных обмена
    одного кода дали бы два действующих токена, и «одноразовый» перестало бы
    что-либо значить.
    """
    device_id = str(device_id or "").strip()[:128]
    if not device_id:
        raise ValueError("не передан отпечаток устройства")
    digest = _enroll_code_hash(code)
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        row = conn.execute(
            "SELECT id, name, used_at, role FROM admin_enroll_codes WHERE code_hash=?",
            (digest,)).fetchone()
        if row is None:
            raise ValueError("код не найден")
        if row["used_at"]:
            raise ValueError("код уже использован")
        changed = conn.execute(
            "UPDATE admin_enroll_codes SET used_at=?, used_by_device=? "
            "WHERE id=? AND used_at IS NULL",
            (now, device_id, row["id"])).rowcount
        if not changed:
            raise ValueError("код уже использован")
        conn.commit()
        code_name = row["name"] or ""
        code_role = row["role"] or "operator"
    made = enroll_admin_device(device_id, str(name or "").strip() or code_name, code_role)
    audit_access(0, "enroll_code_redeemed", str(row["id"]), device_id[:16])
    return made


def list_enroll_codes(include_used: bool = False) -> list[dict]:
    where = "" if include_used else " WHERE used_at IS NULL"
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at, used_at, used_by_device, role "
            f"FROM admin_enroll_codes{where} ORDER BY id DESC").fetchall()
    return [{"id": r["id"], "name": r["name"] or "", "created_at": r["created_at"] or "",
             "used_at": r["used_at"] or "",
             "used_by_device": (r["used_by_device"] or "")[:12],
             "role": r["role"] or "operator"}
            for r in rows]


def audit_access(actor: int, action: str, target: str | None = None,
                 detail: str | None = None) -> None:
    """Записать факт доступа к ПД (кто-что-когда). Append-only, не валит основной поток."""
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO audit (ts, actor, action, target, detail) VALUES (?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), actor, action, target, detail),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 — аудит не должен ломать работу выездника
        pass


def audit_tail(limit: int = 200) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def log_visit(contract_id: str, agent_id: int, payload: str,
              op_id: str | None = None, lat: float | None = None,
              lon: float | None = None, dist_m: int | None = None,
              geo_far: bool | None = None, on_date: str | None = None,
              fill_seconds: int | None = None, saved_at: str | None = None) -> bool:
    """Записать выезд в историю. op_id — идемпотентный ключ (uuid от клиента): повторная
    доставка того же выезда из оффлайн-очереди НЕ создаёт дубль. Возвращает True, если
    строка реально добавлена (значит, можно слать уведомление в группу); False — если это
    повтор уже сохранённого выезда. Без op_id (старый путь) — всегда True (новая строка).
    lat/lon/dist_m — гео-контроль: точка закрытия и дистанция до точки должника.
    geo_far — вердикт «далеко» с учётом допуска по точности адреса; None — не судили.
    on_date — ДЕНЬ ВЫЕЗДА (ISO). Выезд, отработанный без сети, доезжает до сервера
    позже — иногда через двое суток, — и днём досыла он ложился в чужой день:
    сотрудник из Худжанда откатал адреса 8.09, а в системе они появились 10.09."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO visits (contract_id, agent_id, created_at, payload, "
            "op_id, visit_lat, visit_lon, dist_m, geo_far, received_at, "
            "fill_seconds, saved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (contract_id, agent_id, on_date or date.today().isoformat(), payload, op_id,
             lat, lon, dist_m, None if geo_far is None else int(bool(geo_far)),
             datetime.now().isoformat(timespec="seconds"), fill_seconds,
             saved_at),
        )
        conn.commit()
        return cur.rowcount > 0


def same_visit_logged_today(contract_id: str, agent_id: int, payload: str,
                            on_date: str | None = None) -> bool:
    """Был ли СЕГОДНЯ записан ТОТ ЖЕ выезд: тот же выездник и тот же результат.

    Нужно для дедупа УВЕДОМЛЕНИЙ, а не записи. op_id ловит только повтор из оффлайн-очереди
    (тот же ключ), а повторное нажатие «Сохранить выезд» рождает НОВЫЙ op_id — и раньше это
    было ещё одно уведомление руководителю по тому же должнику. В живой базе так набралось
    149 лишних записей из 516, рекорд — 34 отбивки по одному договору за день.

    Сравниваем именно СОДЕРЖИМОЕ: дедуп «была ли сегодня хоть одна строка» глушил и
    второй настоящий выезд к тому же должнику — утром «нет контакта», вечером «обещает
    оплатить», и об этом не узнавал никто, потому что в беседу отчётов повторов не бывает.

    Сброс визитов из пульта не должен воскрешать дедуп: строки visits переживают reset
    (на них держатся карта дня и активность), поэтому смотрим только на записи, сделанные
    ПОСЛЕ последнего сброса — водяной знак по id, а не по дате, у которой суточная
    гранулярность."""
    on_date = on_date or date.today().isoformat()
    try:
        floor = int(get_setting("visits_reset_id") or 0)
    except (TypeError, ValueError):
        floor = 0
    with connect() as conn:
        row = conn.execute(
            "SELECT agent_id, payload FROM visits WHERE contract_id = ? AND created_at = ? "
            "AND id > ? ORDER BY id DESC LIMIT 1",
            (contract_id, on_date, floor)).fetchone()
    return bool(row) and row["agent_id"] == agent_id and (row["payload"] or "") == (payload or "")


def day_map_points(employee: str, on_date: str) -> dict:
    """Точки «Карты дня» руководителя: визиты (точка закрытия из гео-контроля),
    метки выездника за день и адреса обработанных должников (geo клиента)."""
    with connect() as conn:
        visits = conn.execute(
            "SELECT v.visit_lat lat, v.visit_lon lon, v.dist_m dist_m, "
            "v.geo_far geo_far, v.agent_id agent_id, c.client client "
            "FROM visits v JOIN clients c ON c.contract_id = v.contract_id "
            "WHERE c.responsible = ? AND v.created_at = ? AND v.visit_lat IS NOT NULL "
            "ORDER BY v.id", (employee, on_date)).fetchall()
        tids = [r["telegram_id"] for r in conn.execute(
            "SELECT telegram_id FROM agent_bind WHERE employee = ?", (employee,))]
        markers = []
        if tids:
            qs = ",".join("?" for _ in tids)
            markers = conn.execute(
                f"SELECT lat, lon, label, created_at FROM markers "
                f"WHERE telegram_id IN ({qs}) AND created_at LIKE ? ORDER BY id",
                (*tids, on_date + "%")).fetchall()
    targets = []
    for p in persons_processed(employee, on_date):
        c = next((x for x in p["contracts"]
                  if x["geo_lat"] is not None and x["geo_lon"] is not None), None)
        if c:
            targets.append({"lat": c["geo_lat"], "lon": c["geo_lon"],
                            "client": p["client"], "src": c["geo_src"] or ""})
    tid_set = set(tids)
    return {
        # geo_far — СОХРАНЁННЫЙ вердикт (единый с пушем и карточкой); by_other —
        # визит закрыт чужим аккаунтом (руководитель через act_as): судить
        # сотрудника по точке чужого телефона нельзя.
        "visits": [{"lat": r["lat"], "lon": r["lon"], "dist_m": r["dist_m"],
                    "geo_far": r["geo_far"],
                    "by_other": bool(tid_set) and r["agent_id"] not in tid_set,
                    "client": r["client"]} for r in visits],
        "markers": [{"lat": r["lat"], "lon": r["lon"], "label": r["label"] or "",
                     "at": (r["created_at"] or "")[11:16]} for r in markers],
        "targets": targets,
    }


def agent_tids(employee: str) -> list[int]:
    """Все telegram-привязки этого ФИО (у людей бывает два аккаунта).
    Нужно карточке дайджеста: визит, закрытый ЧУЖИМ аккаунтом (руководитель
    через act_as), нельзя судить как гео-нарушение сотрудника."""
    if not (employee or "").strip():
        return []
    with connect() as conn:
        return [r["telegram_id"] for r in conn.execute(
            "SELECT telegram_id FROM agent_bind WHERE employee = ?", (employee,))]


def last_visit_row(contract_ids: list[str]):
    """Последний визит по любому из договоров человека — вся строка.

    last_visit_geo ниже берёт последний визит С ДИСТАНЦИЕЙ (ему нужна гео), а
    карточке нужно время на карточке и у визита без точки телефона.
    """
    ids = [c for c in contract_ids if c]
    if not ids:
        return None
    qs = ",".join("?" for _ in ids)
    with connect() as conn:
        return conn.execute(
            f"SELECT * FROM visits WHERE contract_id IN ({qs}) "
            "ORDER BY id DESC LIMIT 1", tuple(ids)).fetchone()


def last_visit_geo(contract_ids: list[str]):
    """Последний визит с гео-дистанцией по любому из договоров человека
    (для карточки дайджеста: «закрыт в X м от адреса»)."""
    ids = [c for c in contract_ids if c]
    if not ids:
        return None
    qs = ",".join("?" for _ in ids)
    with connect() as conn:
        return conn.execute(
            f"SELECT * FROM visits WHERE contract_id IN ({qs}) AND dist_m IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", tuple(ids)).fetchone()


# Собственная погрешность точки должника — по уровню, до которого разобран адрес.
# Один порог на всех врал: «улица» в нашем справочнике собрана усреднением
# сегментов по километровой сетке, то есть это середина улицы, а не дом. 03.08.2026
# на боевых визитах это дало 6 предупреждений «далеко от адреса» из 7, причём у
# четырёх РАЗНЫХ договоров дистанция совпала до метра (4452 м) — верный признак,
# что мерили не от дома, а от одной общей точки на всю улицу.
#
# None = «точка не адресная, мерить нечего». Молчание здесь честнее числа.
_GEO_TOLERANCE_M = {
    "manual": 150,      # метку поставил выездник у двери — это самое точное, что есть
    "exact": 300,       # дом от точного геокодера
    "house": 300,
    "street": 700,      # сетка справочника 110 м + длина отрезка улицы
    # Махалля, кишлак и джамоат — это НЕ «нет адреса». На них заканчивается
    # большинство узбекских и таджикских адресов: дальше в источнике ничего нет.
    # Их 1293 из 4105 — втрое больше, чем улиц. Пока они лежали в «грубых»,
    # гео-контроль по самой массовой группе молчал вообще.
    "mahalla": 800,     # городской квартал
    "village": 1200,    # кишлак
    "jamoat": 3000,     # сельский совет, объединяет несколько кишлаков
    # Город, район и область не судим намеренно: там точка — административный
    # центр, и расстояние до неё не проверяет ничего.
}


def geo_tolerance_m(precision: str | None, src: str | None = None) -> int | None:
    """Допуск для «далеко от адреса» или None, если судить не по чему."""
    if (src or "").strip().lower() == "manual":
        return _GEO_TOLERANCE_M["manual"]
    return _GEO_TOLERANCE_M.get((precision or "").strip().lower())


PROMISE_AHEAD_DEFAULT = 3


def promise_ahead_days() -> int:
    """На сколько дней ВПЕРЁД выездник может поставить обещание оплаты.

    Решение офиса (Худжанд, 11.09.2026): «не более чем на три дня + к дню
    выезда». Смысл — в контроле: обещание «через месяц» нельзя ни проверить на
    этой неделе, ни вернуть должника в маршрут вовремя. Ноль отключает предел —
    это настройка офиса, а не константа кода.

    НЕ ПУТАТЬ с `promise_window()`: там окно ПОКАЗА уже просроченных обещаний
    («не старше недели»), и ключ настройки у него свой — promise_max_days.
    Имена однажды совпали, и рассылка руководителю молча похудела до трёх дней.
    """
    raw = str(get_setting("promise_ahead_days") or "").strip()
    if not raw:
        return PROMISE_AHEAD_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return PROMISE_AHEAD_DEFAULT
    return value if 0 <= value <= 365 else PROMISE_AHEAD_DEFAULT


def visit_plan_map() -> dict[str, int]:
    """{ФИО: личная норма выездов в день}. Пусто — у всех общая норма.

    Офис делит выездников на группы по фактической выработке — 8, 10 и 12
    выездов в операционный день (решение рабочей встречи 11.09.2026). Норма
    живёт рядом с человеком, а не в коде: состав групп меняет офис.
    """
    import json as _json
    raw = (get_setting("visit_plan_by_employee") or "").strip()
    if not raw:
        return {}
    try:
        data = _json.loads(raw)
    except ValueError:
        return {}
    out: dict[str, int] = {}
    if isinstance(data, dict):
        for employee, norm in data.items():
            try:
                value = int(norm)
            except (TypeError, ValueError):
                continue
            if 0 < value <= 100 and str(employee).strip():
                out[str(employee).strip()] = value
    return out


def visit_plan_for(employee: str, plan_map: dict[str, int] | None = None) -> int:
    """Норма конкретного сотрудника: личная, иначе общая."""
    plan_map = visit_plan_map() if plan_map is None else plan_map
    return plan_map.get((employee or "").strip()) or visit_plan_per_day()


VISIT_PLAN_DEFAULT = 10


def visit_plan_per_day() -> int:
    """Сколько выездов в день офис ждёт от одного сотрудника.

    Норму ставит офис («план 10 на сотрудника»), и меняться она будет без нас —
    поэтому настройка, а не константа в коде. Мусор и ноль трактуем как
    «плана нет по умолчанию» и возвращаем базовые 10: сводка без плана снова
    заставила бы считать процент проработки на бумаге.
    """
    try:
        value = int(str(get_setting("visit_plan_per_day") or "").strip() or 0)
    except (TypeError, ValueError):
        return VISIT_PLAN_DEFAULT
    return value if 0 < value <= 1000 else VISIT_PLAN_DEFAULT


def geo_alert_m() -> int:
    """Порог «далеко от адреса» в метрах (settings.geo_alert_m, дефолт 1000).

    Остался для точек без известной точности (старые записи) и как настройка.
    Новый путь считает допуск по уровню разбора адреса — см. geo_tolerance_m."""
    try:
        return int(get_setting("geo_alert_m") or 1000)
    except (TypeError, ValueError):
        return 1000


def set_reminder(contract_id: str, when: str) -> None:
    update_fields(contract_id, {"reminder_date": when})


def add_photo(contract_id: str) -> int:
    with connect() as conn:
        conn.execute(
            "UPDATE clients SET photos_count = COALESCE(photos_count,0)+1 "
            "WHERE contract_id = ?",
            (contract_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT photos_count FROM clients WHERE contract_id = ?", (contract_id,)
        ).fetchone()
    return row["photos_count"] if row else 0


def ensure_photos_count(contract_id: str, permanent_files: int) -> int:
    """Поднять счётчик минимум до числа уже опубликованных файлов.

    Файл и SQLite не могут быть одной транзакцией. Поэтому /api/photo вызывает
    эту операцию и после первой публикации, и после duplicate-retry: если процесс
    упал между hard-link и UPDATE, повтор того же op_id ремонтирует счётчик.
    MAX защищает от гонки двух параллельных загрузок и не уменьшает историческое
    значение при внешнем удалении/архивации файлов.
    """
    n = max(0, int(permanent_files))
    with connect() as conn:
        conn.execute(
            "UPDATE clients SET photos_count = "
            "MAX(COALESCE(photos_count,0), ?) WHERE contract_id = ?",
            (n, contract_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT photos_count FROM clients WHERE contract_id = ?", (contract_id,)
        ).fetchone()
    return (row["photos_count"] or 0) if row else 0


def photos_count(contract_id: str) -> int:
    """Текущее число фото у договора (для идемпотентной загрузки: повтор из очереди
    отдаёт актуальный счётчик, ничего не инкрементируя)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT photos_count FROM clients WHERE contract_id = ?", (contract_id,)
        ).fetchone()
    return (row["photos_count"] or 0) if row else 0


def set_geo(contract_id: str, lat: float, lon: float) -> None:
    update_fields(contract_id, {"geo_lat": lat, "geo_lon": lon})


# --- Заметки выездника «для себя» -------------------------------------------
NOTE_MAX_LEN = 4000


def add_note(telegram_id: int, text: str, employee: str | None = None,
             client_rep: str | None = None, op_id: str | None = None) -> int:
    """Сохранить заметку. op_id идемпотентен так же, как у визитов и меток:
    оффлайн-очередь досылает одно и то же по нескольку раз, и дубль здесь выглядел
    бы как «приложение размножает мои записи»."""
    text = (text or "").strip()[:NOTE_MAX_LEN]
    if not text:
        return 0
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO notes "
            "(telegram_id, employee, client_rep, text, created_at, op_id) "
            "VALUES (?,?,?,?,?,?)",
            (telegram_id, employee, client_rep, text,
             datetime.now().isoformat(timespec="seconds"), op_id),
        )
        conn.commit()
        if cur.rowcount > 0:
            return cur.lastrowid
        row = conn.execute("SELECT id FROM notes WHERE op_id = ?", (op_id,)).fetchone()
        return row["id"] if row else 0        # уже была (в т.ч. удалённая) — не воскрешаем


def notes_for(telegram_id: int, limit: int = 300) -> list[sqlite3.Row]:
    """Заметки одного человека, свежие сверху. Чужие не отдаём: это личные записи."""
    with connect() as conn:
        return conn.execute(
            "SELECT id, client_rep, text, created_at FROM notes "
            "WHERE telegram_id = ? AND deleted_at IS NULL ORDER BY id DESC LIMIT ?",
            (telegram_id, limit)).fetchall()


def delete_note(telegram_id: int, note_id: int) -> bool:
    """Удалить свою заметку. Чужую — нельзя: telegram_id в условии обязателен.

    Не стираем строку, а гасим её: op_id обязан остаться занятым. Иначе досылка
    из оффлайн-очереди по тому же op_id создаст запись заново, и удалённая
    заметка воскреснет — при живой сети этого не видно, а после дня без связи
    видно сразу.
    """
    with connect() as conn:
        cur = conn.execute(
            "UPDATE notes SET text = '', deleted_at = ? "
            "WHERE id = ? AND telegram_id = ? AND deleted_at IS NULL",
            (datetime.now().isoformat(timespec="seconds"), note_id, telegram_id))
        conn.commit()
        return cur.rowcount > 0


# --- Метки (разведочные геокоординаты) -------------------------------------
def add_marker(telegram_id: int, kind: str, lat: float, lon: float,
               client_rep: str | None = None, label: str | None = None,
               op_id: str | None = None) -> int:
    """Сохранить метку. op_id — идемпотентный ключ: повтор из оффлайн-очереди не плодит
    дубль (вернём id уже сохранённой метки). Если задан client_rep — также проставить точку
    человеку (geo_src=manual), но только при реальной вставке."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO markers "
            "(telegram_id, kind, client_rep, lat, lon, label, created_at, op_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (telegram_id, kind, client_rep, lat, lon, label,
             datetime.now().isoformat(timespec="seconds"), op_id),
        )
        conn.commit()
        inserted = cur.rowcount > 0
        if inserted:
            mid = cur.lastrowid
        else:   # повтор по op_id — вернуть id уже сохранённой метки
            row = conn.execute("SELECT id FROM markers WHERE op_id = ?", (op_id,)).fetchone()
            mid = row["id"] if row else cur.lastrowid
    if client_rep and inserted:
        update_person(client_rep, {"geo_lat": lat, "geo_lon": lon, "geo_src": "manual"})
    return mid


def list_markers(limit: int = 100000) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM markers ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


def markers_for_telegram(telegram_id: int, limit: int = 500) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM markers WHERE telegram_id = ? ORDER BY created_at DESC LIMIT ?",
            (telegram_id, limit),
        ).fetchall()


def markers_rows() -> list[dict]:
    """Метки с подтянутым клиентом/адресом — общий источник для xlsx-легаси
    и JSON-выдачи пульту (пульт пишет xlsx сам, rust_xlsxwriter)."""
    out = []
    for m in list_markers():
        client = addr = ""
        if m["client_rep"]:
            row = get_client(m["client_rep"])
            if row:
                client, addr = row["client"] or "", row["address"] or ""
        out.append({"created_at": m["created_at"], "kind": m["kind"],
                    "telegram_id": m["telegram_id"], "rep": m["client_rep"] or "",
                    "client": client, "address": addr, "label": m["label"] or "",
                    "lat": m["lat"], "lon": m["lon"]})
    return out


def export_markers(path: str) -> str:
    """Выгрузка меток в xlsx (легаси-путь: openpyxl на сервере; пульт делает то же сам)."""
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Метки"
    ws.append(["Дата", "Тип", "telegram_id", "Кейс (rep)", "Клиент", "Адрес",
               "Метка/подпись", "Широта", "Долгота"])
    for m in markers_rows():
        ws.append([m["created_at"], m["kind"], m["telegram_id"], m["rep"],
                   m["client"], m["address"], m["label"], m["lat"], m["lon"]])
    wb.save(path)
    return path


# --- Демо-данные (чтобы было что покрутить сразу) ---------------------------
def seed_demo(responsible: str | None = None) -> None:
    """Пара тестовых должников из примера GoldenCrown. responsible — закрепить за
    сотрудником (назначение по новой модели: responsible + agent_bind)."""
    demo = [
        {
            "partner": "ЗК", "pool": "Узбекистан", "contract_id": "ДГ-1234",
            "client": "Абдуллаев Абдулла", "phone": "998955555555",
            "phone_main": "998946965686", "city": "Ташкент",
            "address": "г. Ташкент, ул. Навои 15 кв. 66",
            "debt_total": "10 000 000", "debt_today": "10 000 000",
            "assign_date": "2026-06-12", "overall_result": "Не обработан",
            "responsible": responsible,
        },
        {
            "partner": "Делимобиль", "pool": "Москва", "contract_id": "DL-77013",
            "client": "Иванов Пётр Сергеевич", "phone": "79161234567",
            "city": "Москва", "address": "г. Москва, Ленинский пр-т 10, кв. 5",
            "dtp": "85 000", "rent": "12 400", "camera_fine": "3 500",
            "debt_total": "100 900", "debt_today": "100 900",
            "assign_date": "2026-06-10", "overall_result": "В работе",
            "responsible": responsible,
        },
    ]
    with connect() as conn:
        for row in demo:
            keys = ", ".join(f'"{k}"' for k in row)
            qs = ", ".join("?" for _ in row)
            conn.execute(
                f"INSERT OR IGNORE INTO clients ({keys}) VALUES ({qs})",
                tuple(row.values()),
            )
        conn.commit()
    _bump_version()


# --- Импорт / экспорт xlsx --------------------------------------------------
def _block_spans() -> list[tuple[int, int, str]]:
    """Границы блоков в колонках (start, end, name), 0-индексные."""
    spans, start = [], 0
    for i in range(1, len(FIELDS) + 1):
        if i == len(FIELDS) or FIELDS[i].block != FIELDS[start].block:
            spans.append((start, i - 1, FIELDS[start].block))
            start = i
    return spans


# Колонки-даты: в экспорте пишем как настоящие даты Excel (числа — текстом,
# чтобы ничего не потерялось; даты — типизированно, чтобы сортировались/фильтровались).
DATE_KEYS = {"birth_date", "last_payment_date", "promise_date", "visit_date",
             "meeting_date", "kd_date", "assign_date"}

# Границы разумного для серийной даты Excel: 1990-01-01 .. 2100-01-01. Всё, что
# вне — не дата, а просто число в колонке даты, и выдавать его за дату опаснее,
# чем оставить как есть.
_EXCEL_EPOCH = datetime(1899, 12, 30)
_EXCEL_MIN, _EXCEL_MAX = 32874, 73051


def _excel_serial_date(value) -> str | None:
    """Серийная дата Excel → «ГГГГ-ММ-ДД». None, если это не похоже на дату."""
    try:
        days = float(value)
    except (TypeError, ValueError):
        return None
    if not (_EXCEL_MIN <= days <= _EXCEL_MAX):
        return None
    return (_EXCEL_EPOCH + timedelta(days=int(days))).date().isoformat()

# Денежные/числовые колонки — подсказка писателю xlsx (пульт, rust_xlsxwriter):
# парсить в число и ставить формат # ##0, чтобы суммировалось и не зеленело.
# В БД значения остаются текстом как пришли — это только метаданные экспорта.
NUM_KEYS = {"debt_overdue", "interest_overdue", "penalty", "duty", "dtp", "rent",
            "camera_fine", "dirt", "trash", "new_damage", "evacuation", "peni",
            "other", "debt_total", "last_payment_sum", "forecast_sum",
            "debt_today", "issued_sum", "visits_count"}


def _as_date(v):
    """Дата из реестра в любом виде, в каком её туда занесли руками.

    Форматов больше, чем кажется: в одном и том же файле встречаются «01.08.2026»,
    «01/08/2026», хвостовые точки и запятые после копипаста. Нераспознанная дата
    раньше молча прятала договор от выездника (см. _date_in), поэтому список
    форматов лучше держать щедрым.
    """
    if not v:
        return None
    s = str(v).strip().rstrip(".,;")
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


_ZERO_WIDTH = ("​", "‌", "‍", "﻿")  # невидимый мусор из копипаста в Excel

# Латиница, неотличимая на глаз от кириллицы. В самом «Хард.xlsb» заголовок
# «Cтатус звонка» начинается с латинской C — колонка молча уезжала в
# unknown_columns, и статус звонка не импортировался вообще. Сворачиваем обе
# стороны матчинга к кириллице (в БД и экспорте заголовки не меняются).
_HOMOGLYPHS = str.maketrans("acekmoptxy", "асекмортху")


# Колонки реестра, которые нужны СКОРИНГУ, но не нужны офису. Контракт их уже
# знает и разрешает игнорировать (allowed_ignored_headers) — здесь мы перестаём
# выбрасывать их значения, не заводя полями реестра. Плюс такого решения: форма
# экспортируемого файла, отпечаток контракта и строка типов не меняются вообще,
# а значит и пульт с офисными шаблонами ломать нечего.
_SCORING_ONLY_TITLES = {
    "Страна": "country",
    "Долг на начало месяца": "debt_start_month",
    "Текущие платежи": "paid_current",
}

# Помесячные оплаты названы по месяцу («Оплаты за 04.26») и меняются каждый месяц,
# поэтому в контракте они заданы шаблоном, а в БД сворачиваются в счётчик месяцев.
_PAID_MONTH_RX = re.compile(r"^Оплаты за (?:0[1-9]|1[0-2])\.[0-9]{2}$")

SCORING_ONLY_HEADERS: dict[str, str] = {}


def _scoring_only_headers() -> dict[str, str]:
    """Нормализованный заголовок → служебная колонка. Считаем лениво: `_norm_title`
    объявлен ниже по файлу, а сравнивать заголовки надо уже нормализованными."""
    global SCORING_ONLY_HEADERS
    if not SCORING_ONLY_HEADERS:
        SCORING_ONLY_HEADERS = {_norm_title(t): k
                                for t, k in _SCORING_ONLY_TITLES.items()}
    return SCORING_ONLY_HEADERS


# --- Карта колонок реестра (правится оператором, без правки кода) -------------
# Зачем: общий реестр живёт своей жизнью. В сентябре 2026 офис разделил
# «ответственный Сотрудник» на «…ХАРД» и «…СОФТ», «Дата закрепления» — так же,
# и добавил блок акции. Обязательная колонка исчезла по имени — импорт встал
# целиком, и починить его можно было только правкой кода и выкладкой.
#
# Карта снимает это с кода и отдаёт оператору. Три вида решений:
#   * alias   — «эта колонка файла и есть вот это поле реестра» (переименование);
#   * ignore  — «эта колонка нам не нужна, не шуми ею в отчёте»;
#   * extra   — «читай её как дополнительное поле» (новая колонка попадает в БД
#               и в карточку, не меняя экспортную схему и её отпечаток).
# Схема полей (fields.py), контракт обмена и форма файла при этом НЕ меняются:
# карта живёт в settings рабочей базы и накладывается поверх контракта.
REGISTRY_COLUMN_MAP_SETTING = "registry_column_map"
REGISTRY_COLUMN_MAP_VERSION = 1
# Границы намеренно тесные: карта — инструмент оператора, а не второй схемы.
MAX_COLUMN_MAP_ALIASES = 64
MAX_COLUMN_MAP_IGNORED = 128
MAX_COLUMN_MAP_EXTRAS = 24
MAX_COLUMN_TITLE_LEN = 160
MAX_EXTRA_VALUE_LEN = 500


def _empty_column_map() -> dict:
    return {"version": REGISTRY_COLUMN_MAP_VERSION, "aliases": {},
            "ignored": [], "extras": [], "updated_at": "", "updated_by": ""}


def _clean_title(value) -> str:
    """Заголовок как его написал офис: схлопнутые пробелы, без хвостов."""
    return " ".join(str(value or "").split())[:MAX_COLUMN_TITLE_LEN]


def _extra_slot(title: str) -> str:
    """Ключ доп. поля в БД — нормализованный заголовок.

    Именно нормализованный: офис правит регистр и пробелы, а история значений
    должна остаться той же самой колонкой, а не завестись заново.
    """
    return _norm_title(title)


def validate_registry_column_map(payload) -> dict:
    """Проверить и нормализовать карту. ValueError — с человеческим текстом.

    Правила, которые важнее удобства:
      * alias ведёт только на существующее поле схемы;
      * один заголовок — одно решение (alias / ignore / extra, не два сразу);
      * заголовок, который уже канонический для другого поля, алиасом не
        перебивается: иначе импорт получил бы два источника одного ключа и
        отбился бы как дубль — но уже на боевом файле и без объяснений.
    """
    if not isinstance(payload, dict):
        raise ValueError("карта колонок должна быть объектом")
    out = _empty_column_map()

    canonical = {_norm_title(f.title): f.key for f in FIELDS}
    reserved = dict(canonical)
    for title, key in _SCORING_ONLY_TITLES.items():
        reserved.setdefault(_norm_title(title), key)
    for title, key in RECALL_TITLES.items():
        reserved.setdefault(_norm_title(title), key)

    decided: dict[str, str] = {}          # нормализованный заголовок → решение

    aliases_raw = payload.get("aliases") or {}
    if not isinstance(aliases_raw, dict):
        raise ValueError("aliases должен быть объектом «поле → заголовки»")
    total_aliases = 0
    for key, titles in aliases_raw.items():
        key = str(key or "").strip()
        if key not in BY_KEY:
            raise ValueError(f"поля «{key}» нет в схеме реестра")
        if isinstance(titles, str):
            titles = [titles]
        if not isinstance(titles, list):
            raise ValueError(f"aliases.{key} должен быть списком заголовков")
        cleaned: list[str] = []
        for title in titles:
            title = _clean_title(title)
            if not title:
                continue
            slot = _norm_title(title)
            if not slot:
                continue
            owner = reserved.get(slot)
            if owner is not None and owner != key:
                raise ValueError(
                    f"«{title}» — это штатный заголовок поля «{owner}»; "
                    "его нельзя назначить другому полю"
                )
            if slot in decided and decided[slot] != f"alias:{key}":
                raise ValueError(
                    f"«{title}» уже занят другим решением карты колонок"
                )
            decided[slot] = f"alias:{key}"
            if title not in cleaned:
                cleaned.append(title)
                total_aliases += 1
        if cleaned:
            out["aliases"][key] = cleaned
    if total_aliases > MAX_COLUMN_MAP_ALIASES:
        raise ValueError(f"слишком много алиасов (максимум {MAX_COLUMN_MAP_ALIASES})")

    ignored_raw = payload.get("ignored") or []
    if not isinstance(ignored_raw, list):
        raise ValueError("ignored должен быть списком заголовков")
    for title in ignored_raw:
        title = _clean_title(title)
        if not title:
            continue
        slot = _norm_title(title)
        if not slot:
            continue
        if slot in reserved:
            raise ValueError(
                f"«{title}» — штатная колонка реестра, её нельзя выключить"
            )
        if slot in decided and decided[slot] != "ignore":
            raise ValueError(f"«{title}» уже занят другим решением карты колонок")
        decided[slot] = "ignore"
        if title not in out["ignored"]:
            out["ignored"].append(title)
    if len(out["ignored"]) > MAX_COLUMN_MAP_IGNORED:
        raise ValueError(f"слишком много скрытых колонок (максимум {MAX_COLUMN_MAP_IGNORED})")

    extras_raw = payload.get("extras") or []
    if not isinstance(extras_raw, list):
        raise ValueError("extras должен быть списком колонок")
    seen_slots: set[str] = set()
    for item in extras_raw:
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            raise ValueError("каждая дополнительная колонка — объект {title, label}")
        title = _clean_title(item.get("title"))
        if not title:
            continue
        slot = _norm_title(title)
        if not slot:
            continue
        if slot in reserved:
            raise ValueError(
                f"«{title}» — штатная колонка реестра; дополнительной она быть не может"
            )
        if slot in decided and decided[slot] != "extra":
            raise ValueError(f"«{title}» уже занят другим решением карты колонок")
        if slot in seen_slots:
            continue
        decided[slot] = "extra"
        seen_slots.add(slot)
        label = _clean_title(item.get("label")) or title
        show = bool(item.get("show_in_card", True))
        out["extras"].append({"title": title, "label": label,
                              "show_in_card": show})
    if len(out["extras"]) > MAX_COLUMN_MAP_EXTRAS:
        raise ValueError(f"слишком много дополнительных колонок (максимум {MAX_COLUMN_MAP_EXTRAS})")
    return out


def registry_column_map() -> dict:
    """Карта из рабочей базы. Битая запись — не авария: возвращаем пустую.

    Импорт обязан пережить испорченную настройку: без карты он просто ведёт
    себя как до её появления, а не отказывается работать целиком.
    """
    raw = get_setting(REGISTRY_COLUMN_MAP_SETTING)
    if not raw:
        return _empty_column_map()
    try:
        stored = json.loads(raw)
        out = validate_registry_column_map(stored)
    except (ValueError, TypeError):
        return _empty_column_map()
    out["updated_at"] = str((stored or {}).get("updated_at") or "")
    out["updated_by"] = str((stored or {}).get("updated_by") or "")
    return out


def save_registry_column_map(payload, *, author: str = "") -> dict:
    """Сохранить проверенную карту. Возвращает то, что реально записано."""
    out = validate_registry_column_map(payload)
    out["updated_at"] = datetime.now().isoformat(timespec="seconds")
    out["updated_by"] = _clean_title(author)
    set_setting(REGISTRY_COLUMN_MAP_SETTING,
                json.dumps(out, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")))
    return out


def registry_extra_values(contract_ids) -> dict[str, list[dict]]:
    """{КД: [{slot, title, label, value}]} — дополнительные поля из реестра.

    Порядок и подписи берутся из ТЕКУЩЕЙ карты колонок, а не из строки в базе:
    оператор может переименовать подпись или скрыть колонку из карточки, и это
    должно сработать сразу, не дожидаясь следующего импорта. Колонку, которую
    из карты убрали совсем, не показываем — значения остаются в базе до
    ближайшего импорта, но карточка о них больше не знает.
    """
    ids = [str(c) for c in contract_ids if c]
    if not ids:
        return {}
    declared = {}
    for index, item in enumerate(registry_column_map()["extras"]):
        declared[_extra_slot(item["title"])] = (index, item)
    if not declared:
        return {}
    out: dict[str, list[dict]] = {}
    with connect() as conn:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT contract_id, slot, title, label, value FROM client_extra "
            f"WHERE contract_id IN ({marks}) AND COALESCE(value,'') <> ''",
            tuple(ids),
        ).fetchall()
    for row in rows:
        found = declared.get(row["slot"])
        if not found:
            continue
        index, item = found
        out.setdefault(row["contract_id"], []).append({
            "slot": row["slot"], "title": item["title"], "label": item["label"],
            "value": row["value"], "show_in_card": bool(item["show_in_card"]),
            "_order": index,
        })
    for items in out.values():
        items.sort(key=lambda item: (item["_order"], item["label"]))
        for item in items:
            item.pop("_order", None)
    return out


# --- Разбор ячейки отзыва -----------------------------------------------------
# Мусор, который в живых реестрах пишут вместо пустоты. «Непусто = отозван»
# без этого списка снимало бы договоры на «нет», «-», «н/д» и протянутой
# нулевой дате (транспорт превращает её в «00:00:00»).
_RECALL_EMPTY_TOKENS = {
    "", "0", "00", "0.0", "0,0", "0.00", "0,00",
    "-", "--", "---", "—", "–", "_",
    "нет", "не", "нету", "нетотзыва",
    "нетребуется", "отсутствует", "no", "none", "n/a", "na",
    "н/д", "нд", "#н/д", "#n/a", "null", "nil", "false", "ложь",
    "00:00:00", "000000", "18991230", "30121899", "19000101",
    "нетданных", "неподлежит", "неизвестно", "неприменимо",
}
# Дата отзыва раньше этой границы — артефакт формата (нулевая дата Excel,
# протянутая ячейка), а не решение офиса.
_RECALL_DATE_FLOOR = date(2015, 1, 1)


def _recall_token(text) -> str:
    t = " ".join(str(text).split()).lower().replace("ё", "е")
    return t.strip(".,;:!").replace(" ", "")


def _recall_cell(value, *, is_date: bool = False) -> tuple[str, bool]:
    """(что писать в БД, считается ли отзывом).

    Порядок веток проверен экспериментом на транспорте: булева галочка офиса —
    отзыв (True) / не отзыв (False) ЯВНО; нулевая протянутая дата приезжает
    из calamine как time(0,0) → строка «00:00:00» и гасится чёрным списком;
    float-флаг «1.0» пишется как «1», чтобы не зависеть от канала доставки."""
    if value is None:
        return "", False
    if value is True:
        return "да", True
    if value is False:
        return "", False
    if isinstance(value, datetime):
        d = value.date()
        if d < _RECALL_DATE_FLOOR:
            return "", False
        return d.strftime("%Y-%m-%d"), True
    if isinstance(value, date):
        if value < _RECALL_DATE_FLOOR:
            return "", False
        return value.isoformat(), True
    if isinstance(value, (int, float)):
        if float(value) == 0.0:
            return "", False
        iso = _excel_serial_date(value)
        if iso:
            parsed = _as_date(iso)
            if parsed and parsed < _RECALL_DATE_FLOOR:
                return "", False
            return iso, True
        if isinstance(value, float) and value.is_integer():
            return str(int(value)), True
        return str(value).strip(), True
    text = " ".join(str(value).split())
    if not text:
        return "", False
    token = _recall_token(text)
    if token in _RECALL_EMPTY_TOKENS:
        return "", False
    # формы слова: «не отозван(о/ы)», «нет отзыв(а/ов)» — по корню
    if token.startswith("неотозван") or token.startswith("нетотзыв"):
        return "", False
    if is_date:
        parsed = _as_date(text)
        if parsed:
            if parsed < _RECALL_DATE_FLOOR:
                return "", False
            return parsed.isoformat(), True
    return text, True


def _recall_is_set(value) -> bool:
    return _recall_cell(value)[1]


RECALL_HEADERS: dict[str, str] = {}


def _recall_headers() -> dict[str, str]:
    global RECALL_HEADERS
    if not RECALL_HEADERS:
        RECALL_HEADERS = {_norm_title(t): k for t, k in RECALL_TITLES.items()}
    return RECALL_HEADERS


def _norm_title(s) -> str:
    """Нормализованный заголовок для устойчивого матчинга: регистр, пробелы (юникод-пробелы
    и неразрывный — покрываются штатным классом пробельных символов regex), невидимые
    нулевой-ширины символы, латиница-двойники, хвостовая пунктуация (:;*.) — реальные
    реестры нередко добавляют её к заголовку без изменения смысла колонки."""
    if s in (None, ""):
        return ""
    t = str(s)
    for zw in _ZERO_WIDTH:
        t = t.replace(zw, "")
    t = re.sub(r"\s+", " ", t.strip().lower()).translate(_HOMOGLYPHS)
    return t.rstrip(":;*.")


def _canonical_contract_id(value) -> str:
    """Канонический КД для duplicate preflight и SQLite lookup.

    Calamine/openpyxl могут вернуть один Excel-КД как ``12345``, ``12345.0`` или
    строку ``"12345.0"``. Все три формы обязаны попасть в один конфликтный домен;
    буквенно-цифровые КД и значащие ведущие нули не переписываем.
    """
    if value is None:
        return ""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    numeric_excel = re.fullmatch(r"([+-]?\d+)\.0+", text)
    return numeric_excel.group(1) if numeric_excel else text


# Публичный алиас: поиск фото по КД обязан канонизировать номер ТОЙ ЖЕ
# функцией, что импорт, иначе 12345 / 12345.0 / "12345.0" разъезжаются.
canonical_contract_id = _canonical_contract_id


def _pick_sheet(wb, _title_to_key):
    """Production import accepts only the contract-bound portfolio sheet."""
    if WORKBOOK_SHEET_NAME not in wb.sheetnames:
        raise ValueError(
            f"обязательный лист «{WORKBOOK_SHEET_NAME}» отсутствует; "
            "fallback на похожий лист запрещён"
        )
    return wb[WORKBOOK_SHEET_NAME]


def write_xlsx(path: str, rows: list[dict]) -> str:
    """Записать список словарей в реестр-шаблон: 3 строки шапки + данные.

    Шапки блоков объединяются по колонкам блока (как в исходном «Хард.xlsb»).
    Даты — типизированно (DD.MM.YYYY), числа/прочее — текстом. Используется
    и экспортом из БД, и генератором синтетики.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Портфели"

    spans = _block_spans()
    row1 = [None] * len(FIELDS)
    for s, _e, name in spans:
        row1[s] = name
    ws.append(row1)                                 # строка 1 — блоки
    ws.append([f.title for f in FIELDS])            # строка 2 — заголовки
    ws.append([f.source for f in FIELDS])           # строка 3 — сх/хард

    date_cols = [i + 1 for i, f in enumerate(FIELDS) if f.key in DATE_KEYS]
    r_idx = 4
    for r in rows:
        out = []
        for f in FIELDS:
            v = r.get(f.key)
            out.append(_as_date(v) or v if f.key in DATE_KEYS else v)
        ws.append(out)
        for c in date_cols:
            cell = ws.cell(row=r_idx, column=c)
            if isinstance(cell.value, date):
                cell.number_format = "DD.MM.YYYY"
        r_idx += 1

    # объединяем и оформляем шапки блоков + жирные заголовки
    for s, e, _name in spans:
        if e > s:
            ws.merge_cells(start_row=1, start_column=s + 1, end_row=1, end_column=e + 1)
        cell = ws.cell(row=1, column=s + 1)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for col in range(1, len(FIELDS) + 1):
        ws.cell(row=2, column=col).font = Font(bold=True)
    ws.freeze_panes = "A4"

    wb.save(path)
    return path


def registry_rows(
    *,
    employees: list[str] | None = None,
    visit_from: str | None = None,
    visit_to: str | None = None,
) -> list[dict]:
    """Raw-слепок с явными, независимыми фильтрами выгрузки.

    ``employees=None`` сохраняет прежний полный экспорт, включая строки без
    ответственного. Переданный список ограничивает выгрузку только выбранными
    сотрудниками. Окно дат относится к доказанному ``bot_visit_at`` и потому не
    принимает импортную/ручную ``visit_date`` за состоявшийся визит.
    """
    if isinstance(employees, str):
        employees = [employees]
    employee_keys = None if employees is None else {
        _name_key(employee) for employee in employees if _name_key(employee)
    }
    since = _as_date(visit_from) if str(visit_from or "").strip() else None
    until = _as_date(visit_to) if str(visit_to or "").strip() else None
    if str(visit_from or "").strip() and since is None:
        raise ValueError("неверная дата начала выгрузки")
    if str(visit_to or "").strip() and until is None:
        raise ValueError("неверная дата окончания выгрузки")
    if since and until and since > until:
        raise ValueError("начало окна выгрузки позже окончания")
    with connect() as conn:
        rows = conn.execute("SELECT * FROM clients ORDER BY client").fetchall()
    result = []
    for row in rows:
        if (
            employee_keys is not None
            and _name_key(row["responsible"]) not in employee_keys
        ):
            continue
        if since or until:
            visit_date = _as_date(row["bot_visit_at"])
            if not row["bot_visit_data"] or visit_date is None:
                continue
            if since and visit_date < since:
                continue
            if until and visit_date > until:
                continue
        out = {f.key: row[f.key] for f in FIELDS}
        # Поля визита — из ДОКАЗАННОГО снимка, а не из сырых колонок. Сырые
        # хранят копию последнего импорта: у 440 из 890 договоров с визитом там
        # лежит офисное «Не обработан», потому что офис по этому кейсу ещё не
        # высказывался. Карточка и дайджест всё это время показывали правду
        # (они читают снимок), а полная выгрузка — импортную копию, и файл,
        # собранный из неё, затирал живые статусы дефолтом. Обогащение так
        # делало всегда (enrichment_rows), выгрузка — нет.
        visit = _merged_visit(row)
        if visit:
            for key in BOT_VISIT_KEYS:
                if key in out and key in visit:
                    out[key] = visit[key]
        result.append(out)
    return result


def enrichment_rows() -> list[dict]:
    """Безопасные строки для точечного обогащения живых файлов.

    Все потенциально импортные поля визита сначала очищаются. Вернуть их можно
    только из валидного служебного снимка ``bot_visit_data``; дополнительный
    boolean-маркер нужен новому пульту. Поэтому даже старый пульт, который всё
    ещё смотрит лишь на непустой visit_date, не увидит неподтверждённый мусор.
    """
    with connect() as conn:
        rows = conn.execute("SELECT * FROM clients ORDER BY client").fetchall()
    result = []
    for raw in rows:
        row = {f.key: raw[f.key] for f in FIELDS}
        for key in BOT_VISIT_KEYS:
            if key in row:
                row[key] = None
        snapshot = _merged_visit(raw)
        row[BOT_VISIT_MARKER] = snapshot is not None
        if snapshot:
            # Снимки, созданные до фикса маппинга 24.07, ещё не содержат ``tips``.
            # Сама колонка bot-owned (import_grid её не знает и не пишет), поэтому
            # при наличии валидного доказанного визита можно безопасно дослать
            # сохранённые наводки из raw. Новые record_bot_visit уже кладут tips
            # непосредственно в bot_visit_data.
            tips = str(snapshot.get("tips") or raw["tips"] or "").strip()
            if tips:
                snapshot = {**snapshot, "tips": tips}
            row.update(snapshot)
        result.append(row)
    return result


ENRICHMENT_LEDGER_KEYS = frozenset(ENRICHMENT_SOURCE_KEYS)
ENRICHMENT_LEDGER_MAX_VALUE = 32767   # предел ячейки Excel


def enrichment_ledger(employees: list[str] | None = None) -> dict[str, dict]:
    """Реестр доставленного {КД: {поле: {"value", "visit_date", "at",
    "responsible"}}}. employees — сузить до книг этих ответственных; пусто —
    весь реестр. Пульт передаёт его в enrich.build_plan как ``ledger``."""
    wanted = {str(e).strip().casefold() for e in (employees or []) if str(e).strip()}
    with connect() as conn:
        rows = conn.execute(
            "SELECT contract_id, field_key, value, visit_date, responsible, "
            "delivered_at FROM enrichment_ledger ORDER BY contract_id, field_key"
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        if wanted and str(r["responsible"] or "").strip().casefold() not in wanted:
            continue
        out.setdefault(r["contract_id"], {})[r["field_key"]] = {
            "value": r["value"],
            "visit_date": r["visit_date"] or "",
            "at": r["delivered_at"],
            "responsible": r["responsible"] or "",
        }
    return out


def record_enrichment_deliveries(entries: list, receipt_digest: str = "") -> int:
    """Записать/обновить доставленные значения. Идемпотентно: последнее
    значение на (КД, поле). Мусор — ValueError до первой записи, чтобы половина
    реестра не осталась записанной."""
    if not isinstance(entries, list):
        raise ValueError("entries должен быть списком")
    digest = str(receipt_digest or "").strip()[:128]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    prepared: list[tuple] = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            raise ValueError(f"запись {i}: не объект")
        cid = str(e.get("contract_id") or "").strip()
        key = str(e.get("key") or e.get("field_key") or "").strip()
        value = e.get("value")
        if not cid:
            raise ValueError(f"запись {i}: пустой КД")
        if key not in ENRICHMENT_LEDGER_KEYS:
            raise ValueError(f"запись {i}: поле {key!r} не относится к обогащению")
        if value is None:
            raise ValueError(f"запись {i}: нет значения")
        value = str(value)
        if len(value) > ENRICHMENT_LEDGER_MAX_VALUE:
            raise ValueError(f"запись {i}: значение длиннее ячейки Excel")
        prepared.append((
            cid, key, value,
            str(e.get("visit_date") or "")[:32],
            str(e.get("responsible") or "").strip()[:200],
            str(e.get("file") or e.get("file_path") or "")[:1000],
            now, digest,
        ))
    if not prepared:
        return 0
    with connect() as conn:
        conn.executemany(
            "INSERT INTO enrichment_ledger (contract_id, field_key, value, "
            "visit_date, responsible, file_path, delivered_at, receipt_digest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(contract_id, field_key) DO UPDATE SET "
            "value=excluded.value, visit_date=excluded.visit_date, "
            "responsible=excluded.responsible, file_path=excluded.file_path, "
            "delivered_at=excluded.delivered_at, "
            "receipt_digest=excluded.receipt_digest",
            prepared,
        )
        conn.commit()
    return len(prepared)


def export_schema() -> list[dict]:
    """Схема колонок для писателя xlsx на стороне пульта: блоки, заголовки,
    типы (дата/число). Единственный источник правды — fields.FIELDS."""
    schema = [
        {
            "key": f.key,
            "title": f.title,
            "block": f.block,
            "source": f.source,
            "date": f.key in DATE_KEYS,
            "num": f.key in NUM_KEYS,
        }
        for f in FIELDS
    ]
    verify_export_schema(schema)
    return schema


def export_xlsx(path: str) -> str:
    """Выгрузить БД обратно в реестр-шаблон (легаси-путь: openpyxl на сервере)."""
    return write_xlsx(path, registry_rows())


def pick_grid(sheets: list[dict]) -> list[list]:
    """Select only the exact contract-bound sheet from JSON workbook grids."""
    if not isinstance(sheets, list):
        raise ValueError("sheets должен быть списком листов")
    names = [
        str(sheet.get("name"))
        for sheet in sheets
        if isinstance(sheet, dict) and sheet.get("name") is not None
    ]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(
            "книга содержит повторяющиеся имена листов: "
            + ", ".join(duplicates)
        )
    for sheet in sheets:
        if (
            isinstance(sheet, dict)
            and sheet.get("name") == WORKBOOK_SHEET_NAME
        ):
            grid = sheet.get("grid")
            if not isinstance(grid, list):
                raise ValueError(
                    f"лист «{WORKBOOK_SHEET_NAME}» не содержит grid"
                )
            return grid
    raise ValueError(
        f"обязательный лист «{WORKBOOK_SHEET_NAME}» отсутствует; "
        "fallback на похожий лист запрещён"
    )


def import_xlsx(
    path: str,
    *,
    source_kind: str | None = None,
    source_proof: dict | None = None,
) -> dict:
    """Легаси-обёртка: открыть файл openpyxl-ом и прогнать через import_grid.
    Пульт этим путём больше не ходит — парсит файл сам (calamine) и шлёт грид
    в POST /admin/import.json."""
    from openpyxl import load_workbook

    title_to_key = {_norm_title(f.title): f.key for f in FIELDS}
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = _pick_sheet(wb, title_to_key)
    return import_grid(
        list(ws.iter_rows(values_only=True)),
        source_kind=source_kind,
        source_proof=source_proof,
    )


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_digest(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def register_import_authorization(
    proof: dict,
    *,
    source_kind: str,
    ttl_hours: int = 24,
) -> dict:
    """Register one exact import proof through a local/root-only boundary.

    By default there is no HTTP route at all: the registry operator validates
    the external publication or test scope and invokes the CLI next to the
    server data store, so knowing the admin key is not enough to load anything.

    An instance owner may open ``/admin/import/authorize.json`` by switching
    ``IMPORT_REGISTRAR_HTTP`` on, accepting that the admin key then becomes a
    sufficient condition. The validation below is identical either way; only the
    entry point differs, and turning the flag off restores the two-step order.
    """

    if isinstance(ttl_hours, bool) or not isinstance(ttl_hours, int):
        raise ExchangeContractError("authorization ttl_hours must be integer")
    if ttl_hours < 1 or ttl_hours > 168:
        raise ExchangeContractError(
            "authorization ttl_hours must be between 1 and 168"
        )
    verified = validate_import_source_proof_record(
        proof,
        source_kind=source_kind,
    )
    now = datetime.now().astimezone()
    expires = now + timedelta(hours=ttl_hours)
    digest = verified["digest"]
    source_id = verified["source_id"]
    sequence = verified["sequence"]
    proof_json = _canonical_json(proof)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM import_receipts WHERE proof_digest=?",
            (digest,),
        ).fetchone()
        if existing is not None:
            if (
                existing["source_kind"] != source_kind
                or existing["source_id"] != source_id
                or int(existing["sequence"]) != sequence
                or existing["proof_json"] != proof_json
            ):
                raise ExchangeContractError(
                    "stored import authorization disagrees with exact proof"
                )
            conn.commit()
            return {
                "proof_digest": digest,
                "source_kind": source_kind,
                "source_id": source_id,
                "sequence": sequence,
                "status": existing["status"],
                "expires_at": existing["expires_at"],
                "idempotent": True,
            }
        collision = conn.execute(
            "SELECT proof_digest FROM import_receipts "
            "WHERE source_id=? AND sequence=?",
            (source_id, sequence),
        ).fetchone()
        if collision is not None:
            raise ExchangeContractError(
                "another receipt already owns this source sequence"
            )
        head = conn.execute(
            "SELECT sequence, proof_digest FROM import_source_heads "
            "WHERE source_id=?",
            (source_id,),
        ).fetchone()
        if head is not None and sequence <= int(head["sequence"]):
            raise ExchangeContractError(
                "authorization is not newer than the imported source head"
            )
        conn.execute(
            "INSERT INTO import_receipts "
            "(proof_digest, source_kind, source_id, sequence, proof_json, "
            "status, registered_at, expires_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                digest,
                source_kind,
                source_id,
                sequence,
                proof_json,
                "pending",
                now.isoformat(timespec="microseconds"),
                expires.isoformat(timespec="microseconds"),
            ),
        )
        conn.commit()
    return {
        "proof_digest": digest,
        "source_kind": source_kind,
        "source_id": source_id,
        "sequence": sequence,
        "status": "pending",
        "expires_at": expires.isoformat(timespec="microseconds"),
        "idempotent": False,
    }


def import_authorization_status(proof_digest: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT proof_digest, source_kind, source_id, sequence, status, "
            "registered_at, expires_at, consumed_at, result_digest "
            "FROM import_receipts WHERE proof_digest=?",
            (str(proof_digest or "").strip(),),
        ).fetchone()
    return dict(row) if row is not None else None


def clear_pending_import_authorizations() -> int:
    """Invalidate pre-reset permissions while preserving consumed audit/head."""

    with connect() as conn:
        count = conn.execute(
            "DELETE FROM import_receipts WHERE status='pending'"
        ).rowcount
        conn.commit()
    return count


def _register_authorization_row(
    conn: sqlite3.Connection,
    verified_source: dict,
    *,
    source_kind: str,
    self_issued: bool = False,
    ttl_hours: int = 24,
) -> None:
    """Вставить расписку прямо в открытой транзакции импорта.

    Отличие от ``register_import_authorization``: та открывает своё соединение и
    коммитит, а здесь мы уже внутри ``BEGIN IMMEDIATE`` импорта — второй коннект
    к тому же SQLite встал бы на блокировке насмерть.

    Проверки source_id/sequence НЕ дублируем: их только что сделал
    ``validate_import_source_proof``, а обгон head'а всё равно ловится ниже, в
    ``_authorized_import_state``, для любого происхождения расписки.
    """
    now = datetime.now().astimezone()
    conn.execute(
        "INSERT INTO import_receipts "
        "(proof_digest, source_kind, source_id, sequence, proof_json, "
        "status, registered_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            verified_source["digest"],
            source_kind,
            verified_source["source_id"],
            verified_source["sequence"],
            _canonical_json(verified_source.get("proof") or {}),
            "pending",
            now.isoformat(timespec="microseconds"),
            (now + timedelta(hours=ttl_hours)).isoformat(timespec="microseconds"),
        ),
    )
    # Пишем аудит ТОЙ ЖЕ транзакцией. `audit_access` открывает своё соединение, а
    # мы внутри BEGIN IMMEDIATE — второй коннект встал бы на блокировке, и запись
    # молча терялась бы (audit_access глушит исключения намеренно).
    conn.execute(
        "INSERT INTO audit (ts, actor, action, target, detail) VALUES (?,?,?,?,?)",
        (now.isoformat(timespec="seconds"), 0,
         "import_self_authorized" if self_issued else "import_authorized",
         verified_source["digest"], source_kind))


def _authorized_import_state(
    conn: sqlite3.Connection,
    verified_source: dict,
    *,
    source_kind: str,
) -> dict | None:
    digest = verified_source["digest"]
    row = conn.execute(
        "SELECT * FROM import_receipts WHERE proof_digest=?",
        (digest,),
    ).fetchone()
    if row is None:
        # ПРЕДУПРЕЖДЕНИЕ, а не отказ (03.08.2026, решение владельца). Требование
        # «разрешение выдаётся отдельным актом на сервере» снято: незарегистрированный
        # proof теперь регистрируется тут же и помечается как самовыданный.
        #
        # Что при этом НЕ потеряно и потеряться не должно: расписка всё равно
        # создаётся и тут же гасится, поэтому повтор того же файла остаётся
        # идемпотентным, а не пишет второй раз; source_id/sequence по-прежнему
        # защищают от отката на старую версию источника; в аудите видно, что
        # разрешение выдал сам импорт, а не оператор.
        _register_authorization_row(conn, verified_source,
                                    source_kind=source_kind, self_issued=True)
        row = conn.execute(
            "SELECT * FROM import_receipts WHERE proof_digest=?",
            (digest,),
        ).fetchone()
        if row is None:                       # запись не удержалась — это уже отказ
            raise ExchangeContractError(
                "source proof could not be registered"
            )
    if (
        row["source_kind"] != source_kind
        or row["source_id"] != verified_source["source_id"]
        or int(row["sequence"]) != verified_source["sequence"]
    ):
        raise ExchangeContractError(
            "registered authorization metadata disagrees with source proof"
        )
    if row["proof_json"] != _canonical_json(verified_source["proof"]):
        raise ExchangeContractError(
            "registered authorization bytes disagree with source proof"
        )
    head = conn.execute(
        "SELECT sequence, proof_digest FROM import_source_heads "
        "WHERE source_id=?",
        (verified_source["source_id"],),
    ).fetchone()
    if head is not None:
        head_sequence = int(head["sequence"])
        if verified_source["sequence"] < head_sequence:
            raise ExchangeContractError(
                "source proof is older than the imported source head"
            )
        if (
            verified_source["sequence"] == head_sequence
            and head["proof_digest"] != digest
        ):
            raise ExchangeContractError(
                "source sequence is already owned by another receipt"
            )
    if row["status"] == "consumed":
        if (
            head is None
            or int(head["sequence"]) != verified_source["sequence"]
            or head["proof_digest"] != digest
        ):
            raise ExchangeContractError(
                "consumed receipt is not the current source head"
            )
        try:
            stored = json.loads(row["result_json"] or "")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ExchangeContractError(
                "consumed import receipt has no trusted result"
            ) from exc
        if (
            not isinstance(stored, dict)
            or row["result_digest"] != _json_digest(stored)
        ):
            raise ExchangeContractError(
                "consumed import result digest is invalid"
            )
        replay = dict(stored)
        replay["idempotent_replay"] = True
        replay["original_result"] = {
            key: stored.get(key)
            for key in ("inserted", "updated", "office_cells", "auto_unassigned")
        }
        for key in (
            "inserted",
            "updated",
            "office_cells",
            "office_contracts",
            "protected_cells",
            "protected_contracts",
            "auto_unassigned",
            "recalled",
            "recalled_had_owner",
            "recalled_previously_unassigned",
            "recalled_without_owner",
            "recalled_future_dated",
            "recalled_unparsed_date",
            "recalled_unrecognized",
            "recalled_partial_persons",
            "recall_held",
            "recall_restored",
            "recall_restored_to_owner",
            "recall_restored_without_owner",
            "recall_restored_to_manual",
        ):
            if key in replay:
                replay[key] = 0
        # Повтор apply после таймаута не должен рисовать «отозвано 1469»,
        # ничего не записав: эффектные списки и digest — к пустым значениям.
        empty_digest = hashlib.sha256(b"[]").hexdigest()
        for key in ("recalled_by_employee", "recall_restored_by_employee"):
            if key in replay:
                replay[key] = {}
        for key in ("recalled_sample", "recalled_unrecognized_sample"):
            if key in replay:
                replay[key] = []
        for key in ("recalled_digest", "recall_restored_digest"):
            if key in replay:
                replay[key] = empty_digest
        return replay
    if row["status"] != "pending":
        raise ExchangeContractError("import authorization is not pending")
    try:
        expires_at = datetime.fromisoformat(str(row["expires_at"]))
    except (TypeError, ValueError) as exc:
        raise ExchangeContractError(
            "import authorization expiry is invalid"
        ) from exc
    if expires_at.tzinfo is None:
        expires_at = expires_at.astimezone()
    if datetime.now().astimezone() >= expires_at:
        raise ExchangeContractError("import authorization has expired")
    if head is not None:
        if verified_source["sequence"] == int(head["sequence"]):
            raise ExchangeContractError(
                "source sequence is already owned by another receipt"
            )
    return None


def _consume_import_authorization(
    conn: sqlite3.Connection,
    verified_source: dict,
    report: dict,
) -> None:
    now = datetime.now().astimezone().isoformat(timespec="microseconds")
    result_json = _canonical_json(report)
    updated = conn.execute(
        "UPDATE import_receipts SET status='consumed', consumed_at=?, "
        "result_json=?, result_digest=? "
        "WHERE proof_digest=? AND status='pending'",
        (
            now,
            result_json,
            _json_digest(report),
            verified_source["digest"],
        ),
    ).rowcount
    if updated != 1:
        raise ExchangeContractError(
            "import authorization was consumed concurrently"
        )
    conn.execute(
        "INSERT INTO import_source_heads "
        "(source_id, sequence, proof_digest, imported_at) VALUES (?,?,?,?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "sequence=excluded.sequence, proof_digest=excluded.proof_digest, "
        "imported_at=excluded.imported_at "
        "WHERE excluded.sequence > import_source_heads.sequence",
        (
            verified_source["source_id"],
            verified_source["sequence"],
            verified_source["digest"],
            now,
        ),
    )


def import_grid(
    rows: list[list],
    *,
    source_kind: str | None = None,
    source_proof: dict | None = None,
    require_source_proof: bool = False,
    full_registry: bool = False,
    authoritative_employees: list[str] | None = None,
    preview_only: bool = False,
    restore_manual_unassigned: bool = False,
    replace_current_visit_comments: bool = False,
    import_historical_visit_comments: bool = False,
    expected_auto_unassigned: int | None = None,
    expected_auto_unassigned_digest: str | None = None,
    expected_manual_restored: int | None = None,
    expected_manual_restore_digest: str | None = None,
    expected_comment_effect_digest: str | None = None,
    expected_recalled: int | None = None,
    expected_recalled_digest: str | None = None,
    expected_recall_restored: int | None = None,
    expected_recall_restored_digest: str | None = None,
) -> dict:
    """Ядро импорта опубликованного master/test subset.

    Источник обязателен и проверяется до разбора/записи. Личные книги сюда не
    допускаются: их обмен с master выполняет только :mod:`registry_cycle`.
    Колонки матчатся по заголовку — переживает «сдвиг влево».
    Upsert: для существующих КД обновляем только пришедшие поля, не затирая
    назначение/визиты/фото.

    Исключение — колонки разбивки по долгу (debt.SOURCE_OWNED). Если такая колонка
    в файле ЕСТЬ, её пустая ячейка означает «статья погашена», а не «данные не
    пришли»: иначе при ежемесячной переимпортации в карточке навсегда оставались
    призраки старых начислений (эвакуация, ДТП…), и сумма статей расходилась с
    «долгом всего». Колонки, которых в файле нет вообще, по-прежнему не трогаем.

    Поля визита договоров с завершённым визитом бота проходят через якорный шлюз:
      * якорь — «Дата выезда» строки файла совпадает с датой визита бота И хотя
        бы одно непустое поле визита в строке дословно повторяет текущий снимок.
        Одной даты мало: «Дата выезда» — поле HARD, его печатают руками, поэтому
        файл с нужной датой мог никогда не видеть результата бота. Эхо и есть
        доказательство «эта строка видела ЭТОТ визит»: правка офиса меняет одну
        ячейку уже обогащённой строки, остальные остаются эхом и проходят, а
        старая или довизитная копия эха не даёт и отбивается;
      * «Общий результат» reverse-map-ится во внутренний call_status;
        «Статус звонка» и «Комментарий звонка» никогда не становятся оверлеем;
      * отличие принимается как офисная правка, только если значение НЕ известно
        журналу visit_value_log (эхо прежнего снимка — файл, обогащённый между
        визитами, не должен откатывать повторный выезд), не является сигнатурой
        июльского яда (call_comment == серверный visit_comment) и для dropdown
        сводится к каноническому варианту;
      * принятая правка идёт в raw-поля и office_edit_data ВСЕХ договоров
        человека с этим визитом (карточка/дайджест читают первый КД человека);
        снимок bot_visit_data не меняется никогда;
      * без якоря (старый файл, мусорная дата) — как раньше: всё выбрасывается.

    Любой повтор канонического КД блокирует весь файл до записи, включая
    одинаковые/дополняющие строки и числовые эквиваленты. UNIQUE(contract_id) не
    умеет представить две реальные строки master, поэтому неявное слияние
    запрещено; коллизия уходит в отчёт/карантин для ручного решения.

    ``full_registry`` — отдельное явное решение оператора, а не следствие
    ``source_kind``. При нём ``authoritative_employees`` задаёт точную область:
    снимаются только отсутствующие договоры этих сотрудников, остальные
    портфели не затрагиваются. Пустой вход допустим и означает нулевой портфель
    явно выбранного сотрудника.
    ``preview_only`` выполняет тот же расчёт в транзакции и откатывает все записи.
    При применении пульт передаёт увиденный ``expected_auto_unassigned``; если
    состояние БД успело измениться, импорт откатывается и требует новый preview.

    Ручные снятия по умолчанию переживают импорт того же ответственного.
    ``restore_manual_unassigned`` явно возвращает только совпавшие входные КД;
    apply обязан повторить count+digest точного множества из preview.

    Комментарии имеют два независимых, выключенных по умолчанию режима:
      * текущий комментарий существующего договора сохраняется как есть; только
        ``replace_current_visit_comments`` заменяет его значением файла, включая
        пустую ячейку;
      * историчный комментарий вообще не импортируется без явного
        ``import_historical_visit_comments`` и никогда не входит в форму/обратную
        запись сотрудника.
    Точный набор замен связывается preview/apply через
    ``expected_comment_effect_digest`` так же, как набор автоснятий.

    Возвращает отчёт — чтобы было видно ТОЧНО, что вошло:
    {source_kind, total_rows, inserted, updated, skipped_no_id, duplicates,
    unknown_columns,
    protected_cells, protected_contracts, office_cells, office_contracts,
    office_applied, auto_unassigned, blocked, duplicate_conflicts}.
    protected_* — отклонено
    шлюзом (устаревший файл/мусор);
    office_* — принятые офисные правки, office_applied = {КД: {поле: значение}}
    (пульту — чтобы обогащение сразу писало свежепринятые значения).
    """
    report = {"source_kind": source_kind,
              "full_registry": bool(full_registry),
              "preview_only": bool(preview_only),
              "total_rows": 0, "inserted": 0, "updated": 0,
              "skipped_no_id": 0, "duplicates": 0, "unknown_columns": [],
              # Карта колонок: что оператор связал руками и что прочитано как
              # дополнительные поля. Видно в том же отчёте, что и всё остальное.
              "operator_aliases": [], "extra_columns": [], "extra_cells": 0,
              "protected_cells": 0, "protected_contracts": 0,
              "office_cells": 0, "office_contracts": 0, "office_applied": {},
              "office_by_key": {}, "protected_by_key": {},
              "office_promise_unparsed": 0,
              "office_result_unprocessed": 0,
              "isolated_call_cells": 0, "isolated_call_contracts": 0,
              # Строки без ответственного попадают в базу, но невидимы выезднику и не
              # входят ни в один отчёт руководителя. Раньше это не показывалось нигде,
              # поэтому 1970 договоров из 5267 «потерялись» молча.
              "no_responsible": 0,
              "auto_unassigned": 0,
              "auto_unassigned_by_employee": {},
              "auto_unassigned_digest": hashlib.sha256(b"[]").hexdigest(),
              # Ручное снятие по умолчанию переживает импорт. Оператор может
              # отдельно разрешить вернуть только те снятые договоры, которые
              # снова присутствуют во входе с тем же ответственным.
              "restore_manual_unassigned": bool(restore_manual_unassigned),
              "manual_restore_candidates": 0,
              "manual_restored": 0,
              "manual_restore_by_employee": {},
              "manual_restore_digest": hashlib.sha256(b"[]").hexdigest(),
              "replace_current_visit_comments": bool(
                  replace_current_visit_comments
              ),
              "import_historical_visit_comments": bool(
                  import_historical_visit_comments
              ),
              "current_comments_preserved": 0,
              "current_comments_replaced": 0,
              "current_comments_cleared": 0,
              "current_comments_unchanged": 0,
              "historical_comments_preserved": 0,
              "historical_comments_imported": 0,
              "historical_comments_cleared": 0,
              "historical_comments_unchanged": 0,
              "comment_effect_digest": hashlib.sha256(b"[]").hexdigest(),
              # --- отзыв офисом (колонки «Дата отзыва» и соседние) ---
              "recall_columns": [],
              "recalled": 0,
              "recalled_had_owner": 0,
              "recalled_without_owner": 0,
              "recalled_previously_unassigned": 0,
              "recalled_by_employee": {},
              "recalled_digest": hashlib.sha256(b"[]").hexdigest(),
              "recalled_future_dated": 0,
              "recalled_unparsed_date": 0,
              "recalled_unrecognized": 0,
              "recalled_unrecognized_sample": [],
              "recalled_partial_persons": 0,
              "recalled_sample": [],
              "recall_held": 0,
              "recall_restored": 0,
              "recall_restored_to_owner": 0,
              "recall_restored_without_owner": 0,
              "recall_restored_to_manual": 0,
              "recall_restored_by_employee": {},
              "recall_restored_digest": hashlib.sha256(b"[]").hexdigest(),
              "authoritative_employees": [],
              "source_proof_digest": None,
              "blocked": False, "duplicate_conflicts": [],
              "quarantined_contracts": []}

    for option_name, option_value in (
        ("restore_manual_unassigned", restore_manual_unassigned),
        ("replace_current_visit_comments", replace_current_visit_comments),
        ("import_historical_visit_comments", import_historical_visit_comments),
    ):
        if type(option_value) is not bool:
            report["blocked"] = True
            report["error"] = (
                f"импорт заблокирован: {option_name} должен быть boolean"
            )
            return report

    if (
        expected_auto_unassigned is not None
        and (
            isinstance(expected_auto_unassigned, bool)
            or not isinstance(expected_auto_unassigned, int)
            or expected_auto_unassigned < 0
        )
    ):
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: expected_auto_unassigned должен быть "
            "неотрицательным целым"
        )
        return report

    if (
        expected_manual_restored is not None
        and (
            isinstance(expected_manual_restored, bool)
            or not isinstance(expected_manual_restored, int)
            or expected_manual_restored < 0
        )
    ):
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: expected_manual_restored должен быть "
            "неотрицательным целым"
        )
        return report

    if expected_auto_unassigned_digest is not None and (
        not isinstance(expected_auto_unassigned_digest, str)
        or len(expected_auto_unassigned_digest) != 64
        or any(ch not in "0123456789abcdef"
               for ch in expected_auto_unassigned_digest)
    ):
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: expected_auto_unassigned_digest должен быть "
            "lowercase SHA-256"
        )
        return report

    if expected_manual_restore_digest is not None and (
        not isinstance(expected_manual_restore_digest, str)
        or len(expected_manual_restore_digest) != 64
        or any(ch not in "0123456789abcdef"
               for ch in expected_manual_restore_digest)
    ):
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: expected_manual_restore_digest должен быть "
            "lowercase SHA-256"
        )
        return report

    if (
        restore_manual_unassigned
        and not preview_only
        and (
            expected_manual_restored is None
            or expected_manual_restore_digest is None
        )
    ):
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: для возврата вручную снятых договоров "
            "обязателен свежий предпросмотр точного множества возвратов"
        )
        return report

    if expected_comment_effect_digest is not None and (
        not isinstance(expected_comment_effect_digest, str)
        or len(expected_comment_effect_digest) != 64
        or any(ch not in "0123456789abcdef"
               for ch in expected_comment_effect_digest)
    ):
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: expected_comment_effect_digest должен быть "
            "lowercase SHA-256"
        )
        return report

    for _name, _count_value, _digest_value in (
        ("recalled", expected_recalled, expected_recalled_digest),
        ("recall_restored", expected_recall_restored,
         expected_recall_restored_digest),
    ):
        if _count_value is not None and (
            isinstance(_count_value, bool)
            or not isinstance(_count_value, int)
            or _count_value < 0
        ):
            report["blocked"] = True
            report["error"] = (f"импорт заблокирован: expected_{_name} должен "
                               "быть неотрицательным целым")
            return report
        if _digest_value is not None and (
            not isinstance(_digest_value, str)
            or len(_digest_value) != 64
            or any(ch not in "0123456789abcdef" for ch in _digest_value)
        ):
            report["blocked"] = True
            report["error"] = (f"импорт заблокирован: expected_{_name}_digest "
                               "должен быть lowercase SHA-256")
            return report
    if (
        (replace_current_visit_comments or import_historical_visit_comments)
        and not preview_only
        and expected_comment_effect_digest is None
    ):
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: для изменения комментариев обязателен "
            "свежий предпросмотр точного множества замен"
        )
        return report

    authoritative_by_key: dict[str, str] = {}
    if full_registry:
        if not isinstance(authoritative_employees, list) or not authoritative_employees:
            report["blocked"] = True
            report["error"] = (
                "импорт заблокирован: для полного среза обязателен непустой "
                "список authoritative_employees"
            )
            return report
        for index, raw_employee in enumerate(authoritative_employees):
            if not isinstance(raw_employee, str):
                report["blocked"] = True
                report["error"] = (
                    "импорт заблокирован: authoritative_employees"
                    f"[{index}] должен быть строкой"
                )
                return report
            employee = " ".join(raw_employee.split())
            key = _name_key(employee)
            if not key:
                report["blocked"] = True
                report["error"] = (
                    "импорт заблокирован: authoritative_employees содержит "
                    "пустое имя"
                )
                return report
            authoritative_by_key.setdefault(key, employee)
        report["authoritative_employees"] = list(authoritative_by_key.values())
    elif authoritative_employees not in (None, []):
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: authoritative_employees применим только к "
            "полному срезу"
        )
        return report

    if source_kind not in IMPORT_SOURCE_KINDS:
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: обязателен явный source_kind "
            f"из {sorted(IMPORT_SOURCE_KINDS)}; личные реестры импортировать "
            "в hardbot запрещено"
        )
        return report

    if source_proof is None and not require_source_proof:
        # Trusted in-process fixtures/dev tools have no transport boundary.
        # Production admin HTTP always sets require_source_proof=True.
        verified_source = {"digest": None, "scope": None}
    else:
        try:
            verified_source = validate_import_source_proof(
                source_proof,
                source_kind=source_kind,
                grid=rows,
            )
        except ExchangeContractError as exc:
            report["blocked"] = True
            report["error"] = f"импорт заблокирован: {exc}"
            return report
        report["source_proof_digest"] = verified_source["digest"]

    schema = export_schema()
    title_to_key = {_norm_title(f["title"]): f["key"] for f in schema}
    for alias_key, aliases in WORKBOOK_HEADER_ALIASES.items():
        for alias in aliases:
            normalized = _norm_title(alias)
            prior = title_to_key.get(normalized)
            if prior is not None and prior != alias_key:
                raise ExchangeContractError(
                    "workbook header alias collides after normalization: "
                    f"{alias!r} -> {prior}/{alias_key}"
                )
            title_to_key[normalized] = alias_key
    # Карта колонок оператора ложится ПОВЕРХ контракта. Она не может перебить
    # штатный заголовок чужого поля (это проверено при сохранении), поэтому
    # добавляет только то, чего контракт про этот файл ещё не знает.
    column_map = registry_column_map()
    operator_aliases: list[str] = []
    for alias_key, aliases in column_map["aliases"].items():
        for alias in aliases:
            normalized = _norm_title(alias)
            if not normalized or title_to_key.get(normalized) == alias_key:
                continue
            title_to_key[normalized] = alias_key
            operator_aliases.append(f"{alias} → {BY_KEY[alias_key].title}")
    report["operator_aliases"] = operator_aliases
    if not rows or len(rows) < 2:
        report["blocked"] = True
        report["error"] = "импорт заблокирован: книга не содержит данных и заголовков"
        return report

    # Авто-поиск строки заголовков: реальный файл может иметь иную/сдвинутую шапку.
    # Критерий — распознанная колонка-КЛЮЧ («Номер КД / ID»→contract_id), а не «просто
    # много совпадений»: реестр с кучей лишних/незнакомых колонок, но нормальным ключом,
    # должен импортироваться; несколько опечаток в незначащих заголовках не должны
    # тихо обнулять весь импорт (было: best_n < 3 → return report без объяснений).
    best_h, col_keys, best_n, best_has_id = 0, [], -1, False
    for i, row in enumerate(rows[:WORKBOOK_HEADER_SEARCH_ROWS]):
        keys = [title_to_key.get(_norm_title(c)) for c in row]
        n = sum(1 for k in keys if k)
        has_id = "contract_id" in keys
        if (has_id, n) > (best_has_id, best_n):
            best_has_id, best_n, best_h, col_keys = has_id, n, i, keys
    if not best_has_id:
        # Без колонки-ключа сопоставить строки нечем — не тихий ноль, а понятная причина
        # + что реально было в лучшей из первых 10 строк, чтобы диагностировать заголовок.
        report["blocked"] = True
        report["error"] = (
            f"импорт заблокирован: не нашли колонку-ключ "
            f"«{BY_KEY[WORKBOOK_CONTRACT_KEY].title}» ни в одной из первых "
            f"{WORKBOOK_HEADER_SEARCH_ROWS} строк"
        )
        report["header_row_tried"] = best_h + 1
        report["header_sample"] = [str(c).strip() for c in rows[best_h] if c not in (None, "")][:20]
        return report
    recognized_positions: dict[str, list[int]] = {}
    normalized_title_positions: dict[str, list[int]] = {}
    normalized_title_samples: dict[str, str] = {}
    for column, key in enumerate(col_keys, start=1):
        if key:
            recognized_positions.setdefault(key, []).append(column)
        raw_title = rows[best_h][column - 1]
        if raw_title not in (None, ""):
            normalized_title = _norm_title(raw_title)
            if normalized_title:
                normalized_title_positions.setdefault(
                    normalized_title, []
                ).append(column)
                normalized_title_samples.setdefault(
                    normalized_title, str(raw_title).strip()
                )
    duplicate_headers = {
        key: positions
        for key, positions in recognized_positions.items()
        if len(positions) > 1
    }
    duplicate_titles = {
        normalized_title_samples[title]: positions
        for title, positions in normalized_title_positions.items()
        if len(positions) > 1
    }
    if duplicate_headers or duplicate_titles:
        report["blocked"] = True
        report["duplicate_headers"] = duplicate_headers
        report["duplicate_titles"] = duplicate_titles
        details = [
            f"{key} ({','.join(map(str, columns))})"
            for key, columns in sorted(duplicate_headers.items())
        ]
        details.extend(
            f"{title!r} ({','.join(map(str, columns))})"
            for title, columns in sorted(duplicate_titles.items())
        )
        report["error"] = (
            "импорт заблокирован: заголовок дублируется или один логический "
            "ключ распознан в нескольких колонках: "
            + ", ".join(details)
        )
        return report

    present_keys = {key for key in col_keys if key}
    requested_comment_headers = {
        key
        for key, requested in (
            ("visit_comment", replace_current_visit_comments),
            ("historical_visit_comment", import_historical_visit_comments),
        )
        if requested
    }
    missing_comment_headers = sorted(requested_comment_headers - present_keys)
    if missing_comment_headers:
        report["blocked"] = True
        report["missing_comment_headers"] = missing_comment_headers
        report["error"] = (
            "импорт заблокирован: запрошенная операция с комментариями требует "
            "колонки файла: "
            + ", ".join(BY_KEY[key].title for key in missing_comment_headers)
        )
        return report
    required_keys = (
        {item["key"] for item in schema}
        if source_kind == IMPORT_SOURCE_PUBLISHED_MASTER
        else set(WORKBOOK_TEST_SUBSET_REQUIRED_KEYS)
    )
    missing_headers = sorted(required_keys - present_keys)
    if missing_headers:
        report["blocked"] = True
        report["missing_headers"] = missing_headers
        report["error"] = (
            "импорт заблокирован: отсутствуют обязательные колонки: "
            + ", ".join(BY_KEY[key].title for key in missing_headers)
        )
        return report

    allowed_ignored_titles = {
        _norm_title(title) for title in WORKBOOK_ALLOWED_IGNORED_HEADERS
    }
    # Скрытые оператором колонки — такие же «известные лишние», как в контракте.
    allowed_ignored_titles |= {
        _norm_title(title) for title in column_map["ignored"]
    }
    # Дополнительные колонки: оператор решил их читать.
    extra_by_slot = {
        _extra_slot(item["title"]): item for item in column_map["extras"]
    }
    allowed_ignored_patterns = tuple(
        re.compile(pattern)
        for pattern in WORKBOOK_ALLOWED_IGNORED_HEADER_PATTERNS
    )
    ignored_columns: list[str] = []
    unknown_columns: list[str] = []
    extra_columns: list[str] = []
    extra_cols: dict[int, dict] = {}
    for index, (cell, key) in enumerate(zip(rows[best_h], col_keys)):
        if cell in (None, "") or key is not None:
            continue
        title = str(cell).strip()
        if not title:
            continue
        extra = extra_by_slot.get(_extra_slot(title))
        if extra is not None:
            extra_cols[index] = extra
            extra_columns.append(extra["label"])
        elif (
            _norm_title(title) in allowed_ignored_titles
            or any(pattern.fullmatch(title) for pattern in allowed_ignored_patterns)
        ):
            ignored_columns.append(title)
        else:
            unknown_columns.append(title)
    report["ignored_columns"] = ignored_columns
    report["unknown_columns"] = unknown_columns
    report["extra_columns"] = extra_columns
    if report["unknown_columns"]:
        # ПРЕДУПРЕЖДЕНИЕ, а не отказ (03.08.2026, решение владельца). Раньше одна
        # незнакомая колонка отбивала весь файл: в боевом реестре 88+ колонок,
        # офис добавляет свои, и импорт на 52 тысячи строк падал из-за заголовка,
        # который боту вообще не нужен. Колонки по-прежнему не читаются — они
        # просто не мешают, и их видно в отчёте.
        report["warnings"] = report.get("warnings", []) + [
            "незнакомые заголовки пропущены — свяжи их с полем или включи как "
            "дополнительные на экране «Колонки реестра»: "
            + ", ".join(report["unknown_columns"][:20])
        ]

    # У production master сразу после заголовка находится строка профилей
    # («сх» / «хард» / «софт»). Это часть формы книги, а не клиент. Раньше
    # эвристика знала только два тега и реальная строка с «софт» превращалась
    # в клиента contract_id='сх'. Теперь строка распознаётся только в
    # контрактной позиции и сверяется по каждой колонке до любых записей.
    type_row_index = best_h + WORKBOOK_TYPE_ROW_OFFSET
    type_row = rows[type_row_index] if type_row_index < len(rows) else []
    allowed_type_tags = {
        _norm_title(tag) for tag in WORKBOOK_TYPE_ROW_TAGS
    }
    contract_column = col_keys.index(WORKBOOK_CONTRACT_KEY)
    contract_type_tag = (
        _norm_title(type_row[contract_column])
        if contract_column < len(type_row)
        else ""
    )
    type_row_claimed = contract_type_tag in allowed_type_tags
    if (
        source_kind == IMPORT_SOURCE_PUBLISHED_MASTER
        and not type_row_claimed
    ):
        report["blocked"] = True
        report["type_row"] = {
            "row": type_row_index + 1,
            "status": "missing_or_malformed",
        }
        report["error"] = (
            "импорт заблокирован: опубликованный master не содержит "
            f"контрактную строку типов сразу после заголовка "
            f"(ожидалась строка {type_row_index + 1})"
        )
        return report

    type_row_to_skip: int | None = None
    if type_row_claimed:
        schema_by_key = {item["key"]: item for item in schema}
        allowed_blank_keys = set(WORKBOOK_TYPE_ROW_ALLOWED_BLANK_KEYS)
        type_mismatches: list[dict[str, object]] = []
        for column, (header_cell, key) in enumerate(
            zip(rows[best_h], col_keys),
            start=1,
        ):
            raw_tag = (
                type_row[column - 1]
                if column - 1 < len(type_row)
                else None
            )
            actual_tag = _norm_title(raw_tag)
            if key is not None:
                expected_tag = _norm_title(schema_by_key[key]["source"])
                if (
                    actual_tag == expected_tag
                    or (not actual_tag and key in allowed_blank_keys)
                ):
                    continue
                type_mismatches.append(
                    {
                        "column": column,
                        "key": key,
                        "actual": str(raw_tag or "").strip(),
                        "expected": schema_by_key[key]["source"],
                    }
                )
                continue
            if not _norm_title(header_cell):
                if actual_tag:
                    type_mismatches.append(
                        {
                            "column": column,
                            "key": None,
                            "actual": str(raw_tag or "").strip(),
                            "expected": "empty (header is empty)",
                        }
                    )
                continue
            if actual_tag and actual_tag not in allowed_type_tags:
                type_mismatches.append(
                    {
                        "column": column,
                        "key": None,
                        "actual": str(raw_tag or "").strip(),
                        "expected": sorted(WORKBOOK_TYPE_ROW_TAGS),
                    }
                )
        if type_mismatches:
            report["blocked"] = True
            report["type_row"] = {
                "row": type_row_index + 1,
                "status": "mismatch",
                "mismatches": type_mismatches[:20],
            }
            report["error"] = (
                "импорт заблокирован: строка типов workbook не совпадает "
                "с versioned контрактом"
            )
            return report
        type_row_to_skip = type_row_index
        report["type_row"] = {
            "row": type_row_index + 1,
            "status": "validated",
        }
    else:
        report["type_row"] = {"row": None, "status": "absent"}

    # Колонки разбивки, которые реально есть в этом файле → пустая ячейка = ноль.
    owned = debt.SOURCE_OWNED.intersection(k for k in col_keys if k)

    # Колонки «только для скоринга»: контракт разрешает их игнорировать, а мы
    # читаем значения, не заводя их полями реестра. Экспортная схема, её отпечаток
    # и строка типов при этом не меняются вообще.
    scoring_cols: dict[int, str] = {}
    recall_cols: dict[int, str] = {}
    paid_month_cols: list[int] = []
    for index, (cell, key) in enumerate(zip(rows[best_h], col_keys)):
        if key is not None or cell in (None, ""):
            continue
        title = str(cell).strip()
        mapped = _scoring_only_headers().get(_norm_title(title))
        if mapped:
            scoring_cols[index] = mapped
            continue
        mapped = _recall_headers().get(_norm_title(title))
        if mapped:
            recall_cols[index] = mapped
            continue
        if _PAID_MONTH_RX.fullmatch(title):
            paid_month_cols.append(index)
    # Колонки отзыва, которые РЕАЛЬНО есть в этой книге. Пустая ячейка такой
    # колонки — «отзыва нет», отсутствие колонки — «не знаем».
    recall_owned = set(recall_cols.values())
    report["recall_columns"] = [RECALL_TITLE_BY_KEY[k] for k in RECALL_KEYS
                                if k in recall_owned]
    # Ключи РЕЕСТРА, реально присутствующие в шапке этой книги. Общий признак
    # «колонка есть» для всех политик, различающих пустую ячейку и отсутствие
    # колонки (отзыв здесь; снятие обещания — якорный шлюз, вер. контракта 3).
    book_keys = {k for k in col_keys if k}

    # Сначала разобрать ВЕСЬ файл в памяти. Это обязательный preflight: при
    # повторе КД не должна успеть записаться ни одна предыдущая строка.
    grouped: dict[str, dict] = {}
    extra_values: dict[str, dict[str, str]] = {}
    duplicate_conflicts: dict[str, set[str]] = {}
    duplicate_rows: dict[str, list[int]] = {}
    first_rows: dict[str, int] = {}
    for offset, raw in enumerate(rows[best_h + 1:], start=best_h + 2):
        if (
            type_row_to_skip is not None
            and offset == type_row_to_skip + 1
        ):
            continue
        if raw is None or all(v is None for v in raw):
            continue
        report["total_rows"] += 1
        data = {}
        exact_comment_keys = requested_comment_headers
        for index, key in enumerate(col_keys):
            val = raw[index] if index < len(raw) else None
            if key in exact_comment_keys:
                # Для явной месячной операции пустая ячейка — полноценное
                # значение «очистить», в том числе если строка короче шапки.
                data[key] = ("" if val is None else str(val).strip())
            elif key and val is not None:
                if key == "contract_id":
                    canonical = _canonical_contract_id(val)
                    if canonical:
                        data[key] = canonical
                elif isinstance(val, datetime):
                    data[key] = val.strftime("%Y-%m-%d")
                elif isinstance(val, date):
                    data[key] = val.isoformat()
                elif key in DATE_KEYS and isinstance(val, (int, float)) \
                        and not isinstance(val, bool):
                    # Ячейка, потерявшая в Excel формат даты, приезжает числом —
                    # серийной датой (46204 = 21.07.2026). Раньше она сохранялась
                    # как текст «46204», и договор с такой «Датой выезда» выпадал
                    # из фильтров по дате: визит вроде есть, а в отчётах его нет.
                    data[key] = _excel_serial_date(val) or str(val).strip()
                elif key in FORM_DATE_KEYS:
                    text = str(val).strip()
                    if text:
                        data[key] = canon_iso_date(text) or text
                else:
                    text = str(val).strip()
                    if text:
                        data[key] = text
        for index, target in scoring_cols.items():
            val = raw[index] if index < len(raw) else None
            if val is None:
                continue
            if target == "country":
                text = str(val).strip()
                if text:
                    data[target] = text
            else:
                number = _money_int(val)
                if number:
                    data[target] = number
        for index, target in recall_cols.items():
            val = raw[index] if index < len(raw) else None
            text, is_set = _recall_cell(val, is_date=(target == "recall_date"))
            if is_set:
                data[target] = text
            elif val is not None and str(val).strip():
                # непусто, но отзывом не признано: без счётчика провал
                # угадывания формата неотличим от «офис ничего не отзывал»
                report["recalled_unrecognized"] += 1
                if len(report["recalled_unrecognized_sample"]) < 20:
                    report["recalled_unrecognized_sample"].append({
                        "contract_id": str(data.get("contract_id") or ""),
                        "column": RECALL_TITLE_BY_KEY.get(target, target),
                        "value": str(val)[:80]})
        if paid_month_cols:
            # Пустой месяц — это «не платил», а не «нет данных»: колонки помесячных
            # оплат в реестре есть всегда, поэтому ноль здесь осмысленный.
            data["paid_months"] = sum(
                1 for index in paid_month_cols
                if index < len(raw) and _money_int(raw[index]) > 0)
        cid = data.get("contract_id")
        if not cid:
            report["skipped_no_id"] += 1
            continue
        if extra_cols:
            # Дополнительные поля сохраняем как ТЕКСТ, как он стоит в файле:
            # смысл новой колонки боту неизвестен, и любое «умное» приведение
            # типа здесь означало бы догадку о чужих данных.
            slot_values = extra_values.setdefault(cid, {})
            for index, item in extra_cols.items():
                val = raw[index] if index < len(raw) else None
                if val is None:
                    continue
                if isinstance(val, datetime):
                    text = val.strftime("%Y-%m-%d")
                elif isinstance(val, date):
                    text = val.isoformat()
                elif isinstance(val, float) and val.is_integer():
                    text = str(int(val))
                else:
                    text = str(val).strip()
                if text:
                    slot_values.setdefault(_extra_slot(item["title"]),
                                           text[:MAX_EXTRA_VALUE_LEN])
        if cid not in grouped:
            grouped[cid] = data
            first_rows[cid] = offset
            continue
        report["duplicates"] += 1
        duplicate_rows.setdefault(cid, [first_rows[cid]]).append(offset)
        first = grouped[cid]
        for key, value in data.items():
            if key in first and first[key] != value:
                duplicate_conflicts.setdefault(cid, set()).add(key)
            # Отзыв липкий по договору: берётся ПЕРВАЯ строка КД, и повтор без
            # отзыва молча отменял бы решение офиса, стоящее во второй строке.
            if (key in RECALL_KEYS and _recall_is_set(value)
                    and not _recall_is_set(first.get(key))):
                first[key] = value

    if duplicate_rows:
        # ПРЕДУПРЕЖДЕНИЕ, а не отказ (03.08.2026, решение владельца). Повторы
        # канонического КД в боевом реестре встречаются, и отказ всего файла из-за
        # них означал «импорта не будет вообще». Слияния как не было, так и нет:
        # берём ПЕРВУЮ строку договора (она уже в `grouped`), остальные не трогаем
        # и перечисляем поимённо — вместе с полями, где они разошлись.
        report["duplicate_conflicts"] = [
            {
                "contract_id": cid,
                "rows": duplicate_rows[cid],
                "fields": sorted(duplicate_conflicts.get(cid, set())),
            }
            for cid in sorted(duplicate_rows)
        ]
        report["quarantined_contracts"] = sorted(duplicate_rows)
        conflicting = sorted(duplicate_conflicts)
        report["warnings"] = report.get("warnings", []) + [
            f"повторы канонического КД: {len(duplicate_rows)} договоров, взята "
            f"первая строка каждого"
            + (f"; расходятся значения у {len(conflicting)} из них: "
               + ", ".join(conflicting[:10]) if conflicting else "")
        ]

    if (
        source_kind == IMPORT_SOURCE_TEST_SUBSET
        and verified_source["scope"] is not None
    ):
        raw_scope = verified_source["scope"] or {}
        expected_scope: dict[str, str] = {}
        for raw_contract_id, responsible in raw_scope.items():
            contract_id = _canonical_contract_id(raw_contract_id)
            if not contract_id or contract_id in expected_scope:
                report["blocked"] = True
                report["error"] = (
                    "импорт заблокирован: test-subset proof имеет неоднозначные "
                    "канонические номера КД"
                )
                return report
            expected_scope[contract_id] = responsible
        actual_scope = {
            contract_id: str(data.get(WORKBOOK_RESPONSIBLE_KEY) or "").strip()
            for contract_id, data in grouped.items()
        }
        if actual_scope != expected_scope:
            missing = sorted(set(expected_scope) - set(actual_scope))
            extra = sorted(set(actual_scope) - set(expected_scope))
            owner_mismatches = sorted(
                contract_id
                for contract_id in set(actual_scope) & set(expected_scope)
                if actual_scope[contract_id] != expected_scope[contract_id]
            )
            report["blocked"] = True
            report["scope_mismatch"] = {
                "missing_contract_ids": missing,
                "extra_contract_ids": extra,
                "owner_mismatch_contract_ids": owner_mismatches,
            }
            report["error"] = (
                "импорт заблокирован: строки/ответственные test-subset не "
                "совпадают с exact утверждённым scope"
            )
            return report

    if full_registry:
        outside_scope = sorted({
            str(data.get(WORKBOOK_RESPONSIBLE_KEY) or "").strip()
            for data in grouped.values()
            if str(data.get(WORKBOOK_RESPONSIBLE_KEY) or "").strip()
            and _name_key(data.get(WORKBOOK_RESPONSIBLE_KEY))
            not in authoritative_by_key
        })
        if outside_scope:
            report["blocked"] = True
            report["outside_authoritative_scope"] = outside_scope
            report["error"] = (
                "импорт заблокирован: во входе есть ответственные вне явно "
                "выбранного полного среза: " + ", ".join(outside_scope[:20])
            )
            return report

    # Для SOURCE_OWNED пустота во всём наборе строк КД означает погашение.
    # Колонки, отсутствующей в файле целиком, в ``owned`` нет и мы её не трогаем.
    for data in grouped.values():
        for key in owned - data.keys():
            data[key] = None

    with connect() as conn:
        if require_source_proof:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = _authorized_import_state(
                    conn,
                    verified_source,
                    source_kind=source_kind,
                )
            except ExchangeContractError as exc:
                conn.rollback()
                report["blocked"] = True
                report["error"] = f"импорт заблокирован: {exc}"
                return report
            if replay is not None:
                conn.rollback()
                return replay
        # address/phone нужны person_key: без даты рождения человек различается
        # именно по ним, иначе однофамильцы склеятся (см. person_key).
        existing = {r["contract_id"]: r for r in conn.execute(
            "SELECT contract_id, client, birth_date, responsible, address, phone, "
            "visit_comment, historical_visit_comment, "
            "unassigned_from, unassigned_reason, recall_prev_reason, "
            "recall_date, recall_reason, recall_transfer, recall_withdrawn, "
            "bot_visit_at, bot_visit_data, office_edit_data, visit_value_log "
            "FROM clients")}
        # Визит — на человека: принятая правка разносится по всем его договорам
        # с тем же визитом, иначе карточка/дайджест (первый КД человека) её не
        # увидят, а файлы разъедутся построчно.
        person_map: dict[tuple, list[str]] = {}
        for r in existing.values():
            if r["bot_visit_data"]:
                # Ключ тот же, что и везде: без ДР человек = один договор,
                # иначе офисная правка разъедется по однофамильцам (см. person_key).
                person_map.setdefault(
                    person_key(r) + (r["responsible"] or "",),
                    []).append(r["contract_id"])
        # Договоры одного человека: накапливаем принятые правки/журнал, чтобы
        # следующий sibling видел свежее состояние (row0 из префетча стар).
        fresh_overlays: dict[str, dict] = {}
        fresh_logs: dict[str, dict] = {}
        office_cids: set[str] = set()
        protected_cids: set[str] = set()
        isolated_call_cids: set[str] = set()
        comment_effect_rows: list[dict[str, str]] = []
        manual_restore_effect: list[tuple[str, str]] = []
        # Отзыв: (cid, у кого забрали, был ли живой владелец). Эффект считается
        # на ПЕРЕХОДЕ: повторный импорт того же файла не переобъявляет снятия.
        recall_effect: list[tuple[str, str, bool]] = []
        recall_restore_effect: list[tuple[str, str]] = []
        recall_sample: list[dict] = []
        # Владелец каждого уникального КД после duplicate preflight.
        owners: dict[str, str] = {}
        # Как ответственные УЖЕ записаны в базе — чтобы опечатка регистра в файле
        # не заводила двойника сотрудника с собственным портфелем.
        known_resp: dict[str, str] = {}
        for r in existing.values():
            nm = (r["responsible"] or "").strip()
            if nm:
                known_resp.setdefault(_name_key(nm), nm)
        if full_registry:
            report["authoritative_employees"] = [
                known_resp.get(key, employee)
                for key, employee in authoritative_by_key.items()
            ]
        for cid, data in grouped.items():
            is_update = cid in existing
            row0 = existing.get(cid)
            comment_updates: dict[str, str] = {}
            office_echo_values = {
                key: data.get(key)
                for key in OFFICE_ECHO_ONLY_KEYS
            }

            if replace_current_visit_comments:
                incoming_current = str(data.pop("visit_comment", "") or "").strip()
                previous_current = (
                    str(row0["visit_comment"] or "").strip()
                    if row0 is not None else ""
                )
                comment_updates["visit_comment"] = incoming_current
                if previous_current == incoming_current:
                    report["current_comments_unchanged"] += 1
                else:
                    key = (
                        "current_comments_cleared"
                        if not incoming_current
                        else "current_comments_replaced"
                    )
                    report[key] += 1
                    comment_effect_rows.append({
                        "contract_id": cid,
                        "field": "visit_comment",
                        "before": previous_current,
                        "after": incoming_current,
                    })
            elif row0 is not None:
                # Обычный импорт никогда не подменяет совместный текст текущего
                # месяца. Для новой строки непустой текст файла можно принять:
                # сохранять в БД там ещё нечего.
                if "visit_comment" in present_keys:
                    report["current_comments_preserved"] += 1
                data.pop("visit_comment", None)

            if import_historical_visit_comments:
                incoming_historical = str(
                    data.pop("historical_visit_comment", "") or ""
                ).strip()
                previous_historical = (
                    str(row0["historical_visit_comment"] or "").strip()
                    if row0 is not None else ""
                )
                comment_updates[
                    "historical_visit_comment"
                ] = incoming_historical
                if previous_historical == incoming_historical:
                    report["historical_comments_unchanged"] += 1
                else:
                    key = (
                        "historical_comments_cleared"
                        if not incoming_historical
                        else "historical_comments_imported"
                    )
                    report[key] += 1
                    comment_effect_rows.append({
                        "contract_id": cid,
                        "field": "historical_visit_comment",
                        "before": previous_historical,
                        "after": incoming_historical,
                    })
            else:
                if row0 is not None and "historical_visit_comment" in present_keys:
                    report["historical_comments_preserved"] += 1
                # Историчный текст по природе односторонний и не возникает в БД
                # даже у новой строки без отдельного решения аналитика.
                data.pop("historical_visit_comment", None)

            # Пустой владелец = кейс в базе есть, а у выездника его нет.
            prev_resp = existing[cid]["responsible"] if cid in existing else ""
            # Ручное снятие держится, пока в реестре стоит ТОТ ЖЕ ответственный:
            # значит офис ничего не менял. Появилось другое имя — это законное
            # переназначение. Автоснятие имеет обратную политику: сама вернувшаяся
            # строка означает, что офис вернул договор, даже тому же сотруднику.
            was_unassigned = (existing[cid]["unassigned_from"]
                              if cid in existing else None) or ""
            unassigned_reason = (existing[cid]["unassigned_reason"]
                                 if cid in existing else None) or ""
            if was_unassigned and not unassigned_reason:
                # Совместимость с БД, которую открыли без предварительного init_db.
                unassigned_reason = UNASSIGNED_REASON_MANUAL
            incoming_resp = canonical_responsible(
                str(data.get("responsible") or "").strip(), known_resp)
            if incoming_resp:
                data["responsible"] = incoming_resp
                known_resp.setdefault(_name_key(incoming_resp), incoming_resp)
            # --- Отзыв реестром ------------------------------------------
            # Положительное утверждение самой строки: работает и на частичной
            # выборке, в отличие от автоснятия по отсутствию строки. Приоритет
            # выше ВСЕХ прочих веток — строка с отзывом почти всегда ещё несёт
            # ФИО ответственного, и ветка «переназначили» вернула бы договор
            # на следующем же импорте.
            row_recalled = any(_recall_is_set(data.get(k)) for k in recall_owned)
            db_recall_set = {k for k in RECALL_KEYS
                             if row0 is not None and _recall_is_set(row0[k])}
            # Возврат только когда книга ВИДИТ всё, что БД считает отзывом.
            recall_cleared = bool(recall_owned and not row_recalled
                                  and db_recall_set <= recall_owned)
            detached = False
            if row_recalled:
                taken_from = (was_unassigned or prev_resp
                              or incoming_resp or "").strip()
                had_owner = bool((prev_resp or "").strip())
                data["responsible"] = ""
                data["unassigned_from"] = taken_from
                data["unassigned_reason"] = UNASSIGNED_REASON_RECALLED
                # Ручное снятие оператора переживает цикл отзыва: запоминаем
                # прежнюю причину, чтобы после очистки колонок вернуть договор
                # не в работу, а в ручное снятие.
                if unassigned_reason == UNASSIGNED_REASON_MANUAL and not (
                        (row0["recall_prev_reason"] if row0 is not None else "") or ""):
                    data["recall_prev_reason"] = UNASSIGNED_REASON_MANUAL
                if (unassigned_reason != UNASSIGNED_REASON_RECALLED
                        or had_owner):
                    recall_effect.append((cid, taken_from, had_owner))
                    if unassigned_reason and not had_owner:
                        report["recalled_previously_unassigned"] += 1
                    if not had_owner and not unassigned_reason:
                        report["recalled_without_owner"] += 1
                    if len(recall_sample) < 100:
                        recall_sample.append({
                            "contract_id": cid,
                            "client": str(data.get("client")
                                          or (row0["client"] if row0 is not None
                                              else "") or ""),
                            "employee": taken_from,
                            "recall_date": str(data.get("recall_date") or ""),
                            "recall_reason": str(data.get("recall_reason") or ""),
                        })
                else:
                    # переход не случился — та же книга держит тот же отзыв
                    report["recall_held"] += 1
                raw_day = data.get("recall_date")
                if raw_day:
                    parsed_day = _as_date(raw_day)
                    if parsed_day is None:
                        report["recalled_unparsed_date"] += 1
                    elif parsed_day > date.today():
                        report["recalled_future_dated"] += 1
                for key in recall_owned - data.keys():
                    data[key] = None
                incoming_resp = ""
                detached = True
            elif unassigned_reason == UNASSIGNED_REASON_RECALLED and recall_cleared:
                # Офис очистил колонки отзыва. Прежнее РУЧНОЕ снятие не
                # воскрешает договор в работу — возвращаем в ручное снятие.
                prev_manual = ((row0["recall_prev_reason"]
                                if row0 is not None else "") or "") \
                    == UNASSIGNED_REASON_MANUAL
                back_to = (incoming_resp or was_unassigned or "").strip()
                if prev_manual:
                    data["responsible"] = ""
                    data["unassigned_from"] = was_unassigned or prev_resp or ""
                    data["unassigned_reason"] = UNASSIGNED_REASON_MANUAL
                    data["recall_prev_reason"] = None
                    incoming_resp = ""
                    detached = True
                    report["recall_restored_to_manual"] += 1
                else:
                    data["responsible"] = back_to
                    data["unassigned_from"] = ""
                    data["unassigned_reason"] = ""
                    data["recall_prev_reason"] = None
                    incoming_resp = back_to
                    if back_to:
                        known_resp.setdefault(_name_key(back_to), back_to)
                        recall_restore_effect.append((cid, back_to))
                    else:
                        # «возврат в никуда»: метку снимаем (иначе каждая та же
                        # книга заново объявляла бы возврат), но в эффект не
                        # кладём — договор не достался никому.
                        report["recall_restored_without_owner"] += 1
                for key in recall_owned - data.keys():
                    data[key] = None
            elif unassigned_reason == UNASSIGNED_REASON_RECALLED:
                # Отзыв держится: колонок отзыва в этой книге нет или книга не
                # видит всего, что БД считает отзывом. Непустое ФИО договор НЕ
                # возвращает — иначе отзыв отменялся бы каждой загрузкой.
                data["responsible"] = ""
                data["unassigned_from"] = was_unassigned or prev_resp or ""
                data["unassigned_reason"] = UNASSIGNED_REASON_RECALLED
                # Пока отзыв держится, колонки отзыва в БД не переписываются:
                # книга, где офис переименовал одну из четырёх колонок, иначе
                # стирала бы дату отзыва, поставленную офисом.
                for key in list(recall_owned):
                    data.pop(key, None)
                incoming_resp = ""
                report["recall_held"] += 1
                detached = True
            elif unassigned_reason == UNASSIGNED_REASON_ABSENT_FROM_REGISTRY:
                data["unassigned_from"] = ""
                data["unassigned_reason"] = ""
                for key in recall_owned - data.keys():
                    data[key] = None
            elif (
                unassigned_reason == UNASSIGNED_REASON_MANUAL
                and was_unassigned
                and _name_key(incoming_resp) == _name_key(was_unassigned)
            ):
                manual_restore_effect.append((cid, was_unassigned))
                if restore_manual_unassigned:
                    incoming_resp = was_unassigned
                    data["responsible"] = was_unassigned
                    data["unassigned_from"] = ""
                    data["unassigned_reason"] = ""
                else:
                    data["responsible"] = ""
                    incoming_resp = ""
                for key in recall_owned - data.keys():
                    data[key] = None
            else:
                if was_unassigned and incoming_resp:
                    data["unassigned_from"] = ""  # переназначили — метку снимаем
                    data["unassigned_reason"] = ""
                # Колонка отзыва в книге есть, ячейка пуста — это ответ
                # «отзыва нет», а не «данных нет»: держим БД актуальной.
                for key in recall_owned - data.keys():
                    data[key] = None
            owners[cid] = "" if detached \
                else str(incoming_resp or prev_resp or "").strip()
            # После подтверждённого визита очередной импорт может обновлять долг,
            # адрес и прочие поля источника, а поля визита — только через якорный
            # шлюз (см. докстринг): совпавшая «Дата выезда» доказывает, что файл
            # видел результат бота, и отличия — офисная правка, а не старая копия.
            if row0 is not None and row0["bot_visit_data"]:
                merged = _merged_visit(row0)
                if merged:
                    merged.update(fresh_overlays.get(cid) or {})
                known = fresh_logs.get(cid) or _decode_value_log(row0)
                anchor_value = data.pop("visit_date", None)
                # ЯКОРЬ — И ЕСТЬ ДОКАЗАТЕЛЬСТВО. Совпавшая «Дата выезда»
                # означает, что файл видел результат бота, а значит отличия в
                # нём — осознанная правка офиса, а не старая копия.
                #
                # Требование `source_kind == published_master` отсюда снято
                # 05.08.2026. Оно защищало не от порчи данных (от неё защищает
                # якорь), а от «неофициального файла» — и на практике не
                # открывалось никогда: реестр грузят из пульта, метка всегда
                # test_subset, и потому за всё время в базу не попало НИ ОДНОЙ
                # офисной правки (office_cells = 0 во всех расписках). Офис
                # правил «Общий результат» в книгах, бот об этом не узнавал, и
                # очередное обогащение возвращало туда своё значение — так
                # пропали правки по 28 договорам Уткирова за 03.08.
                #
                # Замок, который не открывается никогда, — это стена.
                anchor_ok = (
                             merged is not None
                             and _as_date(anchor_value) is not None
                             and _as_date(anchor_value)
                             == _as_date(merged.get("visit_date")))
                office: dict = {}
                rejected = 0
                # Call-only колонки master не являются результатом визита. Для
                # завершённого визита они не попадают ни в raw upsert, ни в overlay.
                for call_key in OFFICE_CALL_FIELDS:
                    sval = str(data.pop(call_key, None) or "").strip()
                    if sval:
                        report["isolated_call_cells"] += 1
                        isolated_call_cids.add(cid)

                candidates = {}
                for key in OFFICE_DIRECT_EDIT_KEYS:
                    # Промис-колонки офис ведёт и ДО визита (софт-линия): пока
                    # бот по ключу не высказался, значение файла идёт обычным
                    # upsert'ом — office_cells/protected_cells не растут, и
                    # киллер-фильтр маршрута продолжает кормиться офисными
                    # обещаниями. Защищать нечего — нечего и гейтить.
                    if key in FORM_TYPED_KEYS and key not in (merged or {}):
                        continue
                    candidates[key] = data.pop(key, None)
                # Для завершённого визита «Общий результат» тоже защищён якорем:
                # до успешной проверки убираем его из обычного master-upsert.
                candidates[OFFICE_RESULT_SNAPSHOT_KEY] = data.pop(
                    OFFICE_RESULT_SOURCE_KEY,
                    None,
                )
                # ЭХО — вторая половина якоря для РЕЗУЛЬТАТА визита.
                #
                # Одной даты мало: «Дата выезда» это поле HARD, его печатает
                # руками выездник или офис. Файл с совпавшей датой мог никогда
                # не видеть результата бота — например старая копия реестра, где
                # дата уже проставлена, а «Общий результат» ещё довизитный
                # («В работе», «Не обработан»). Таких значений нет в
                # visit_value_log, потому что их породил не бот, и шлюз принял бы
                # их за осознанную правку офиса: доказанный визит откатывается, а
                # следующее обогащение разносит откат по живым книгам.
                #
                # Требование узкое и только к результату — именно он едет в
                # книги, в дайджест и в отчёты, и именно у него есть довизитные
                # значения по умолчанию. Комментарий или «кто был на адресе»
                # такой цены не имеют, и для них правило прежнее.
                #
                # Эхо доказывает «эта строка видела ЭТОТ визит»: правка офиса
                # меняет одну ячейку уже обогащённой строки, остальные поля
                # остаются эхом снимка. Довизитная строка эха не даёт — там, где
                # бот ещё не был, эти поля пусты.
                echo_hits = 0
                for key, raw_value in office_echo_values.items():
                    sval = str(raw_value or "").strip()
                    if sval and sval == str(merged.get(key) or ""):
                        echo_hits += 1
                for key, raw_value in candidates.items():
                    if key not in _ECHO_EVIDENCE_KEYS:
                        continue    # эхо доказывают только поля, которые ввёл
                                    # ВЫЕЗДНИК (см. комментарий выше): у промис-
                                    # колонок и у выведенного ботом прогноза
                                    # довизитная копия совпадёт сама собой
                    sval = str(raw_value or "").strip()
                    if not sval:
                        continue
                    option_key = (
                        OFFICE_RESULT_SOURCE_KEY
                        if key == OFFICE_RESULT_SNAPSHOT_KEY
                        else key
                    )
                    if (
                        option_key in BOT_DROPDOWN_KEYS
                        or option_key == OFFICE_RESULT_SOURCE_KEY
                    ):
                        canon = _canon_option(option_key, sval)
                        if canon is None:
                            continue
                        sval = canon
                    if sval == str(merged.get(key) or ""):
                        echo_hits += 1
                for key, raw_value in candidates.items():
                    sval = str(raw_value or "").strip()
                    if (not sval and key in FORM_TYPED_KEYS
                            and str(merged.get(key) or "")
                            and anchor_ok and echo_hits and key in book_keys):
                        # Пустая ячейка промис-ключа при доказательстве той же
                        # силы, что у результата («Дата выезда» + эхо другого
                        # поля визита, И КОЛОНКА В КНИГЕ ЕСТЬ — book_keys
                        # отличает «очистил человек» от «в книге нет колонки»):
                        # офис СНИМАЕТ обещание. Без этой ветки вернуть
                        # должника в маршрут не мог никто, кроме того
                        # выездника, из чьего маршрута он уже исчез.
                        office[key] = ""
                        continue
                    if not sval:
                        continue                # пустота никогда не затирает
                    if key in FORM_TYPED_KEYS:
                        canon2 = canon_form_value(key, sval)
                        if canon2 is None:
                            # «после 20-го», «≈500000»: бот обещание давал, офис
                            # написал не-дату/не-число. Значение бота остаётся,
                            # но protected_cells врал бы оператору — отдельный
                            # счётчик.
                            report["office_promise_unparsed"] += 1
                            continue
                        sval = canon2
                    if not anchor_ok:
                        # Старая копия или мусорная дата: строка не доказала,
                        # что видела результат бота.
                        rejected += 1
                        continue
                    option_key = (
                        OFFICE_RESULT_SOURCE_KEY
                        if key == OFFICE_RESULT_SNAPSHOT_KEY
                        else key
                    )
                    if (
                        option_key in BOT_DROPDOWN_KEYS
                        or option_key == OFFICE_RESULT_SOURCE_KEY
                    ):
                        canon = _canon_option(option_key, sval)
                        if canon is None:
                            rejected += 1       # мусор вроде CONSUMER не проходит
                            continue
                        sval = canon
                    if sval == str(merged.get(key) or ""):
                        if key == OFFICE_RESULT_SNAPSHOT_KEY:
                            data[OFFICE_RESULT_SOURCE_KEY] = sval
                        continue                # эхо текущего состояния
                    if sval in known.get(key, ()):
                        continue                # эхо ПРЕЖНЕГО снимка/правки: файл
                                                # обогащён между визитами — это не
                                                # человек, откатывать нечего
                    if (key == OFFICE_RESULT_SNAPSHOT_KEY
                            and sval == UNPROCESSED_RESULT):
                        # «Не обработан» — довизитный дефолт реестра, а не
                        # решение офиса. Обогащение заполняет строке поля визита,
                        # но результат мог не приехать — и тогда у строки есть
                        # эхо по остальным полям, проверка echo_hits ниже её
                        # пропускает, и пустышка ложится в office_edit_data
                        # ПОВЕРХ доказанного визита. Дальше _merged_visit отдаёт
                        # её всем — карточке, дайджесту, обогащению, полной
                        # выгрузке, — и обогащение разносит «Не обработан» по
                        # живым книгам поверх настоящего результата выездника.
                        # 28.08.2026 так затёрло 107 статусов у трёх сотрудников;
                        # всего в базе накопилось 210 таких строк. Офис снимает
                        # результат осмысленным значением, а не пустышкой.
                        rejected += 1
                        report["office_result_unprocessed"] += 1
                        continue
                    if key == OFFICE_RESULT_SNAPSHOT_KEY and not echo_hits:
                        # Дошли сюда — значит это настоящее изменение результата:
                        # не эхо текущего снимка и не эхо прежнего. Но строка ни
                        # одним полем визита не подтвердила, что видела ЭТОТ
                        # визит, а «Дата выезда» могла быть проставлена руками.
                        # Так довизитное «В работе» из старой копии реестра
                        # откатывало доказанный выезд, и следующее обогащение
                        # разносило откат по живым книгам.
                        rejected += 1
                        continue
                    if key == OFFICE_RESULT_SNAPSHOT_KEY:
                        data[OFFICE_RESULT_SOURCE_KEY] = sval
                    office[key] = sval
                for okey in office:
                    report["office_by_key"][okey] = \
                        report["office_by_key"].get(okey, 0) + 1
                if anchor_value not in (None, "") and not anchor_ok:
                    rejected += 1
                if office:
                    pkey = person_key(row0) + (row0["responsible"] or "",)
                    sibs = [c for c in person_map.get(pkey, [])
                            if existing[c]["bot_visit_at"] == row0["bot_visit_at"]]
                    # У файла со scope правка не должна выходить за его пределы.
                    # person_map собран по ВСЕЙ таблице, поэтому правка из
                    # выборки на одного сотрудника разлеталась на договоры того
                    # же человека, которых в файле нет вовсе — а расписка
                    # считала их обычными office_cells, и увидеть это было
                    # неоткуда.
                    if verified_source["scope"] is not None:
                        sibs = [c for c in sibs if c in grouped]
                    if cid not in sibs:
                        sibs.append(cid)
                    at = date.today().isoformat()
                    for sc in sibs:
                        base = existing[sc]
                        ov = {**(fresh_overlays.get(sc)
                                 or _decode_office_edits(base)), **office}
                        lg = _log_append(fresh_logs.get(sc)
                                         or _decode_value_log(base), office)
                        fresh_overlays[sc], fresh_logs[sc] = ov, lg
                        sets = ", ".join(f'"{k}"=?' for k in office)
                        conn.execute(
                            f'UPDATE clients SET {sets}, office_edit_data=?, '
                            f'visit_value_log=? WHERE contract_id=?',
                            (*office.values(),
                             json.dumps({"at": at,
                                         "visit_at": base["bot_visit_at"],
                                         "fields": ov}, ensure_ascii=False,
                                        separators=(",", ":")),
                             _log_json(lg), sc))
                        report["office_applied"].setdefault(sc, {}).update(office)
                    for key, val in office.items():
                        data[key] = val         # и в upsert самой строки файла
                    office_cids.add(cid)
                    report["office_cells"] += len(office)
                if rejected:
                    protected_cids.add(cid)
                    report["protected_cells"] += rejected
            data.update(comment_updates)
            keys = ", ".join(f'"{k}"' for k in data)
            qs = ", ".join("?" for _ in data)
            updates = ", ".join(f'"{k}"=excluded."{k}"' for k in data if k != "contract_id")
            # Строка «лишних столбцов» может дать ТОЛЬКО contract_id (все прочие заголовки
            # незнакомы) — тогда updates пуст, и "DO UPDATE SET" без полей — синтаксическая
            # ошибка SQLite. В этом случае обновлять нечего — просто не трогаем строку.
            on_conflict = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
            conn.execute(
                f"INSERT INTO clients ({keys}) VALUES ({qs}) "
                f"ON CONFLICT(contract_id) {on_conflict}",
                tuple(data.values()),
            )
            report["updated" if is_update else "inserted"] += 1
            slot_values = extra_values.get(cid) or {}
            if extra_cols:
                # Пустая ячейка дополнительной колонки — это «значения больше
                # нет» (акция закончилась), поэтому строки, которых нет во
                # входе, удаляем: иначе карточка вечно показывала бы прошлое.
                keep = tuple(slot_values)
                if keep:
                    marks = ",".join("?" for _ in keep)
                    conn.execute(
                        "DELETE FROM client_extra WHERE contract_id=? "
                        f"AND slot NOT IN ({marks})",
                        (cid, *keep),
                    )
                else:
                    conn.execute(
                        "DELETE FROM client_extra WHERE contract_id=?", (cid,))
                for item in extra_cols.values():
                    slot = _extra_slot(item["title"])
                    value = slot_values.get(slot)
                    if not value:
                        continue
                    conn.execute(
                        "INSERT INTO client_extra "
                        "(contract_id, slot, title, label, value, updated_at) "
                        "VALUES (?,?,?,?,?,?) "
                        "ON CONFLICT(contract_id, slot) DO UPDATE SET "
                        "title=excluded.title, label=excluded.label, "
                        "value=excluded.value, updated_at=excluded.updated_at",
                        (cid, slot, item["title"], item["label"], value,
                         date.today().isoformat()),
                    )
                    report["extra_cells"] += 1
        report["office_contracts"] = len(office_cids)        # договоры, не строки
        report["protected_contracts"] = len(protected_cids)
        report["isolated_call_contracts"] = len(isolated_call_cids)
        comment_effect_rows.sort(
            key=lambda item: (item["contract_id"], item["field"])
        )
        report["comment_effect_digest"] = hashlib.sha256(
            json.dumps(
                comment_effect_rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        # Считаем по итогу разбора всего файла, а не построчно: у одного договора в
        # реестре бывает несколько строк, и владелец может стоять в любой из них.
        report["no_responsible"] = sum(1 for owner in owners.values() if not owner)
        manual_effect_rows = [
            {"contract_id": cid, "employee": employee}
            for cid, employee in sorted(manual_restore_effect)
        ]
        report["manual_restore_candidates"] = len(manual_effect_rows)
        report["manual_restored"] = (
            len(manual_effect_rows) if restore_manual_unassigned else 0
        )
        report["manual_restore_digest"] = hashlib.sha256(
            json.dumps(
                manual_effect_rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        manual_by_employee: dict[str, int] = {}
        for _cid, employee in manual_restore_effect:
            manual_by_employee[employee] = manual_by_employee.get(employee, 0) + 1
        report["manual_restore_by_employee"] = {
            employee: manual_by_employee[employee]
            for employee in sorted(manual_by_employee, key=_name_key)
        }
        auto_unassigned_effect: list[tuple[str, str]] = []
        if full_registry:
            # Отсутствие считается только по каноническим КД, разобранным тем же
            # preflight выше, и только внутри явно выбранных сотрудников. Строки,
            # визиты, фото и долги не удаляются.
            for cid, old in existing.items():
                former = str(old["responsible"] or "").strip()
                if (
                    not former
                    or _name_key(former) not in authoritative_by_key
                    or cid in grouped
                ):
                    continue
                changed = conn.execute(
                    "UPDATE clients SET responsible='', unassigned_from=?, "
                    "unassigned_reason=? WHERE contract_id=? AND responsible=?",
                    (former, UNASSIGNED_REASON_ABSENT_FROM_REGISTRY, cid,
                     old["responsible"]),
                ).rowcount
                report["auto_unassigned"] += changed
                if changed:
                    auto_unassigned_effect.append((cid, former))
        effect_rows = [
            {"contract_id": cid, "employee": employee}
            for cid, employee in sorted(auto_unassigned_effect)
        ]
        report["auto_unassigned_digest"] = hashlib.sha256(
            json.dumps(
                effect_rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        by_employee: dict[str, int] = {}
        for _cid, employee in auto_unassigned_effect:
            by_employee[employee] = by_employee.get(employee, 0) + 1
        report["auto_unassigned_by_employee"] = {
            employee: by_employee[employee]
            for employee in sorted(by_employee, key=_name_key)
        }
        # --- Отзыв: счётчики, digest и частично затронутые люди --------------
        report["recalled_sample"] = recall_sample if preview_only else []
        for prefix, pairs in (
            ("recalled", [(cid, emp) for cid, emp, _h in recall_effect]),
            ("recall_restored", list(recall_restore_effect)),
        ):
            rows_out = [{"contract_id": cid, "employee": employee}
                        for cid, employee in sorted(pairs)]
            report[prefix] = len(rows_out)
            report[prefix + "_digest"] = hashlib.sha256(json.dumps(
                rows_out, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode("utf-8")).hexdigest()
        report["recalled_had_owner"] = sum(1 for _c, _e, h in recall_effect if h)
        recalled_per: dict[str, int] = {}
        for _cid, employee, had in recall_effect:
            if had and employee:
                recalled_per[employee] = recalled_per.get(employee, 0) + 1
        report["recalled_by_employee"] = {
            employee: recalled_per[employee]
            for employee in sorted(recalled_per, key=_name_key)}
        report["recall_restored_to_owner"] = len(recall_restore_effect)
        restored_per: dict[str, int] = {}
        for _cid, employee in recall_restore_effect:
            if employee:
                restored_per[employee] = restored_per.get(employee, 0) + 1
        report["recall_restored_by_employee"] = {
            employee: restored_per[employee]
            for employee in sorted(restored_per, key=_name_key)}
        # Люди, у которых отозвана только ЧАСТЬ договоров: у них меняется номер
        # представителя карточки (rep = min КД), визит из очереди вернётся с
        # «отклонено сервером», фото выпадут из альбома руководителя.
        if recall_effect:
            taken_cids = {cid for cid, _e, h in recall_effect if h}
            partial: set = set()
            for cid in taken_cids:
                old_row = existing.get(cid)
                if old_row is None:
                    continue
                pkey = person_key(old_row)
                for other_cid, other in existing.items():
                    if other_cid in taken_cids or other_cid == cid:
                        continue
                    if person_key(other) == pkey and (
                            str(other["responsible"] or "").strip()
                            == str(old_row["responsible"] or "").strip()):
                        partial.add(pkey)
                        break
            report["recalled_partial_persons"] = len(partial)
        # Гейт по ФАКТУ ЭФФЕКТА: слепое массовое снятие/возвращение без свежего
        # предпросмотра не применяется. Установившееся состояние (эффект нулевой)
        # проходит — операторский CLI продолжает работать, хотя боевой мастер
        # всегда несёт колонки отзыва. Расписка НЕ гасится (consume ниже по коду
        # выполняется только после успешной сверки).
        recall_unverified = (
            require_source_proof and not preview_only
            and (report["recalled"] or report["recall_restored"])
            and (expected_recalled is None or expected_recalled_digest is None
                 or expected_recall_restored is None
                 or expected_recall_restored_digest is None)
        )
        if recall_unverified:
            conn.rollback()
            report["blocked"] = True
            report["recall_preview_required"] = True
            report["error"] = (
                "импорт заблокирован: этот импорт снимает договоры по колонкам "
                f"отзыва (отозвать {report['recalled']}, вернуть "
                f"{report['recall_restored']}); нужен свежий предпросмотр "
                "точного множества — примените через пульт"
            )
            return report
        stale_count = (
            expected_auto_unassigned is not None
            and report["auto_unassigned"] != expected_auto_unassigned
        )
        stale_digest = (
            expected_auto_unassigned_digest is not None
            and report["auto_unassigned_digest"]
            != expected_auto_unassigned_digest
        )
        stale_comment_digest = (
            expected_comment_effect_digest is not None
            and report["comment_effect_digest"]
            != expected_comment_effect_digest
        )
        stale_manual_count = (
            expected_manual_restored is not None
            and report["manual_restored"] != expected_manual_restored
        )
        stale_manual_digest = (
            expected_manual_restore_digest is not None
            and report["manual_restore_digest"]
            != expected_manual_restore_digest
        )
        stale_recalled = (
            expected_recalled is not None
            and report["recalled"] != expected_recalled
        )
        stale_recalled_digest = (
            expected_recalled_digest is not None
            and report["recalled_digest"] != expected_recalled_digest
        )
        stale_recall_restored = (
            expected_recall_restored is not None
            and report["recall_restored"] != expected_recall_restored
        )
        stale_recall_restored_digest = (
            expected_recall_restored_digest is not None
            and report["recall_restored_digest"]
            != expected_recall_restored_digest
        )
        if (
            stale_count or stale_digest or stale_comment_digest
            or stale_manual_count or stale_manual_digest
            or stale_recalled or stale_recalled_digest
            or stale_recall_restored or stale_recall_restored_digest
        ):
            conn.rollback()
            report["blocked"] = True
            report["preview_stale"] = True
            report["expected_auto_unassigned"] = expected_auto_unassigned
            report["expected_auto_unassigned_digest"] = (
                expected_auto_unassigned_digest
            )
            report["expected_comment_effect_digest"] = (
                expected_comment_effect_digest
            )
            report["expected_manual_restored"] = expected_manual_restored
            report["expected_manual_restore_digest"] = (
                expected_manual_restore_digest
            )
            report["expected_recalled"] = expected_recalled
            report["expected_recalled_digest"] = expected_recalled_digest
            report["expected_recall_restored"] = expected_recall_restored
            report["expected_recall_restored_digest"] = (
                expected_recall_restored_digest
            )
            report["error"] = (
                "импорт заблокирован: после предпросмотра состояние БД "
                "изменилось; ожидаемые множества снятий/возвратов/замен комментариев "
                "больше не совпадают с фактическими. Сделайте новый "
                "предпросмотр"
            )
            return report
        if preview_only:
            conn.rollback()
        else:
            if require_source_proof:
                _consume_import_authorization(conn, verified_source, report)
            conn.commit()
    # Человеку в пульте нужны заголовки реестра, а не ключи схемы: в отчёте
    # появились «дата обещания», «Сумма прогноза» и «Прогноз», и строка вида
    # «forecast — 12» читалась бы как чужой лог. Титулы отдаёт СЕРВЕР: пульт
    # ходит по HTTP и своей копии реестра не имеет.
    report["key_titles"] = {
        key: BY_KEY[key].title
        for key in set(report.get("office_by_key") or {})
        | set(report.get("protected_by_key") or {})
        if key in BY_KEY
    }
    if not preview_only:
        _bump_version()   # массовая запись реестра → инвалидируем кэш приоритета
        reset_role_caches()   # состав направленцев считается по clients
    return report


# Поля, которые заполняет ВЫЕЗД (форма миниаппа + служебные визита). Сбрасываются при
# «очистке сессии», не трогая источник: долги, адрес, назначение (responsible), гео, период.
VISIT_FILL_COLUMNS = list(
    dict.fromkeys(
        (
            *BOT_SNAPSHOT_KEYS,
            "meeting_date",
            OFFICE_RESULT_SOURCE_KEY,
            "reminder_date",
        )
    )
)


def reset_visits() -> dict:
    """Сброс данных визитов у ВСЕХ клиентов (новая сессия на том же портфеле): поля
    формы + служебные визита в NULL, счётчики фото/визитов в 0. Клиенты, долги, адреса,
    закрепление (responsible), гео, период, привязки сотрудников, история visits — целы."""
    sets = ", ".join(f'"{c}"=NULL' for c in VISIT_FILL_COLUMNS)
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        conn.execute(f"UPDATE clients SET {sets}, bot_visit_at=NULL, "
                     f"bot_visit_data=NULL, office_edit_data=NULL, "
                     f"visit_value_log=NULL, photos_count=0, visits_count=0")
        # Водяной знак для дедупа уведомлений: строки visits намеренно переживают сброс
        # (на них держатся карта дня и активность), но выезд, закрытый ПОСЛЕ сброса,
        # не должен считаться повтором вчерашнего — иначе первая же отбивка новой
        # сессии молча пропадёт.
        last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM visits").fetchone()[0]
        conn.commit()
    set_setting("visits_reset_id", str(last))
    _bump_version()
    return {"ok": True, "scope": "visits", "clients_reset": n}


def wipe_registry() -> dict:
    """Полная очистка реестра под свежий импорт: удалить всех клиентов, историю визитов
    и метки. Привязки сотрудников (agent_bind), настройки (settings) и период — СОХРАНЯЮТСЯ,
    чтобы после нового импорта сотрудники сразу видели своих клиентов без повторной привязки."""
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        conn.execute("DELETE FROM clients")
        conn.execute("DELETE FROM visits")
        conn.execute("DELETE FROM markers")
        conn.commit()
    _bump_version()
    return {"ok": True, "scope": "all", "clients_removed": n}
