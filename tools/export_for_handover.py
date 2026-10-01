"""Перенос живой базы «Хард» с одного сервера на другой.

Этот файл — «мозги» переноса; руками его обычно не запускают. Его запускает
``deploy/units/core/migrate.sh``, ПОДАВАЯ ЕГО ВНУТРЬ КОНТЕЙНЕРА через stdin:

    docker ... python - take --data /data > handover.tar   < этот файл

Почему внутри контейнера, а не на хосте: у чужой ИТ-службы на сервере может
не быть ни питона, ни sqlite3 нужной версии, а образ приложения есть всегда —
он и так стоит. Заодно версия SQLite совпадает с той, что писала базу.

Три команды:

  take       снять с работающего сервера: онлайн-копия базы + фотографии +
             контрольные суммы + опись. Пишет tar в stdout.
  verify     проверить распакованный архив ПЕРЕД подстановкой: целостность
             базы, совпадение контрольных сумм, пересчёт описи.
  inventory  опись установленной базы — ею отчитываются после переноса
             («перенесено столько-то»).

Почему онлайн-копия, а не ``cp``. База работает в режиме WAL: часть свежих
страниц лежит не в самом файле, а в ``hardbot.db-wal``. Скопированный на ходу
файл — это середина транзакции, и открывается он в лучшем случае с потерей
последних визитов, в худшем — «database disk image is malformed». Штатный
``sqlite3.Connection.backup()`` снимает согласованный снимок под блокировкой
страниц, не останавливая приложение.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
ARCHIVE_ROOT = "hard-handover"
DB_NAME = "hardbot.db"
SUMS_NAME = "SHA256SUMS"
MANIFEST_NAME = "manifest.json"
INVENTORY_NAME = "ОПИСЬ.txt"


def log(msg: str) -> None:
    """Сообщения — только в stderr: в stdout идёт tar-поток."""
    print(msg, file=sys.stderr, flush=True)


class Stop(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(2)
        self.message = message

    def __str__(self) -> str:  # noqa: D105
        return self.message


# ---------------------------------------------------------------------------
# ПОЛИТИКА ПЕРЕНОСА
#
# Каждая таблица базы обязана быть названа ровно в одном из четырёх списков.
# Если в базе найдётся таблица, которой нет ни в одном, перенос ОСТАНАВЛИВАЕТСЯ
# с требованием решить, что с ней делать. Молчание здесь и есть главная ошибка:
# новая таблица появляется вместе с новой возможностью, и «оно как-нибудь
# переедет само» однажды увезёт на чужой сервер очередной секрет.
# ---------------------------------------------------------------------------

# Данные взыскания. Ради них перенос и делается.
CARRY = {
    "clients",                # портфели: договоры, адреса, суммы, закрепление
    "client_extra",           # дополнительные колонки реестра (привязаны к КД)
    "visits",                 # визиты и заполненные формы
    "markers",                # метки на карте
    "notes",                  # заметки выездников про кейсы
    "photo_geo",              # индекс фотографий: гео, EXIF, размеры
    "track_points",           # треки поездок
    "training_rows",          # корпус для обучения скоринга (ПД нет по построению)
    "audit",                  # журнал обращения к карточкам — история по тем же данным
    "push_log",               # телеметрия доставки уведомлений
    "import_source_heads",    # якорь против отката реестра (только хэши и номера)
    "enrichment_ledger",      # что пульт уже положил в книгу офиса (см. WIPE_COLUMNS)
    "visit_presence",         # следы телефона, которыми подтверждается выезд
    "addr_candidates",        # выученные адреса: где выездник реально был
    "employee_city",          # офис сотрудника — из него считается разбивка сводок
    "payment_evidence",       # чеки об оплате: доказательства по тем же договорам
    "assignment_events",      # журнал смен ответственного: история по тем же договорам
}
# Почему эти три едут, хотя в них есть люди. Они не открывают ни одной двери:
# доступ живёт в agent_bind/admin_devices/app_tokens, а здесь лежит сделанная
# работа. visit_presence — доказательство выезда, без него у переехавших визитов
# пропадает вердикт «был на адресе»; telegram_id в нём — такая же запись о
# прошлом, как audit.actor, который переезжает с самого начала. addr_candidates
# — адреса, выученные по фактическим приездам: выбросить их значит заставить
# людей заново объезжать то, что уже объезжено. employee_city — какой сотрудник
# в каком офисе; без него сводки на новом сервере молча теряют разбивку, пока
# офисы не соберутся заново из папок портфелей.

# Доступ и секреты этой установки. Обнуляются ВСЕГДА, флагом не включаются.
#
# Это не осторожность ради осторожности. agent_bind — не «список сотрудников»,
# а белый список доступа к персональным данным должников: db.get_binding()
# пускает в карточки любого, у кого здесь approved=1. Переехавшая строка — это
# человек, которому новый владелец доступ не выдавал и о котором не знает.
# admin_devices и app_tokens — то же самое для пульта и мини-приложения.
ALWAYS_CLEAR = {
    "admin_devices",          # токены машин прежнего оператора (хэши, но всё же доступ)
    "app_tokens",             # девайс-токены мини-приложения
    "app_links",              # одноразовые ссылки входа — выписаны на старый домен
    # Неиспользованный код привязки — это дверь, которую ещё не открывали.
    # Переехав, он остаётся годным: кто угодно с бумажкой от прежней установки
    # заводит себе пульт или панель инженера на новом сервере.
    "admin_enroll_codes",
    # Компьютеры с пультом руководителя: токены (хэши) — такой же выданный доступ.
    "manager_devices",
    # Приложение выездника на ПК: тот же класс выданного доступа.
    "agent_devices",
    # Контрольный обзвон безопасника: внутренняя проверка людей этой установки,
    # другому владельцу она не передаётся.
    "control_checks",
}

# Люди и их привязки. По умолчанию НЕ переезжают: новый владелец выдаёт доступ
# сам. С флагом --with-people переезжают, но обезвреженными (см. TAME).
PEOPLE = {
    "users",                  # профили гостей телеграма (язык, @username)
    "agent_bind",             # привязка telegram_id → сотрудник = ДОСТУП
    "managers",               # руководители: кому какие сводки с ПД должников
    "report_chats",           # беседы для отчётов — принадлежат старому боту
    "report_chat_employees",
}

# Особый случай: чистится не таблица, а отдельные строки/колонки.
SPECIAL = {
    "settings",               # смесь настроек и секретов, см. SETTINGS_DROP
    "import_receipts",        # незавершённые авторизации импорта не переезжают
}

KNOWN = CARRY | ALWAYS_CLEAR | PEOPLE | SPECIAL

# Ключи settings, которые не переезжают никогда.
SETTINGS_DROP = {
    # Токен бота лежит В БАЗЕ (app.bot_token(): db.get_setting("bot_token")
    # имеет приоритет над переменной окружения). Это настоящий секрет: с ним
    # можно писать от имени бота всем, кто когда-либо ему написал.
    "bot_token",
    "bot_token_changed_at",
    # Беседа для сводок — чат СТАРОГО бота. Новый бот в него не добавлен, и
    # попытка постить туда закончится молчаливой ошибкой доставки.
    "group_chat_id",
    # Незавершённая заявка на роль руководителя — состояние диалога, который
    # на новом сервере никто не продолжит.
    "mgr_request_from",
    "mgr_request_from_name",
    # Секрет подписи «Карты дня» (maplink): у каждого сервера свой. Приехав
    # в архиве, он дал бы держателю архива подписывать ссылки на карту
    # принимающего сервера; там сервер при первой подписи заведёт новый.
    "maplink_secret",
}

# Колонки, которые обнуляются у переезжающих строк.
WIPE_COLUMNS = {
    # Путь к личной книге прежнего оператора («C:\\Users\\…\\Общий.xlsb»).
    # Сам реестр доставленного нужен — он не даёт следующему прогону пульта
    # затереть правку офиса своей же старой доставкой. А вот путь на чужой
    # машине не значит ничего и заодно вывозит структуру каталогов владельца.
    ("enrichment_ledger", "file_path"),
}

# Что чинится у людей, когда их всё же переносят (--with-people).
TAME = (
    # Привязка едет как ЗАЯВКА, а не как выданный доступ: строка сохраняет
    # «кто есть кто», но в карточки не пускает, пока новый владелец не одобрит
    # её в пульте (db.pending_binds() показывает такие строки списком заявок).
    ("agent_bind", "UPDATE agent_bind SET approved=0",
     "привязки сотрудников приехали ЗАЯВКАМИ — доступ откроется "
     "после одобрения в пульте"),
    # «Боту можно писать этому руководителю в личку» — свойство ПАРЫ бот+человек.
    # Новый бот никому ещё не писал, и пока человек не нажмёт /start, любая
    # отправка вернёт Forbidden. «Не знаем» честнее унаследованной единицы.
    ("managers", "UPDATE managers SET can_dm=NULL, can_dm_at=NULL",
     "у руководителей снята отметка «боту можно писать в личку» — "
     "новый бот им ещё не писал"),
    # Беседа остаётся в списке, но выключенной: новый бот в неё не добавлен.
    ("report_chats", "UPDATE report_chats SET enabled=0",
     "беседы для отчётов приехали ВЫКЛЮЧЕННЫМИ — нового бота в них ещё нет"),
)

# Проверка ПОСЛЕ чистки: то, чего в уезжающей копии быть не должно.
# Пустой результат у каждого запроса — условие выпуска архива.
POSTCHECKS = (
    ("остались устройства пульта", "SELECT COUNT(*) FROM admin_devices"),
    ("остались коды привязки устройств", "SELECT COUNT(*) FROM admin_enroll_codes"),
    ("остались компьютеры с пультом руководителя", "SELECT COUNT(*) FROM manager_devices"),
    ("остались токены мини-приложения", "SELECT COUNT(*) FROM app_tokens"),
    ("остались ссылки входа", "SELECT COUNT(*) FROM app_links"),
    ("остался токен бота или беседа",
     "SELECT COUNT(*) FROM settings WHERE key IN "
     "('bot_token','bot_token_changed_at','group_chat_id')"),
    ("остались незавершённые авторизации импорта",
     "SELECT COUNT(*) FROM import_receipts WHERE status='pending'"),
)

# Отдельная сеть безопасности на случай, если секрет положили в settings под
# новым именем: значение, похожее на токен Telegram, останавливает выпуск.
TG_TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")


# ---------------------------------------------------------------------------
# Общее
# ---------------------------------------------------------------------------

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def tables_of(conn: sqlite3.Connection) -> set[str]:
    """Пользовательские таблицы. Служебные sqlite_* к политике не относятся."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def counts_of(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in sorted(tables_of(conn)):
        out[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    return out


def _one(conn: sqlite3.Connection, sql: str, default=0):
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error:
        return default
    if not row or row[0] is None:
        return default
    return row[0]


def inventory_of(conn: sqlite3.Connection) -> dict:
    """Опись: то, что человек на той стороне может сверить глазами.

    Считаем и договоры, и людей отдельно: в этом проекте это разные единицы
    измерения (у одного должника бывает несколько договоров), и сводка
    руководителя считает людей, а выгрузки — договоры. Одно число вместо двух
    гарантированно приведёт к спору «у нас не сходится».
    """
    inv = {
        "договоров": _one(conn, "SELECT COUNT(*) FROM clients"),
        "должников": _one(
            conn,
            "SELECT COUNT(*) FROM (SELECT DISTINCT "
            "COALESCE(client,''), COALESCE(birth_date,'') FROM clients "
            "WHERE COALESCE(client,'') <> '')",
        ),
        "договоров в портфелях": _one(
            conn,
            "SELECT COUNT(*) FROM clients WHERE COALESCE(responsible,'') <> '' "
            "AND COALESCE(unassigned_from,'') = ''",
        ),
        "сотрудников в реестре": _one(
            conn,
            "SELECT COUNT(*) FROM (SELECT DISTINCT responsible FROM clients "
            "WHERE COALESCE(responsible,'') <> '')",
        ),
        "визитов": _one(conn, "SELECT COUNT(*) FROM visits"),
        "визитов с координатой": _one(
            conn, "SELECT COUNT(*) FROM visits WHERE visit_lat IS NOT NULL"),
        "фотографий в индексе": _one(conn, "SELECT COUNT(*) FROM photo_geo"),
        "заметок": _one(
            conn, "SELECT COUNT(*) FROM notes WHERE deleted_at IS NULL"),
        "меток на карте": _one(conn, "SELECT COUNT(*) FROM markers"),
        "точек трека": _one(conn, "SELECT COUNT(*) FROM track_points"),
        "строк корпуса обучения": _one(conn, "SELECT COUNT(*) FROM training_rows"),
        "привязок сотрудников": _one(conn, "SELECT COUNT(*) FROM agent_bind"),
        "руководителей": _one(conn, "SELECT COUNT(*) FROM managers"),
    }
    first = _one(conn, "SELECT MIN(created_at) FROM visits", None)
    last = _one(conn, "SELECT MAX(created_at) FROM visits", None)
    inv["первый визит"] = (str(first)[:10] if first else "—")
    inv["последний визит"] = (str(last)[:10] if last else "—")
    return inv


def _hostname() -> str:
    """Имя машины — только для описи. В контейнере это его короткий id, поэтому
    migrate.sh подставляет сюда имя хоста через --host."""
    return os.environ.get("HOSTNAME") or "неизвестно"


def size_str(n: int) -> str:
    v = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if v < 1024 or unit == "ТБ":
            return f"{v:.0f} {unit}" if unit == "Б" else f"{v:.1f} {unit}"
        v /= 1024.0
    return f"{n} Б"


# ---------------------------------------------------------------------------
# Фотографии
# ---------------------------------------------------------------------------

def scan_photos(root: Path) -> list[tuple[str, Path, int]]:
    """Фотографии как (относительный путь, файл, размер).

    Пропускаем то же, что пропускает photostore.contract_photos: времянки
    незавершённой загрузки («~…», «….part»). Это не фото, а середина записи —
    на новом сервере они были бы битыми файлами в карточке. Символические
    ссылки не берём вообще: за пределы каталога фотографий архив не выходит.
    """
    out: list[tuple[str, Path, int]] = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if not Path(dirpath, d).is_symlink())
        for name in sorted(filenames):
            if name.startswith("~") or name.endswith(".part"):
                continue
            p = Path(dirpath, name)
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            out.append((rel, p, p.stat().st_size))
    return out


# ---------------------------------------------------------------------------
# take — снятие на исходном сервере
# ---------------------------------------------------------------------------

def online_backup(src_path: Path, dst_path: Path) -> None:
    """Согласованная копия работающей базы (см. заголовок файла)."""
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(dst_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


def sanitize(db_path: Path, with_people: bool) -> dict:
    """Вычистить из КОПИИ всё, что на новом сервере станет ложью или дырой.

    Чистка не касается рабочей базы вообще: она правит только что снятый
    снимок, лежащий во временном каталоге контейнера. Даже если здесь что-то
    пойдёт не так, на исходном сервере ничего не изменится.

    В конце VACUUM: без него удалённые строки остаются лежать в свободных
    страницах файла и читаются любым просмотрщиком. «Удалили» без VACUUM — это
    не удалили, а спрятали.
    """
    report: dict = {"очищено": {}, "оставлено": {},
                    "обезврежено": [], "исправлено": []}
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=DELETE")  # архив без спутников -wal/-shm
    try:
        present = tables_of(conn)

        unknown = sorted(present - KNOWN)
        if unknown:
            raise Stop(
                "в базе есть таблицы, для которых не решено, переезжают они "
                "или нет: " + ", ".join(unknown) + ".\n"
                "Так и задумано: новая таблица должна быть названа явно в "
                "tools/export_for_handover.py (списки CARRY / ALWAYS_CLEAR / "
                "PEOPLE / SPECIAL). Молча увезти незнакомую таблицу нельзя — "
                "в ней может лежать очередной секрет."
            )

        clear = set(ALWAYS_CLEAR)
        if not with_people:
            clear |= PEOPLE

        for name in sorted(clear & present):
            n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            conn.execute(f'DELETE FROM "{name}"')
            report["очищено"][name] = n

        if with_people:
            for name, sql, note in TAME:
                if name in present:
                    cur = conn.execute(sql)
                    report["оставлено"][name] = conn.execute(
                        f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                    report["обезврежено"].append(f"{note} ({cur.rowcount})")

        # settings: точечно, а не целиком. Здесь же лежат отметки миграций
        # (migration_bot_visit_provenance_v1 и подобные) — снести их значит
        # заставить init_db на новом сервере прогнать одноразовую починку
        # поверх уже починенных данных.
        if "settings" in present:
            marks = ",".join("?" * len(SETTINGS_DROP))
            cur = conn.execute(
                f"DELETE FROM settings WHERE key IN ({marks})",
                tuple(sorted(SETTINGS_DROP)),
            )
            report["очищено"]["settings (ключей)"] = cur.rowcount

        if "import_receipts" in present:
            cur = conn.execute("DELETE FROM import_receipts WHERE status='pending'")
            report["очищено"]["import_receipts (незавершённых)"] = cur.rowcount

        for table, column in sorted(WIPE_COLUMNS):
            if table not in present:
                continue
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            if column in cols:
                cur = conn.execute(
                    f'UPDATE "{table}" SET "{column}"=NULL '
                    f'WHERE "{column}" IS NOT NULL')
                report["исправлено"].append(
                    f"{table}.{column}: обнулено {cur.rowcount}")

        conn.commit()

        # --- контроль после чистки -----------------------------------------
        for label, sql in POSTCHECKS:
            try:
                left = conn.execute(sql).fetchone()[0]
            except sqlite3.Error:
                continue
            if left:
                raise Stop(f"чистка не сработала: {label} ({left}). Архив не выпущен.")

        if "settings" in present:
            for key, value in conn.execute("SELECT key, value FROM settings"):
                if value and TG_TOKEN_RE.search(str(value)):
                    raise Stop(
                        f"в settings[{key}] лежит значение, похожее на токен "
                        "Telegram. Так секрет уехал бы на чужой сервер. "
                        "Добавьте ключ в SETTINGS_DROP "
                        "(tools/export_for_handover.py) и повторите."
                    )

        conn.execute("VACUUM")
        report["оставлено настроек"] = _one(conn, "SELECT COUNT(*) FROM settings")
        report["ключи настроек"] = [
            r[0] for r in conn.execute("SELECT key FROM settings ORDER BY key")
        ]
    finally:
        conn.close()
    return report


def render_inventory(manifest: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("ОПИСЬ ПЕРЕНОСА «ХАРД»")
    add("=" * 60)
    add(f"снято:            {manifest['снято']}")
    add(f"исходный сервер:  {manifest['исходный сервер']}")
    add(f"люди и привязки:  {manifest['люди']}")
    add("")
    add("СВЕРИТЬ ПОСЛЕ ПЕРЕНОСА (эти числа должны совпасть):")
    for key, value in manifest["опись"].items():
        add(f"  {key:.<34} {value}")
    add("")
    add("ФАЙЛЫ:")
    add(f"  база .............................. {manifest['база']['размер']}")
    add(f"  sha256 базы ....................... {manifest['база']['sha256']}")
    add(f"  фотографий, файлов ................ {manifest['фотографии']['файлов']}")
    add(f"  фотографий, объём ................. {manifest['фотографии']['размер']}")
    add("")
    add("СТРОК В ТАБЛИЦАХ:")
    for name, n in manifest["таблицы"].items():
        add(f"  {name:.<34} {n}")
    add("")
    add("ЧТО НАМЕРЕННО НЕ ПЕРЕЕХАЛО (доступы, секреты, привязки к старому серверу):")
    for name, n in manifest["чистка"]["очищено"].items():
        add(f"  {name:.<34} убрано строк: {n}")
    for note in manifest["чистка"]["исправлено"]:
        add(f"  {note}")
    tamed = manifest["чистка"].get("обезврежено") or []
    if tamed:
        add("")
        add("ЧТО ПЕРЕЕХАЛО ОБЕЗВРЕЖЕННЫМ:")
        for note in tamed:
            add(f"  • {note}")
    add("")
    add("Полный список настроек, которые переехали:")
    add("  " + (", ".join(manifest["чистка"].get("ключи настроек", [])) or "—"))
    add("")
    add("Проверка на принимающей стороне:  ./migrate.sh receive <архив>")
    return "\n".join(lines) + "\n"


def cmd_take(args: argparse.Namespace) -> int:
    data = Path(args.data)
    src_db = data / DB_NAME
    if not src_db.is_file():
        raise Stop(f"нет {src_db} — базы ещё нет (приложение ни разу не запускалось?)")

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    copy = work / DB_NAME
    if copy.exists():
        copy.unlink()

    log("  снимаю согласованную копию базы…")
    online_backup(src_db, copy)

    conn = sqlite3.connect(copy)
    try:
        bad = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    if bad != "ok":
        raise Stop(f"снятая копия не проходит проверку целостности: {bad}")
    log("  копия целая (quick_check: ok)")

    log("  убираю доступы и секреты этой установки…")
    cleaning = sanitize(copy, with_people=args.with_people)

    conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        inv = inventory_of(conn)
        counts = counts_of(conn)
        tables = sorted(tables_of(conn))
    finally:
        conn.close()

    photos: list[tuple[str, Path, int]] = []
    if not args.db_only:
        log("  считаю контрольные суммы фотографий…")
        photos = scan_photos(data / "photos")

    db_sha = sha256_file(copy)
    photo_bytes = sum(sz for _, _, sz in photos)

    manifest = {
        "схема": SCHEMA_VERSION,
        "снято": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "исходный сервер": args.host or _hostname(),
        "люди": ("перенесены как заявки (--with-people)" if args.with_people
                 else "не переносятся (доступ выдаёт новый владелец)"),
        "только база": bool(args.db_only),
        "опись": inv,
        "таблицы": counts,
        "список таблиц": tables,
        "база": {"имя": DB_NAME, "sha256": db_sha,
                 "байт": copy.stat().st_size,
                 "размер": size_str(copy.stat().st_size)},
        "фотографии": {"файлов": len(photos), "байт": photo_bytes,
                       "размер": size_str(photo_bytes)},
        "чистка": cleaning,
    }
    inventory_txt = render_inventory(manifest)

    sums = io.StringIO()
    sums.write(f"{db_sha}  {DB_NAME}\n")
    for rel, path, _size in photos:
        sums.write(f"{sha256_file(path)}  photos/{rel}\n")
    sums_bytes = sums.getvalue().encode("utf-8")

    # Опись печатаем ЗДЕСЬ, а не достаём потом из архива: вытащить один файл
    # из tar-а на десятки гигабайт — значит прочитать его целиком. Опись
    # уходит в stderr (в stdout идёт сам архив), поэтому её видно в терминале
    # сразу, а в архиве она всё равно лежит первым файлом.
    log("")
    log(inventory_txt)
    log(f"  пакую: база {manifest['база']['размер']}, "
        f"фотографий {len(photos)} ({manifest['фотографии']['размер']})")

    out = sys.stdout.buffer
    with tarfile.open(fileobj=out, mode="w|") as tar:
        def add_bytes(name: str, payload: bytes) -> None:
            info = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{name}")
            info.size = len(payload)
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(payload))

        # Опись и манифест кладём ПЕРВЫМИ: их можно вытащить из головы архива,
        # не распаковывая гигабайты фотографий:
        #   tar -xOf архив.tar hard-handover/ОПИСЬ.txt
        add_bytes(INVENTORY_NAME, inventory_txt.encode("utf-8"))
        add_bytes(MANIFEST_NAME,
                  json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        add_bytes(SUMS_NAME, sums_bytes)
        tar.add(copy, arcname=f"{ARCHIVE_ROOT}/{DB_NAME}")
        for rel, path, _size in photos:
            tar.add(path, arcname=f"{ARCHIVE_ROOT}/photos/{rel}")
    out.flush()

    copy.unlink(missing_ok=True)
    log("  архив собран")
    return 0


# ---------------------------------------------------------------------------
# verify — проверка на принимающей стороне
# ---------------------------------------------------------------------------

def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    manifest_path = root / MANIFEST_NAME
    db_path = root / DB_NAME
    sums_path = root / SUMS_NAME
    for p in (manifest_path, db_path, sums_path):
        if not p.is_file():
            raise Stop(f"в архиве нет {p.name} — это не архив переноса «Хард»")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("схема") != SCHEMA_VERSION:
        raise Stop(
            f"архив снят другой версией переноса (схема {manifest.get('схема')}, "
            f"здесь {SCHEMA_VERSION}). Снимите заново тем же выпуском системы.")

    problems: list[str] = []
    say = print

    say("1. Контрольные суммы файлов")
    bad_sums = 0
    checked = 0
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        target = root / rel
        if not target.is_file():
            problems.append(f"файла нет: {rel}")
            bad_sums += 1
            continue
        if sha256_file(target) != digest:
            problems.append(f"не совпала сумма: {rel}")
            bad_sums += 1
        checked += 1
    say(f"   проверено файлов: {checked}, расхождений: {bad_sums}")

    say("2. Целостность базы")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        say(f"   quick_check ....... {quick}")
        if quick != "ok":
            problems.append(f"quick_check: {quick}")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        say(f"   integrity_check ... {integrity}")
        if integrity != "ok":
            problems.append(f"integrity_check: {integrity}")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        say(f"   foreign_key_check . {'ok' if not fk else f'{len(fk)} нарушений'}")

        say("3. Состав базы")
        have = tables_of(conn)
        expected = set(manifest.get("список таблиц") or [])
        missing = sorted(expected - have)
        extra = sorted(have - expected)
        if missing:
            problems.append("в базе нет таблиц: " + ", ".join(missing))
        say(f"   таблиц: {len(have)}"
            + (f", нет: {', '.join(missing)}" if missing else "")
            + (f", лишние: {', '.join(extra)}" if extra else ""))

        say("4. Опись")
        actual = inventory_of(conn)
        declared = manifest.get("опись") or {}
        for key, want in declared.items():
            got = actual.get(key)
            mark = "ок" if got == want else "РАСХОЖДЕНИЕ"
            if got != want:
                problems.append(f"опись: {key} заявлено {want}, в базе {got}")
            say(f"   {key:.<34} {got}  [{mark}]")

        say("5. Доступы и секреты (их в архиве быть не должно)")
        for label, sql in POSTCHECKS:
            try:
                left = conn.execute(sql).fetchone()[0]
            except sqlite3.Error:
                continue
            if left:
                problems.append(f"{label}: {left}")
            say(f"   {label:.<44} {left}")
    finally:
        conn.close()

    photos_declared = int((manifest.get("фотографии") or {}).get("файлов") or 0)
    photos_found = len(scan_photos(root / "photos"))
    say("6. Фотографии")
    say(f"   заявлено {photos_declared}, в архиве {photos_found}")
    if photos_declared != photos_found:
        problems.append(
            f"фотографий: заявлено {photos_declared}, найдено {photos_found}")

    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps({"manifest": manifest, "problems": problems},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")

    if problems:
        say("")
        say("АРХИВ НЕ ПРИНЯТ. Найдено:")
        for p in problems:
            say(f"  • {p}")
        return 1
    say("")
    say("Архив проверен: база целая, суммы сошлись, опись сходится.")
    return 0


# ---------------------------------------------------------------------------
# inventory — опись установленной базы (отчёт после переноса)
# ---------------------------------------------------------------------------

def cmd_inventory(args: argparse.Namespace) -> int:
    data = Path(args.data)
    db_path = data / DB_NAME
    if not db_path.is_file():
        raise Stop(f"нет {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        inv = inventory_of(conn)
        counts = counts_of(conn)
    finally:
        conn.close()
    photos = scan_photos(data / "photos")
    payload = {
        "опись": inv,
        "таблицы": counts,
        "фотографии": {"файлов": len(photos),
                       "байт": sum(s for _, _, s in photos)},
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # Манифест читаем ОДИН раз и мягко: отчёт о переносе не должен падать
    # трассировкой питона из-за того, что не нашёлся файл описи. Без описи
    # просто показываем числа — это всё ещё полезнее молчания.
    declared: dict = {}
    declared_photos = None
    if args.compare:
        try:
            manifest = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  (опись из архива не прочиталась: {exc} — сверять не с чем)")
        else:
            declared = manifest.get("опись") or {}
            declared_photos = (manifest.get("фотографии") or {}).get("файлов")

    mismatch = 0
    for key, value in inv.items():
        line = f"  {key:.<34} {value}"
        if key in declared:
            if declared[key] != value:
                line += f"   <-- в описи было {declared[key]}"
                mismatch += 1
            else:
                line += "   ок"
        print(line)
    line = f"  {'фотографий на диске':.<34} {len(photos)}"
    if declared_photos is not None:
        if int(declared_photos) != len(photos):
            line += f"   <-- в описи было {declared_photos}"
            mismatch += 1
        else:
            line += "   ок"
    print(line)
    return 1 if mismatch else 0


# ---------------------------------------------------------------------------
# state — «база пустая или в ней уже работают?»
#
# Отдельная команда, а не проверка наличия файла: hardbot.db появляется сразу
# после install.sh (приложение создаёт схему на первом запуске), поэтому «файл
# есть» ничего не говорит. Занятой считаем базу, в которой есть хоть что-то из
# рабочего: договоры, визиты, фотографии.
# ---------------------------------------------------------------------------

def cmd_state(args: argparse.Namespace) -> int:
    data = Path(args.data)
    db_path = data / DB_NAME
    photos = len(scan_photos(data / "photos"))
    if not db_path.is_file():
        print("пусто (базы нет)" if not photos
              else f"занято (базы нет, но фотографий {photos})")
        return 0
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        clients = _one(conn, "SELECT COUNT(*) FROM clients")
        visits = _one(conn, "SELECT COUNT(*) FROM visits")
    finally:
        conn.close()
    if clients or visits or photos:
        print(f"занято: договоров {clients}, визитов {visits}, "
              f"фотографий {photos}")
    else:
        print("пусто")
    return 0


# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="export_for_handover",
        description="Перенос живой базы «Хард» между серверами")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("take", help="снять копию (tar в stdout)")
    p.add_argument("--data", default="/data")
    p.add_argument("--work", default="/tmp/hard-handover")
    p.add_argument("--with-people", action="store_true")
    p.add_argument("--db-only", action="store_true")
    p.add_argument("--host", default="")
    p.set_defaults(func=cmd_take)

    p = sub.add_parser("verify", help="проверить распакованный архив")
    p.add_argument("--dir", required=True)
    p.add_argument("--out-json", default="")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("state", help="пуста ли установленная база")
    p.add_argument("--data", default="/data")
    p.set_defaults(func=cmd_state)

    p = sub.add_parser("inventory", help="опись установленной базы")
    p.add_argument("--data", default="/data")
    p.add_argument("--compare", default="", help="manifest.json для сверки")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_inventory)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Stop as stop:
        print(f"ОШИБКА: {stop}", file=sys.stderr)
        raise SystemExit(2)
