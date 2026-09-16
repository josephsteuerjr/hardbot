"""Сборщик пакета исходников «Хард» для передачи ИТ-службе.

    python tools/build_it_package.py [--version 1.2.0] [--out dist] [--root .]

Результат: ``dist/hard-<версия>-<дата>.zip`` и рядом ``.sha256``. Внутри —
снимок исходников без git-истории: сервер (server/), служба речи (stt/),
пульт (pult/), перенос реестров (registry_cycle/), установка (deploy/),
документы (docs/), тесты (tests/), инструменты (tools/) и корневые файлы
проекта, плюс README пакета с картой, что где лежит.

Перед упаковкой сканер ищет утечки: настоящие токены Telegram, адреса наших
серверов, пароли, приватные ключи. Любая находка — STOP с путём к файлу, пакет
не создаётся. Версия берётся из тега git (``git describe``) или ``--version``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_PREFIX = "hard"

# Что едет в пакет: каталоги целиком (после исключений) и корневые файлы.
INCLUDE_DIRS = ("server", "stt", "pult", "registry_cycle", "deploy", "docs", "tests", "tools")
INCLUDE_ROOT_FILES = ("pytest.ini", "SECURITY.md")
INCLUDE_ROOT_GLOBS = ("requirements-*.txt", "*.pyw")   # .pyw — точки входа без консоли

# Каталоги проекта, которых в пакете быть не должно ни при каком раскладе.
EXCLUDE_TOP = {"_local", "_infra", "!Архив проекта", "engine", "outputs", "ДЛЯ ШАРЫ",
               "скрины бота", "_СПАСЁННОЕ", "dist"}
# Имена каталогов, вырезаемые на любой глубине.
EXCLUDE_DIR_NAMES = {".git", "__pycache__", ".pytest_cache", ".qa", ".venv", "venv",
                     "node_modules", "target", "src-tauri", "_devdata", "data_dev", "data",
                     "_priodemo", "photos", "backups", "models", "secrets", "отчёты возврата",
                     "audit_reports", "production_snapshots"}
# Имена файлов, вырезаемые на любой глубине.
EXCLUDE_FILE_NAMES = {"plink.exe", "start_server_proxy.bat", "Сервер.txt",
                      "настройки пульта.json", "конфигурация путей.json", "Thumbs.db",
                      ".DS_Store"}
# Точечные исключения (относительные POSIX-пути от корня проекта).
EXCLUDE_PATHS = {
    "server/gui.py",             # устаревший Tk-пульт; актуальный — pult/
    "server/run.bat",
    "server/_dev_seed.py",
    "server/miniapp_dev.py",
    "server/miniapp_dev.bat",
    "server/docker-compose.dev.yml",
    "server/docker-compose.local.yml",
    "tools/build_registry_transfer_share.py",   # собирает локальную папку «ДЛЯ ШАРЫ»
}
EXCLUDE_PATH_GLOBS = ("server/_devdata/*", "server/data_dev/*", "server/miniapp_dev.*",
                      "server/*.backup-*", "server/.deploy-*",
                      # Готовый офлайн-пакет (образы, модель, колёса) — это гигабайты,
                      # он едет на флешке, а не внутри архива с исходниками. Сами
                      # скрипты его сборки в пакет попадают, содержимое out/ — нет.
                      "deploy/offline/out/*")
# Расширения, которым в пакете не место: данные, секреты, офисные файлы, бинарники.
EXCLUDE_SUFFIXES = {".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3",
                    ".xlsb", ".xlsx", ".xlsm", ".xls", ".docx", ".doc", ".pptx", ".pyc", ".pyo",
                    ".log", ".csv", ".jsonl", ".pmtiles", ".zip", ".7z", ".tar", ".gz", ".exe",
                    ".pem", ".key", ".ppk", ".tmp", ".part"}

# --- поставка без интернета ---------------------------------------------------------
# Правила выше вырезают каталог «models» на любой глубине и любые архивы. Для
# рабочих каталогов это верно: там лежит скачанный кэш моделей и чей-то zip с
# данными. Но ровно эти же правила молча съедали и то, ради чего офлайн-поставка
# существует, — модель речи и архивы образов, положенные для ИТ-службы.
#
# Послабление сделано узким: только внутри deploy/offline/, только для каталога
# «models» и только для архивных суффиксов. Всё остальное там действует как
# везде — .env, ключи, базы, секреты и сканер утечек. То, что вырезано этими
# двумя правилами ГДЕ-ТО ЕЩЁ, сборщик не молчит, а перечисляет в конце работы:
# «молча не доехало» — худший из возможных исходов.
OFFLINE_PREFIX = "deploy/offline/"
PAYLOAD_DIR_NAMES = {"models"}
PAYLOAD_SUFFIXES = (".zip", ".tar", ".gz", ".tgz")

# --- подготовленная модель речи -----------------------------------------------------
# Каталог с весами, которые запекаются в образ службы речи. Он НАМЕРЕННО закрыт
# собственным .gitignore: гигабайту весов не место в истории репозитория. Но
# список кандидатов сборщик берёт у git (`ls-files --others --exclude-standard`),
# а git про игнорируемое молчит — и модель не доезжала до ИТ-службы БЕЗ ЕДИНОГО
# СЛОВА. На месте это выглядело так: образ речи собирается, контейнер уходит в
# цикл перезапусков «в образе модели нет, а скачивание выключено», и инженер
# ищет поломку там, где её нет. Поэтому каталог добавляется к кандидатам прямо
# с диска, а собранный пакет в конце работы говорит вслух, есть в нём модель
# или нет.
#
# Байт в байт и без сканера: в hard-model.json записаны РАЗМЕРЫ и SHA-256 каждого
# файла, служба сверяет их при старте. Замена переводов строк (нормализация
# текста) изменила бы размер — и служба честно отказалась бы работать с
# «недовезённой» моделью. Сканер утечек здесь тоже не нужен и вреден: словарь на
# три мегабайта он читает как текст, медленно и с шансом ложного срабатывания.
BAKED_MODEL_DIR = "stt/model"
BAKED_MODEL_WEIGHTS = "stt/model/model.bin"
BAKED_MODEL_MANIFEST = "stt/model/hard-model.json"

TEXT_SUFFIXES = {".py", ".pyw", ".md", ".txt", ".sh", ".yml", ".yaml", ".json", ".ini", ".cfg", ".toml",
                 ".bat", ".html", ".js", ".css", ".webmanifest", ".example", ""}
TEXT_NAMES = {"Dockerfile", "Caddyfile", ".dockerignore", ".gitignore", ".gitattributes",
              ".env.example"}
CRLF_SUFFIXES = {".bat"}

# Сканер утечек: (название правила, регулярное выражение).
LEAK_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("токен Telegram-бота", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("BOT_TOKEN со значением", re.compile(r"BOT_TOKEN\s*=\s*['\"]?\d{6,}:[A-Za-z0-9_-]{20,}")),
    # Адреса наших серверов записаны в экранированном виде, чтобы сканер не
    # ловил сам себя; в сообщении о находке печатается только номер правила.
    ("адрес боевого сервера (№1)", re.compile(r"209\.222\.251\.112")),
    ("адрес боевого сервера (№2)", re.compile(r"38\.135\.54\.29")),
    # Прежний адрес, снятый провайдером в августе. Ловим не потому, что он
    # ещё живой, а потому что один раз он уже уехал в пакет в geo/caddy.sh.
    ("адрес боевого сервера (№3, прежний)", re.compile(r"166\.88\.185\.90")),
    ("пароль в открытом виде",
     re.compile(r"(?im)^[^\n#]*(?:password|passwd|пароль)\s*[:=]\s*['\"]?[^\s'\"]{6,}")),
    ("приватный ключ", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("ключ доступа AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
)


class BuildStop(SystemExit):
    """STOP сборки с человеческим объяснением (код выхода 2)."""

    def __init__(self, message: str) -> None:
        super().__init__(2)
        self.message = message

    def __str__(self) -> str:
        return self.message


# --- состав ------------------------------------------------------------------------

def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def resolve_version(root: Path, explicit: str | None) -> str:
    if explicit:
        version = explicit.strip()
    else:
        described = (_git(root, "describe", "--tags", "--always", "--dirty=-dirty") or "").strip()
        version = described.lstrip("v") or "0.0.0-unversioned"
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", version):
        raise BuildStop(f"недопустимая версия: {version!r} (буквы, цифры, точка, дефис)")
    return version


def candidate_files(root: Path) -> list[Path]:
    """Все файлы, которые могут попасть в пакет: в git-репозитории — tracked и
    untracked-не-ignored (то есть ровно то, что видит `git status`), иначе —
    обход каталога. Исключения применяются после."""
    listing = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listing is not None:
        names = [n for n in listing.split("\0") if n]
        paths = [root / Path(*PurePosixPath(n).parts) for n in names]
        found = [p for p in paths if p.is_file()]
        # Модель речи git не показывает (см. BAKED_MODEL_DIR) — берём её с диска.
        seen = {p.resolve() for p in found}
        model_dir = root / Path(*PurePosixPath(BAKED_MODEL_DIR).parts)
        if model_dir.is_dir():
            found.extend(p for p in sorted(model_dir.rglob("*"))
                         if p.is_file() and p.resolve() not in seen)
        return found
    found: list[Path] = []
    for base in INCLUDE_DIRS:
        base_path = root / base
        if base_path.is_dir():
            found.extend(p for p in base_path.rglob("*") if p.is_file())
    for name in INCLUDE_ROOT_FILES:
        if (root / name).is_file():
            found.append(root / name)
    for pattern in INCLUDE_ROOT_GLOBS:
        found.extend(p for p in root.glob(pattern) if p.is_file())
    return found


def wanted(relative: PurePosixPath, *, payload_anywhere: bool = False) -> bool:
    """Едет ли файл в пакет. ``payload_anywhere`` — служебный режим для отчёта о
    пропущенном: он снимает послабление с привязки к deploy/offline/, и разница
    между двумя вызовами показывает, что вырезано именно правилами о моделях и
    архивах, а не по другой причине."""
    parts = relative.parts
    if not parts:
        return False
    top = parts[0]
    if top in EXCLUDE_TOP:
        return False
    if len(parts) == 1:
        return top in INCLUDE_ROOT_FILES or any(
            fnmatch.fnmatch(top, g) for g in INCLUDE_ROOT_GLOBS)
    if top not in INCLUDE_DIRS:
        return False
    text = relative.as_posix()
    payload = payload_anywhere or text.startswith(OFFLINE_PREFIX)
    for part in parts[:-1]:
        if part in EXCLUDE_DIR_NAMES and not (payload and part in PAYLOAD_DIR_NAMES):
            return False
    name = parts[-1]
    if name in EXCLUDE_FILE_NAMES:
        return False
    if name.startswith(".env") and name != ".env.example":
        return False
    if text in EXCLUDE_PATHS or any(fnmatch.fnmatch(text, g) for g in EXCLUDE_PATH_GLOBS):
        return False
    lower = name.lower()
    for suffix in EXCLUDE_SUFFIXES:
        if lower.endswith(suffix) and not (payload and suffix in PAYLOAD_SUFFIXES):
            return False
    return True


def skipped_payload(root: Path) -> list[PurePosixPath]:
    """Файлы, вырезанные ТОЛЬКО правилами «каталог models» и «архив». Их надо
    показать человеку: если это привезённая модель или образ, они нужны, просто
    лежат не там, где сборщик их пропускает."""
    found: list[PurePosixPath] = []
    for path in candidate_files(root):
        try:
            relative = PurePosixPath(path.relative_to(root).as_posix())
        except ValueError:
            continue
        if not wanted(relative) and wanted(relative, payload_anywhere=True):
            found.append(relative)
    return sorted(found, key=lambda p: p.as_posix().casefold())


def select_files(root: Path) -> list[PurePosixPath]:
    chosen: set[PurePosixPath] = set()
    for path in candidate_files(root):
        try:
            relative = PurePosixPath(path.relative_to(root).as_posix())
        except ValueError:
            continue
        if wanted(relative):
            chosen.add(relative)
    if not chosen:
        raise BuildStop(f"в {root} не найдено исходников для пакета")
    return sorted(chosen, key=lambda p: p.as_posix().casefold())


# --- утечки и нормализация ---------------------------------------------------------------

def is_text(relative: PurePosixPath) -> bool:
    posix = relative.as_posix()
    # Файлы модели едут байт в байт: их размеры и суммы записаны в манифест,
    # который служба сверяет на старте (см. BAKED_MODEL_DIR). README каталога —
    # обычный текст и проверяется как везде.
    if posix.startswith(BAKED_MODEL_DIR + "/") and relative.suffix.lower() != ".md":
        return False
    name = relative.name
    return name in TEXT_NAMES or relative.suffix.lower() in TEXT_SUFFIXES


def scan_for_leaks(root: Path, files: list[PurePosixPath]) -> None:
    findings: list[str] = []
    for relative in files:
        if not is_text(relative):
            continue
        try:
            text = (root / Path(*relative.parts)).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise BuildStop(f"не читается {relative}: {exc}") from exc
        for title, pattern in LEAK_RULES:
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{relative}:{line} — {title}")
                break
    if findings:
        raise BuildStop("STOP: в исходниках найдены секреты, пакет не собран:\n  "
                        + "\n  ".join(findings))


def normalized_bytes(root: Path, relative: PurePosixPath) -> bytes:
    """Текст едет с LF (как в git), .bat — с CRLF; остальное байт в байт."""
    raw = (root / Path(*relative.parts)).read_bytes()
    if not is_text(relative):
        return raw
    unified = raw.replace(b"\r\n", b"\n")
    if relative.suffix.lower() in CRLF_SUFFIXES:
        return unified.replace(b"\n", b"\r\n")
    return unified


# --- README пакета -------------------------------------------------------------------------

def package_readme(version: str, built: str) -> str:
    return f"""# Хард — пакет исходников для ИТ-службы

Версия {version}, собрано {built}. Снимок исходников без истории изменений.

## Карта пакета

| Каталог / файл | Что это |
|---|---|
| `deploy/` | **Начинать отсюда.** Установка сервера одной командой (`install.sh`), обновление, резервные копии, диагностика. Инструкция — `deploy/README.md` |
| `deploy/offline/` | Установка и обновление **без интернета**: образы собираются на машине с сетью и увозятся файлом. Инструкция — `deploy/offline/README.md` |
| `deploy/MIGRATION.md` | Переезд **работающей** системы на другой сервер: база, фотографии, портфели. Обычным копированием базы переезжать нельзя — там же сказано, почему |
| `server/` | Сервер: приложение выездника (PWA), административный API для пульта, Telegram-бот (необязателен). Собирается в контейнер `hard-app` |
| `stt/` | Служба распознавания речи для голосовых комментариев. Свой контейнер `hard-stt`, без внешних сервисов. Справка — `stt/README.md` |
| `pult/` | Пульт аналитика — настольная программа (Windows, Python + Qt). Запуск двойным кликом `pult/Пульт.pyw`. Инструкция — `pult/README.md` |
| `deploy/ops-agent.sh` | Помощник панели обслуживания. Ставится службой автоматически, руками запускать не нужно |
| `registry_cycle/` | Перенос реестров между личными книгами и общим реестром (Excel, Windows). Инструкция — `registry_cycle/README.md`, `registry_cycle/MANUAL_TRANSFER.md` |
| `docs/security/` | Модель безопасности: архитектура, потоки данных, инвентаризация ПД, угрозы, контроли |
| `SECURITY.md` | Памятка по безопасному запуску и обращению с секретами |
| `tests/` | Автотесты всего пакета (см. ниже) |
| `tools/` | Служебные скрипты, в том числе сборщик этого пакета |
| `requirements-*.txt`, `pytest.ini` | Зависимости настольных программ и настройка тестов |

## Установка сервера

Нужен Linux с Docker и Docker Compose v2, домен с сертификатом (HTTPS
обязателен — без него приложение на телефонах не работает).

```bash
cd deploy/units/core
./install.sh          # создаст .env, соберёт и запустит приложение
nano .env             # ADMIN_ID, MINIAPP_URL; BOT_TOKEN — по желанию
./update.sh           # применить правки .env
./doctor.sh           # состояние
```

Голос и маршруты — отдельные единицы (`deploy/units/stt`, `deploy/units/gps`),
ставятся тем же `./install.sh` в своём каталоге и в любом порядке: приложение
работает и без них.

**Если интернета на сервере нет** — сборка образов там не пройдёт в принципе.
Тогда сначала `deploy/offline/README.md`: образы собираются на машине с сетью
одной командой, увозятся файлом и заносятся на месте (`./load.sh`), а установка
дальше идёт как обычно.

Подробности, требования к памяти, HTTPS, резервные копии, включение и
выключение Telegram и голоса — в `deploy/README.md`.

## Пульт и перенос реестров

Обе программы — настольные, для Windows с установленным Python 3.12+ и Excel.
Консольных окон у них нет: точки входа — файлы `.pyw`, их открывает `pythonw`.

- Пульт: `pip install -r pult/requirements.txt` один раз, затем двойной клик по
  `pult/Пульт.pyw`. При первом запуске указать адрес сервера (`https://…`) и
  привязать машину **одноразовым кодом** — его выдаёт панель обслуживания
  (раздел ниже) или команда на сервере. Мастер-ключ вводить не нужно и не надо:
  после первой привязки он данные не открывает. Подробно — `pult/УСТРОЙСТВА.md`.
- Перенос реестров: `pip install -r requirements-registry-cycle.txt`, затем
  двойной клик по `Перенос реестров.pyw` в корне пакета.

## Панель обслуживания — чтобы не жить в SSH

Адрес: **`https://ваш-адрес/ops/`**. Оттуда видно состояние всех трёх единиц и
делается то, за чем иначе пришлось бы идти в консоль: диагностика, журнал, копия
базы, перезапуск, обновление, восстановление из копии. Там же выдаются коды
привязки для пультов аналитиков.

Вход — одноразовым кодом, который выдают на сервере один раз:

```bash
cd deploy/units/core
docker compose exec -T app python /app/admin_access.py issue --name "Инженер" --role engineer
```

Дальше браузер помнит доступ сам. Доступ привязан именно к этому браузеру:
перенести его копированием нельзя.

**Чего панель НЕ умеет — намеренно.** Ни произвольных команд, ни запросов к базе,
ни сборки образов: всё это потребовало бы отдать контейнеру docker-сокет, то есть
root на всём сервере. Карточек должников панель не показывает вовсе — для них
есть пульт аналитика, и там каждый просмотр пишется в журнал. Заявки исполняет
служба `hard-ops-agent` на хосте, и она умеет ровно шесть перечисленных действий.
Проверить, что она жива: `systemctl status hard-ops-agent`.

## Тесты

Из корня пакета, Python 3.12+:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Ставить зависимости по одной не надо: `requirements-dev.txt` — это ровно тот
набор, на котором прогон зелёный. Одного `server/requirements.txt` мало: он
описывает сервер, а тесты покрывают ещё пульт (Qt), перенос реестров (Excel) и
движок, и без них сбор тестов падает на полусотне файлов ещё до первой проверки.

Тесты, требующие модели распознавания речи или Excel, пропускаются, если
их нет. Аналитический движок `engine/` в пакет не входит — его тесты
пропускаются автоматически.

## Что важно знать

- В пакете нет ни одного секрета и ни одной записи с персональными данными:
  `.env` создаётся при установке, база и фотографии живут в `deploy/units/core/data/`.
- Внутренние идентификаторы (имена таблиц, заголовки версий оболочки) могут
  содержать историческое имя `hardbot` — это не отдельный продукт.
"""


# --- сборка --------------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def baked_model_report(root: Path) -> list[str]:
    """Что сказать про модель речи: она едет или её нет. Молчать нельзя ни в том,
    ни в другом случае — молчание здесь и есть та поломка, которую ловим."""
    weights = root / Path(*PurePosixPath(BAKED_MODEL_WEIGHTS).parts)
    manifest_path = root / Path(*PurePosixPath(BAKED_MODEL_MANIFEST).parts)
    ready = (weights.is_file() and manifest_path.is_file()
             and wanted(PurePosixPath(BAKED_MODEL_WEIGHTS))
             and wanted(PurePosixPath(BAKED_MODEL_MANIFEST)))
    if ready:
        name = "?"
        try:
            name = str(json.loads(manifest_path.read_text("utf-8")).get("name", "?"))
        except (OSError, ValueError):
            pass
        size = weights.stat().st_size / 1e9
        return [f"Модель речи: «{name}», {size:.2f} ГБ — едет в пакете, "
                f"единица stt поднимется без интернета."]
    return [
        "ВНИМАНИЕ: модели речи в пакете НЕТ.",
        "  Единица stt на месте установки НЕ ПОДНИМЕТСЯ: образ соберётся пустым, и",
        "  контейнер уйдёт в цикл перезапусков — «в образе модели нет, а скачивание",
        "  выключено». Скачать её там нечем: служба в сеть не ходит намеренно.",
        "  Что сделать ДО сборки пакета:  python tools/prepare_stt_model.py",
        "  (нужен интернет и около получаса; подробности — stt/README.md).",
        "  Если голос компании не нужен — так и надо: единицу stt не ставят, а в",
        "  .env ядра оставляют VOICE_BACKEND=off.",
    ]


def build(root: Path, out_dir: Path, version: str | None = None,
          today: _dt.date | None = None) -> tuple[Path, Path]:
    root = root.resolve()
    version = resolve_version(root, version)
    day = (today or _dt.date.today()).isoformat()
    files = select_files(root)
    scan_for_leaks(root, files)

    commit = (_git(root, "rev-parse", "--short", "HEAD") or "").strip() or "-"
    folder = f"{PACKAGE_PREFIX}-{version}"
    archive_name = f"{PACKAGE_PREFIX}-{version}-{day}.zip"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / archive_name
    checksum = out_dir / (archive_name + ".sha256")

    with tempfile.NamedTemporaryFile(delete=False, dir=out_dir, suffix=".part") as tmp:
        tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for relative in files:
                info = zipfile.ZipInfo(f"{folder}/{relative.as_posix()}",
                                       date_time=(2026, 1, 1, 0, 0, 0))
                # Веса модели кладём без сжатия. Они уже квантованы в int8 —
                # сжимаются на единицы процентов, а времени это стоит полминуты
                # на каждой сборке пакета И на каждом прогоне тестов, которые
                # пакет собирают. Дешёвый обмен не в ту сторону.
                if relative.as_posix() == BAKED_MODEL_WEIGHTS:
                    info.compress_type = zipfile.ZIP_STORED
                else:
                    info.compress_type = zipfile.ZIP_DEFLATED
                if relative.suffix.lower() in {".sh"}:
                    info.external_attr = 0o755 << 16
                else:
                    info.external_attr = 0o644 << 16
                zf.writestr(info, normalized_bytes(root, relative))
            # Эти два файла тоже кладём с фиксированной датой. Без ZipInfo writestr
            # берёт ТЕКУЩЕЕ время, и две сборки одного коммита давали разные
            # SHA-256: по сумме нельзя было ответить «тот ли пакет мне привезли»,
            # только «не побился ли он в дороге».
            for extra_name, extra_body in (
                    (f"{folder}/README.md", package_readme(version, day)),
                    (f"{folder}/VERSION", f"{version}\n{day}\n{commit}\n")):
                extra = zipfile.ZipInfo(extra_name, date_time=(2026, 1, 1, 0, 0, 0))
                extra.compress_type = zipfile.ZIP_DEFLATED
                extra.external_attr = 0o644 << 16
                zf.writestr(extra, extra_body)
            bad = zf.testzip()
            if bad:
                raise BuildStop(f"ZIP не прошёл проверку CRC: {bad}")
        shutil.move(str(tmp_path), archive)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    # newline="\n" обязателен: файл суммы читает `sha256sum -c` на Linux, а под
    # Windows write_text молча ставит CRLF. Имя архива тогда кончается на \r, и
    # проверка падает на ИСПРАВНОМ архиве словами «sha256sum: 'hard-….zip'$'\r':
    # No such file or directory» — это первое, что делает ИТ-служба, открыв пакет,
    # и выглядит это как «архив побился в дороге». LF внутри пакета мы держим
    # давно (normalized_bytes и тест про .sh); файл рядом с ним из правила выпал.
    checksum.write_text(f"{sha256_of(archive)}  {archive.name}\n", encoding="utf-8",
                        newline="\n")
    return archive, checksum


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Пакет исходников «Хард» для ИТ-службы")
    parser.add_argument("--version", help="версия пакета (по умолчанию — из тега git)")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="куда положить zip (dist/)")
    parser.add_argument("--root", default=str(ROOT), help="корень проекта")
    args = parser.parse_args(argv)
    try:
        archive, checksum = build(Path(args.root), Path(args.out), args.version)
    except BuildStop as stop:
        print(stop.message, file=sys.stderr)
        return 2
    with zipfile.ZipFile(archive) as zf:
        count = len(zf.namelist())
    print(f"Пакет:   {archive}")
    print(f"SHA-256: {checksum.read_text(encoding='utf-8').split()[0]}")
    print(f"Файлов:  {count}, размер {archive.stat().st_size:,} байт".replace(",", " "))

    print("")
    for line in baked_model_report(Path(args.root).resolve()):
        print(line)

    # Тишина здесь опаснее лишней строки: раньше модель речи и архивы образов
    # вырезались без единого слова, и обнаруживалось это уже у ИТ-службы.
    missed = skipped_payload(Path(args.root))
    if missed:
        print("")
        print("Не поехало — правила «каталог models» и «архивы»:")
        for relative in missed[:10]:
            print(f"  {relative}")
        if len(missed) > 10:
            print(f"  … и ещё {len(missed) - 10}")
        print("Если это модель речи или образы для установки без интернета — их место")
        print("в deploy/offline/ (см. deploy/offline/README.md), оттуда они едут в пакет.")
        print("Если это рабочий кэш или чей-то архив — всё правильно, так и надо.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
