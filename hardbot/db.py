"""
Хранилище на SQLite (stdlib sqlite3 — без лишних зависимостей).

Таблица `clients` создаётся динамически из fields.FIELDS, поэтому схема БД
всегда совпадает со схемой реестра. Плюс несколько служебных колонок для бота
и таблица `visits` — история выездов (кнопка «История» в карточке).
"""

import json
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import debt
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
    ("reminder_date", "TEXT"),      # дата повторного выезда (для «Мои планы»)
    ("geo_lat", "REAL"),
    ("geo_lon", "REAL"),
    ("geo_src", "TEXT"),            # источник точки: nominatim/geonames/manual (выездник)
    ("tips", "TEXT"),               # наводки выездника (староста, соседи, ориентиры)
    ("updated_at", "TEXT"),
]


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
        # Настройки (например, принятая беседа group_chat_id).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT
            )
        """)
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
        # Миграция ранней дев-схемы app_tokens (без expires_at).
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(app_tokens)")}
        if "expires_at" not in tcols:
            conn.execute("ALTER TABLE app_tokens ADD COLUMN expires_at TEXT")
        # Миграция app_links времён одноразовых кодов (без счётчика uses).
        lcols = {r[1] for r in conn.execute("PRAGMA table_info(app_links)")}
        if "uses" not in lcols:
            conn.execute("ALTER TABLE app_links ADD COLUMN uses INTEGER NOT NULL DEFAULT 0")
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
            f"UPDATE clients SET responsible = '' WHERE responsible = ? "
            f"AND contract_id IN ({','.join('?' * len(ids))})", (employee, *ids))
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
    НЕ трогаем: сообщение боту не разлогинивает телефон."""
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
    к telegram (agent_bind). Не привязан → None (телеметрии нет). Тонкая обёртка над
    activity_stats для карточки сотрудника в дайджесте."""
    for w in activity_stats():
        if w["employee"] == employee:
            return w
    return None


def persons_processed(employee: str, on_date: str) -> list[dict]:
    """Люди сотрудника, у кого выезд состоялся в дату on_date (ISO). Сигнал —
    clients.visit_date (его ставит миниапп при завершении визита). Возвращает
    сгруппированных людей (как persons_for_employee), но только обработанных в этот день."""
    target = _as_date(on_date)
    if target is None:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE responsible = ? "
            "AND visit_date IS NOT NULL AND TRIM(visit_date) <> ''",
            (employee,),
        ).fetchall()
    rows = [r for r in rows if _as_date(r["visit_date"]) == target]
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
            "AND visit_date IS NOT NULL AND TRIM(visit_date) <> ''",
            (employee,),
        ).fetchall()
    rows = [r for r in rows if (d := _as_date(r["visit_date"])) and lo <= d <= hi]
    return _group_persons(rows)


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
    d = _as_date(val)
    if d is None:
        return not (lo or hi)        # без даты закрепления — только когда фильтр не задан
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


def _group_persons(rows: list[sqlite3.Row]) -> list[dict]:
    """Объединить договоры в людей по (ФИО, дата рождения)."""
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["client"] or "", r["birth_date"] or ""), []).append(r)
    persons = []
    for (name, dob), rs in groups.items():
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
    """Все договоры человека, к которому относится этот договор."""
    row = get_client(rep_contract_id)
    if not row:
        return []
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE client IS ? "
            "AND IFNULL(birth_date,'') = IFNULL(?, '') "
            "AND IFNULL(responsible,'') = IFNULL(?, '') ORDER BY contract_id",
            (row["client"], row["birth_date"], row["responsible"]),
        ).fetchall()


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
            "SELECT COUNT(*) FROM clients WHERE visit_date IS NOT NULL"
        ).fetchone()[0]
        by_result = conn.execute(
            "SELECT COALESCE(overall_result,'(нет)') r, COUNT(*) c FROM clients "
            "GROUP BY r ORDER BY c DESC"
        ).fetchall()
    return {
        "total": total, "visited": visited,
        "by_result": [(r["r"], r["c"]) for r in by_result],
    }


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

# Денежные/числовые колонки — подсказка писателю xlsx (пульт, rust_xlsxwriter):
# парсить в число и ставить формат # ##0, чтобы суммировалось и не зеленело.
# В БД значения остаются текстом как пришли — это только метаданные экспорта.
NUM_KEYS = {"debt_overdue", "interest_overdue", "penalty", "duty", "dtp", "rent",
            "camera_fine", "dirt", "trash", "new_damage", "evacuation", "peni",
            "other", "debt_total", "last_payment_sum", "forecast_sum",
            "debt_today", "issued_sum", "visits_count"}


def _as_date(v):
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d %H:%M:%S"):
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


def _pick_sheet(wb, title_to_key):
    """Лист «Портфели» или тот, где больше всего узнаваемых заголовков (для реальных файлов)."""
    if "Портфели" in wb.sheetnames:
        return wb["Портфели"]
    best, best_n = wb.active, -1
    for name in wb.sheetnames:
        ws = wb[name]
        n = 0
        for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
            n = max(n, sum(1 for c in row if title_to_key.get(_norm_title(c))))
        if n > best_n:
            best_n, best = n, ws
    return best


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
    Общий источник для xlsx-легаси и JSON-выдачи пульту."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM clients ORDER BY client").fetchall()
    return [{f.key: r[f.key] for f in FIELDS} for r in rows]


def export_schema() -> list[dict]:
    """Схема колонок для писателя xlsx на стороне пульта: блоки, заголовки,
    типы (дата/число). Единственный источник правды — fields.FIELDS."""
    return [{"key": f.key, "title": f.title, "block": f.block, "source": f.source,
             "date": f.key in DATE_KEYS, "num": f.key in NUM_KEYS} for f in FIELDS]


def export_xlsx(path: str) -> str:
    """Выгрузить БД обратно в реестр-шаблон (легаси-путь: openpyxl на сервере)."""
    return write_xlsx(path, registry_rows())


def pick_grid(sheets: list[dict]) -> list[list]:
    """Выбрать лист из [{name, grid}]: «Портфели», иначе тот, где больше всего
    узнаваемых заголовков. Зеркало _pick_sheet для JSON-гридов из пульта."""
    if not sheets:
        return []
    for s in sheets:
        if s.get("name") == "Портфели":
            return s.get("grid") or []
    title_to_key = {_norm_title(f.title): f.key for f in FIELDS}
    best, best_n = sheets[0].get("grid") or [], -1
    for s in sheets:
        grid = s.get("grid") or []
        n = 0
        for row in grid[:10]:
            n = max(n, sum(1 for c in row if title_to_key.get(_norm_title(c))))
        if n > best_n:
            best_n, best = n, grid
    return best


def import_xlsx(path: str) -> dict:
    """Легаси-обёртка: открыть файл openpyxl-ом и прогнать через import_grid.
    Пульт этим путём больше не ходит — парсит файл сам (calamine) и шлёт грид
    в POST /admin/import.json."""
    from openpyxl import load_workbook

    title_to_key = {_norm_title(f.title): f.key for f in FIELDS}
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = _pick_sheet(wb, title_to_key)
    return import_grid(list(ws.iter_rows(values_only=True)))


def import_grid(rows: list[list]) -> dict:
    """Ядро импорта: грид значений листа, источник любой (openpyxl на сервере,
    calamine в пульте). Колонки матчатся по заголовку — переживает «сдвиг влево».
    Upsert: для существующих КД обновляем только пришедшие поля, не затирая
    назначение/визиты/фото.

    Исключение — колонки разбивки по долгу (debt.SOURCE_OWNED). Если такая колонка
    в файле ЕСТЬ, её пустая ячейка означает «статья погашена», а не «данные не
    пришли»: иначе при ежемесячной переимпортации в карточке навсегда оставались
    призраки старых начислений (эвакуация, ДТП…), и сумма статей расходилась с
    «долгом всего». Колонки, которых в файле нет вообще, по-прежнему не трогаем.

    Возвращает отчёт — чтобы было видно ТОЧНО, что вошло:
    {total_rows, inserted, updated, skipped_no_id, duplicates, unknown_columns}.
    """
    report = {"total_rows": 0, "inserted": 0, "updated": 0,
              "skipped_no_id": 0, "duplicates": 0, "unknown_columns": []}

    title_to_key = {_norm_title(f.title): f.key for f in FIELDS}
    if not rows or len(rows) < 2:
        return report

    # Авто-поиск строки заголовков: реальный файл может иметь иную/сдвинутую шапку.
    # Критерий — распознанная колонка-КЛЮЧ («Номер КД / ID»→contract_id), а не «просто
    # много совпадений»: реестр с кучей лишних/незнакомых колонок, но нормальным ключом,
    # должен импортироваться; несколько опечаток в незначащих заголовках не должны
    # тихо обнулять весь импорт (было: best_n < 3 → return report без объяснений).
    best_h, col_keys, best_n, best_has_id = 0, [], -1, False
    for i, row in enumerate(rows[:10]):
        keys = [title_to_key.get(_norm_title(c)) for c in row]
        n = sum(1 for k in keys if k)
        has_id = "contract_id" in keys
        if (has_id, n) > (best_has_id, best_n):
            best_has_id, best_n, best_h, col_keys = has_id, n, i, keys
    if not best_has_id:
        # Без колонки-ключа сопоставить строки нечем — не тихий ноль, а понятная причина
        # + что реально было в лучшей из первых 10 строк, чтобы диагностировать заголовок.
        report["error"] = "не нашли колонку-ключ «Номер КД / ID» ни в одной из первых 10 строк"
        report["header_row_tried"] = best_h + 1
        report["header_sample"] = [str(c).strip() for c in rows[best_h] if c not in (None, "")][:20]
        return report
    report["unknown_columns"] = [str(c).strip() for c, k in zip(rows[best_h], col_keys) if c and k is None]
    # Колонки разбивки, которые реально есть в этом файле → пустая ячейка = ноль.
    owned = debt.SOURCE_OWNED.intersection(k for k in col_keys if k)

    with connect() as conn:
        existing = {r[0] for r in conn.execute("SELECT contract_id FROM clients")}
        seen: set[str] = set()
        for raw in rows[best_h + 1:]:
            if raw is None or all(v is None for v in raw):
                continue  # пустая строка
            nonempty = [str(v).strip().lower() for v in raw if v is not None]
            if nonempty and all(v in ("сх", "хард") for v in nonempty):
                continue  # строка-источник (сх/хард), не данные
            report["total_rows"] += 1
            data = {}
            for key, val in zip(col_keys, raw):
                if key and val is not None:
                    if isinstance(val, datetime):
                        data[key] = val.strftime("%Y-%m-%d")   # дату-ячейку → ISO
                    elif isinstance(val, date):
                        data[key] = val.isoformat()
                    else:
                        data[key] = str(val).strip()
            cid = data.get("contract_id")
            if not cid:
                report["skipped_no_id"] += 1
                continue
            for key in owned - data.keys():   # статья есть в файле, в строке пусто → погашена
                data[key] = None
            is_update = cid in existing or cid in seen
            if cid in seen:
                report["duplicates"] += 1
            seen.add(cid)

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
        conn.commit()
    _bump_version()   # массовая запись реестра → инвалидируем кэш приоритета
    return report


# Поля, которые заполняет ВЫЕЗД (форма миниаппа + служебные визита). Сбрасываются при
# «очистке сессии», не трогая источник: долги, адрес, назначение (responsible), гео, период.
VISIT_FILL_COLUMNS = [
    "call_status", "contact_with", "who_at_address", "client_location",
    "purpose_rf", "agreed_payment", "call_comment", "tips",
    "visit_date", "visit_comment", "meeting_date", "overall_result", "reminder_date",
]


def reset_visits() -> dict:
    """Сброс данных визитов у ВСЕХ клиентов (новая сессия на том же портфеле): поля
    формы + служебные визита в NULL, счётчики фото/визитов в 0. Клиенты, долги, адреса,
    закрепление (responsible), гео, период, привязки сотрудников, история visits — целы."""
    sets = ", ".join(f'"{c}"=NULL' for c in VISIT_FILL_COLUMNS)
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        conn.execute(f"UPDATE clients SET {sets}, photos_count=0, visits_count=0")
        conn.commit()
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

