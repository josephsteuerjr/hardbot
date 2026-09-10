"""Загрузчик офлайн-пакета: либо отметка офлайн-режима поставлена, либо остановка.

Ловушка была такая. Пакет копируют на флешку и запускают ОТТУДА — так велит
deploy/offline/README.md. Каталог deploy/ с исходниками при этом лежит на диске
сервера, рядом с пакетом его нет. Старый load.sh искал deploy/ только около
себя, промахивался — и продолжал работу: заносил образы, печатал два
предупреждения, слово «Готово» и возвращал ноль. Отметки deploy/offline/OFFLINE
после него не было, а без неё install.sh идёт собирать образы — в закрытом
контуре это не «медленно», а «никогда».

Поэтому проверяем не текст скрипта, а поведение: реально запускаем load.sh на
поддельном пакете с заглушкой вместо docker. Два исхода и никакого третьего:
путь к deploy/ известен — отметка на месте; неизвестен — остановка с ненулевым
кодом, без слова «Готово» и без единого изменения.
"""

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOAD = ROOT / "deploy" / "offline" / "load.sh"

DOCKER_STUB = """#!/bin/sh
# Заглушка docker: молча соглашается на info и load, на image inspect отвечает
# «образа нет» — это в load.sh предупреждение, а не остановка.
case "$1" in
  image) exit 1 ;;
  *) exit 0 ;;
esac
"""


def _bash():
    found = shutil.which("bash")
    if not found:
        pytest.skip("нет bash — скрипты установки здесь не запускаются")
    return found


def _fake_bundle(tmp_path):
    """Поддельный офлайн-пакет: images/ с пустым .tar и модель в model/."""
    bundle = tmp_path / "flash" / "hard-offline-test"
    (bundle / "images").mkdir(parents=True)
    (bundle / "images" / "hard-app_latest.tar").write_bytes(b"")
    model = bundle / "model"
    model.mkdir()
    (model / "ИМЯ-МОДЕЛИ.txt").write_text("small\n", encoding="utf-8")
    payload = tmp_path / "small"
    payload.mkdir()
    (payload / "model.bin").write_text("веса\n", encoding="utf-8")
    with tarfile.open(model / "models.tar", "w") as tar:
        tar.add(payload, arcname="small")
    shutil.copy2(LOAD, bundle / "load.sh")
    return bundle


def _fake_deploy(tmp_path):
    """Поддельные исходники: load.sh обязан узнавать их по units/core/install.sh."""
    deploy = tmp_path / "opt" / "hard" / "deploy"
    (deploy / "units" / "core").mkdir(parents=True)
    (deploy / "units" / "core" / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    return deploy


def _run(bundle, tmp_path, *args):
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "docker"
    stub.write_text(DOCKER_STUB, encoding="utf-8")
    stub.chmod(0o755)
    # Окружение дочернего процесса. Путь поиска программ — это путь ОС, а не
    # настройка «Харда»: в манифесте переменных ему делать нечего.
    child = dict(os.environ)
    child["PATH"] = str(stub_dir) + os.pathsep + child["PATH"]
    child["HOME"] = str(tmp_path / "home")        # чтобы поиск не ушёл в чужой ~/hard
    return subprocess.run(
        [_bash(), "./load.sh", "--no-verify", *args],
        cwd=bundle, env=child, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )


def test_marker_is_set_when_deploy_is_given(tmp_path):
    bundle = _fake_bundle(tmp_path)
    deploy = _fake_deploy(tmp_path)
    done = _run(bundle, tmp_path, "--deploy", str(deploy))
    assert done.returncode == 0, done.stdout + done.stderr
    marker = deploy / "offline" / "OFFLINE"
    assert marker.is_file(), "отметка офлайн-режима не поставлена:\n" + done.stdout + done.stderr
    assert (deploy / "units" / "stt" / "models" / "small" / "model.bin").is_file(), \
        "модель речи не разложена:\n" + done.stdout


def test_no_deploy_means_stop_not_a_cheerful_zero(tmp_path):
    for real in (Path("/opt/hard/deploy"), Path("/opt/hard"), Path("/srv/hard/deploy"),
                 Path("/srv/hard")):
        if real.exists():
            pytest.skip(f"на этой машине есть {real} — поиск найдёт настоящую систему, "
                        "а трогать её тест не должен")
    bundle = _fake_bundle(tmp_path)
    _fake_deploy(tmp_path)          # исходники есть, но лежат не там, где ищут
    done = _run(bundle, tmp_path)
    assert done.returncode != 0, "промах по каталогу deploy/ обязан быть остановкой:\n" + done.stdout
    assert "Готово" not in done.stdout, "сказал «Готово», не поставив отметку:\n" + done.stdout
    assert "--deploy" in (done.stdout + done.stderr), "не подсказал ключ --deploy"


def test_wrong_deploy_path_is_named_out_loud(tmp_path):
    bundle = _fake_bundle(tmp_path)
    not_deploy = tmp_path / "просто-каталог"
    not_deploy.mkdir()
    done = _run(bundle, tmp_path, "--deploy", str(not_deploy))
    assert done.returncode != 0
    assert "units/core/install.sh" in done.stderr, done.stdout + done.stderr


def test_readme_teaches_the_key(tmp_path):
    """Ключ, о котором знает только код, для инженера не существует."""
    readme = (ROOT / "deploy" / "offline" / "README.md").read_text(encoding="utf-8")
    assert "--deploy" in readme, "offline/README.md не рассказывает про --deploy"
