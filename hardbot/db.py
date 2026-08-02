"""
Хранилище на SQLite (stdlib sqlite3 — без лишних зависимостей).

Таблица `clients` создаётся динамически из fields.FIELDS, поэтому схема БД
всегда совпадает со схемой реестра. Плюс несколько служебных колонок для бота
и таблица `visits` — история выездов (кнопка «История» в карточке).
"""

import hashlib
import json
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import country_scope
import debt
from exchange_contract import (
    ExchangeContractError,
    FORM_DROPDOWN_KEYS,
    IMPORT_SOURCE_KINDS,
    IMPORT_SOURCE_PUBLISHED_MASTER,
    IMPORT_SOURCE_TEST_SUBSET,
    OFFICE_CALL_FIELDS,
    OFFICE_DIRECT_EDIT_KEYS,
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

# БД лежит рядом со скриптом, либо в каталоге HARDBOT_DATA (том в Docker)
_data_dir = Path(os.getenv("HARDBOT_DATA", Path(__file__).parent))
_data_dir.mkdir(parents=True, exist_ok=True)
DB_PATH = _data_dir / "hardbot.db"

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
    ("tips", "TEXT"),               # наводки выездника (староста, соседи, ориентиры)
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

_exchange_registry_keys = (
    set(BOT_VISIT_KEYS)
    | set(BOT_DROPDOWN_KEYS)
    | set(OFFICE_DIRECT_EDIT_KEYS)
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
        if value in (None, ""):
            continue
        value = str(value).strip()
        if not value:
            continue
        if key in BOT_DROPDOWN_KEYS and value not in (BY_KEY[key].options or []):
            return None
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
        if key not in OFFICE_EDIT_KEYS or value in (None, ""):
            continue
        value = str(value).strip()
        if not value:
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
    """Подтверждённый результат визита: снимок миниаппа + принятые офисные правки.
    Без валидного снимка офисные правки не существуют (некуда накладывать)."""
    snapshot = _decode_bot_snapshot(row)
    if snapshot is None:
        return None
    edits = _decode_office_edits(row)
    return {**snapshot, **edits} if edits else snapshot


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
        # Гео-контроль: точка телефона в момент закрытия визита + дистанция до
        # известной точки должника (метры). NULL = точки не было (нет разрешения/сигнала).
        for col, decl in (("visit_lat", "REAL"), ("visit_lon", "REAL"), ("dist_m", "INTEGER")):
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
              op_id TEXT
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_op ON notes(op_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_tg ON notes(telegram_id)")
        # Настройки (например, принятая беседа group_chat_id).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT
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
              created_at TEXT
            )
        """)
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
    """Закрепить за пользователем сотрудника + тип (agent/courier). Одноразово.
    approved=False — привязка создаётся в статусе «ждёт аппрува ADMIN» (доступа пока нет)."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO agent_bind (telegram_id, employee, kind, approved, created_at) "
            "VALUES (?,?,?,?,?)",
            (telegram_id, employee, kind, 1 if approved else 0, date.today().isoformat()),
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
        binds = {b["employee"]: b for b in
                 conn.execute("SELECT * FROM agent_bind").fetchall()}
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
            f"UPDATE clients SET responsible = '', unassigned_from = ? "
            f"WHERE responsible = ? "
            f"AND contract_id IN ({','.join('?' * len(ids))})",
            (employee, employee, *ids))
        conn.commit()
        return cur.rowcount


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
    with connect() as conn:
        row = conn.execute(
            "SELECT telegram_id FROM agent_bind WHERE employee = ? LIMIT 1", (employee,)
        ).fetchone()
    return row["telegram_id"] if row else None


def list_bindings() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM agent_bind ORDER BY employee").fetchall()


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


def _manager_dict(r: sqlite3.Row) -> dict:
    return {"telegram_id": r["telegram_id"], "name": r["name"] or "",
            "cities": _json_list(r["cities"]), "employees": _json_list(r["employees"])}


def set_manager(telegram_id: int, name: str, cities: list[str],
                employees: list[str]) -> None:
    """Создать/обновить руководителя (upsert по telegram_id). cities — ярлык-подпись
    (свободный ввод), employees — ФИО из реестра (кого он видит в дайджесте)."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO managers (telegram_id, name, cities, employees, created_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET "
            "name=excluded.name, cities=excluded.cities, employees=excluded.employees",
            (telegram_id, (name or "").strip(),
             json.dumps(list(cities or []), ensure_ascii=False),
             json.dumps(list(employees or []), ensure_ascii=False),
             date.today().isoformat()),
        )
        conn.commit()


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
        allowed = [e for e in mgr.get("employees") or [] if str(e).strip()]
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
    merged = dict(rows[0])
    for key in ("visits7", "visits30", "marks7", "marks30", "d7", "d30"):
        if key in merged:
            merged[key] = sum(int(r.get(key) or 0) for r in rows)
    lasts = [r.get("last") for r in rows if r.get("last")]
    if "last" in merged:
        merged["last"] = max(lasts) if lasts else None
    idles = [r.get("idle_days") for r in rows if r.get("idle_days") is not None]
    if "idle_days" in merged:
        merged["idle_days"] = min(idles) if idles else None
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


# --- Гео фото (фактическая точка съёмки) -------------------------------------
def add_photo_geo(contract_id: str, file: str, telegram_id: int,
                  lat: float, lon: float, acc_m: int | None,
                  captured_at: str | None = None) -> None:
    """Запомнить точку загрузки фото. file = «КД/имя файла» (уникален);
    повторная доставка того же фото из оффлайн-очереди строк не плодит."""
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO photo_geo (contract_id, file, telegram_id, lat, lon, "
            "acc_m, captured_at, created_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(file) DO NOTHING",
            (contract_id, file, telegram_id, lat, lon, acc_m,
             captured_at or now, now))
        conn.commit()


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


_TRAIN_COLS = ("debt", "product_type", "partner", "country", "assign_date", "kd_date",
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


def remove_report_chat(chat_id: int) -> None:
    """Бота выгнали из беседы: помечаем, настройки и сотрудников не теряем."""
    with connect() as conn:
        conn.execute("UPDATE report_chats SET removed=1 WHERE chat_id=?", (chat_id,))
        conn.commit()


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
             "employees": sorted(emps.get(r["chat_id"], []))} for r in chats]


def set_report_chat(chat_id: int, enabled: bool | None = None,
                    employees: list[str] | None = None) -> None:
    """Настройка из пульта: включённость и/или список ФИО (None = не трогать)."""
    with connect() as conn:
        if enabled is not None:
            conn.execute("UPDATE report_chats SET enabled=? WHERE chat_id=?",
                         (1 if enabled else 0, chat_id))
        if employees is not None:
            conn.execute("DELETE FROM report_chat_employees WHERE chat_id=?",
                         (chat_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO report_chat_employees (chat_id, employee) "
                "VALUES (?,?)",
                [(chat_id, e.strip()) for e in employees if e and e.strip()])
        conn.commit()


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


def report_chats_for(employee: str) -> list[int]:
    """Куда постить отчёт о визите сотрудника: беседа включена, бот в ней есть,
    ФИО закреплено."""
    if not (employee or "").strip():
        return []
    with connect() as conn:
        return [r[0] for r in conn.execute(
            "SELECT c.chat_id FROM report_chats c "
            "JOIN report_chat_employees e ON e.chat_id = c.chat_id "
            "WHERE c.enabled=1 AND c.removed=0 AND e.employee=?", (employee,))]


# Путь к .env, откуда docker-compose (env_file) и load_dotenv() берут BOT_TOKEN.
# Лежит рядом с кодом; в контейнере — /data не подходит (env_file читает .env билда),
# поэтому пишем в файл рядом со скриптами. HARDBOT_ENV — переопределение пути на всякий.
_ENV_PATH = Path(os.getenv("HARDBOT_ENV", Path(__file__).parent / ".env"))


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
            conn.execute(
                "UPDATE clients SET bot_visit_at=?, bot_visit_data=?, "
                "office_edit_data=NULL, visit_value_log=?, updated_at=? "
                "WHERE contract_id=?",
                (completed_at, payload, _log_json(log), now, r["contract_id"]),
            )
        conn.commit()
    _bump_version()
    return len(contracts)


def has_bot_visit(rep_contract_id: str) -> bool:
    """Есть ли у человека валидный текущий снимок завершённого визита."""
    return any(_decode_bot_snapshot(r) is not None
               for r in person_contracts(rep_contract_id))


def bot_visit_snapshot(row) -> dict | None:
    """Публичное read-only представление подтверждённого результата визита строки:
    снимок миниаппа + принятые офисные правки (карточка, миниапп, дайджест)."""
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


def overdue_promises(employee: str, on_date: str) -> list[dict]:
    """Люди сотрудника с ПРОСРОЧЕННЫМ обещанием: promise_date (реестр) в прошлом,
    кейс не «Оплачен» и после даты обещания платежа не было (last_payment_date).
    Группировка по людям; в каждом — promise_date (самая ранняя просроченная)
    и days_overdue. Самые «тухлые» сверху."""
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
    persons.sort(key=lambda p: -p["days_overdue"])
    return persons


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
              lon: float | None = None, dist_m: int | None = None) -> bool:
    """Записать выезд в историю. op_id — идемпотентный ключ (uuid от клиента): повторная
    доставка того же выезда из оффлайн-очереди НЕ создаёт дубль. Возвращает True, если
    строка реально добавлена (значит, можно слать уведомление в группу); False — если это
    повтор уже сохранённого выезда. Без op_id (старый путь) — всегда True (новая строка).
    lat/lon/dist_m — гео-контроль: точка закрытия и дистанция до точки должника."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO visits (contract_id, agent_id, created_at, payload, "
            "op_id, visit_lat, visit_lon, dist_m) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (contract_id, agent_id, date.today().isoformat(), payload, op_id,
             lat, lon, dist_m),
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
            "SELECT v.visit_lat lat, v.visit_lon lon, v.dist_m dist_m, c.client client "
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
    return {
        "visits": [{"lat": r["lat"], "lon": r["lon"], "dist_m": r["dist_m"],
                    "client": r["client"]} for r in visits],
        "markers": [{"lat": r["lat"], "lon": r["lon"], "label": r["label"] or "",
                     "at": (r["created_at"] or "")[11:16]} for r in markers],
        "targets": targets,
    }


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


def geo_alert_m() -> int:
    """Порог «далеко от адреса» в метрах (settings.geo_alert_m, дефолт 1000)."""
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
        return row["id"] if row else 0


def notes_for(telegram_id: int, limit: int = 300) -> list[sqlite3.Row]:
    """Заметки одного человека, свежие сверху. Чужие не отдаём: это личные записи."""
    with connect() as conn:
        return conn.execute(
            "SELECT id, client_rep, text, created_at FROM notes "
            "WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
            (telegram_id, limit)).fetchall()


def delete_note(telegram_id: int, note_id: int) -> bool:
    """Удалить свою заметку. Чужую — нельзя: telegram_id в условии обязателен."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ? AND telegram_id = ?",
                           (note_id, telegram_id))
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


def registry_rows() -> list[dict]:
    """Строки реестра для экспорта: только колонки FIELDS, порядок как в БД-запросе.
    Это полный raw-слепок для отдельного режима сверки, включая импортные HARD-поля."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM clients ORDER BY client").fetchall()
    return [{f.key: r[f.key] for f in FIELDS} for r in rows]


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

    There is deliberately no HTTP route for this operation. The registry
    operator (or a later thin orchestrator) must first validate the external
    publication/test scope and invoke the CLI next to the server data store.
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
        raise ExchangeContractError(
            "source proof is not pre-registered by the trusted server registrar"
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
            for key in ("inserted", "updated", "office_cells")
        }
        for key in (
            "inserted",
            "updated",
            "office_cells",
            "office_contracts",
            "protected_cells",
            "protected_contracts",
        ):
            if key in replay:
                replay[key] = 0
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
      * якорь — «Дата выезда» строки файла совпадает с датой визита бота. Дату
        пишет в personal только обогащение; затем registry_cycle публикует её в
        master, значит опубликованный master ВИДЕЛ результат бота;
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

    Возвращает отчёт — чтобы было видно ТОЧНО, что вошло:
    {source_kind, total_rows, inserted, updated, skipped_no_id, duplicates,
    unknown_columns,
    protected_cells, protected_contracts, office_cells, office_contracts,
    office_applied, blocked, duplicate_conflicts}. protected_* — отклонено
    шлюзом (устаревший файл/мусор);
    office_* — принятые офисные правки, office_applied = {КД: {поле: значение}}
    (пульту — чтобы обогащение сразу писало свежепринятые значения).
    """
    report = {"source_kind": source_kind,
              "total_rows": 0, "inserted": 0, "updated": 0,
              "skipped_no_id": 0, "duplicates": 0, "unknown_columns": [],
              "protected_cells": 0, "protected_contracts": 0,
              "office_cells": 0, "office_contracts": 0, "office_applied": {},
              "isolated_call_cells": 0, "isolated_call_contracts": 0,
              # Строки без ответственного попадают в базу, но невидимы выезднику и не
              # входят ни в один отчёт руководителя. Раньше это не показывалось нигде,
              # поэтому 1970 договоров из 5267 «потерялись» молча.
              "no_responsible": 0,
              "source_proof_digest": None,
              "blocked": False, "duplicate_conflicts": [],
              "quarantined_contracts": []}

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
    allowed_ignored_patterns = tuple(
        re.compile(pattern)
        for pattern in WORKBOOK_ALLOWED_IGNORED_HEADER_PATTERNS
    )
    ignored_columns: list[str] = []
    unknown_columns: list[str] = []
    for cell, key in zip(rows[best_h], col_keys):
        if cell in (None, "") or key is not None:
            continue
        title = str(cell).strip()
        if not title:
            continue
        if (
            _norm_title(title) in allowed_ignored_titles
            or any(pattern.fullmatch(title) for pattern in allowed_ignored_patterns)
        ):
            ignored_columns.append(title)
        else:
            unknown_columns.append(title)
    report["ignored_columns"] = ignored_columns
    report["unknown_columns"] = unknown_columns
    if report["unknown_columns"]:
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: незнакомые заголовки требуют явного alias "
            "или новой версии контракта: "
            + ", ".join(report["unknown_columns"][:20])
        )
        return report

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
    paid_month_cols: list[int] = []
    for index, (cell, key) in enumerate(zip(rows[best_h], col_keys)):
        if key is not None or cell in (None, ""):
            continue
        title = str(cell).strip()
        mapped = _scoring_only_headers().get(_norm_title(title))
        if mapped:
            scoring_cols[index] = mapped
        elif _PAID_MONTH_RX.fullmatch(title):
            paid_month_cols.append(index)

    # Сначала разобрать ВЕСЬ файл в памяти. Это обязательный preflight: при
    # повторе КД не должна успеть записаться ни одна предыдущая строка.
    grouped: dict[str, dict] = {}
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
        for key, val in zip(col_keys, raw):
            if key and val is not None:
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

    if duplicate_rows:
        report["blocked"] = True
        report["error"] = (
            "импорт заблокирован: опубликованный master содержит повтор "
            "канонического КД; hardbot не сливает одинаковые, дополняющие или "
            "конфликтующие строки — исправьте источник и опубликуйте заново"
        )
        report["duplicate_conflicts"] = [
            {
                "contract_id": cid,
                "rows": duplicate_rows[cid],
                "fields": sorted(duplicate_conflicts.get(cid, set())),
            }
            for cid in sorted(duplicate_rows)
        ]
        report["quarantined_contracts"] = sorted(duplicate_rows)
        return report

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
            "unassigned_from, "
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
        # Владелец каждого уникального КД после duplicate preflight.
        owners: dict[str, str] = {}
        for cid, data in grouped.items():
            is_update = cid in existing
            # Пустой владелец = кейс в базе есть, а у выездника его нет.
            prev_resp = existing[cid]["responsible"] if cid in existing else ""
            # Договор сняли в пульте — файл не должен вернуть его молча. Снятие
            # держится, пока в реестре стоит ТОТ ЖЕ ответственный: значит офис
            # ничего не менял, просто переразбросил как было. Появилось другое
            # имя — это законное переназначение, снятие отменяется вместе с ним.
            was_unassigned = (existing[cid]["unassigned_from"]
                              if cid in existing else None) or ""
            incoming_resp = str(data.get("responsible") or "").strip()
            if was_unassigned and incoming_resp == was_unassigned:
                data["responsible"] = ""
                incoming_resp = ""
            elif was_unassigned and incoming_resp:
                data["unassigned_from"] = ""      # переназначили — метку снимаем
            owners[cid] = str(incoming_resp or prev_resp or "").strip()
            # После подтверждённого визита очередной импорт может обновлять долг,
            # адрес и прочие поля источника, а поля визита — только через якорный
            # шлюз (см. докстринг): совпавшая «Дата выезда» доказывает, что файл
            # видел результат бота, и отличия — офисная правка, а не старая копия.
            row0 = existing.get(cid)
            if row0 is not None and row0["bot_visit_data"]:
                merged = _merged_visit(row0)
                if merged:
                    merged.update(fresh_overlays.get(cid) or {})
                known = fresh_logs.get(cid) or _decode_value_log(row0)
                anchor_value = data.pop("visit_date", None)
                anchor_ok = (
                             source_kind == IMPORT_SOURCE_PUBLISHED_MASTER
                             and merged is not None
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

                candidates = {
                    key: data.pop(key, None)
                    for key in OFFICE_DIRECT_EDIT_KEYS
                }
                # Для завершённого визита «Общий результат» тоже защищён якорем:
                # до успешной проверки убираем его из обычного master-upsert.
                candidates[OFFICE_RESULT_SNAPSHOT_KEY] = data.pop(
                    OFFICE_RESULT_SOURCE_KEY,
                    None,
                )
                for key, raw_value in candidates.items():
                    sval = str(raw_value or "").strip()
                    if not sval:
                        continue                # пустота никогда не затирает
                    if not anchor_ok:
                        # test_subset никогда не является published-office
                        # overlay, даже если дата случайно совпала. Для master
                        # сюда также попадает старая копия/мусорная дата.
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
                    if key == OFFICE_RESULT_SNAPSHOT_KEY:
                        data[OFFICE_RESULT_SOURCE_KEY] = sval
                    office[key] = sval
                if anchor_value not in (None, "") and not anchor_ok:
                    rejected += 1
                if office:
                    pkey = person_key(row0) + (row0["responsible"] or "",)
                    sibs = [c for c in person_map.get(pkey, [])
                            if existing[c]["bot_visit_at"] == row0["bot_visit_at"]]
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
        report["office_contracts"] = len(office_cids)        # договоры, не строки
        report["protected_contracts"] = len(protected_cids)
        report["isolated_call_contracts"] = len(isolated_call_cids)
        # Считаем по итогу разбора всего файла, а не построчно: у одного договора в
        # реестре бывает несколько строк, и владелец может стоять в любой из них.
        report["no_responsible"] = sum(1 for owner in owners.values() if not owner)
        if require_source_proof:
            _consume_import_authorization(conn, verified_source, report)
        conn.commit()
    _bump_version()   # массовая запись реестра → инвалидируем кэш приоритета
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
