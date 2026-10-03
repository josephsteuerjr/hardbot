"""Перенос портфелей НЕСКОЛЬКИХ сотрудников с рабочего сервера на другой.

Не путать с ``tools/export_for_handover.py``: тот увозит установку ЦЕЛИКОМ и на
принимающей стороне ЗАМЕНЯЕТ базу. Здесь другое: с работающего сервера снимается
ЧАСТЬ — портфели поимённого списка людей — и ДОЛИВАЕТСЯ в чужую базу, в которой
уже может что-то лежать.

Отсюда три правила, под которые написан каждый шаг:

  1. В исходную базу не пишется ни одной строки. Копия снимается штатным
     ``sqlite3.Connection.backup()`` с соединения, открытого ``mode=ro``, и
     дальше режется уже КОПИЯ во временном каталоге контейнера. Рабочий сервер
     не надо ни останавливать, ни предупреждать: в режиме WAL читатель не мешает
     писателю, ради этого WAL и сделан.
  2. Архив самодостаточен: база той же схемы (``subset.sqlite3``), фотографии
     только по перенесённым КД, опись и контрольные суммы. По нему проверяется,
     что приехало ровно заявленное, — без доступа к источнику.
  3. Приём идемпотентен: повторный приём того же архива не плодит строк и не
     стирает того, что на машине уже есть. Строки узнаются по естественным
     ключам (KEYS), а где ключа нет — по всей строке целиком.

Запускается двумя способами, и оба обязаны работать:

    # внутри контейнера, как это делает deploy/units/core/migrate.sh: файл
    # подаётся в чужой образ через stdin, потому что на сервере может не быть
    # питона, а образ приложения есть всегда
    docker run --rm -i -v /opt/hard/.../data:/data hard-app python - \
        take --data /data --only-employees "Иванов Иван;Петров Пётр" < этот файл

    # обычным скриптом, с путями к двум файлам баз — иначе инструмент
    # невозможно ни проверить, ни применить руками
    python server/subset_handover.py compat --db рабочая.db --subset приехало.sqlite3
    python server/subset_handover.py merge  --db рабочая.db --subset приехало.sqlite3

Поэтому здесь только стандартная библиотека: у файла, поданного через stdin, нет
ни ``__file__``, ни соседей по пакету.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
ARCHIVE_ROOT = "hard-subset"      # каталог внутри tar; у полного переезда свой
DB_NAME = "subset.sqlite3"        # база в архиве: имя иное, чем у рабочей,
LIVE_DB_NAME = "hardbot.db"       # чтобы архивы двух видов нельзя было спутать
SUMS_NAME = "SHA256SUMS"
MANIFEST_NAME = "manifest.json"
INVENTORY_NAME = "ОПИСЬ.txt"


def log(msg: str = "") -> None:
    """Всё человеческое — в stderr: в stdout может идти tar-поток."""
    print(msg, file=sys.stderr, flush=True)


class Stop(SystemExit):
    """Отказ с объяснением. Код 2 — тот же, что у полного переезда."""

    def __init__(self, message: str) -> None:
        super().__init__(2)
        self.message = message

    def __str__(self) -> str:  # noqa: D105
        return self.message


# ---------------------------------------------------------------------------
# ЧТО ЕДЕТ С ЧЕЛОВЕКОМ
#
# Ключ — таблица, значение — (зачем едет, условие отбора). Условие пишется от
# четырёх временных таблиц, которые собираются ДО первого DELETE:
#   sel_emp  — ФИО выбранных сотрудников, как они написаны в реестре;
#   sel_tg   — их telegram_id из agent_bind: связка «сотрудник → визит»;
#   sel_kd   — номера договоров их портфелей (clients.responsible);
#   sel_sess — сессии карточек из их визитов (visits.session_id).
#
# COALESCE в каждом условии — не украшение. NULL не равен ничему, поэтому без
# него строка с пустой колонкой не попала бы ни в «оставить», ни в «удалить», и
# судьба её зависела бы от того, с какой стороны на неё посмотрели.
# ---------------------------------------------------------------------------

CARRY: dict[str, tuple[str, str]] = {
    "clients": (
        "портфели: договоры, адреса, суммы, закрепление",
        "COALESCE(responsible,'') IN (SELECT name FROM sel_emp)"),
    "client_extra": (
        "дополнительные колонки реестра — привязаны к КД, без них карточка неполная",
        "COALESCE(contract_id,'') IN (SELECT kd FROM sel_kd)"),
    "visits": (
        "выезды и заполненные формы — работа самого сотрудника",
        "COALESCE(agent_id,-1) IN (SELECT tg FROM sel_tg)"),
    "notes": (
        "заметки выездника про кейсы — он писал их себе, они едут с ним",
        "COALESCE(telegram_id,-1) IN (SELECT tg FROM sel_tg)"
        " OR COALESCE(employee,'') IN (SELECT name FROM sel_emp)"),
    "markers": (
        "метки на карте, поставленные этим человеком",
        "COALESCE(telegram_id,-1) IN (SELECT tg FROM sel_tg)"),
    "photo_geo": (
        "индекс снимков по перенесённым КД: гео, EXIF, размеры",
        "COALESCE(contract_id,'') IN (SELECT kd FROM sel_kd)"),
    "track_points": (
        "треки поездок этого сотрудника",
        "COALESCE(employee,'') IN (SELECT name FROM sel_emp)"
        " OR COALESCE(telegram_id,-1) IN (SELECT tg FROM sel_tg)"),
    "visit_presence": (
        "следы телефона, которыми подтверждается выезд: без них у переехавших "
        "визитов пропадает вердикт «был на адресе»",
        "COALESCE(employee,'') IN (SELECT name FROM sel_emp)"
        " OR COALESCE(telegram_id,-1) IN (SELECT tg FROM sel_tg)"
        " OR COALESCE(session_id,'') IN (SELECT sid FROM sel_sess)"),
    "addr_candidates": (
        "адреса, выученные по фактическим приездам: выбросить их — заставить "
        "объезжать заново уже объезженное",
        "COALESCE(contract_id,'') IN (SELECT kd FROM sel_kd)"
        " OR COALESCE(employee,'') IN (SELECT name FROM sel_emp)"),
    "employee_city": (
        "офис сотрудника: без него сводки молча теряют разбивку по городам",
        "COALESCE(employee,'') IN (SELECT name FROM sel_emp)"),
    "agent_bind": (
        "привязка «телеграм-аккаунт → сотрудник»: по ней ссылка входа знает, "
        "кого пускает, и по ней же визит находит своего автора",
        "COALESCE(employee,'') IN (SELECT name FROM sel_emp)"),
    "users": (
        "профиль тех же людей (имя, язык) — без него в пульте пустые строки",
        "COALESCE(telegram_id,-1) IN (SELECT tg FROM sel_tg)"),
    "enrichment_ledger": (
        "реестр доставленного по этим сотрудникам: без него обогащение с новой "
        "машины повторно доставит в книгу офиса уже доставленное",
        "COALESCE(responsible,'') IN (SELECT name FROM sel_emp)"),
    "payment_evidence": (
        "чеки об оплате по перенесенным КД — доказательства платежей едут с делом",
        "COALESCE(contract_id,'') IN (SELECT kd FROM sel_kd)"
        " OR COALESCE(employee,'') IN (SELECT name FROM sel_emp)"),
    "assignment_events": (
        "журнал закреплений по перенесенным КД: история «у кого договор был»",
        "COALESCE(contract_id,'') IN (SELECT kd FROM sel_kd)"),
    "calls": (
        "журнал звонков софтников по перенесенным КД — работа, как визиты",
        "COALESCE(contract_id,'') IN (SELECT kd FROM sel_kd)"
        " OR COALESCE(employee,'') IN (SELECT name FROM sel_emp)"),
}

# ЧТО НЕ ЕДЕТ НИКОГДА. Не «забыли», а решено: у каждой строки своя причина.
# Список закрытый — незнакомая таблица останавливает снятие (trim_to_employees).
DROP_ALL: dict[str, str] = {
    "app_links": "ссылки входа выписаны на чужой домен",
    "app_tokens": "девайс-токены мини-приложения — это выданный доступ",
    "admin_devices": "устройства пульта прежнего оператора",
    "admin_enroll_codes": "неиспользованный код привязки — дверь, которую ещё не открывали",
    "manager_devices": "компьютеры с пультом руководителя — выданный доступ",
    "agent_devices": "приложение выездника на ПК — выданный доступ",
    "control_checks": "контрольный обзвон той установки: ссылается на её id выездов",
    "managers": "руководители и их рассылки с ПД должников — чужая настройка",
    "report_chats": "беседы для отчётов принадлежат старому боту",
    "report_chat_employees": "закрепление за теми же чужими беседами",
    "push_log": "телеметрия доставки чужих уведомлений",
    "audit": "журнал обращения к карточкам — история ТОЙ установки, а не этих людей",
    "import_receipts": "квитанции импорта чужой машины",
    "import_source_heads": "якорь против отката ЧУЖОГО реестра: приехав, он "
                           "запретил бы загрузку здешнего",
    "training_rows": "корпус обучения — это общий реестр целиком, а не портфель пятерых",
    "settings": "настройки и секреты той установки (токен бота лежит здесь же)",
}

KNOWN = set(CARRY) | set(DROP_ALL)

# Как узнать «ту же самую строку» при доливке. Ключ — естественный, тот же, по
# которому строку узнаёт само приложение. Пустой кортеж = ключа нет вовсе: такие
# строки сверяются целиком (см. merge_table), иначе повторный приём их удвоил бы.
KEYS: dict[str, tuple[str, ...]] = {
    "clients": ("contract_id",),
    "payment_evidence": ("op",),
    "assignment_events": ("contract_id", "window_to", "employee", "prev_employee"),
    "client_extra": ("contract_id", "slot"),
    "visits": ("op_id",),
    "calls": ("op_id",),
    "notes": ("op_id",),
    "markers": ("op_id",),
    "photo_geo": ("file",),
    "track_points": ("session_id", "ts"),
    "visit_presence": ("session_id", "ts", "kind"),
    "addr_candidates": (),
    "employee_city": ("employee",),
    "agent_bind": ("telegram_id",),
    "users": ("telegram_id",),
    "enrichment_ledger": ("contract_id", "field_key"),
}

# Колонки, которые обнуляются у уезжающих строк.
WIPE_COLUMNS = {
    # Путь к личной книге прежнего оператора («C:\\Users\\…\\Общий.xlsb»). Сам
    # реестр доставленного нужен, а путь на чужой машине не значит ничего и
    # заодно вывозит структуру каталогов владельца.
    ("enrichment_ledger", "file_path"),
}

# У кого есть колонка «чей это»: по ней видно, что строка на приёмнике
# принадлежит НЕ нашему человеку. Такие строки не перезаписываются — номер
# договора в проекте не уникален между республиками (47892257 встречался
# дважды), и «доливка» молча стёрла бы чужого должника.
OWNER_COLUMN = {"clients": "responsible", "enrichment_ledger": "responsible"}

# Строки, которые описывают КАРТОЧКУ должника и ключуются номером договора.
# Своей колонки «чей это» у них нет, поэтому спорный номер они не распознают
# сами: доп. поля и индекс снимков ложились бы в карточку чужого должника, пока
# отчёт уверяет, что этот договор «НЕ ТРОНУТ». Такие строки обходим по списку
# спорных КД (contested_contracts).
#
# Визиты сюда НЕ входят осознанно: выезд есть работа сотрудника и едет с ним
# (у него свой ключ op_id, чужую строку он не трогает) — см. «в т.ч. по
# договорам вне архива» в описи. Спорные номера названы в отчёте, чтобы это
# было видно глазами, а не обнаружилось у чужого оператора в карточке.
CONTRACT_COLUMN = {
    "client_extra": "contract_id",
    "photo_geo": "contract_id",
    "addr_candidates": "contract_id",
    "enrichment_ledger": "contract_id",
}

# Колонки, которые у НАЙДЕННОЙ здесь строки не переписываются привезённым
# значением. Написание ФИО в привязке связано со здешним реестром: работник
# сходится с должниками строкой clients.responsible = agent_bind.employee, и
# «Холназаров  Шерали» с двумя пробелами здесь может быть штатным. Остальные
# колонки (kind, approved) доливаются как обычно — иначе человек, чья привязка
# здесь ещё ждёт одобрения, не вошёл бы по привезённой ссылке.
KEEP_HERE: dict[str, tuple[str, ...]] = {"agent_bind": ("employee",)}

# Колонки, в которых лежит id человека. По ним личность приезжего приводится к
# той, что уже заведена на принимающей машине (build_identity_remap).
IDENTITY_COLUMNS: dict[str, tuple[str, ...]] = {
    "agent_bind": ("telegram_id",),
    "users": ("telegram_id",),
    "visits": ("agent_id",),
    "notes": ("telegram_id",),
    "markers": ("telegram_id",),
    "photo_geo": ("telegram_id",),
    "track_points": ("telegram_id",),
    "visit_presence": ("telegram_id",),
}

# Служебные id «личностей без Telegram» начинаются отсюда — то же число, что
# db.LOCAL_UID_BASE. Продублировано намеренно: файл подаётся в контейнер через
# stdin, ни ``__file__``, ни соседей по пакету у него нет, импортировать
# server/db.py неоткуда. Совпадение сторожит тест.
LOCAL_UID_BASE = 900_000_000_000


def _norm(value) -> str:
    """ФИО к сравнимому виду: ё=е, лишние пробелы прочь, регистр не важен.

    Сотрудника в реестр печатают руками, и «Муминов  Абдулло» с двумя пробелами
    — не другой человек."""
    text = str(value or "").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", text).strip().casefold()


# ---------------------------------------------------------------------------
# Чтение схемы и чисел
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
        "AND name NOT LIKE 'sqlite_%'").fetchall()
    return {r[0] for r in rows}


def columns_of(conn: sqlite3.Connection, table: str) -> list[dict]:
    out = []
    for _cid, name, decl, notnull, dflt, pk in conn.execute(
            f'PRAGMA table_info("{table}")'):
        out.append({"имя": name, "тип": (decl or "").upper(),
                    "not_null": bool(notnull), "умолчание": dflt, "ключ": pk})
    return out


def column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [c["имя"] for c in columns_of(conn, table)]


def indexes_of(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """Только явные индексы: у авто-индексов UNIQUE sql пуст, и сравнивать их
    бессмысленно — они рождаются вместе с таблицей."""
    out: dict[str, str] = {}
    for name, sql in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name=?", (table,)):
        if sql:
            out[name] = re.sub(r"\s+", " ", sql).strip()
    return out


def counts_of(conn: sqlite3.Connection, tables=None) -> dict[str, int]:
    names = sorted(tables if tables is not None else tables_of(conn))
    out: dict[str, int] = {}
    for name in names:
        try:
            out[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.Error:
            continue
    return out


def _one(conn: sqlite3.Connection, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return default
    if not row or row[0] is None:
        return default
    return row[0]


def size_str(n: int) -> str:
    v = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if v < 1024 or unit == "ТБ":
            return f"{v:.0f} {unit}" if unit == "Б" else f"{v:.1f} {unit}"
        v /= 1024.0
    return f"{n} Б"


def _hostname() -> str:
    """Имя машины — только для описи. В контейнере это его короткий id, поэтому
    migrate.sh подставляет настоящее имя хоста через --host."""
    return os.environ.get("HOSTNAME") or "неизвестно"


# ---------------------------------------------------------------------------
# Фотографии
# ---------------------------------------------------------------------------

def contract_dir(root: Path, contract_id: str) -> Path | None:
    """Каталог снимков договора: photos/<КД>/… (КД со слэшем даёт вложенный
    каталог — так пишет photostore, так же и читаем).

    Номер договора приходит из реестра, то есть извне, поэтому здесь же и
    единственная проверка: «..» и выход за пределы каталога фотографий — отказ.
    """
    parts = [p for p in str(contract_id or "").split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        return None
    target = root.joinpath(*parts)
    try:
        if not target.resolve().is_relative_to(root.resolve()):
            return None
    except OSError:
        return None
    return target


def scan_photos_for(root: Path, contracts) -> list[tuple[str, Path, int]]:
    """Снимки перенесённых КД как (ключ «КД/имя», файл, размер).

    Пропускаем то же, что пропускает photostore.contract_photos: времянки
    незавершённой загрузки («~…», «….part»). Это не фото, а середина записи.
    Символические ссылки не берём вообще — за пределы каталога фотографий архив
    не выходит.
    """
    found: dict[str, tuple[Path, int]] = {}
    if not root.is_dir():
        return []
    for cid in sorted(set(contracts)):
        d = contract_dir(root, cid)
        if d is None or d.is_symlink() or not d.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(d, followlinks=False):
            dirnames[:] = sorted(x for x in dirnames
                                 if not Path(dirpath, x).is_symlink())
            for name in sorted(filenames):
                if name.startswith("~") or name.endswith(".part"):
                    continue
                p = Path(dirpath, name)
                if p.is_symlink() or not p.is_file():
                    continue
                # Вложенные КД («A» и «A/B») дали бы один файл дважды — ключ
                # один на файл, поэтому словарь, а не список.
                found[p.relative_to(root).as_posix()] = (p, p.stat().st_size)
    return [(rel, path, size) for rel, (path, size) in sorted(found.items())]


def photo_roots(photos_dir: Path) -> list[str]:
    """Верхние каталоги photos/ — ими же считаем файлы там, где номера
    договоров брать больше неоткуда (архив, чужой каталог снимков)."""
    if not photos_dir.is_dir():
        return []
    return sorted(p.name for p in photos_dir.iterdir() if p.is_dir())


# ---------------------------------------------------------------------------
# Выбор сотрудников
# ---------------------------------------------------------------------------

def known_employees(conn: sqlite3.Connection) -> list[str]:
    """Все ФИО, которые база вообще знает: из реестра и из привязок."""
    names: set[str] = set()
    for sql in ("SELECT DISTINCT responsible FROM clients",
                "SELECT DISTINCT employee FROM agent_bind"):
        try:
            rows = conn.execute(sql).fetchall()
        except sqlite3.Error:
            continue
        for (value,) in rows:
            if str(value or "").strip():
                names.add(str(value).strip())
    return sorted(names)


def resolve_employees(conn: sqlite3.Connection, requested) -> list[str]:
    """ФИО из командной строки → ФИО, как они написаны в реестре.

    Главная ошибка этой операции — опечатка в ФИО: отбор по ней даёт ноль строк,
    архив выпускается пустым и выглядит совершенно нормально, пока его не
    откроют на другой машине. Поэтому имя, которого нет, — это отказ со списком
    похожих, а не молчаливый пропуск. Совпадение ищем по нормальному виду
    (ё=е, регистр и лишние пробелы не важны), затем по началу строки («Муминов
    Абдулло» вместо полного «Муминов Абдулло Рахматович»), затем по вхождению.
    """
    known = known_employees(conn)
    if not known:
        raise Stop("в базе нет ни одного сотрудника — переносить нечего "
                   "(реестр не загружен?)")
    by_norm: dict[str, list[str]] = {}
    for name in known:
        by_norm.setdefault(_norm(name), []).append(name)

    out: list[str] = []
    for raw in requested:
        wanted = _norm(raw)
        if not wanted:
            continue
        hits = by_norm.get(wanted) or []
        if not hits:
            hits = sorted({n for n in known if _norm(n).startswith(wanted)})
        if not hits:
            hits = sorted({n for n in known if wanted in _norm(n)})
        if not hits:
            close = difflib.get_close_matches(raw, known, n=8, cutoff=0.4)
            raise Stop(
                f"в базе нет сотрудника «{raw}».\n"
                + ("Похожие: " + "; ".join(close) + "\n" if close else "")
                + "Весь список: ./migrate.sh employees")
        if len(set(hits)) > 1:
            raise Stop(
                f"«{raw}» подходит сразу нескольким: " + "; ".join(sorted(set(hits)))
                + ".\nНапишите ФИО целиком — иначе непонятно, чей портфель везти.")
        name = hits[0]
        if name not in out:
            out.append(name)
    if not out:
        raise Stop("не названо ни одного сотрудника: "
                   "--only-employees \"Фамилия Имя;Фамилия Имя\"")
    return out


# ---------------------------------------------------------------------------
# take — снятие подмножества на рабочем сервере
# ---------------------------------------------------------------------------

def online_backup(src_path: Path, dst_path: Path) -> None:
    """Согласованная копия работающей базы.

    Почему не ``cp``: база живёт в режиме WAL, часть свежих страниц лежит не в
    самом файле, а в hardbot.db-wal. Скопированный на ходу файл — середина
    транзакции. ``backup()`` снимает снимок под блокировкой страниц, не
    останавливая приложение, а mode=ro гарантирует, что мы в источник не пишем.
    """
    try:
        src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=30)
    except sqlite3.OperationalError as exc:
        raise Stop(
            f"не открывается {src_path} на чтение: {exc}.\n"
            "Чаще всего это каталог данных, подключённый только на чтение: базе "
            "в режиме WAL нужен доступ к служебному файлу -shm даже читателю. "
            "Подключайте /data на запись — инструмент в саму базу не пишет "
            "(соединение открыто mode=ro).") from exc
    dst = sqlite3.connect(dst_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


def build_selection(conn: sqlite3.Connection, employees) -> None:
    """Четыре временные таблицы отбора. TEMP — чтобы они не попали в архив: они
    живут в отдельной базе соединения и исчезают вместе с ним."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS sel_emp (name TEXT PRIMARY KEY)")
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS sel_tg (tg INTEGER PRIMARY KEY)")
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS sel_kd (kd TEXT PRIMARY KEY)")
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS sel_sess (sid TEXT PRIMARY KEY)")
    conn.executemany("INSERT OR IGNORE INTO sel_emp(name) VALUES (?)",
                     [(n,) for n in employees])
    # Каждый запрос — необязательный: таблицы или колонки может не быть в очень
    # старой базе, и это не повод ронять перенос. Пустой отбор честнее падения.
    for sql in (
        "INSERT OR IGNORE INTO sel_tg(tg) SELECT telegram_id FROM agent_bind "
        "WHERE COALESCE(employee,'') IN (SELECT name FROM sel_emp) "
        "AND telegram_id IS NOT NULL",
        "INSERT OR IGNORE INTO sel_kd(kd) SELECT contract_id FROM clients "
        "WHERE COALESCE(responsible,'') IN (SELECT name FROM sel_emp) "
        "AND COALESCE(contract_id,'') <> ''",
        "INSERT OR IGNORE INTO sel_sess(sid) SELECT session_id FROM visits "
        "WHERE COALESCE(agent_id,-1) IN (SELECT tg FROM sel_tg) "
        "AND COALESCE(session_id,'') <> ''",
    ):
        try:
            conn.execute(sql)
        except sqlite3.Error:
            continue


def trim_to_employees(conn: sqlite3.Connection, employees,
                      binds_as_requests: bool = False) -> dict:
    """Оставить в КОПИИ только портфели названных людей.

    Работает удалением лишнего, а не копированием нужного, ровно по одной
    причине: схема тогда остаётся той же самой по построению — с теми же
    колонками, индексами и умолчаниями, что на рабочем сервере. Собранная
    заново база неизбежно разошлась бы с оригиналом в мелочи, которую заметят
    уже на приёме.
    """
    present = tables_of(conn)
    unknown = sorted(present - KNOWN)
    if unknown:
        raise Stop(
            "в базе есть таблицы, для которых не решено, едут они с человеком "
            "или нет: " + ", ".join(unknown) + ".\n"
            "Так и задумано: новая таблица должна быть названа явно в "
            "server/subset_handover.py (CARRY — едет с сотрудником, DROP_ALL — "
            "не едет никогда). Молча увезти незнакомую таблицу нельзя: в ней "
            "может лежать и чужой доступ, и чужой портфель.")

    report = {"перенесено": {}, "убрано чужого": {}, "очищено целиком": {},
              "исправлено": [], "обезврежено": []}

    for name in sorted(set(DROP_ALL) & present):
        n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        conn.execute(f'DELETE FROM "{name}"')
        report["очищено целиком"][name] = n

    for name, (_why, keep) in CARRY.items():
        if name not in present:
            continue
        before = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        conn.execute(f'DELETE FROM "{name}" WHERE COALESCE(({keep}),0)=0')
        after = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        report["перенесено"][name] = after
        report["убрано чужого"][name] = before - after

    for table, column in sorted(WIPE_COLUMNS):
        if table not in present:
            continue
        if column in column_names(conn, table):
            cur = conn.execute(f'UPDATE "{table}" SET "{column}"=NULL '
                               f'WHERE "{column}" IS NOT NULL')
            report["исправлено"].append(f"{table}.{column}: обнулено {cur.rowcount}")

    if binds_as_requests and "agent_bind" in present:
        # Не по умолчанию — и это осознанно. Переезд «на чужой сервер» гасит
        # привязки, потому что доступ там выдаёт другой владелец. Здесь перенос
        # ВНУТРИ одной компании: без действующей привязки человек откроет
        # выданную завтра ссылку и не увидит ничего (db.get_binding пускает
        # только approved=1). Кому нужен строгий режим — вот он, флагом.
        cur = conn.execute("UPDATE agent_bind SET approved=0")
        report["обезврежено"].append(
            f"привязки приехали ЗАЯВКАМИ ({cur.rowcount}) — доступ откроется "
            "после одобрения в пульте")

    conn.commit()

    # --- контроль после чистки ------------------------------------------------
    # Дешевле проверить здесь, чем объяснять потом, откуда на машине компании
    # взялся чужой портфель.
    for name in sorted(set(DROP_ALL) & present):
        left = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        if left:
            raise Stop(f"чистка не сработала: в {name} осталось {left}. "
                       "Архив не выпущен.")
    stray = _one(conn, "SELECT COUNT(*) FROM clients WHERE COALESCE(responsible,'') "
                       "NOT IN (SELECT name FROM sel_emp)")
    if stray:
        raise Stop(f"в копии остались чужие договоры ({stray}). Архив не выпущен.")
    stray = _one(conn, "SELECT COUNT(*) FROM visits WHERE COALESCE(agent_id,-1) "
                       "NOT IN (SELECT tg FROM sel_tg)")
    if stray:
        raise Stop(f"в копии остались чужие визиты ({stray}). Архив не выпущен.")

    conn.execute("VACUUM")  # без него удалённые строки лежат в свободных страницах
    return report


def subset_inventory(conn: sqlite3.Connection, employees) -> dict:
    """Опись: числа, которые человек на той стороне сверит глазами."""
    inv = {
        "сотрудников": len(employees),
        "договоров": _one(conn, "SELECT COUNT(*) FROM clients"),
        "договоров в портфелях": _one(
            conn, "SELECT COUNT(*) FROM clients WHERE COALESCE(unassigned_from,'')=''"),
        "визитов": _one(conn, "SELECT COUNT(*) FROM visits"),
        "визитов с координатой": _one(
            conn, "SELECT COUNT(*) FROM visits WHERE visit_lat IS NOT NULL"),
        # Выезд — работа сотрудника, и он едет с ним даже если договор с тех пор
        # передали другому: иначе у человека на новой машине пропала бы часть
        # его же истории. Но сказать об этом надо вслух — карточку такого
        # выезда открыть будет нечем, пока договор не приедет отдельно.
        "в т.ч. по договорам вне архива": _one(
            conn, "SELECT COUNT(*) FROM visits WHERE COALESCE(contract_id,'') "
                  "NOT IN (SELECT COALESCE(contract_id,'') FROM clients)"),
        "снимков в индексе": _one(conn, "SELECT COUNT(*) FROM photo_geo"),
        "заметок": _one(conn, "SELECT COUNT(*) FROM notes WHERE deleted_at IS NULL"),
        "меток на карте": _one(conn, "SELECT COUNT(*) FROM markers"),
        "точек трека": _one(conn, "SELECT COUNT(*) FROM track_points"),
        "следов телефона": _one(conn, "SELECT COUNT(*) FROM visit_presence"),
        "доп. колонок реестра": _one(conn, "SELECT COUNT(*) FROM client_extra"),
        "строк реестра доставленного": _one(
            conn, "SELECT COUNT(*) FROM enrichment_ledger"),
        "привязок сотрудников": _one(conn, "SELECT COUNT(*) FROM agent_bind"),
    }
    first = _one(conn, "SELECT MIN(created_at) FROM visits", (), None)
    last = _one(conn, "SELECT MAX(created_at) FROM visits", (), None)
    inv["первый визит"] = str(first)[:10] if first else "—"
    inv["последний визит"] = str(last)[:10] if last else "—"
    return inv


def per_employee(conn: sqlite3.Connection, employees, photos) -> list[dict]:
    """По каждому человеку отдельно — иначе «приехало 1269 договоров» ничего не
    говорит о том, чей именно портфель не доехал."""
    by_contract: dict[str, int] = {}
    for rel, _path, _size in photos:
        head = rel.split("/")[0]
        by_contract[head] = by_contract.get(head, 0) + 1
    out = []
    for name in employees:
        tg = [r[0] for r in conn.execute(
            "SELECT telegram_id FROM agent_bind WHERE employee=?", (name,))]
        contracts = [r[0] for r in conn.execute(
            "SELECT contract_id FROM clients WHERE responsible=? "
            "AND COALESCE(contract_id,'')<>''", (name,))]
        visits = 0
        if tg:
            marks = "(" + ",".join("?" * len(tg)) + ")"
            visits = _one(conn, f"SELECT COUNT(*) FROM visits WHERE agent_id IN {marks}",
                          tuple(tg))
        out.append({
            "сотрудник": name,
            "telegram_id": tg,
            "договоров": len(contracts),
            "визитов": visits,
            "фотографий": sum(by_contract.get(c, 0) for c in contracts),
        })
    return out


def render_inventory(manifest: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("ОПИСЬ ПЕРЕНОСА ПОРТФЕЛЕЙ «ХАРД»")
    add("=" * 64)
    add(f"снято:            {manifest['снято']}")
    add(f"исходный сервер:  {manifest['исходный сервер']}")
    add(f"привязки:         {manifest['привязки']}")
    add("")
    add("СОТРУДНИКИ (по каждому — что именно едет):")
    for row in manifest["сотрудники"]:
        add(f"  {row['сотрудник']}")
        add(f"      договоров {row['договоров']}, визитов {row['визитов']}, "
            f"фотографий {row['фотографий']}, "
            f"telegram_id {', '.join(str(t) for t in row['telegram_id']) or '—'}")
    add("")
    add("СВЕРИТЬ ПОСЛЕ ПРИЁМА (эти числа должны совпасть):")
    for key, value in manifest["опись"].items():
        add(f"  {key:.<38} {value}")
    add("")
    add("ФАЙЛЫ:")
    add(f"  база .................................. {manifest['база']['размер']}")
    add(f"  sha256 базы ........................... {manifest['база']['sha256']}")
    add(f"  фотографий, файлов .................... {manifest['фотографии']['файлов']}")
    add(f"  фотографий, объём ..................... {manifest['фотографии']['размер']}")
    add("")
    add("СТРОК В ТАБЛИЦАХ (только то, что едет):")
    for name, n in manifest["таблицы"].items():
        add(f"  {name:.<38} {n}")
    add("")
    add("ЧТО НАМЕРЕННО НЕ ЕДЕТ (доступы, чужие настройки, чужая история):")
    for name, reason in sorted(manifest["не переносится"].items()):
        add(f"  {name:.<38} {reason}")
    tamed = manifest.get("обезврежено") or []
    if tamed:
        add("")
        add("ЧТО ПРИЕХАЛО ОБЕЗВРЕЖЕННЫМ:")
        for note in tamed:
            add(f"  • {note}")
    add("")
    add("Приём на другой машине:  ./migrate.sh receive <архив> --merge")
    add("Сверка схем до приёма:   ./migrate.sh compat <архив>")
    return "\n".join(lines) + "\n"


def cmd_take(args: argparse.Namespace) -> int:
    src_db, photos_dir = _source_paths(args)
    if not src_db.is_file():
        raise Stop(f"нет {src_db} — переносить нечего")

    requested: list[str] = []
    for chunk in (args.only_employees or []):
        requested += [p.strip() for p in re.split(r"[;\n]", chunk) if p.strip()]
    if not requested:
        raise Stop('укажите, чьи портфели везём: '
                   '--only-employees "Фамилия Имя;Фамилия Имя"')

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    copy = work / DB_NAME
    copy.unlink(missing_ok=True)

    log("  снимаю согласованную копию базы (в исходную не пишем ни строки)…")
    online_backup(src_db, copy)

    conn = sqlite3.connect(copy)
    try:
        bad = conn.execute("PRAGMA quick_check").fetchone()[0]
        if bad != "ok":
            raise Stop(f"снятая копия не проходит проверку целостности: {bad}")
        log("  копия целая (quick_check: ok)")
        # journal_mode=DELETE — чтобы в архив уехал один файл, без спутников
        # -wal/-shm: приняв базу без её же журнала, приёмник потерял бы хвост.
        conn.execute("PRAGMA journal_mode=DELETE")

        employees = resolve_employees(conn, requested)
        log("  везём портфели: " + "; ".join(employees))
        build_selection(conn, employees)
        cleaning = trim_to_employees(conn, employees,
                                     binds_as_requests=args.binds_as_requests)
        inv = subset_inventory(conn, employees)
        contracts = [r[0] for r in conn.execute(
            "SELECT contract_id FROM clients WHERE COALESCE(contract_id,'')<>''")]
        counts = counts_of(conn, CARRY)
        tables = sorted(tables_of(conn))
    finally:
        conn.close()

    photos: list[tuple[str, Path, int]] = []
    if not args.db_only:
        log("  собираю фотографии перенесённых КД…")
        photos = scan_photos_for(photos_dir, contracts)

    conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        people = per_employee(conn, employees, photos)
    finally:
        conn.close()

    db_sha = sha256_file(copy)
    photo_bytes = sum(size for _rel, _p, size in photos)
    manifest = {
        "схема": SCHEMA_VERSION,
        "вид": "портфели сотрудников",
        "снято": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "исходный сервер": args.host or _hostname(),
        "сотрудники": people,
        "список сотрудников": employees,
        "привязки": ("приехали заявками (--binds-as-requests)"
                     if args.binds_as_requests
                     else "приехали действующими: люди входят по ссылке сразу"),
        "только база": bool(args.db_only),
        "опись": inv,
        "таблицы": counts,
        "список таблиц": tables,
        "не переносится": dict(DROP_ALL),
        "обезврежено": cleaning.get("обезврежено") or [],
        "убрано чужого": cleaning.get("убрано чужого") or {},
        "договоры": {
            "всего": len(contracts),
            # Хэш списка КД вместо самого списка: в архиве и так лежит база, а
            # сверить «те ли договоры доехали» одной строкой удобнее, чем
            # глазами по тысяче номеров.
            "sha256 списка": hashlib.sha256(
                "\n".join(sorted(contracts)).encode("utf-8")).hexdigest(),
        },
        "база": {"имя": DB_NAME, "sha256": db_sha, "байт": copy.stat().st_size,
                 "размер": size_str(copy.stat().st_size)},
        "фотографии": {"файлов": len(photos), "байт": photo_bytes,
                       "размер": size_str(photo_bytes)},
    }
    inventory_txt = render_inventory(manifest)

    sums = io.StringIO()
    sums.write(f"{db_sha}  {DB_NAME}\n")
    for rel, path, _size in photos:
        sums.write(f"{sha256_file(path)}  photos/{rel}\n")
    sums_bytes = sums.getvalue().encode("utf-8")

    log("")
    log(inventory_txt)
    log(f"  пакую: база {manifest['база']['размер']}, "
        f"фотографий {len(photos)} ({manifest['фотографии']['размер']})")

    out_file = open(args.out, "wb") if args.out else None
    stream = out_file if out_file is not None else sys.stdout.buffer
    try:
        with tarfile.open(fileobj=stream, mode="w|") as tar:
            def add_bytes(name: str, payload: bytes) -> None:
                info = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{name}")
                info.size = len(payload)
                info.mtime = int(datetime.now(timezone.utc).timestamp())
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(payload))

            # Опись и манифест — первыми файлами: их вытаскивают из головы
            # архива, не распаковывая фотографии:
            #   tar -xOf архив.tar hard-subset/ОПИСЬ.txt
            add_bytes(INVENTORY_NAME, inventory_txt.encode("utf-8"))
            add_bytes(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False,
                                                indent=2).encode("utf-8"))
            add_bytes(SUMS_NAME, sums_bytes)
            tar.add(copy, arcname=f"{ARCHIVE_ROOT}/{DB_NAME}")
            for rel, path, _size in photos:
                tar.add(path, arcname=f"{ARCHIVE_ROOT}/photos/{rel}")
        stream.flush()
    finally:
        if out_file is not None:
            out_file.close()

    copy.unlink(missing_ok=True)
    log("  архив собран")
    return 0


# ---------------------------------------------------------------------------
# verify — проверка архива до того, как что-то тронуто
# ---------------------------------------------------------------------------

def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    if (root / ARCHIVE_ROOT).is_dir():
        root = root / ARCHIVE_ROOT
    manifest_path = root / MANIFEST_NAME
    db_path = root / DB_NAME
    sums_path = root / SUMS_NAME
    for p in (manifest_path, db_path, sums_path):
        if not p.is_file():
            raise Stop(f"в архиве нет {p.name} — это не архив переноса портфелей "
                       "(полный переезд принимает ./migrate.sh receive без --merge)")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("схема") != SCHEMA_VERSION:
        raise Stop(f"архив снят другой версией переноса (схема "
                   f"{manifest.get('схема')}, здесь {SCHEMA_VERSION})")

    problems: list[str] = []
    say = print

    say("1. Контрольные суммы файлов")
    checked = bad = 0
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        target = root / rel
        if not target.is_file():
            problems.append(f"файла нет: {rel}")
            bad += 1
            continue
        if sha256_file(target) != digest:
            problems.append(f"не совпала сумма: {rel}")
            bad += 1
        checked += 1
    say(f"   проверено файлов: {checked}, расхождений: {bad}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        say("2. Целостность базы")
        for pragma in ("quick_check", "integrity_check"):
            value = conn.execute(f"PRAGMA {pragma}").fetchone()[0]
            say(f"   {pragma:.<20} {value}")
            if value != "ok":
                problems.append(f"{pragma}: {value}")

        say("3. Опись")
        employees = manifest.get("список сотрудников") or []
        actual = subset_inventory(conn, employees)
        for key, want in (manifest.get("опись") or {}).items():
            got = actual.get(key)
            if got != want:
                problems.append(f"опись: {key} заявлено {want}, в базе {got}")
            say(f"   {key:.<38} {got}  [{'ок' if got == want else 'РАСХОЖДЕНИЕ'}]")

        say("4. Чужого в архиве нет")
        doors = 0
        for name in sorted(DROP_ALL):
            left = _one(conn, f'SELECT COUNT(*) FROM "{name}"')
            if left:
                doors += 1
                problems.append(f"в архиве осталась таблица {name} ({left} строк)")
        marks = ("(" + ",".join("?" * len(employees)) + ")") if employees else "('')"
        stray = _one(conn, "SELECT COUNT(*) FROM clients WHERE "
                           f"COALESCE(responsible,'') NOT IN {marks}",
                     tuple(employees))
        if stray:
            problems.append(f"в архиве чужие договоры: {stray}")
        say(f"   договоров вне списка сотрудников ..... {stray}")
        say(f"   таблиц доступа и чужой истории ....... {doors}")
    finally:
        conn.close()

    declared = int((manifest.get("фотографии") or {}).get("файлов") or 0)
    photos_dir = root / "photos"
    found = len(scan_photos_for(photos_dir, photo_roots(photos_dir)))
    say("5. Фотографии")
    say(f"   заявлено {declared}, в архиве {found}")
    if declared != found:
        problems.append(f"фотографий: заявлено {declared}, найдено {found}")

    if problems:
        say("")
        say("АРХИВ НЕ ПРИНЯТ. Найдено:")
        for p in problems:
            say(f"  • {p}")
        return 1
    say("")
    say("Архив проверен: база целая, суммы сошлись, опись сходится, чужого нет.")
    return 0


# ---------------------------------------------------------------------------
# compat — сверка схем двух баз
# ---------------------------------------------------------------------------

def compare_schemas(src: sqlite3.Connection, dst: sqlite3.Connection) -> dict:
    """Схема архива против схемы живой базы.

    Правило простое и неизменное: отсутствие ТАБЛИЦЫ — отказ (данные некуда
    класть), разница в КОЛОНКАХ — перенос по пересечению, но сказанный вслух.
    Обратное направление (в базе есть, в архиве нет) — норма: на принимающей
    машине может стоять код новее, и колонка заполнится значением по умолчанию.
    """
    src_tables = tables_of(src)
    dst_tables = tables_of(dst)
    carried = [t for t in CARRY if t in src_tables]

    out = {"нет таблиц": [], "лишние таблицы": sorted(src_tables - dst_tables),
           "колонки": {}, "индексы": {}, "отказы": [], "замечания": []}

    for table in carried:
        rows = _one(src, f'SELECT COUNT(*) FROM "{table}"')
        if table not in dst_tables:
            out["нет таблиц"].append(table)
            out["отказы"].append(
                f"в принимающей базе нет таблицы {table} (в архиве строк: {rows}). "
                "Обновите принимающую систему до того же выпуска и запустите её "
                "один раз — схему заводит init_db.")
            continue
        src_cols = {c["имя"]: c for c in columns_of(src, table)}
        dst_cols = {c["имя"]: c for c in columns_of(dst, table)}
        only_src = sorted(set(src_cols) - set(dst_cols))
        only_dst = sorted(set(dst_cols) - set(src_cols))
        common = [c for c in src_cols if c in dst_cols]
        out["колонки"][table] = {"общих": len(common), "только в архиве": only_src,
                                 "только в базе": only_dst}
        if only_src and rows:
            out["замечания"].append(
                f"{table}: колонки {', '.join(only_src)} есть в архиве, но не в "
                f"принимающей базе — их содержимое не приедет ({rows} строк "
                "перенесутся по пересечению)")
        for name in only_dst:
            col = dst_cols[name]
            # NOT NULL без умолчания — единственная разница колонок, которая
            # ломает доливку физически: INSERT без этой колонки не пройдёт.
            if col["not_null"] and col["умолчание"] is None and not col["ключ"] and rows:
                out["отказы"].append(
                    f"{table}.{name}: в принимающей базе колонка обязательна и без "
                    "значения по умолчанию, а в архиве её нет — строки не вставятся")
            elif rows:
                out["замечания"].append(
                    f"{table}: колонка {name} есть только в принимающей базе — "
                    "приедет пустой (значение по умолчанию)")
        src_idx, dst_idx = indexes_of(src, table), indexes_of(dst, table)
        diff_idx = {"только в архиве": sorted(set(src_idx) - set(dst_idx)),
                    "только в базе": sorted(set(dst_idx) - set(src_idx)),
                    "разошлись": sorted(n for n in set(src_idx) & set(dst_idx)
                                        if src_idx[n] != dst_idx[n])}
        if any(diff_idx.values()):
            out["индексы"][table] = diff_idx

    # Таблицы, которые не едут, всё равно сверяем — на вопрос «а базы вообще
    # совместимы?» отвечать надо целиком. Сюда попадает, например, app_links:
    # она не переносится никогда, но разошедшаяся схема входа означает, что на
    # машинах стоит разный код, и узнать об этом лучше сейчас.
    out["прочие таблицы"] = {}
    for table in sorted((src_tables & dst_tables) - set(CARRY)):
        src_cols = {c["имя"] for c in columns_of(src, table)}
        dst_cols = {c["имя"] for c in columns_of(dst, table)}
        if src_cols != dst_cols:
            out["прочие таблицы"][table] = {
                "только в архиве": sorted(src_cols - dst_cols),
                "только в базе": sorted(dst_cols - src_cols)}
    return out


def render_compat(diff: dict) -> str:
    lines = ["СВЕРКА СХЕМ", "-" * 64]
    tables = diff["колонки"]
    same = [t for t, c in tables.items()
            if not c["только в архиве"] and not c["только в базе"]]
    lines.append(f"  таблиц сверено: {len(tables) + len(diff['нет таблиц'])}, "
                 f"совпали полностью: {len(same)}")
    for table, cols in sorted(tables.items()):
        if cols["только в архиве"] or cols["только в базе"]:
            lines.append(f"  {table}: общих колонок {cols['общих']}"
                         + (f", только в архиве: {', '.join(cols['только в архиве'])}"
                            if cols["только в архиве"] else "")
                         + (f", только в базе: {', '.join(cols['только в базе'])}"
                            if cols["только в базе"] else ""))
    for table, idx in sorted(diff["индексы"].items()):
        parts = [f"{k}: {', '.join(v)}" for k, v in idx.items() if v]
        lines.append(f"  индексы {table} — " + "; ".join(parts))
    if diff["лишние таблицы"]:
        lines.append("  таблицы архива, которых нет в базе: "
                     + ", ".join(diff["лишние таблицы"]))
    прочие = diff.get("прочие таблицы") or {}
    if прочие:
        lines.append("  (не переносятся, но схемы разошлись — значит на машинах "
                     "разный выпуск кода:)")
        for table, cols in sorted(прочие.items()):
            lines.append(f"    {table}: "
                         + (f"только в архиве: {', '.join(cols['только в архиве'])}"
                            if cols["только в архиве"] else "")
                         + ("; " if cols["только в архиве"] and cols["только в базе"]
                            else "")
                         + (f"только в базе: {', '.join(cols['только в базе'])}"
                            if cols["только в базе"] else ""))
    for note in diff["замечания"]:
        lines.append(f"  [!] {note}")
    for note in diff["отказы"]:
        lines.append(f"  [ОТКАЗ] {note}")
    if diff["отказы"]:
        lines.append("")
        lines.append("Перенос невозможен, пока это не исправлено.")
    elif diff["замечания"]:
        lines.append("")
        lines.append("Перенос возможен ПО ПЕРЕСЕЧЕНИЮ колонок — см. пометки выше.")
    else:
        lines.append("")
        lines.append("Схемы совпадают: перенесётся всё, ничего не потеряется.")
    return "\n".join(lines)


def cmd_compat(args: argparse.Namespace) -> int:
    dst_db, _photos = _target_paths(args)
    src_db, _src_photos = _archive_paths(args)
    if not dst_db.is_file():
        raise Stop(f"нет принимающей базы {dst_db} — сначала ./install.sh и "
                   "один запуск: схему заводит приложение")
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    dst = sqlite3.connect(f"file:{dst_db}?mode=ro", uri=True)
    try:
        diff = compare_schemas(src, dst)
    finally:
        src.close()
        dst.close()
    print(render_compat(diff))
    if diff["отказы"]:
        raise Stop("схемы несовместимы (см. отказы выше)")
    return 0


# ---------------------------------------------------------------------------
# merge — доливка в существующую базу
# ---------------------------------------------------------------------------

def _counter_column(conn: sqlite3.Connection, table: str) -> str | None:
    """Колонка-счётчик: INTEGER PRIMARY KEY, которая НЕ входит в естественный
    ключ таблицы. Она не переносится — номер строки есть свойство ЭТОЙ базы, и
    привезённый чужой номер либо столкнулся бы с местным, либо оставил бы дыру
    в нумерации.

    Оговорка про естественный ключ здесь не формальность: у agent_bind и users
    первичный ключ — telegram_id, и он-то как раз и едет. Выбросив его «как
    счётчик», мы привезли бы привязку без человека."""
    natural = set(KEYS.get(table, ()))
    for col in columns_of(conn, table):
        if col["ключ"] and col["тип"] == "INTEGER" and col["имя"] not in natural:
            return col["имя"]
    return None


def contested_contracts(src: sqlite3.Connection, dst: sqlite3.Connection,
                        owners: set[str]) -> dict[str, str]:
    """Номера договоров, которые на ПРИНИМАЮЩЕЙ машине закреплены за другим.

    Возвращает {номер договора: ФИО здешнего владельца}. Считается один раз, до
    первой записи, и дальше обходится везде, где строка привязана к КД.

    Почему отдельным списком, а не колонкой «чей это»: защита от подмены стояла
    только у самих ``clients`` и ``enrichment_ledger``, у которых есть
    ``responsible``. А ``client_extra``, ``photo_geo`` и файлы снимков едут по
    номеру договора и своего владельца не знают — при совпавшем номере они
    молча ложились в карточку чужого должника, и отчёт при этом писал про этот
    договор «НЕ ТРОНУТО».

    ``owners`` — ФИО приехавших в НОРМАЛЬНОМ виде (см. ``_norm``).
    """
    ours: set[str] = set()
    for sql in ("SELECT DISTINCT contract_id FROM clients",
                "SELECT DISTINCT contract_id FROM enrichment_ledger"):
        try:
            ours |= {str(r[0]).strip() for r in src.execute(sql)
                     if str(r[0] or "").strip()}
        except sqlite3.Error:
            continue
    if not ours:
        return {}
    # Реестр (clients) — это и есть карточка, поэтому его слово последнее:
    # сначала собираем владельцев из реестра доставленного, затем поверх кладём
    # владельцев из реестра.
    owner_of: dict[str, str] = {}
    for sql in ("SELECT contract_id, responsible FROM enrichment_ledger",
                "SELECT contract_id, responsible FROM clients"):
        try:
            rows = dst.execute(sql).fetchall()
        except sqlite3.Error:
            continue
        for contract, owner in rows:
            contract = str(contract or "").strip()
            owner = str(owner or "").strip()
            if contract in ours and owner:
                owner_of[contract] = owner
    return {c: o for c, o in owner_of.items() if _norm(o) not in owners}


def _next_local_uid(conn: sqlite3.Connection) -> int:
    """Свободный служебный id — тем же способом, что db.local_identity_for:
    следующий за максимальным в agent_bind и users, но не ниже базы."""
    top = LOCAL_UID_BASE - 1
    for table in ("agent_bind", "users"):
        try:
            value = int(_one(conn, f'SELECT MAX(telegram_id) FROM "{table}"', (), 0) or 0)
        except (TypeError, ValueError):
            value = 0
        top = max(top, value)
    return top + 1


def build_identity_remap(src: sqlite3.Connection,
                         dst: sqlite3.Connection) -> tuple[dict[int, int], list[str]]:
    """Под каким id приехавшие люди живут НА ЭТОЙ машине. (карта, пояснения)

    В системе «одно ФИО — один id», и db.set_bind держит это правило удалением
    прежних строк ФИО. Доливка ключует agent_bind по telegram_id и правила не
    знала, а это ломается в обе стороны:

      * пульт уже выдал человеку вход (db.local_identity_for завела служебный
        id, телефон вошёл) — архив привозил ВТОРУЮ действующую привязку на то
        же ФИО. db.telegram_for_employee отдаёт первый id по порядку, поэтому
        «Отозвать вход» гасил бы не тот, новые выезды писались бы на здешний
        id, а привезённая история — на привезённый, и статистика сотрудника
        распадалась бы надвое;
      * служебные id раздаются на каждой машине СВОИМ счётчиком (MAX+1), так
        что один и тот же номер на проде и здесь принадлежит разным людям —
        привязка приехала бы поверх чужой и увела бы себе её выезды.

    Поэтому личность приводится к здешней: id из архива заменяется здешним id
    того же ФИО, а если номер здесь занят другим человеком — выдаётся свободный
    служебный id (ровно как в db.local_identity_for, чтобы выданная завтра
    ссылка нашла ту же привязку). ФИО сравниваются нормализованно.
    """
    remap: dict[int, int] = {}
    notes: list[str] = []
    try:
        arrived = [(int(tg), str(emp or "").strip()) for tg, emp in src.execute(
            "SELECT telegram_id, employee FROM agent_bind "
            "WHERE telegram_id IS NOT NULL")]
    except (sqlite3.Error, TypeError, ValueError):
        return remap, notes

    here_by_name: dict[str, int] = {}
    here_by_id: dict[int, str] = {}
    try:
        rows = dst.execute(
            "SELECT telegram_id, employee, COALESCE(approved,0) AS ok FROM agent_bind "
            "WHERE telegram_id IS NOT NULL ORDER BY ok DESC, telegram_id").fetchall()
    except sqlite3.Error:
        return remap, notes
    for tg, emp, _ok in rows:
        try:
            tg = int(tg)
        except (TypeError, ValueError):
            continue
        here_by_id[tg] = str(emp or "").strip()
        # setdefault при таком порядке оставляет ОДОБРЕННУЮ привязку — тот же
        # выбор, что делает db.local_identity_for.
        here_by_name.setdefault(_norm(emp), tg)

    free = _next_local_uid(dst)
    for tg, emp in sorted(arrived):
        here = here_by_name.get(_norm(emp))
        if here is not None:
            if here != tg:
                remap[tg] = here
                notes.append(f"«{emp}»: id {tg} → {here} — вход этому человеку "
                             "на этой машине уже выдан")
            continue
        busy = here_by_id.get(tg)
        if busy is not None and _norm(busy) != _norm(emp):
            remap[tg] = free
            notes.append(f"«{emp}»: id {tg} → {free} — номер {tg} здесь занят "
                         f"«{busy}»")
            here_by_name[_norm(emp)] = free
            here_by_id[free] = emp
            free += 1
    return remap, notes


def merge_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str,
                owners: set[str], contested: dict[str, str] | None = None,
                remap: dict[int, int] | None = None) -> dict:
    """Долить одну таблицу. Возвращает числа «было/приехало/стало».

    ``owners`` — ФИО приехавших в нормальном виде, ``contested`` — спорные
    номера договоров, ``remap`` — карта «id в архиве → id на этой машине».
    """
    stat = {"было": _one(dst, f'SELECT COUNT(*) FROM "{table}"'), "приехало": 0,
            "добавлено": 0, "обновлено": 0, "без изменений": 0, "пропущено": 0}
    src_cols = column_names(src, table)
    dst_cols = set(column_names(dst, table))
    skip = {_counter_column(src, table), _counter_column(dst, table)} - {None}
    cols = [c for c in src_cols if c in dst_cols and c not in skip]
    if not cols:
        return stat
    quoted = ", ".join(f'"{c}"' for c in cols)
    rows = src.execute(f'SELECT {quoted} FROM "{table}"').fetchall()
    stat["приехало"] = len(rows)
    if not rows:
        return stat

    key_cols = [k for k in KEYS.get(table, ()) if k in cols]
    owner_col = OWNER_COLUMN.get(table)
    owner_at = cols.index(owner_col) if owner_col in cols else None
    contract_col = CONTRACT_COLUMN.get(table)
    contract_at = cols.index(contract_col) if contract_col in cols else None
    ident_at = [cols.index(c) for c in IDENTITY_COLUMNS.get(table, ()) if c in cols]
    keep_at = [cols.index(c) for c in KEEP_HERE.get(table, ()) if c in cols]
    insert_sql = (f'INSERT INTO "{table}" ({quoted}) VALUES '
                  f'({", ".join("?" * len(cols))})')
    no_key: list[tuple] = []
    said: set[str] = set()   # про один спорный КД говорим один раз, а не 40

    for row in rows:
        values = tuple(row)
        if remap and ident_at:
            # Личность приезжего — здешняя (см. build_identity_remap). Замена
            # идёт ДО вычисления ключа: у agent_bind и users ключ и есть id.
            swapped = list(values)
            for at in ident_at:
                try:
                    old = int(swapped[at])
                except (TypeError, ValueError):
                    continue
                if old in remap:
                    swapped[at] = remap[old]
            values = tuple(swapped)
        if contract_at is not None and contested:
            contract = str(values[contract_at] or "").strip()
            owner = contested.get(contract)
            if owner:
                # Тот же номер договора здесь у другого человека. Долить сюда
                # доп. поля и снимки — подмешать чужому должнику наши данные.
                stat["пропущено"] += 1
                if contract not in said:
                    said.add(contract)
                    stat.setdefault("чужие ключи", []).append(
                        f"{table}: договор {contract} на этой машине у «{owner}»")
                continue
        keys = [values[cols.index(k)] for k in key_cols] if key_cols else []
        # Ключ годится, только если он весь заполнен: у визитов времён бота
        # op_id пуст, и «ключ из одних NULL» склеил бы разные строки в одну.
        if not key_cols or any(k is None or k == "" for k in keys):
            no_key.append(values)
            continue
        where = " AND ".join(f'"{k}"=?' for k in key_cols)
        found = dst.execute(f'SELECT {quoted} FROM "{table}" WHERE {where}',
                            tuple(keys)).fetchone()
        if found is None:
            try:
                dst.execute(insert_sql, values)
                stat["добавлено"] += 1
            except sqlite3.IntegrityError:
                # Ключ занят другой строкой (совпал op_id, file и т.п.).
                # Ронять весь перенос из-за одной строки нельзя.
                stat["пропущено"] += 1
            continue
        if owner_at is not None:
            here = str(found[owner_at] or "").strip()
            # Сравнение нормализованное: ФИО печатают руками, и «Холназаров
            # Шерали» с двумя пробелами — тот же человек. Точное сравнение
            # объявляло бы КАЖДЫЙ договор такого сотрудника чужим, свежая
            # работа не приезжала бы, а отчёт врал бы про «другого человека».
            if here and _norm(here) not in owners:
                # Номер договора в проекте не уникален между республиками —
                # 47892257 встречался дважды. Перезапись стёрла бы чужого
                # должника вместе с его историей, поэтому не трогаем и считаем.
                stat["пропущено"] += 1
                stat.setdefault("чужие ключи", []).append(
                    f"{table}: {keys[0]} закреплён за «{here}»")
                continue
        if keep_at:
            # Здешнее написание ФИО не трогаем: им привязка сходится со здешним
            # реестром (clients.responsible = agent_bind.employee).
            kept = list(values)
            for at in keep_at:
                kept[at] = found[at]
            values = tuple(kept)
        if tuple(found) == values:
            stat["без изменений"] += 1
            continue
        sets = ", ".join(f'"{c}"=?' for c in cols)
        dst.execute(f'UPDATE "{table}" SET {sets} WHERE {where}',
                    values + tuple(keys))
        stat["обновлено"] += 1

    # Строки без ключа сверяем целиком и ПО КОЛИЧЕСТВУ: две одинаковые строки в
    # источнике — это две строки, и приём не имеет права склеить их в одну, но
    # и удвоить их при повторном приёме тоже не имеет права.
    if no_key:
        where = " AND ".join(f'"{c}" IS ?' for c in cols)  # IS: NULL равен NULL
        for values, want in Counter(no_key).items():
            have = dst.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}',
                               values).fetchone()[0]
            for _ in range(max(0, want - have)):
                try:
                    dst.execute(insert_sql, values)
                    stat["добавлено"] += 1
                except sqlite3.IntegrityError:
                    stat["пропущено"] += 1
            stat["без изменений"] += min(want, have)
    return stat


def merge_photos(src_dir: Path, dst_dir: Path, dry_run: bool = False,
                 contested: dict[str, str] | None = None) -> dict:
    """Снимки: раскладка та же (photos/<КД>/<файл>), поэтому копируем как есть.

    Файл, который уже лежит на месте и совпадает по размеру, не трогаем: у
    снимка нет версий, переписывать его нечем и незачем, а повторный приём
    архива обязан быть бесплатным.

    Спорные КД (``contested``) обходим стороной: каталог снимков называется
    номером договора, и при совпавшем номере наши фотографии легли бы рядом с
    фотографиями чужого должника — открыв карточку, здешний оператор увидел бы
    в ней снимки другого человека.
    """
    stat = {"было": 0, "приехало": 0, "добавлено": 0, "уже было": 0,
            "заменено": 0, "чужих КД": 0}
    if dst_dir.is_dir():
        stat["было"] = len(scan_photos_for(dst_dir, photo_roots(dst_dir)))
    # КД со слэшем даёт вложенный каталог, поэтому сравниваем не имя верхнего
    # каталога, а начало пути.
    skip = tuple(f"{str(c).strip('/')}/" for c in (contested or {}) if str(c).strip())
    if src_dir.is_dir():
        for rel, path, size in scan_photos_for(src_dir, photo_roots(src_dir)):
            stat["приехало"] += 1
            if skip and rel.startswith(skip):
                stat["чужих КД"] += 1
                continue
            target = dst_dir.joinpath(*rel.split("/"))
            if target.is_file() and target.stat().st_size == size:
                stat["уже было"] += 1
                continue
            existed = target.is_file()
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            stat["заменено" if existed else "добавлено"] += 1
    stat["стало"] = stat["было"] + stat["добавлено"]
    return stat


def render_merge(tables: dict, photos: dict, conflicts: list[str],
                 identities: list[str] | None = None) -> str:
    lines = ["ПЕРЕНЕСЕНО (было → стало)", "-" * 84,
             f"  {'таблица':<24}{'было':>8}{'приехало':>10}{'добавлено':>11}"
             f"{'обновлено':>11}{'пропущено':>11}{'стало':>8}"]
    for name, s in tables.items():
        lines.append(f"  {name:<24}{s['было']:>8}{s['приехало']:>10}"
                     f"{s['добавлено']:>11}{s['обновлено']:>11}"
                     f"{s['пропущено']:>11}{s['стало']:>8}")
    lines.append("")
    lines.append(f"  фотографий: было {photos['было']}, в архиве {photos['приехало']}, "
                 f"добавлено {photos['добавлено']}, уже было {photos['уже было']}, "
                 f"стало {photos['стало']}"
                 + (f", не разложено по чужим договорам {photos['чужих КД']}"
                    if photos.get("чужих КД") else ""))
    if identities:
        lines.append("")
        lines.append("ЛИЧНОСТИ ПРИВЕДЕНЫ К ЗДЕШНИМ (правило «одно ФИО — один id»):")
        for note in identities:
            lines.append(f"  • {note}")
        lines.append("  Привезённая история записана на здешний номер. Иначе выезды "
                     "сотрудника разошлись бы по двум id, а «Отозвать вход» в пульте "
                     "гасил бы не тот.")
    if conflicts:
        lines.append("")
        lines.append("НЕ ТРОНУТО — эти ключи на этой машине принадлежат другим людям:")
        for note in conflicts[:20]:
            lines.append(f"  • {note}")
        if len(conflicts) > 20:
            lines.append(f"  … и ещё {len(conflicts) - 20}")
        lines.append("  Это не поломка: номер договора в проекте повторяется между "
                     "республиками. Разберите вручную, если они должны были приехать.")
    return "\n".join(lines)


def cmd_merge(args: argparse.Namespace) -> int:
    dst_db, dst_photos = _target_paths(args)
    src_db, src_photos = _archive_paths(args)
    if not dst_db.is_file():
        raise Stop(f"нет принимающей базы {dst_db} — сначала ./install.sh и "
                   "один запуск: схему заводит приложение")

    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_db, timeout=60)
    tables: dict[str, dict] = {}
    conflicts: list[str] = []
    try:
        diff = compare_schemas(src, dst)
        print(render_compat(diff))
        print("")
        if diff["отказы"]:
            raise Stop("схемы несовместимы — доливка не начата, база не тронута")

        # Чьё именно приехало. Спрашиваем сам архив, а не опись: инструменту
        # должно хватать двух файлов баз (см. --subset), описи рядом может не
        # быть. Три источника, потому что у сотрудника бывает пустой портфель —
        # и тогда его имя знают только привязка и реестр доставленного.
        #
        # Сразу в нормальном виде (_norm): ФИО печатают руками, и «Холназаров
        # Шерали» с двумя пробелами — тот же человек. Точное сравнение объявляло
        # бы все его договоры чужими.
        owners: set[str] = set()
        for sql in ("SELECT DISTINCT responsible FROM clients",
                    "SELECT DISTINCT responsible FROM enrichment_ledger",
                    "SELECT DISTINCT employee FROM agent_bind"):
            try:
                owners |= {_norm(r[0]) for r in src.execute(sql) if _norm(r[0])}
            except sqlite3.Error:
                continue
        # Оба списка считаем ДО первой записи: спорные номера — по нетронутой
        # базе приёмника, карту личностей — по её нынешним привязкам.
        contested = contested_contracts(src, dst, owners)
        remap, identities = build_identity_remap(src, dst)
        src_tables, dst_tables = tables_of(src), tables_of(dst)
        # Одна транзакция на всю доливку: половина перенесённого хуже, чем ничего.
        dst.execute("BEGIN IMMEDIATE")
        try:
            for table in CARRY:
                if table not in src_tables or table not in dst_tables:
                    continue
                stat = merge_table(src, dst, table, owners, contested, remap)
                conflicts += stat.pop("чужие ключи", [])
                stat["стало"] = _one(dst, f'SELECT COUNT(*) FROM "{table}"')
                tables[table] = stat
            if args.dry_run:
                dst.execute("ROLLBACK")
            else:
                dst.execute("COMMIT")
        except BaseException:  # noqa: BLE001
            # Откатываем при ЛЮБОМ исходе, включая отказ инструмента (Stop — это
            # SystemExit, до «except Exception» он бы не дошёл) и Ctrl-C.
            dst.execute("ROLLBACK")
            raise
    finally:
        src.close()
        dst.close()

    photos = merge_photos(src_photos, dst_photos, dry_run=args.dry_run,
                          contested=contested)
    if photos.get("чужих КД"):
        conflicts.append(
            f"фотографии: {photos['чужих КД']} файлов не разложены — их договоры "
            "на этой машине принадлежат другим людям")
    print(render_merge(tables, photos, conflicts, identities))
    if args.dry_run:
        print("")
        print("Это была примерка (--dry-run): база и фотографии не изменены.")
    return 0


# ---------------------------------------------------------------------------
# employees — кого вообще можно выбрать и как их ФИО пишутся в реестре
# ---------------------------------------------------------------------------

def cmd_employees(args: argparse.Namespace) -> int:
    db_path, _photos = _target_paths(args)
    if not db_path.is_file():
        raise Stop(f"нет {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT responsible, COUNT(*) FROM clients "
            "WHERE COALESCE(responsible,'')<>'' GROUP BY responsible "
            "ORDER BY COUNT(*) DESC").fetchall()
        binds: dict[str, int] = {}
        visits: dict[str, int] = {}
        for name, tg in conn.execute("SELECT employee, telegram_id FROM agent_bind"):
            binds[name] = binds.get(name, 0) + 1
            visits[name] = visits.get(name, 0) + _one(
                conn, "SELECT COUNT(*) FROM visits WHERE agent_id=?", (tg,))
    finally:
        conn.close()
    if args.json:
        print(json.dumps([{"сотрудник": n, "договоров": c,
                           "визитов": visits.get(n, 0),
                           "привязок": binds.get(n, 0)} for n, c in rows],
                         ensure_ascii=False, indent=2))
        return 0
    print(f"  {'сотрудник':<34}{'договоров':>10}{'визитов':>9}{'привязок':>10}")
    for name, count in rows:
        print(f"  {name:<34}{count:>10}{visits.get(name, 0):>9}{binds.get(name, 0):>10}")
    print("")
    print("ФИО для --only-employees пишутся ровно так, как в первой колонке.")
    return 0


# ---------------------------------------------------------------------------
# Пути: одинаково и для контейнера (--data /data), и для двух файлов на диске
# ---------------------------------------------------------------------------

def _source_paths(args) -> tuple[Path, Path]:
    data = Path(getattr(args, "data", "") or ".")
    db = Path(args.db) if getattr(args, "db", "") else data / LIVE_DB_NAME
    photos = Path(args.photos) if getattr(args, "photos", "") else data / "photos"
    return db, photos


# Источник и приёмник описываются одинаково — разными они становятся от команды.
_target_paths = _source_paths


def _archive_paths(args) -> tuple[Path, Path]:
    """Сторона архива: либо распакованный каталог (--archive), либо просто файл
    базы (--subset) — второй способ и делает инструмент проверяемым руками."""
    if getattr(args, "archive", ""):
        root = Path(args.archive)
        if (root / ARCHIVE_ROOT).is_dir():
            root = root / ARCHIVE_ROOT   # указали каталог распаковки целиком
        db = root / DB_NAME
        photos = (Path(args.photos_from) if getattr(args, "photos_from", "")
                  else root / "photos")
        if not db.is_file():
            raise Stop(f"в {root} нет {DB_NAME} — это не архив переноса портфелей")
        return db, photos
    if getattr(args, "subset", ""):
        db = Path(args.subset)
        if not db.is_file():
            raise Stop(f"нет файла {db}")
        photos = (Path(args.photos_from) if getattr(args, "photos_from", "")
                  else db.parent / "photos")
        return db, photos
    raise Stop("укажите, что принимаем: --archive КАТАЛОГ или --subset ФАЙЛ")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="subset_handover",
        description="Перенос портфелей нескольких сотрудников «Хард»")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def with_data(p):
        p.add_argument("--data", default="/data",
                       help="каталог данных (внутри контейнера /data)")
        p.add_argument("--db", default="", help="файл базы вместо --data")
        p.add_argument("--photos", default="",
                       help="каталог фотографий вместо --data/photos")

    def with_archive(p):
        p.add_argument("--archive", default="", help="распакованный архив")
        p.add_argument("--subset", default="", help=f"просто файл {DB_NAME}")
        p.add_argument("--photos-from", default="", dest="photos_from",
                       help="каталог фотографий архива")

    p = sub.add_parser("take", help="снять портфели названных сотрудников")
    with_data(p)
    p.add_argument("--only-employees", action="append", default=[],
                   dest="only_employees", help="ФИО через «;»")
    p.add_argument("--db-only", action="store_true", dest="db_only",
                   help="без фотографий")
    p.add_argument("--binds-as-requests", action="store_true",
                   dest="binds_as_requests",
                   help="привязки приедут заявками (по умолчанию — действующими)")
    p.add_argument("--work", default="/tmp/hard-subset")
    p.add_argument("--host", default="")
    p.add_argument("--out", default="", help="файл вместо stdout")
    p.set_defaults(func=cmd_take)

    p = sub.add_parser("verify", help="проверить распакованный архив")
    p.add_argument("--dir", required=True)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("compat", help="сверить схемы архива и принимающей базы")
    with_data(p)
    with_archive(p)
    p.set_defaults(func=cmd_compat)

    p = sub.add_parser("merge", help="долить архив в принимающую базу")
    with_data(p)
    with_archive(p)
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("employees", help="кого можно выбрать и как пишутся ФИО")
    with_data(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_employees)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Stop as stop:
        print(f"ОШИБКА: {stop}", file=sys.stderr)
        raise SystemExit(2)
