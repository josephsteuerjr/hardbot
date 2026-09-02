"""Сборщик пакета исходников «Выезды» для передачи ИТ-службе.

    python tools/build_it_package.py [--version 1.2.0] [--out dist] [--root .]

Результат: ``dist/vyezdy-<версия>-<дата>.zip`` и рядом ``.sha256``. Внутри —
снимок исходников без git-истории: сервер (hardbot/), служба речи (stt/),
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
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_PREFIX = "vyezdy"

# Что едет в пакет: каталоги целиком (после исключений) и корневые файлы.
INCLUDE_DIRS = ("hardbot", "stt", "pult", "registry_cycle", "deploy", "docs", "tests", "tools")
INCLUDE_ROOT_FILES = ("pytest.ini", "SECURITY.md")
INCLUDE_ROOT_GLOBS = ("requirements-*.txt",)

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
    "hardbot/gui.py",             # устаревший Tk-пульт; актуальный — pult/
    "hardbot/run.bat",
    "hardbot/_dev_seed.py",
    "hardbot/miniapp_dev.py",
    "hardbot/miniapp_dev.bat",
    "hardbot/docker-compose.dev.yml",
    "hardbot/docker-compose.local.yml",
    "tools/build_registry_transfer_share.py",   # собирает локальную папку «ДЛЯ ШАРЫ»
}
EXCLUDE_PATH_GLOBS = ("hardbot/_devdata/*", "hardbot/data_dev/*", "hardbot/miniapp_dev.*",
                      "hardbot/*.backup-*", "hardbot/.deploy-*")
# Расширения, которым в пакете не место: данные, секреты, офисные файлы, бинарники.
EXCLUDE_SUFFIXES = {".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3",
                    ".xlsb", ".xlsx", ".xlsm", ".xls", ".docx", ".doc", ".pptx", ".pyc", ".pyo",
                    ".log", ".csv", ".jsonl", ".pmtiles", ".zip", ".7z", ".tar", ".gz", ".exe",
                    ".pem", ".key", ".ppk", ".tmp", ".part"}

TEXT_SUFFIXES = {".py", ".md", ".txt", ".sh", ".yml", ".yaml", ".json", ".ini", ".cfg", ".toml",
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
        return [p for p in paths if p.is_file()]
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


def wanted(relative: PurePosixPath) -> bool:
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
    if any(part in EXCLUDE_DIR_NAMES for part in parts[:-1]):
        return False
    name = parts[-1]
    if name in EXCLUDE_FILE_NAMES:
        return False
    if name.startswith(".env") and name != ".env.example":
        return False
    text = relative.as_posix()
    if text in EXCLUDE_PATHS or any(fnmatch.fnmatch(text, g) for g in EXCLUDE_PATH_GLOBS):
        return False
    lower = name.lower()
    for suffix in EXCLUDE_SUFFIXES:
        if lower.endswith(suffix):
            return False
    return True


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
    return f"""# Выезды — пакет исходников для ИТ-службы

Версия {version}, собрано {built}. Снимок исходников без истории изменений.

## Карта пакета

| Каталог / файл | Что это |
|---|---|
| `deploy/` | **Начинать отсюда.** Установка сервера одной командой (`install.sh`), обновление, резервные копии, диагностика. Инструкция — `deploy/README.md` |
| `hardbot/` | Сервер: приложение выездника (PWA), административный API для пульта, Telegram-бот (необязателен). Собирается в контейнер `vyezdy-app` |
| `stt/` | Служба распознавания речи для голосовых комментариев. Свой контейнер `vyezdy-stt`, без внешних сервисов. Справка — `stt/README.md` |
| `pult/` | Пульт аналитика — настольная программа (Windows, Python/Tkinter). Запуск `Пульт.bat`. Инструкция — `pult/README.md` |
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
cd deploy
./install.sh          # создаст .env, секреты, соберёт и запустит app + stt
nano .env             # ADMIN_ID, MINIAPP_URL; BOT_TOKEN — по желанию
./update.sh           # применить правки .env
./doctor.sh           # состояние
```

Подробности, требования к памяти, HTTPS, резервные копии, включение и
выключение Telegram и голоса — в `deploy/README.md`.

## Пульт и перенос реестров

Обе программы — настольные, для Windows с установленным Python 3.11+.

- Пульт: `pip install -r requirements-registry-cycle.txt` не нужен; пульту
  хватает стандартной библиотеки. Запуск — `pult/Пульт.bat`. При первом
  запуске указать адрес сервера (`https://…`) и `ADMIN_API_KEY` из `deploy/.env`.
- Перенос реестров: `pip install -r requirements-registry-cycle.txt`, запуск —
  `python -m registry_cycle.manual_app` из корня пакета (нужен Excel).

## Тесты

Из корня пакета, Python 3.12+:

```bash
pip install aiohttp aiogram openpyxl python-dotenv pytest
python -m pytest -q
```

Тесты, требующие модели распознавания речи или Excel, пропускаются, если
их нет. Аналитический движок `engine/` в пакет не входит — его тесты
пропускаются автоматически.

## Что важно знать

- В пакете нет ни одного секрета и ни одной записи с персональными данными:
  `.env` создаётся при установке, база и фотографии живут в `deploy/data/`.
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
                info.compress_type = zipfile.ZIP_DEFLATED
                if relative.suffix.lower() in {".sh"} or relative.name in {"Пульт.bat"}:
                    info.external_attr = 0o755 << 16
                else:
                    info.external_attr = 0o644 << 16
                zf.writestr(info, normalized_bytes(root, relative))
            zf.writestr(f"{folder}/README.md", package_readme(version, day))
            zf.writestr(f"{folder}/VERSION", f"{version}\n{day}\n{commit}\n")
            bad = zf.testzip()
            if bad:
                raise BuildStop(f"ZIP не прошёл проверку CRC: {bad}")
        shutil.move(str(tmp_path), archive)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    checksum.write_text(f"{sha256_of(archive)}  {archive.name}\n", encoding="utf-8")
    return archive, checksum


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Пакет исходников «Выезды» для ИТ-службы")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
