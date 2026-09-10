"""Контракт панели инженера с помощником на хосте.

Главное свойство, которое здесь сторожится: заявка НЕ МОЖЕТ заставить помощника
сделать что-то за пределами пяти действий. Панель живёт в контейнере без доступа
к докеру именно поэтому — если бы заявка умела подставить путь или аргумент, вся
затея с помощником теряла бы смысл, и проще было бы отдать docker.sock.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ops_requests as ops

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "deploy" / "ops-agent.sh"


@pytest.fixture(autouse=True)
def _ops_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA", str(tmp_path))
    return tmp_path


def test_only_five_actions_exist():
    """Список действий — не «пока такой», а весь. Расширение — решение владельца."""
    assert ops.ACTIONS == ("doctor", "backup", "restore", "restart", "update")


@pytest.mark.parametrize("action", ["shell", "exec", "psql", "", "doctor;rm -rf /"])
def test_unknown_action_never_reaches_disk(action):
    with pytest.raises(ValueError):
        ops.submit(action)
    assert ops.pending() == [], "непринятая заявка всё равно оказалась на диске"


@pytest.mark.parametrize("name", [
    "../../etc", "20260910T031500/../..", "; rm -rf /", "latest", "",
    "$(whoami)", "20260910t031500",
])
def test_restore_refuses_anything_but_a_timestamp(name):
    """Имя копии — это метка времени и ничего кроме.

    Через него проще всего было бы выйти за пределы каталога копий: строка
    попадает в путь, который помощник открывает у себя, с правами хоста.
    """
    with pytest.raises(ValueError):
        ops.submit("restore", {"unit": "core", "backup": name})


def test_restore_and_backup_belong_to_core_only():
    for action in ("restore", "backup"):
        with pytest.raises(ValueError):
            ops.submit(action, {"unit": "stt", "backup": "20260910T031500"})


def test_unknown_unit_is_refused():
    with pytest.raises(ValueError):
        ops.submit("doctor", {"unit": "../core"})


def test_request_ids_are_random_not_sequential():
    """По номеру нельзя угадать чужую заявку и прочитать её результат."""
    ids = {ops.submit("doctor") for _ in range(5)}
    assert len(ids) == 5
    assert not any(i.isdigit() for i in ids)


def test_result_is_none_until_the_agent_answers():
    rid = ops.submit("doctor")
    assert ops.result(rid) is None
    assert len(ops.pending()) == 1


def test_result_reads_status_and_output_from_two_files(_ops_dir):
    """Статус и текст лежат отдельно, и статус появляется последним.

    Иначе панель могла бы прочитать «готово» раньше, чем дописан вывод.
    """
    rid = ops.submit("doctor")
    results = _ops_dir / "ops" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / f"{rid}.out").write_text("строка с «кавычками»\nи переводом", encoding="utf-8")
    (results / f"{rid}.json").write_text(
        json.dumps({"schema": "hard.ops.result.v2", "id": rid, "exit": 0}), encoding="utf-8")

    got = ops.result(rid)
    assert got["exit"] == 0
    assert "кавычками" in got["output"] and "переводом" in got["output"]


def test_agent_absence_is_named_not_guessed():
    """«Помощник не установлен» и «операция висит» должны различаться."""
    state = ops.agent_alive()
    assert state["alive"] is False and state["reason"]


@pytest.mark.skipif(not AGENT.is_file(), reason="нет deploy/ops-agent.sh")
def test_agent_builds_commands_itself_not_from_the_request():
    """Помощник не подставляет строки из заявки в команду.

    Проверяем по исходнику: единственный способ гарантировать это дёшево —
    убедиться, что в ветках нет подстановки полей заявки в аргументы, кроме
    имени копии, которое перед этим проверено шаблоном.
    """
    text = AGENT.read_text(encoding="utf-8")
    assert 'case "$action" in' in text, "действие больше не выбирается из таблицы"
    assert "eval" not in text, "в помощнике появился eval — заявка сможет выполнить свою строку"
    for field in ('$(json_field "$file" action)"', ):
        assert field in text
    # Единственная строка заявки, попадающая в аргумент, — имя копии, и оно
    # проверено valid_backup прямо перед использованием.
    assert 'valid_backup "$backup"' in text
    assert '"backups/$backup"' in text


@pytest.mark.skipif(sys.platform == "win32",
                    reason="помощник — скрипт для сервера; на Windows пути каталога "
                           "заявок не переживают Git Bash: «C:/…» он считает "
                           "ОТНОСИТЕЛЬНЫМ путём и создаёт каталог «C:» рядом с собой. "
                           "Поведение проверено на Linux и вручную через контейнер")
@pytest.mark.skipif(not AGENT.is_file(), reason="нет deploy/ops-agent.sh")
def test_agent_refuses_action_outside_the_list(tmp_path):
    """Заявка с чужим действием получает отказ, а не тишину.

    Панель такую заявку не создаст, но файл в каталоге может появиться и мимо
    неё — каталог общий. Помощник обязан ответить сам.
    """
    data = tmp_path / "data"
    ops_root = data / "ops"
    (ops_root / "requests").mkdir(parents=True)
    (ops_root / "requests" / "abc.json").write_text(
        json.dumps({"id": "abc", "action": "shell", "params": {"unit": "core"}}),
        encoding="utf-8")

    # Окружение НЕ обрезаем: на Windows bash без системных переменных не
    # запускается вовсе, и тест падал бы не на том, что проверяет.
    env = dict(os.environ, HARD_OPS_DATA=str(data))
    # Запускаем ИЗ каталога помощника по относительному имени. Полный путь bash
    # на Windows не понимает, когда в нём кириллица: он отвечает «нет такого
    # файла» про существующий файл. Каталог отдаём через cwd — его открывает
    # операционная система, а не разбирает оболочка.
    run = subprocess.run(["bash", "./ops-agent.sh", "--once"], cwd=str(AGENT.parent),
                         env=env, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr

    status = json.loads((ops_root / "results" / "abc.json").read_text(encoding="utf-8"))
    assert status["exit"] == 2
    out = (ops_root / "results" / "abc.out").read_text(encoding="utf-8")
    assert "не входит в список" in out
