"""Build the small, sanitized click-to-run registry transfer package.

Окно переноса — на PySide6, поэтому в пакет едут `registry_cycle/theme.py`,
иконка и `PySide6` в requirements; лаунчер проверяет именно Qt, а не tkinter."""

from __future__ import annotations

import ast
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
SHARE_ROOT = ROOT / "ДЛЯ ШАРЫ"
PACKAGE_NAME = "Перенос реестров"
PACKAGE = SHARE_ROOT / PACKAGE_NAME
ARCHIVE = SHARE_ROOT / f"{PACKAGE_NAME}.zip"
ARCHIVE_SUM = SHARE_ROOT / f"{PACKAGE_NAME}.zip.sha256.txt"

RUNTIME_MODULES = (
    "__init__",
    "baseline",
    "config",
    "cycle_lock",
    "directions",
    "discovery",
    "errors",
    "excel_host",
    "excel_retry",
    "file_access",
    "files",
    "force_close",
    "gui",
    "manual_app",
    "manual_downward",
    "manual_upward",
    "models",
    "normalize",
    "ownership",
    "planner",
    "profile_detection",
    "publishing",
    "reader",
    "receipts",
    "reporting",
    "rollover",
    "semantics",
    "service",
    "snapshot",
    "staging",
    "theme",
    "validation",
)

COPY_FILES: tuple[tuple[Path, Path], ...] = (
    (Path("requirements-registry-cycle.txt"), Path("_app/requirements.txt")),
    *(
        (Path("registry_cycle") / f"{name}.py", Path("_app/registry_cycle") / f"{name}.py")
        for name in RUNTIME_MODULES
    ),
    (Path("registry_cycle/config/app.json"), Path("_app/registry_cycle/config/app.json")),
    (Path("registry_cycle/config/fields.json"), Path("_app/registry_cycle/config/fields.json")),
    (Path("registry_cycle/config/theme.json"), Path("_app/registry_cycle/config/theme.json")),
    (Path("registry_cycle/icon.png"), Path("_app/registry_cycle/icon.png")),
    (Path("server/exchange_contract.py"), Path("_app/server/exchange_contract.py")),
    (Path("server/fields.py"), Path("_app/server/fields.py")),
    (
        Path("server/data_exchange_contract.json"),
        Path("_app/server/data_exchange_contract.json"),
    ),
)

LAUNCHER = r"""@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0_app"
set "PYTHONDONTWRITEBYTECODE=1"

if exist ".venv\Scripts\python.exe" goto check_venv
goto check_py

:check_venv
".venv\Scripts\python.exe" -c "import PySide6.QtWidgets, python_calamine, xlwings, win32com.client" >nul 2>&1
if errorlevel 1 goto not_ready
if /I "%REGISTRY_TRANSFER_SMOKE_TEST%"=="1" goto smoke_venv
start "" ".venv\Scripts\pythonw.exe" -m registry_cycle.manual_app
exit /b 0

:smoke_venv
".venv\Scripts\python.exe" -c "from registry_cycle.config import load_config_bundle; load_config_bundle(); import registry_cycle.gui"
exit /b %errorlevel%

:check_py
where py >nul 2>&1
if errorlevel 1 goto check_python
py -3 -c "import PySide6.QtWidgets, python_calamine, xlwings, win32com.client" >nul 2>&1
if errorlevel 1 goto check_python
if /I "%REGISTRY_TRANSFER_SMOKE_TEST%"=="1" goto smoke_py
where pyw >nul 2>&1
if errorlevel 1 goto run_py_console
start "" pyw -3 -m registry_cycle.manual_app
exit /b 0

:run_py_console
start "" /b py -3 -m registry_cycle.manual_app
exit /b 0

:smoke_py
py -3 -c "from registry_cycle.config import load_config_bundle; load_config_bundle(); import registry_cycle.gui"
exit /b %errorlevel%

:check_python
where python >nul 2>&1
if errorlevel 1 goto not_ready
python -c "import PySide6.QtWidgets, python_calamine, xlwings, win32com.client" >nul 2>&1
if errorlevel 1 goto not_ready
if /I "%REGISTRY_TRANSFER_SMOKE_TEST%"=="1" goto smoke_python
where pythonw >nul 2>&1
if errorlevel 1 goto run_python_console
start "" pythonw -m registry_cycle.manual_app
exit /b 0

:run_python_console
start "" /b python -m registry_cycle.manual_app
exit /b 0

:smoke_python
python -c "from registry_cycle.config import load_config_bundle; load_config_bundle(); import registry_cycle.gui"
exit /b %errorlevel%

:not_ready
if /I "%REGISTRY_TRANSFER_SMOKE_TEST%"=="1" exit /b 1
echo.
echo Приложению не хватает Python или библиотек.
echo Один раз запустите «Установить.bat», затем повторите запуск.
pause
exit /b 1
"""

INSTALLER = r"""@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if not exist "_app\requirements.txt" goto broken
if exist "_app\.venv\Scripts\python.exe" goto install

set "BASE_PYTHON="
where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 goto try_python
set "BASE_PYTHON=py -3"
goto create

:try_python
where python >nul 2>&1
if errorlevel 1 goto no_python
python -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 goto no_python
set "BASE_PYTHON=python"

:create
echo Создаю локальное окружение...
%BASE_PYTHON% -m venv "_app\.venv"
if errorlevel 1 goto failed

:install
echo Устанавливаю зависимости...
"_app\.venv\Scripts\python.exe" -m pip install -r "_app\requirements.txt"
if errorlevel 1 goto failed
"_app\.venv\Scripts\python.exe" -c "import PySide6.QtWidgets, python_calamine, xlwings, win32com.client"
if errorlevel 1 goto failed
echo.
echo Готово. Теперь запустите «Перенос реестров.bat».
pause
exit /b 0

:no_python
echo Нужен Python 3.11 или новее.
echo Установите Python с python.org и включите «Add Python to PATH».
pause
exit /b 1

:broken
echo Пакет неполный: нет _app\requirements.txt.
pause
exit /b 1

:failed
echo.
echo Установка не завершена. Причина указана выше.
pause
exit /b 1
"""

README = """ПЕРЕНОС РЕЕСТРОВ

1. Распакуйте всю папку на локальный диск. Из ZIP и прямо с сетевой папки не запускайте.
2. Если приложение ещё не устанавливалось, один раз откройте «Установить.bat».
3. Открывайте «Перенос реестров.bat» двойным кликом — окно без консоли.
4. В приложении выберите корневые папки портфелей.
5. Сначала проверьте и выполните шаг 1 — в общий реестр. Затем отдельно шаг 2 — в личные.

Имена папок и файлов не определяют владельца: перенос идёт по ответственному в реестре.
Берутся все .xlsb во всех группах выбранных корней, кроме архивных папок.
Посторонние открытые книги Excel не мешают; закройте только реестры, которые сейчас переносятся.
Резервные копии и квитанции сохраняются в настроенном центральном архиве, не рядом с книгами.
"""

TEXT_SUFFIXES = {".bat", ".json", ".py", ".txt"}
FORBIDDEN_SUFFIXES = {
    ".csv",
    ".doc",
    ".docx",
    ".exe",
    ".log",
    ".pem",
    ".pyc",
    ".xls",
    ".xlsb",
    ".xlsm",
    ".xlsx",
}
FORBIDDEN_PARTS = {
    ".git",
    ".pytest_cache",
    ".qa",
    ".venv",
    "__pycache__",
    "tests",
    "_local",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def validate_targets() -> None:
    root = ROOT.resolve()
    share = SHARE_ROOT.resolve()
    package = PACKAGE.resolve()
    if share.parent != root or share.name != "ДЛЯ ШАРЫ":
        raise RuntimeError(f"Небезопасная папка поставки: {share}")
    if package.parent != share or package.name != PACKAGE_NAME:
        raise RuntimeError(f"Небезопасная папка пакета: {package}")
    for path in (SHARE_ROOT, PACKAGE):
        if path.exists() and path.is_symlink():
            raise RuntimeError(f"Символьная ссылка запрещена: {path}")


def clean_previous() -> None:
    validate_targets()
    if PACKAGE.exists():
        shutil.rmtree(PACKAGE)
    for path in (ARCHIVE, ARCHIVE_SUM):
        if path.exists():
            path.unlink()


def write_text(relative: Path, value: str, *, windows_lines: bool = False) -> None:
    destination = PACKAGE / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    normalized = value.strip() + "\n"
    if windows_lines:
        normalized = normalized.replace("\n", "\r\n")
    destination.write_text(normalized, encoding="utf-8")


def copy_allowlist() -> set[Path]:
    expected: set[Path] = set()
    for source_relative, destination_relative in COPY_FILES:
        source = ROOT / source_relative
        if not source.is_file():
            raise FileNotFoundError(f"Не найден файл поставки: {source_relative}")
        destination = PACKAGE / destination_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        expected.add(destination_relative)
    return expected


def internal_dependencies(module_name: str) -> set[str]:
    source = ROOT / "registry_cycle" / f"{module_name}.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    result: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
            continue
        dependency = node.module.split(".", 1)[0]
        if (ROOT / "registry_cycle" / f"{dependency}.py").is_file():
            result.add(dependency)
    return result


def validate_dependency_closure() -> None:
    allowed = set(RUNTIME_MODULES)
    missing: dict[str, list[str]] = {}
    for module_name in RUNTIME_MODULES:
        absent = sorted(internal_dependencies(module_name) - allowed)
        if absent:
            missing[module_name] = absent
    if missing:
        raise RuntimeError(f"Белый список не замыкает зависимости: {missing}")


def scan_package() -> None:
    failures: list[str] = []
    current_user = Path.home().name.casefold()
    absolute_user_path = re.compile(r"(?i)\b[A-Z]:[\\/](?:Users|Documents and Settings)[\\/]")
    for path in sorted(PACKAGE.rglob("*")):
        relative = path.relative_to(PACKAGE)
        if {part.casefold() for part in relative.parts} & FORBIDDEN_PARTS:
            failures.append(f"запрещённая папка: {relative}")
        if not path.is_file():
            continue
        if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            failures.append(f"запрещённый тип файла: {relative}")
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        folded = text.casefold()
        if current_user and current_user in folded:
            failures.append(f"имя текущего пользователя: {relative}")
        if absolute_user_path.search(text):
            failures.append(f"абсолютный путь профиля Windows: {relative}")
    if failures:
        raise RuntimeError("Санитизация не пройдена:\n- " + "\n- ".join(failures))


def compile_sources() -> None:
    for relative in sorted(package_files(PACKAGE)):
        if relative.suffix.casefold() == ".py":
            source = (PACKAGE / relative).read_text(encoding="utf-8")
            compile(source, str(relative), "exec")


def write_manifest(expected: set[Path]) -> Path:
    manifest = Path("_app/SHA256SUMS.txt")
    lines = [
        f"{sha256(PACKAGE / relative)}  {relative.as_posix()}"
        for relative in sorted(expected, key=lambda item: item.as_posix().casefold())
    ]
    write_text(manifest, "\n".join(lines))
    return manifest


def verify_manifest(root: Path, expected: set[Path]) -> None:
    manifest_path = root / "_app" / "SHA256SUMS.txt"
    found: dict[Path, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        digest, separator, raw_path = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError(f"Ошибка SHA256SUMS.txt: {line!r}")
        relative = Path(PurePosixPath(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"Небезопасный путь в SHA256SUMS.txt: {raw_path}")
        found[relative] = digest
    if set(found) != expected:
        raise RuntimeError("Состав SHA256SUMS.txt не совпадает с пакетом")
    for relative, expected_digest in found.items():
        if sha256(root / relative) != expected_digest:
            raise RuntimeError(f"Контрольная сумма не совпала: {relative}")


def create_archive() -> None:
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in sorted(package_files(PACKAGE), key=lambda item: item.as_posix().casefold()):
            archive.write(PACKAGE / relative, f"{PACKAGE_NAME}/{relative.as_posix()}")
    with zipfile.ZipFile(ARCHIVE) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP не прошёл CRC: {bad}")
    ARCHIVE_SUM.write_text(f"{sha256(ARCHIVE)}  {ARCHIVE.name}\n", encoding="utf-8")


def extract_safely(destination: Path) -> Path:
    root = destination.resolve()
    with zipfile.ZipFile(ARCHIVE) as archive:
        for info in archive.infolist():
            relative = PurePosixPath(info.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"Небезопасный путь в ZIP: {info.filename}")
            target = (destination / Path(*relative.parts)).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError(f"Путь ZIP выходит за временную папку: {info.filename}")
        archive.extractall(destination)
    extracted = destination / PACKAGE_NAME
    if not extracted.is_dir():
        raise RuntimeError("В ZIP нет корневой папки пакета")
    return extracted


def smoke_test_extracted(extracted: Path, expected: set[Path]) -> None:
    if package_files(extracted) != expected:
        raise RuntimeError("Распакованный ZIP отличается от папки поставки")
    manifest = Path("_app/SHA256SUMS.txt")
    verify_manifest(extracted, expected - {manifest})
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from registry_cycle.config import load_config_bundle; "
                "bundle=load_config_bundle(); "
                "assert bundle.app.manual_upward['enabled']; "
                "assert bundle.app.manual_downward['enabled']; "
                "import registry_cycle.gui"
            ),
        ],
        cwd=extracted / "_app",
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        details = "\n".join(value.strip() for value in (result.stdout, result.stderr) if value.strip())
        raise RuntimeError(f"Изолированный smoke-test не пройден:\n{details}")


def main() -> None:
    validate_dependency_closure()
    clean_previous()
    PACKAGE.mkdir(parents=True)
    expected = copy_allowlist()
    write_text(Path("Перенос реестров.bat"), LAUNCHER, windows_lines=True)
    write_text(Path("Установить.bat"), INSTALLER, windows_lines=True)
    write_text(Path("ПЕРЕД ЗАПУСКОМ.txt"), README)
    expected.update(
        {
            Path("Перенос реестров.bat"),
            Path("Установить.bat"),
            Path("ПЕРЕД ЗАПУСКОМ.txt"),
        }
    )
    manifest = write_manifest(expected)
    expected.add(manifest)
    if package_files(PACKAGE) != expected:
        raise RuntimeError("В пакете есть лишние или отсутствующие файлы")
    verify_manifest(PACKAGE, expected - {manifest})
    scan_package()
    compile_sources()
    create_archive()
    with tempfile.TemporaryDirectory(prefix="registry-transfer-share-") as temporary:
        extracted = extract_safely(Path(temporary))
        smoke_test_extracted(extracted, expected)
    print(f"Папка: {PACKAGE}")
    print(f"ZIP: {ARCHIVE}")
    print(f"SHA-256: {sha256(ARCHIVE)}")
    print(f"Файлов: {len(expected)}")
    print(f"Размер ZIP: {ARCHIVE.stat().st_size} байт")


if __name__ == "__main__":
    main()
