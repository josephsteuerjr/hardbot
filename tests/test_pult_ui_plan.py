"""Норма выездов в пульте: колонка на «Сотрудниках», рядом с офисом.

Офис делит выездников на группы по фактической выработке — 8, 10 и 12 выездов в
операционный день. Раньше норма была одна на всех и лежала в коде; теперь её
ставит оператор здесь, а руководитель — со своего экрана в приложении.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from pult.core.settings import Settings  # noqa: E402
from pult.ui import dialogs, theme  # noqa: E402
from pult.ui.main_window import Pult  # noqa: E402
from pult.ui.people import PLAN_COMMON  # noqa: E402

EMPS = [{"employee": "Акназаров Карим", "contracts": 280, "plan": 12,
         "plan_personal": True},
        {"employee": "Рахимов Махмадулло", "contracts": 348, "plan": 10,
         "plan_personal": False}]


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(application)
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    settings = Settings({"url": "https://127.0.0.1:9", "sec": "dash"},
                        tmp_path / "настройки пульта.json")
    w = Pult(settings)
    calls: list[tuple] = []

    def api(method, path, body=None, timeout=20):
        calls.append((method, path, body))
        if path == "/admin/employees":
            return {"employees": EMPS, "plan_common": 10, "plan_groups": [8, 10, 12]}
        if path == "/admin/visit_plan.json":
            return {"ok": True, "plan": {}, "common": 10, "groups": [8, 10, 12]}
        if path == "/admin/employee_cities.json":
            return {"ok": True, "cities": {}, "employees": [], "known": []}
        if path == "/admin/guests":
            return {"guests": []}
        if path == "/admin/activity":
            return {"workers": [], "generated_at": ""}
        return {}

    def bg_task(fn, done=None, fail=None):
        result = fn()
        if done:
            done(result)

    w.api = api
    w.bg_task = bg_task
    w.calls = calls
    monkeypatch.setattr(dialogs, "askyesno", lambda *_a, **_k: True)
    monkeypatch.setattr(dialogs, "showwarning", lambda *_a, **_k: None)
    w.connected = True
    w.show()
    yield w
    w.close()


def _plan_combos(window) -> list:
    return [c for c in window.roles_frame.findChildren(QtWidgets.QComboBox)
            if c.count() and c.itemText(0) == PLAN_COMMON]


def _open_people(window, app):
    window.nav("people")
    window.load_roles()
    app.processEvents()


def test_column_shows_personal_norm_and_common_one(app, window):
    _open_people(window, app)
    combos = _plan_combos(window)
    assert len(combos) == 2, "по комбобоксу нормы на каждого сотрудника"
    assert combos[0].currentText() == "12", "личная норма видна как есть"
    assert combos[1].currentText() == PLAN_COMMON, "без личной — «общая»"


def test_groups_from_the_server_are_offered(app, window):
    _open_people(window, app)
    combo = _plan_combos(window)[0]
    values = [combo.itemText(i) for i in range(combo.count())]
    assert values == [PLAN_COMMON, "8", "10", "12"]


def test_choosing_norm_saves_at_once(app, window):
    _open_people(window, app)
    combo = _plan_combos(window)[1]
    combo.setCurrentText("8")
    window.calls.clear()
    window.set_plan("Рахимов Махмадулло", combo)
    app.processEvents()
    assert [c for c in window.calls if c[0] == "POST"] == [
        ("POST", "/admin/visit_plan.json", {"plan": {"Рахимов Махмадулло": 8}})]


def test_common_returns_zero(app, window):
    """«Общая» — это снятие личной нормы, сервер понимает её как ноль."""
    _open_people(window, app)
    combo = _plan_combos(window)[0]
    combo.setCurrentText(PLAN_COMMON)
    window.calls.clear()
    window.set_plan("Акназаров Карим", combo)
    app.processEvents()
    assert [c for c in window.calls if c[0] == "POST"] == [
        ("POST", "/admin/visit_plan.json", {"plan": {"Акназаров Карим": 0}})]


def test_garbage_is_not_sent(app, window):
    _open_people(window, app)
    combo = _plan_combos(window)[0]
    combo.setCurrentText("десять")
    window.calls.clear()
    window.set_plan("Акназаров Карим", combo)
    app.processEvents()
    assert [c for c in window.calls if c[0] == "POST"] == [], "число или «общая»"
