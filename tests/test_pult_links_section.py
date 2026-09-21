"""Раздел «Вход»: персональные ссылки PWA — выдача, ожидание, QR-диалог.

Контур компании работает без Telegram, ссылка из пульта — единственный путь
сотрудника в систему. Проверяется офскрин: раздел есть в навигации, список
«ожидают входа» строится по /admin/activity, кнопка выдачи зовёт
/admin/app_link.json и показывает окно с QR, без segno диалог не падает."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", exc_type=ImportError,
    reason="нет работающего PySide6 (pip install -r requirements-dev.txt; на голом Linux Qt дополнительно требует системную libGL)")

from pult.core.settings import Settings  # noqa: E402
from pult.ui import dialogs  # noqa: E402
from pult.ui.main_window import Pult  # noqa: E402
from pult.ui.nav import SECTIONS  # noqa: E402

EMPS = [
    {"employee": "Иванов Иван", "contracts": 120},
    {"employee": "Петров Пётр", "contracts": 80, "kind": "courier"},
    {"employee": "Сидорова Анна", "contracts": 40},
]
WORKERS = [
    {"employee": "Иванов Иван", "kind": "agent", "entry": "none",
     "portfolio_contracts": 120, "last_seen": "", "shell_version": 56},
    {"employee": "Сидорова Анна", "kind": "agent", "entry": "pwa",
     "portfolio_contracts": 40, "last_seen": "2026-09-20T09:15:00",
     "shell_version": 56},
]


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    settings = Settings({"url": "https://127.0.0.1:9", "sec": "links"},
                        tmp_path / "настройки пульта.json")
    w = Pult(settings)
    calls: list[tuple] = []
    shown: list[tuple] = []
    refreshed: list[bool] = []

    def api(method, path, body=None, timeout=20):
        calls.append((method, path, body))
        if path == "/admin/employees":
            return {"employees": [dict(e) for e in EMPS]}
        if path == "/admin/activity":
            return {"workers": [dict(x) for x in WORKERS], "generated_at": ""}
        if path == "/admin/app_link.json":
            return {"ok": True, "url": "https://hard.example/#link=abc123",
                    "employee": (body or {}).get("employee"),
                    "days": 7, "local_identity": True, "created": True}
        return {}

    def bg_task(fn, done=None, fail=None):
        result = fn()
        if done:
            done(result)

    w.api = api
    w.bg_task = bg_task
    w.calls = calls
    w.shown = shown
    w.refreshed = refreshed
    w.refresh = lambda: refreshed.append(True)
    monkeypatch.setattr("pult.ui.people.show_link",
                        lambda title, url, note="", parent=None:
                        shown.append((title, url, note)))
    w.connected = True
    yield w
    w.close()


def test_links_section_is_in_navigation(app, window):
    assert ("links", "Вход") in SECTIONS
    assert "links" in window.pages
    window.nav("links")
    assert window.stack.currentWidget() is window.pages["links"]
    assert window.sec_title.text() == "Вход сотрудников"


def test_load_links_fills_combo_and_waiting(app, window):
    window.load_links()
    assert [window.ln_emp.itemText(i) for i in range(window.ln_emp.count())] \
        == [e["employee"] for e in EMPS]
    assert window.ln_emp.get() == "Иванов Иван"          # первый из реестра
    assert window.ln_kind.get() == "Выездник"            # тип подтянулся из реестра
    # Петров — курьер: выбор его в комбобоксе переключает тип
    window.ln_emp.setCurrentIndex(1)     # setCurrentIndex шлёт сигнал, set_current гасит
    assert window.ln_kind.get() == "Курьер ГПХ"
    # ждут входа только непрошедшие: Иванов, не Сидорова
    assert "не заходили 1" in window.ln_summary.text()
    assert "зашли 1 из 2" in window.ln_summary.text()


def test_issue_link_from_links_section(app, window):
    window.load_links()
    window.ln_emp.setCurrentIndex(0)
    window.ln_issue()
    posts = [c for c in window.calls if c[1] == "/admin/app_link.json"]
    assert posts and posts[-1][2] == {"employee": "Иванов Иван", "kind": "agent"}
    assert window.shown and window.shown[-1][1] == "https://hard.example/#link=abc123"
    assert "персональная" in window.shown[-1][2].lower()
    assert window.refreshed            # created=True → список «ожидают» обновится


def test_link_dialog_has_qr_and_url(app, window):
    pytest.importorskip("segno", reason="QR показывает только с segno")
    dialog = dialogs._build_link_dialog("Ссылка входа", "https://hard.example/#link=abc",
                                        "проверка", parent=None)
    qr = dialog.findChild(QtWidgets.QLabel, "LinkQR")
    assert qr is not None and qr.pixmap() is not None
    field = [c for c in dialog.findChildren(QtWidgets.QLineEdit)][0]
    assert field.text() == "https://hard.example/#link=abc"


def test_link_dialog_survives_missing_segno(app, window, monkeypatch):
    monkeypatch.setitem(sys.modules, "segno", None)      # import segno → ImportError
    dialog = dialogs._build_link_dialog("Ссылка входа", "https://hard.example/#link=abc")
    qr = dialog.findChild(QtWidgets.QLabel, "LinkQR")
    assert qr is None                                    # без QR, но диалог жив
    texts = " ".join(l.text() for l in dialog.findChildren(QtWidgets.QLabel))
    assert "segno" in texts
    dialog.deleteLater()
