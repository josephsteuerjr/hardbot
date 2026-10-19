"""Сборщик офлайн-пакета не имеет права отчитаться об успехе, упаковав пустоту.

Ловушка была такая. Шаг «модель речи» запускал контейнер-загрузчик, тот честно
печатал «модель скачана и проверена загрузкой» — а наружу, в каталог пакета, не
выходило ничего (в Git Bash под Windows монтирование уезжало по дороге, но так
же выглядят и полный диск, и не расшаренный докеру путь). Скрипт паковал пустой
каталог, печатал «[ок] model/models.tar — 12K (модель «small»)» рядом со
словами «≈ 460 МБ» — и ехал дальше: пустышка попадала в SHA256SUMS, verify.sh на
ней зеленел, а на закрытой машине распаковывать было нечего.

Поэтому проверяем поведение, а не текст скрипта: реально запускаем bundle.sh с
заглушкой вместо docker. Загрузчик «скачал» веса — пакет собран; загрузчик
отчитался, но ничего не положил — остановка с ненулевым кодом и без models.tar.
"""

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "deploy" / "offline" / "bundle.sh"

# Заглушка docker. Отвечает на всё, что зовёт bundle.sh, а поведение
# «загрузчика модели» задаётся переменными окружения:
#   STUB_DOWNLOAD=good  — кладёт в примонтированный каталог настоящие по размеру веса
#   STUB_DOWNLOAD=none  — не кладёт ничего, но отчитывается об успехе
#   STUB_BAKED          — строка, которой отвечает опрос запечённой в образ модели
DOCKER_STUB = r"""#!/bin/sh
case "$1" in
  info|compose|pull|build|load) exit 0 ;;
  image) echo "sha256:0000000000000000000000000000000000000000000000000000000000000000"; exit 0 ;;
  save)
    out=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "-o" ]; then out="$2"; fi
      shift
    done
    if [ -n "$out" ]; then : > "$out"; fi
    exit 0 ;;
  run)
    dst=""
    for a in "$@"; do
      case "$a" in
        *:/models) dst="${a%:/models}" ;;
      esac
    done
    if [ -z "$dst" ]; then
      # Вызов без монтирования — это опрос модели, запечённой в образ.
      printf '%s\n' "$STUB_BAKED"
      exit 0
    fi
    if [ "$STUB_DOWNLOAD" = "good" ]; then
      mkdir -p "$dst/models--stub--small"
      dd if=/dev/zero of="$dst/models--stub--small/model.bin" bs=1048576 count=12 2>/dev/null
      printf '{}' > "$dst/models--stub--small/config.json"
    fi
    echo "  модель скачана и проверена загрузкой"
    exit 0 ;;
esac
exit 0
"""

BAKED_OK = "large-v3-turbo|5|814054531|"
BAKED_NONE = "|0|0|манифеста нет"


def _bash():
    if os.name == "nt":
        git = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if git.is_file():
            return str(git)
        pytest.skip("Windows POSIX build test needs Git Bash, not the WSL bash.exe launcher")
    found = shutil.which("bash")
    if not found:
        pytest.skip("нет bash — скрипты сборки здесь не запускаются")
    return found


def _posix(path):
    """Путь в том виде, в каком его получил бы скрипт от своей же оболочки.

    bundle.sh — это bash-скрипт; на Windows он живёт внутри Git Bash, где пути
    выглядят как /c/Users/…. Тест не должен подсовывать ему форму «C:\\…»,
    которой в его мире не бывает.
    """
    cygpath = (str(Path(_bash()).parent.parent / "usr/bin/cygpath.exe")
               if os.name == "nt" else shutil.which("cygpath"))
    if cygpath:
        return subprocess.run([cygpath, "-u", str(path)], capture_output=True,
                              text=True, check=True).stdout.strip()
    return str(path)


def _run(tmp_path, *args, download="good", baked=BAKED_OK):
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "docker"
    stub.write_text(DOCKER_STUB, encoding="utf-8")
    stub.chmod(0o755)
    child = dict(os.environ)
    child["PATH"] = str(stub_dir) + os.pathsep + child["PATH"]
    child["STUB_DOWNLOAD"] = download
    child["STUB_BAKED"] = baked
    out = tmp_path / "out"
    done = subprocess.run(
        [_bash(), _posix(BUNDLE), "--version", "test", "--out", _posix(out), *args],
        cwd=str(ROOT / "deploy" / "offline"), env=child, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    packages = sorted(out.glob("hard-offline-*")) if out.is_dir() else []
    return done, (packages[0] if packages else None)


def test_real_model_is_packed_and_counted(tmp_path):
    done, package = _run(tmp_path, download="good")
    assert done.returncode == 0, done.stdout + done.stderr
    assert package is not None, "пакет не собрался:\n" + done.stdout + done.stderr
    tar_path = package / "model" / "models.tar"
    assert tar_path.is_file(), "модели в пакете нет:\n" + done.stdout
    with tarfile.open(tar_path) as tar:
        weights = [m for m in tar.getmembers() if m.name.endswith("model.bin")]
    assert weights and weights[0].size >= 10 * 1024 * 1024, "в пакете лежат не веса, а огрызок"
    assert "12K" not in done.stdout


def test_download_that_left_nothing_is_a_stop(tmp_path):
    """Контейнер отчитался об успехе, файлов нет — пакет собирать нельзя."""
    done, package = _run(tmp_path, download="none")
    assert done.returncode != 0, "пустая модель уехала в пакет с кодом 0:\n" + done.stdout
    if package is not None:
        assert not (package / "model" / "models.tar").exists(), \
            "в пакете остался tar с пустотой:\n" + done.stdout
        assert not (package / "SHA256SUMS").exists(), \
            "пустышку успели занести в контрольные суммы:\n" + done.stdout
    assert "[ок] model/models.tar" not in done.stdout, \
        "отчитался об успехе, упаковав пустоту:\n" + done.stdout


def test_image_without_baked_model_is_named_out_loud(tmp_path):
    """Голос работает на модели ИЗ ОБРАЗА: нет её — пакет везти незачем."""
    done, _ = _run(tmp_path, "--no-model", download="none", baked=BAKED_NONE)
    assert done.returncode != 0, "пакет без голоса собрался молча:\n" + done.stdout
    assert "prepare_stt_model" in (done.stdout + done.stderr), \
        "не сказал, чем чинить:\n" + done.stdout + done.stderr
