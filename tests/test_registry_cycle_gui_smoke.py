"""Окно переноса реестров строится офскрин на PySide6: заголовок, две
операции, диалог папок, подтверждение фразой. Публичный API сохранён."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from registry_cycle import gui, manual_app, theme  # noqa: E402
from registry_cycle.config import load_config_bundle  # noqa: E402


@pytest.fixture(scope="module")
def app():
    bundle = load_config_bundle()
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(application, bundle.theme)
    return application, bundle


def test_public_api_is_intact():
    assert callable(gui.run_gui)
    assert gui.RegistryCycleApp is gui.ManualTransferApp
    assert manual_app.main.__module__ == "registry_cycle.manual_app"


def test_window_and_dialogs_build(app):
    application, bundle = app
    window = gui.ManualTransferApp(bundle)
    assert window.windowTitle() == "Хард · Перенос реестров"
    assert len(window.action_buttons) == 2            # «Проверить шаг 1», «Проверить шаг 2»
    assert window.states == {"upward": "idle", "downward": "idle"}
    window.toggle_details("upward")
    assert window.details["upward"] is True

    dialog = gui.SourceFoldersDialog(window)
    before = len(dialog.rows)
    dialog._add_row(None)
    assert len(dialog.rows) == before + 1
    dialog._remove_row(dialog.rows[-1])
    assert len(dialog.rows) == before
    dialog.reject()

    confirm = gui.PublishDialog(window, "upward", "ДА")
    confirm.entry.setText("нет")
    confirm._confirm()
    assert confirm.confirmed is False and confirm.error.text()
    confirm.entry.setText("ДА")
    confirm._confirm()
    assert confirm.confirmed is True
    window.close()


def test_failure_from_worker_marks_operation(app, monkeypatch):
    application, bundle = app
    window = gui.ManualTransferApp(bundle)
    monkeypatch.setattr(gui, "_message", lambda *a, **k: None)
    window.busy_operation = "upward"
    window._on_failed(window._new_signals(), RuntimeError("занято"))
    assert window.busy is False
    assert window.states["upward"] == "failed"
    assert window.status_details["upward"] == "занято"
    window.close()
