"""Сборщик пакета для ИТ (tools/build_it_package.py): состав, исключения, утечки."""

import fnmatch
import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_it_package", ROOT / "tools" / "build_it_package.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    archive, checksum = builder.build(ROOT, out, version="test.1")
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert zf.testzip() is None
        return {"archive": archive, "checksum": checksum, "names": names, "zip": archive}


def _inner(names):
    """Пути внутри корневой папки пакета."""
    prefix = "hard-test.1/"
    assert all(n.startswith(prefix) for n in names)
    return {n[len(prefix):] for n in names}


# Каталоги внутри deploy/, которые рождаются ПРИ УСТАНОВКЕ и в пакет не едут:
# база и фото, копии, архивы переезда (handover/), скачанная модель речи,
# дорожные графы, готовый офлайн-пакет (offline/out/).
DEPLOY_RUNTIME_DIRS = {"data", "backups", "models", "osrm", "secrets", "__pycache__",
                       "handover", "out"}
# Отдельные файлы того же рода: .env с секретами (в пакет едет только
# .env.example) и отметка закрытого контура deploy/offline/OFFLINE.
DEPLOY_RUNTIME_FILES = {"OFFLINE"}


def _tracked_deploy_files():
    """Пути внутри deploy/, которые git считает частью исходников.

    None — git недоступен: так выглядит РАСПАКОВАННЫЙ ПАКЕТ, репозитория там
    нет. В этом случае рабочие файлы отсеиваются списками выше и правилами
    .gitignore, доехавшими внутри самого deploy/ (см. ниже).
    """
    listing = builder._git(ROOT, "ls-files", "-z", "--cached", "--", "deploy")
    if listing is None:
        return None
    return {name for name in listing.split("\0") if name}


def _ignore_rules_inside_deploy():
    """Правила .gitignore, лежащие ВНУТРИ deploy/ и доезжающие в пакете.

    Нужны только там, где git спросить не у кого, — в распакованном пакете.
    Рабочие файлы заводятся и там: каталог настроек единицы gps монтируется в
    контейнер целиком, и vroom пишет в него свой журнал обращений
    (`deploy/units/gps/vroom/access.log`). Тест требовал, чтобы такой журнал
    оказался в пакете, и падал у того, кто сделал ровно то, что велит README:
    сначала поставил три единицы, потом запустил тесты. Хуже того, сообщение
    «AssertionError: …/access.log» читалось как требование положить рабочие
    файлы установки в пакет для чужой ИТ-службы.

    Второй список имён в тесте эту ловушку закрыл бы ровно один раз: следующий
    контейнер, который что-то допишет в свой каталог, снова уронил бы прогон.
    Поэтому спрашиваем не список, а сами правила: «это рабочий файл» уже
    записано в deploy/units/gps/vroom/.gitignore, и записано там намеренно —
    каталог отвечает за себя сам, потому что переезжает вместе с единицей.
    Поддерживаем простое подмножество .gitignore: строки без отрицания,
    с шаблоном по имени файла или по пути от каталога с правилом.
    """
    rules = []
    for ignore_file in sorted((ROOT / "deploy").rglob(".gitignore")):
        folder = ignore_file.parent
        for line in ignore_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            rules.append((folder, line.rstrip("/")))
    return rules


def _ignored_inside_deploy(path, rules):
    for folder, pattern in rules:
        if folder != path.parent and folder not in path.parents:
            continue
        inside = path.relative_to(folder).as_posix()
        target = inside if "/" in pattern else path.name
        if fnmatch.fnmatch(target, pattern):
            return True
    return False


def _deploy_files_that_must_ship():
    """Содержимое deploy/ БЕЗ рождённого установкой — список СЧИТАН с диска.

    Раньше здесь стояли вписанные руками пути (`deploy/docker-compose.yml` и
    прочие). Разрез deploy/ на три единицы сделал такой список неправдой в тот
    же день: файл переехал, тест покраснел, хотя пакет собран правильно.
    Проверка обязана ловить «сборщик потерял файл», а не «файл переименовали».

    Но «всё, что лежит в deploy/» — тоже неправда, и хуже: установка САМА
    кладёт туда .env с токеном бота, архив переезда, отметку офлайн-режима. На
    установленной системе тест падал с «AssertionError: deploy/units/core/.env»
    и читался как требование положить секреты в пакет, отдаваемый чужой ИТ-
    службе. А README пакета ставит установку РАНЬШЕ тестов — то есть красный
    прогон получался при нормальном порядке действий.

    Поэтому спрашиваем не диск, а git: обязан доехать тот файл, который лежит
    в РЕПОЗИТОРИИ. Источник по-прежнему независим от правил сборщика, так что
    «сборщик потерял файл» ловится как раньше; рождённое установкой git не
    показывает вовсе.
    """
    base = ROOT / "deploy"
    tracked = _tracked_deploy_files()
    ignore_rules = [] if tracked is not None else _ignore_rules_inside_deploy()
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix in (".pyc",):
            continue
        inside = path.relative_to(base)
        if set(inside.parts[:-1]) & DEPLOY_RUNTIME_DIRS:
            continue
        if inside.name in DEPLOY_RUNTIME_FILES:
            continue
        if inside.name.startswith(".env") and inside.name != ".env.example":
            continue
        if _ignored_inside_deploy(path, ignore_rules):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if tracked is not None and relative not in tracked:
            continue
        yield relative


def test_package_contains_every_component(built):
    inner = _inner(built["names"])
    for expected in (
        "README.md", "VERSION", "pytest.ini", "SECURITY.md",
        "server/app.py", "server/web.py", "server/Dockerfile", "server/.env.example",
        "server/miniapp/index.html",
        "stt/server.py", "stt/Dockerfile", "stt/README.md", "stt/requirements.txt",
        "pult/pult.py", "pult/Пульт.pyw", "pult/app.py", "registry_cycle/__init__.py",
        "Перенос реестров.pyw", "Хард — Реестры.pyw",
        "docs/security/00-README.md", "tests/conftest.py", "tools/build_it_package.py",
        "requirements-registry-cycle.txt", "requirements-dev.txt",
    ):
        assert expected in inner, expected

    deploy_files = list(_deploy_files_that_must_ship())
    for expected in deploy_files:
        assert expected in inner, (
            f"{expected}: файл установки есть в исходниках, но не доехал в пакет — "
            "либо его потерял сборщик, либо это рабочий файл, рождённый установкой, "
            "и тогда его место в .gitignore рядом с ним, а не в пакете"
        )
    # Страховка от вырожденного случая «в deploy/ ничего не нашлось»: установка
    # без compose-файла и без скрипта — это пустой пакет, а не зелёный тест.
    assert any(n.endswith("docker-compose.yml") for n in deploy_files)
    assert any(n.endswith("install.sh") for n in deploy_files)
    assert any(n.endswith(".env.example") for n in deploy_files)
    assert built["archive"].name.startswith("hard-test.1-") and built["archive"].suffix == ".zip"
    digest, _, name = built["checksum"].read_text(encoding="utf-8").split()[0:3:2][0], None, None
    assert len(digest) == 64
    assert built["archive"].name in built["checksum"].read_text(encoding="utf-8")


def test_package_excludes_data_secrets_and_legacy(built):
    inner = _inner(built["names"])
    forbidden_prefixes = ("_local/", "_infra/", "!Архив проекта/", "engine/", "outputs/",
                          "ДЛЯ ШАРЫ/", "скрины бота/", "server/_devdata/", "server/data_dev/",
                          "server/data/", "pult/src-tauri/", "pult/node_modules/")
    forbidden_exact = {"server/gui.py", "server/run.bat", "server/_dev_seed.py",
                       "server/miniapp_dev.py", "server/miniapp_dev.bat", "server/.env",
                       "deploy/.env", "plink.exe", "start_server_proxy.bat",
                       "tools/build_registry_transfer_share.py"}
    for name in inner:
        assert not name.startswith(forbidden_prefixes), name
        assert name not in forbidden_exact, name
        assert "__pycache__" not in name and ".git/" not in name, name
        base = name.rsplit("/", 1)[-1]
        assert not (base.startswith(".env") and base != ".env.example"), name
        lower = base.lower()
        for suffix in (".db", ".db-wal", ".xlsb", ".xlsx", ".docx", ".pptx", ".pyc", ".log",
                       ".csv", ".exe"):
            assert not lower.endswith(suffix), name


def test_shell_scripts_travel_with_lf_and_launchers_are_python(built):
    """Серверные скрипты — LF и бит исполнения; точки входа Windows — .pyw
    (pythonw, без консоли), а не .bat: cmd-окно даже на «start» мигало."""
    with zipfile.ZipFile(built["zip"]) as zf:
        # Проверяем ВСЕ .sh в пакете, а не один вписанный руками путь. Прежде
        # здесь стоял deploy/install.sh — при разрезе deploy/ на единицы файл
        # переехал, и тест упал на KeyError, хотя пакет был собран правильно.
        # Смысл проверки — «скрипты доехали пригодными к запуску», и он не
        # должен зависеть от того, как сегодня называются каталоги.
        scripts = [n for n in zf.namelist() if n.endswith(".sh")]
        assert scripts, "в пакете нет ни одного .sh — установщики не доехали"
        for name in scripts:
            body = zf.read(name)
            assert b"\r\n" not in body, f"{name}: CRLF — на Linux это «bad interpreter: ...^M»"
            # Рецепты сборки графов (deploy/geo/) запускают как `bash файл` или
            # копируют в консоль кусками — шебанга у них нет намеренно, и это
            # написано в deploy/geo/README.md. Строку запуска и бит исполнения
            # требуем от того, что инженер вызывает по имени: ./install.sh и т. п.
            if "/deploy/geo/" in name:
                continue
            assert body.startswith(b"#!"), f"{name}: нет строки запуска (#!)"
            assert (zf.getinfo(name).external_attr >> 16) & 0o111, f"{name}: нет бита исполнения"
        # Точка входа установки обязана быть на месте под известным именем:
        # с неё начинается инструкция, и её отсутствие — не переименование.
        assert "hard-test.1/deploy/units/core/install.sh" in scripts
        pyw = zf.read("hard-test.1/pult/Пульт.pyw")
        assert b"pult.app" in pyw and b"\r\n" not in pyw
        assert not any(n.lower().endswith(".bat") for n in zf.namelist()), \
            "в пакете не должно быть .bat: консольные окна убраны"
        readme = zf.read("hard-test.1/README.md").decode("utf-8")
        assert "deploy/README.md" in readme and "stt/" in readme and "pult/" in readme
        assert "Пульт.pyw" in readme and "Tkinter" not in readme


def test_checksum_file_travels_with_lf(built):
    """Файл суммы — тоже поставка, и читает его `sha256sum -c` на Linux. CRLF
    здесь означает имя архива, кончающееся на возврате каретки: проверка
    целостности падает на исправном архиве, и выглядит это как «побился в дороге»."""
    raw = built["checksum"].read_bytes()
    assert b"\r" not in raw, "CRLF в файле суммы — sha256sum -c не найдёт архив"
    assert raw.endswith(b"\n"), "без перевода строки в конце coreutils ругается на формат"
    digest, name = raw.decode("utf-8").split()
    assert len(digest) == 64 and name == built["archive"].name


def test_leak_scanner_stops_the_build(tmp_path):
    root = tmp_path / "proj"
    (root / "server").mkdir(parents=True)
    (root / "server" / "ok.py").write_text("X = 1\n", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    archive, _ = builder.build(root, tmp_path / "out", version="0.1")
    assert archive.exists()

    leaky = root / "server" / "settings.py"
    leaky.write_text('TOKEN = "123456789:' + "A" * 35 + '"\n', encoding="utf-8")
    with pytest.raises(builder.BuildStop) as stop:
        builder.build(root, tmp_path / "out2", version="0.2")
    assert "server/settings.py" in str(stop.value)
    leaky.unlink()

    # Образцы собираются из кусков: иначе сканер поймал бы сам этот файл тестов.
    server_ip = ".".join(("209", "222", "251", "112"))
    for payload in (f"SERVER={server_ip}\n", "ssh_" + "password" + ": hunter2secret\n",
                    "-----BEGIN OPENSSH " + "PRIVATE KEY-----\n"):
        leaky.write_text(payload, encoding="utf-8")
        with pytest.raises(builder.BuildStop):
            builder.build(root, tmp_path / "out3", version="0.3")
    leaky.unlink()

    # Пустой BOT_TOKEN= в примере настроек и код, собирающий строку, — не утечка.
    (root / "server" / ".env.example").write_text("BOT_TOKEN=\nADMIN_ID=\n", encoding="utf-8")
    (root / "server" / "db.py").write_text('line = f"BOT_TOKEN={token}"\n', encoding="utf-8")
    archive, _ = builder.build(root, tmp_path / "out4", version="0.4")
    with zipfile.ZipFile(archive) as zf:
        assert "hard-0.4/server/.env.example" in zf.namelist()


def test_wanted_rules():
    P = builder.PurePosixPath
    assert builder.wanted(P("server/app.py"))
    assert builder.wanted(P("deploy/.env.example"))
    assert builder.wanted(P("requirements-registry-cycle.txt"))
    assert not builder.wanted(P("deploy/.env"))
    assert not builder.wanted(P("deploy/data/hardbot.db"))
    assert not builder.wanted(P("server/gui.py"))
    assert not builder.wanted(P("engine/app/main.py"))
    assert not builder.wanted(P("_local/anything.py"))
    assert not builder.wanted(P("pult/настройки пульта.json"))
    assert not builder.wanted(P("server/__pycache__/x.pyc"))
    assert not builder.wanted(P("Хард.xlsb"))
    assert not builder.wanted(P("docs/report.docx"))
