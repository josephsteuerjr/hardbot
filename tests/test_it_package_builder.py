"""Сборщик пакета для ИТ (tools/build_it_package.py): состав, исключения, утечки."""

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
    prefix = "vyezdy-test.1/"
    assert all(n.startswith(prefix) for n in names)
    return {n[len(prefix):] for n in names}


def test_package_contains_every_component(built):
    inner = _inner(built["names"])
    for expected in (
        "README.md", "VERSION", "pytest.ini", "SECURITY.md",
        "hardbot/app.py", "hardbot/web.py", "hardbot/Dockerfile", "hardbot/.env.example",
        "hardbot/miniapp/index.html",
        "stt/server.py", "stt/Dockerfile", "stt/README.md", "stt/requirements.txt",
        "deploy/docker-compose.yml", "deploy/.env.example", "deploy/install.sh",
        "deploy/update.sh", "deploy/backup.sh", "deploy/restore.sh", "deploy/doctor.sh",
        "deploy/README.md",
        "pult/pult.py", "pult/Пульт.bat", "registry_cycle/__init__.py",
        "docs/security/00-README.md", "tests/conftest.py", "tools/build_it_package.py",
        "requirements-registry-cycle.txt",
    ):
        assert expected in inner, expected
    assert built["archive"].name.startswith("vyezdy-test.1-") and built["archive"].suffix == ".zip"
    digest, _, name = built["checksum"].read_text(encoding="utf-8").split()[0:3:2][0], None, None
    assert len(digest) == 64
    assert built["archive"].name in built["checksum"].read_text(encoding="utf-8")


def test_package_excludes_data_secrets_and_legacy(built):
    inner = _inner(built["names"])
    forbidden_prefixes = ("_local/", "_infra/", "!Архив проекта/", "engine/", "outputs/",
                          "ДЛЯ ШАРЫ/", "скрины бота/", "hardbot/_devdata/", "hardbot/data_dev/",
                          "hardbot/data/", "pult/src-tauri/", "pult/node_modules/")
    forbidden_exact = {"hardbot/gui.py", "hardbot/run.bat", "hardbot/_dev_seed.py",
                       "hardbot/miniapp_dev.py", "hardbot/miniapp_dev.bat", "hardbot/.env",
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


def test_shell_scripts_travel_with_lf_and_bat_with_crlf(built):
    with zipfile.ZipFile(built["zip"]) as zf:
        sh = zf.read("vyezdy-test.1/deploy/install.sh")
        assert b"\r\n" not in sh and sh.startswith(b"#!/usr/bin/env bash")
        assert (zf.getinfo("vyezdy-test.1/deploy/install.sh").external_attr >> 16) & 0o111
        bat = zf.read("vyezdy-test.1/pult/Пульт.bat")
        assert b"\r\n" in bat
        readme = zf.read("vyezdy-test.1/README.md").decode("utf-8")
        assert "deploy/README.md" in readme and "stt/" in readme and "pult/" in readme


def test_leak_scanner_stops_the_build(tmp_path):
    root = tmp_path / "proj"
    (root / "hardbot").mkdir(parents=True)
    (root / "hardbot" / "ok.py").write_text("X = 1\n", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    archive, _ = builder.build(root, tmp_path / "out", version="0.1")
    assert archive.exists()

    leaky = root / "hardbot" / "settings.py"
    leaky.write_text('TOKEN = "123456789:' + "A" * 35 + '"\n', encoding="utf-8")
    with pytest.raises(builder.BuildStop) as stop:
        builder.build(root, tmp_path / "out2", version="0.2")
    assert "hardbot/settings.py" in str(stop.value)
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
    (root / "hardbot" / ".env.example").write_text("BOT_TOKEN=\nADMIN_ID=\n", encoding="utf-8")
    (root / "hardbot" / "db.py").write_text('line = f"BOT_TOKEN={token}"\n', encoding="utf-8")
    archive, _ = builder.build(root, tmp_path / "out4", version="0.4")
    with zipfile.ZipFile(archive) as zf:
        assert "vyezdy-0.4/hardbot/.env.example" in zf.namelist()


def test_wanted_rules():
    P = builder.PurePosixPath
    assert builder.wanted(P("hardbot/app.py"))
    assert builder.wanted(P("deploy/.env.example"))
    assert builder.wanted(P("requirements-registry-cycle.txt"))
    assert not builder.wanted(P("deploy/.env"))
    assert not builder.wanted(P("deploy/data/hardbot.db"))
    assert not builder.wanted(P("hardbot/gui.py"))
    assert not builder.wanted(P("engine/app/main.py"))
    assert not builder.wanted(P("_local/anything.py"))
    assert not builder.wanted(P("pult/настройки пульта.json"))
    assert not builder.wanted(P("hardbot/__pycache__/x.pyc"))
    assert not builder.wanted(P("Хард.xlsb"))
    assert not builder.wanted(P("docs/report.docx"))
