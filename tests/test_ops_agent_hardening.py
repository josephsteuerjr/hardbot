"""Помощник на хосте (``deploy/ops-agent.sh``) устойчив к подмене от контейнера.

Контейнер пишет в тот же том, откуда помощник (root) читает заявки. Значит поле
``id`` заявки и подменённый симлинк — это тоже вход, которому доверять нельзя.
Здесь три поведенческие проверки на реально запущенном скрипте и несколько
статических — на текст скриптов пакета C.

Про bash на Windows. ``bash`` в PATH может оказаться WSL-заглушкой
``C:\\Windows\\System32\\bash.exe`` — она открывает совсем другую файловую
систему. Поэтому ищем настоящий Git Bash (как это делают соседние тесты через
``shutil.which``), а заглушку из System32 отбрасываем; не нашли — тест себя
пропускает. Симлинки в Git Bash без прав администратора не создаются: такую
проверку пропускаем с причиной, если настоящий симлинк создать не удалось.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "deploy" / "ops-agent.sh"
CORE = ROOT / "deploy" / "units" / "core"
INSTALL = CORE / "install.sh"
BACKUP = CORE / "backup.sh"
RESTORE = CORE / "restore.sh"


def _bash() -> str:
    """Настоящий Git Bash, но не WSL-заглушка из System32."""
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    for candidate in (
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\usr\bin\bash.exe"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    pytest.skip("нет Git Bash — помощник на хосте здесь не запускается")


def _posix(path: Path) -> str:
    """Путь в форме, понятной bash (для HARD_OPS_DATA)."""
    cygpath = shutil.which("cygpath")
    if cygpath:
        return subprocess.run([cygpath, "-u", str(path)], capture_output=True,
                              text=True, check=True).stdout.strip()
    return str(path)


def _docker_stub(tmp_path: Path) -> Path:
    """Каталог с заглушкой docker: любой вызов — успех, вывода нет."""
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "docker"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub_dir


def _run_once(data: Path, *, stub: Path | None = None):
    """Один круг помощника на каталоге данных ``data``."""
    env = dict(os.environ, HARD_OPS_DATA=_posix(data))
    if stub is not None:
        env["PATH"] = str(stub) + os.pathsep + env["PATH"]
    # Запускаем по относительному имени из каталога помощника — как соседний
    # тест контракта: полный путь с кириллицей bash на Windows не открывает, а
    # каталог через cwd открывает операционная система, не оболочка.
    return subprocess.run(
        [_bash(), "./ops-agent.sh", "--once"], cwd=str(AGENT.parent), env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )


def _make_symlinked_dir(link: Path, target: Path) -> bool:
    """Настоящий симлинк-каталог или False, если ОС/права не дают его создать."""
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return os.path.islink(link)


# --- поведение на запущенном скрипте ----------------------------------------

@pytest.mark.skipif(not AGENT.is_file(), reason="нет deploy/ops-agent.sh")
def test_bad_id_writes_nothing_outside_ops(tmp_path):
    """Заявка с id='/../../../ESC' не создаёт файла вне ops/ и удаляется.

    id ушёл бы в путь результата '$OPS/results/$id.out'; при id='/../../../ESC'
    это на три уровня выше results/ — в корне временного каталога. Помощник
    обязан отклонить такой id ДО любой записи (образец эксплуатации — F-admin-2).
    """
    data = tmp_path / "data"
    (data / "ops" / "requests").mkdir(parents=True)
    (data / "ops" / "requests" / "evil.json").write_text(
        json.dumps({"id": "/../../../ESC", "action": "doctor",
                    "params": {"unit": "core"}}),
        encoding="utf-8")

    run = _run_once(data)
    assert run.returncode == 0, run.stderr

    # На три уровня выше results/ = корень tmp: сюда увёл бы id.
    assert not (tmp_path / "ESC.out").exists(), "запись ушла ВНЕ ops/ от имени агента"
    assert not (tmp_path / "ESC.json").exists(), "статус ушёл ВНЕ ops/ от имени агента"
    # Заявка удалена, результат не написан.
    assert not any((data / "ops" / "requests").iterdir()), "заявку не убрали"
    results = data / "ops" / "results"
    assert not results.exists() or not any(results.iterdir()), "результат всё же написан"


@pytest.mark.skipif(not AGENT.is_file(), reason="нет deploy/ops-agent.sh")
def test_valid_request_reaches_write_result(tmp_path):
    """Годная заявка на безобидное действие доходит до результата.

    docker в тестовой среде нет, поэтому подменяем его заглушкой (exit 0):
    важно проверить, что путь дошёл до write_result с ожидаемым кодом и в
    формате контракта v2, а не то, что настоящий docker что-то сделал.
    """
    data = tmp_path / "data"
    (data / "ops" / "requests").mkdir(parents=True)
    (data / "ops" / "requests" / "req.json").write_text(
        json.dumps({"id": "okitem01", "action": "restart",
                    "params": {"unit": "core"}}),
        encoding="utf-8")

    run = _run_once(data, stub=_docker_stub(tmp_path))
    assert run.returncode == 0, run.stderr

    status_path = data / "ops" / "results" / "okitem01.json"
    assert status_path.is_file(), "результат не написан:\n" + run.stdout + run.stderr
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["schema"] == "hard.ops.result.v2"
    assert status["id"] == "okitem01"
    assert status["exit"] == 0
    assert (data / "ops" / "results" / "okitem01.out").is_file()
    # Заявка забрана и убрана.
    assert not any((data / "ops" / "requests").iterdir())


@pytest.mark.skipif(not AGENT.is_file(), reason="нет deploy/ops-agent.sh")
def test_symlinked_results_dir_stops_the_cycle(tmp_path):
    """results/ подменён ссылкой наружу → круг пропущен, наружу ничего не ушло."""
    data = tmp_path / "data"
    (data / "ops" / "requests").mkdir(parents=True)
    (data / "ops" / "running").mkdir(parents=True)
    outside = tmp_path / "OUTSIDE"
    if not _make_symlinked_dir(data / "ops" / "results", outside):
        pytest.skip("настоящий симлинк создать нельзя (нет прав/ОС) — проверку пропускаем")

    (data / "ops" / "requests" / "req.json").write_text(
        json.dumps({"id": "okitem02", "action": "restart",
                    "params": {"unit": "core"}}),
        encoding="utf-8")

    run = _run_once(data, stub=_docker_stub(tmp_path))
    assert run.returncode == 0, run.stderr

    assert not any(outside.iterdir()), "запись ушла в каталог, на который указывал симлинк"
    # Круг пропущен целиком: заявка не тронута, heartbeat и опись не написаны.
    assert (data / "ops" / "requests" / "req.json").exists(), "заявку тронули на пропущенном круге"
    assert not (data / "ops" / "heartbeat").exists(), "heartbeat поставлен на пропущенном круге"
    assert not (data / "ops" / "inventory.json").exists(), "опись написана на пропущенном круге"


# --- статические проверки текста скриптов пакета C ---------------------------

def test_agent_validates_id_before_any_work():
    text = AGENT.read_text(encoding="utf-8")
    assert "valid_id()" in text, "нет функции valid_id"
    assert "[A-Za-z0-9_-]{1,64}" in text, "шаблон id не совпадает с ops_requests.result()"
    assert "eval" not in text, "в помощнике появился eval"


def test_agent_guards_dirs_and_writes_without_following_symlinks():
    text = AGENT.read_text(encoding="utf-8")
    assert "ops_dirs_ok" in text and "[ -L" in text, "нет посимвольной сверки каталогов"
    # Запись через временный файл и атомарный перенос, не следующий за ссылкой.
    assert "mktemp" in text and "mv -f -T" in text, "запись не через mktemp + mv -f -T"


def test_unit_template_has_systemd_hardening():
    text = INSTALL.read_text(encoding="utf-8")
    for directive in (
        "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectHome=yes",
        "ProtectSystem=full", "ProtectKernelTunables=yes",
        "ProtectControlGroups=yes", "RestrictSUIDSGID=yes",
    ):
        assert directive in text, f"в шаблоне unit нет директивы {directive}"


def test_install_locks_down_data_and_backups():
    text = INSTALL.read_text(encoding="utf-8")
    assert "chmod 700 data backups" in text, "install.sh не закрывает data и backups на 700"


def test_backup_sets_umask_before_creating_files():
    lines = BACKUP.read_text(encoding="utf-8").splitlines()
    umask_lines = [n for n, ln in enumerate(lines) if ln.strip() == "umask 077"]
    assert umask_lines, "в backup.sh нет строки 'umask 077'"
    # umask должен стоять до первого создания временного файла/каталога копии.
    # Смотрим КОД (без комментариев): слово mktemp в пояснении не в счёт.
    def _code(ln):
        return ln.split("#", 1)[0]
    first_write = next((n for n, ln in enumerate(lines)
                        if "mktemp" in _code(ln) or 'mkdir -p "$dest"' in _code(ln)),
                       len(lines))
    assert umask_lines[0] < first_write, "umask 077 стоит позже первой записи файла"


def test_restore_refuses_symlinks_and_replaces_destination():
    text = RESTORE.read_text(encoding="utf-8")
    assert "[ -L" in text, "restore.sh не проверяет символьные ссылки на data/базе/фото"
    assert "data/hardbot.db" in text
    # Restore now stages a whole generation and moves old DB/WAL aside before
    # publishing with rename; a copy over an existing symlink is no longer used.
    assert 'mv "data/$asset" "$aside/$asset"' in text
    assert 'mv "$stage/$asset" "data/$asset"' in text


@pytest.mark.parametrize("script", [
    AGENT, RESTORE, BACKUP, INSTALL,
])
def test_changed_scripts_parse(script):
    """bash -n на каждом изменённом скрипте (пропуск, если bash недоступен)."""
    if not script.is_file():
        pytest.skip(f"нет {script}")
    run = subprocess.run([_bash(), "-n", str(script)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert run.returncode == 0, f"{script.name}: {run.stderr}"


def test_no_crlf_in_changed_scripts():
    for script in (AGENT, RESTORE, BACKUP, INSTALL):
        if script.is_file():
            assert b"\r" not in script.read_bytes(), f"{script.name} содержит CRLF"
