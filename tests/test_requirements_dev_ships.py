"""Файлы зависимостей, которые едут в пакет, не должны ссылаться на то, чего в пакете нет.

ЗАЧЕМ. README пакета учит ставить зависимости тестов одной командой
``pip install -r requirements-dev.txt``. Внутри такого файла строка ``-r путь``
подключает соседний файл — и если этот сосед в пакет не входит, команда падает
на первой же секунде, причём ТОЛЬКО у заказчика: в репозитории сосед лежит на
месте, и у автора всё зелено. Ровно так и случилось со строкой
``-r engine/requirements.txt``: каталог engine/ сборщик вырезает намеренно
(EXCLUDE_TOP), а ссылка на него осталась.

Тест дешёвый: читает файлы глазами, ничего не собирает и не ставит.
"""

import importlib.util
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_it_package_reqs", ROOT / "tools" / "build_it_package.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_builder()

# Строка подключения соседнего файла: «-r путь» или «--requirement путь».
_INCLUDE = re.compile(r"^\s*(?:-r|--requirement)[=\s]+(\S+)")


def _requirements_files_in_package():
    """Файлы требований, которые реально едут в пакет."""
    found = []
    for path in sorted(ROOT.glob("requirements*.txt")):
        relative = PurePosixPath(path.relative_to(ROOT).as_posix())
        if builder.wanted(relative):
            found.append(relative)
    return found


def test_requirements_dev_ships_in_package():
    """Сам файл, которым README велит ставить зависимости, обязан быть в пакете."""
    assert builder.wanted(PurePosixPath("requirements-dev.txt"))


def test_included_requirements_also_ship():
    files = _requirements_files_in_package()
    # Вырожденный случай «ничего не нашлось» — это не зелёный тест.
    assert any(f.name == "requirements-dev.txt" for f in files), files
    for relative in files:
        text = (ROOT / relative).read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            match = _INCLUDE.match(line)
            if not match:
                continue
            target = (relative.parent / match.group(1)).as_posix().lstrip("./")
            assert (ROOT / target).exists(), f"{relative}:{number} → нет файла {target}"
            assert builder.wanted(PurePosixPath(target)), (
                f"{relative}:{number} подключает «{target}», а сборщик пакета этот файл "
                f"вырезает: у заказчика «pip install -r {relative}» упадёт на первой секунде")


def test_engine_dependencies_mirrored_in_requirements_dev():
    """engine/ в пакет не едет, поэтому его зависимости продублированы вручную.

    Дубль без сверки протухает молча: в engine/requirements.txt добавили пакет,
    тесты движка у заказчика начали пропускаться «сами собой». Здесь сверяем.
    """
    engine = ROOT / "engine" / "requirements.txt"
    if not engine.exists():          # в пакете engine/ нет — сверять нечего
        return
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    server = (ROOT / "server" / "requirements.txt").read_text(encoding="utf-8")
    for line in engine.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0].strip()
        present = re.search(rf"(?mi)^\s*{re.escape(name)}\b", dev) \
            or re.search(rf"(?mi)^\s*{re.escape(name)}\b", server)
        assert present, (
            f"engine/requirements.txt требует «{name}», но в requirements-dev.txt его нет. "
            f"Ссылку «-r engine/requirements.txt» ставить нельзя — engine/ не едет в пакет; "
            f"добавьте строку с пакетом прямо в requirements-dev.txt")
