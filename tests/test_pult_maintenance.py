"""Раздел «Обслуживание» пульта аналитика: состояние, операции, ключи.

Пульт говорит с сервером тем же контрактом, что веб-панель /ops/: состояние
одним запросом, операция — заявка и поллинг результата. Тесты гоняют раздел
без сети: api подменяется, фоновые задачи исполняются тут же.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", exc_type=ImportError,
    reason="нет работающего PySide6 (pip install -r requirements-dev.txt)")

from pult.core.settings import Settings  # noqa: E402
from pult.ui import theme  # noqa: E402
from pult.ui.main_window import Pult  # noqa: E402
from pult.ui.nav import SECTIONS  # noqa: E402

STATE = {
    "agent": {"alive": True, "seen": "2026-09-22T12:00:00Z"},
    "inventory": {
        "units": [
            {"unit": "core", "installed": True,
             "backups": ["20260922T103748", "20260921T103748"]},
            {"unit": "stt", "installed": True, "backups": []},
            {"unit": "gps", "installed": True, "backups": []},
        ],
        "disk_free": "34G", "at": "2026-09-22T12:00:05Z",
    },
    "pending": [],
    "buttons": [
        {"action": "doctor", "title": "Диагностика", "kind": "safe", "hint": "состояние"},
        {"action": "logs", "title": "Журнал", "kind": "safe", "hint": "хвост"},
        {"action": "backup", "title": "Снять копию", "kind": "safe", "hint": "копия"},
        {"action": "restart", "title": "Перезапустить", "kind": "care", "hint": "заново"},
        {"action": "update", "title": "Обновить", "kind": "care", "hint": "пересборка"},
        {"action": "restore", "title": "Восстановить из копии", "kind": "danger",
         "hint": "откат"},
    ],
    "you": {"name": "Пульт", "id": 1},
}


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(application)
    return application


@pytest.fixture
def window(app, tmp_path):
    settings = Settings({}, tmp_path / "настройки пульта.json")
    settings["profiles"] = {"Прод": {"url": "https://prod.example",
                                     "device_token": "tok"}}
    settings["active"] = "Прод"
    w = Pult(settings)

    calls: list[tuple] = []

    def fake_api(method, path, body=None, timeout=20):
        calls.append((method, path, body))
        if path == "/ops/state.json":
            return STATE
        if path == "/ops/run.json":
            return {"ok": True, "id": "req-1"}
        if path.startswith("/ops/result.json"):
            return {"done": True, "exit": 0, "output": "всё хорошо"}
        if path == "/ops/devices.json":
            return {"devices": [{"id": 2, "name": "Ноут", "role": "operator",
                                 "last_seen": "только что", "revoked": False}],
                    "codes": []}
        if path == "/ops/code.json":
            return {"ok": True, "id": 9, "code": "ODNORAZOVYIKOD", "role": "operator"}
        raise AssertionError(f"неожиданный запрос {method} {path}")

    def fake_bg(fn, done=None, fail=None):
        try:
            result = fn()
        except Exception as exc:                          # noqa: BLE001
            if fail:
                fail(exc)
            return
        if done:
            done(result)

    w.api = fake_api
    w.bg_task = fake_bg
    w.calls = calls
    yield w
    w.ops_timer.stop()
    w.close()


def test_maintenance_is_in_the_rail_and_lazy(app, window):
    assert ("maint", "Обслуживание") in SECTIONS
    assert "maint" in window.pages
    assert window.pages["maint"] is not None


def test_state_renders_units_buttons_and_disk(app, window):
    window.maint_load()
    assert "Помощник на хосте отвечает" in window.ops_agent_note.text()
    assert "34G" in window.ops_disk_note.text()
    assert [window.ops_unit_combo.itemText(i)
            for i in range(window.ops_unit_combo.count())] == ["core", "stt", "gps"]
    assert window.ops_backup_combo.count() == 2, "копии core не приехали"
    titles = [b.text() for b in window.ops_actions.findChildren(
        QtWidgets.QPushButton)]
    assert "Восстановить из копии" in titles and "Диагностика" in titles


def test_backup_combo_follows_the_unit(app, window):
    window.maint_load()
    window.ops_unit_combo.set_current(2)   # gps
    window._ops_fill_backups(window._ops_units_list)
    assert window.ops_backup_combo.count() == 0


def test_operation_runs_and_polls_to_result(app, window):
    window.maint_run("doctor")
    run = [c for c in window.calls if c[1] == "/ops/run.json"]
    assert run and run[0][2] == {"action": "doctor", "params": {"unit": "core"}}
    window._ops_tick()
    log = window.ops_log.toPlainText()
    assert "Диагностика · core — готово" in log and "всё хорошо" in log
    assert window._ops_poll is None, "поллинг не остановился"


def test_careful_actions_ask_first(app, window):
    window.ask_yes_no = lambda title, text: False
    window.calls.clear()
    window.maint_run("restart")
    assert not [c for c in window.calls if c[1] == "/ops/run.json"], (
        "перезапуск ушёл без подтверждения")

    window.ask_yes_no = lambda title, text: True
    window.maint_run("restart")
    assert [c for c in window.calls if c[1] == "/ops/run.json"]
    window._ops_tick()


def test_restore_refuses_without_a_backup(app, window):
    window.maint_load()
    window.ops_unit_combo.set_current(2)   # gps — копий нет
    window._ops_fill_backups(window._ops_units_list)
    window.calls.clear()
    window.maint_run("restore")
    assert not [c for c in window.calls if c[1] == "/ops/run.json"]


def test_issue_code_shows_it_once_and_lists_devices(app, window):
    window.ops_new_device.set("")
    window.maint_issue_code()
    assert not [c for c in window.calls if c[1] == "/ops/code.json"], (
        "код выдан для безымянного пульта")

    window.ops_new_device.set("Ноют второго аналитика")
    window.maint_issue_code()
    issued = [c for c in window.calls if c[1] == "/ops/code.json"]
    assert issued and issued[0][2]["role"] == "operator"
    assert "ODNORAZOVYIKOD" in window.ops_code_note.text()


def test_old_server_is_explained_in_russian(app, window):
    def broken_api(method, path, body=None, timeout=20):
        raise RuntimeError(
            "На сервере нет /ops/state.json — сервер старой версии?")
    window.api = broken_api
    window.maint_load()
    assert "старой версии" in window.ops_agent_note.text()
    assert "недоступно" in window.ops_actions.body.itemAt(0).widget().text()
