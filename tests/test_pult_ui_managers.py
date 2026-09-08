"""Раздел «Руководители» офскрин: «изменить» заполняет форму и прокручивает к ней,
подсказка направления не стирается счётчиком «выбрано», галочки сотрудников у
руководителя направления гаснут, сохранение шлёт direction и сбрасывает форму.

08.09.2026 владелец жаловался «перестала работать функция изменения»: сервер
принимал сохранения (200), а в пульте форма лежала в самом низу страницы —
клик по «изменить» ничего видимого не делал, подсказка направления затиралась,
а погасшие по смыслу галочки сотрудников оставались живыми.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from pult.core.settings import Settings  # noqa: E402
from pult.ui import dialogs, theme  # noqa: E402
from pult.ui.main_window import Pult  # noqa: E402
from pult.ui.managers import MGR_FORM_TITLE  # noqa: E402
from pult.ui.widgets import Button  # noqa: E402

MANAGERS = {
    "managers": [
        {"telegram_id": 8539622577, "name": "Рук ТЖ", "cities": ["Таджикистан"],
         "employees": [], "direction": "tj", "can_dm": 1, "spelling_mismatch": 0},
        {"telegram_id": 111222333, "name": "Точечный", "cities": ["Худжанд"],
         "employees": ["Иванов И."], "direction": "", "can_dm": 1, "spelling_mismatch": 0},
    ],
    "cities": [{"name": "Худжанд", "count": 5}],
    "employees": [{"employee": "Иванов И.", "contracts": 5},
                  {"employee": "Петров П.", "contracts": 2}],
    "city_employees": {"Худжанд": ["Иванов И."]},
    "digest": {"time": "16:30", "enabled": True, "promise_time": "09:30",
               "promise_enabled": True, "visit_push": True, "delivery": {}},
}
STATS = {"clients": 0, "visited": 0, "markers": 0, "countries": [], "employees": [],
         "bindings": [], "by_result": {}, "period": {}}


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(application)
    # Окна здесь показываются и закрываются без event loop; иначе закрытие
    # последнего окна ставит Qt «выход», и app.exec() соседнего теста
    # возвращается сразу.
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    # Без ключа: окно с учётными данными само ставит таймер refresh(), который
    # выстрелил бы уже в чужом тесте с живым event loop — по удалённому окну.
    settings = Settings({"url": "https://127.0.0.1:9", "sec": "dash"},
                        tmp_path / "настройки пульта.json")
    w = Pult(settings)
    calls: list[tuple] = []

    def api(method, path, body=None, timeout=20):
        calls.append((method, path, body))
        if path == "/admin/guests":
            return {"guests": [{"telegram_id": 8539622577, "name": "Рук ТЖ"}]}
        if path == "/admin/managers":
            return {**MANAGERS, "managers": [dict(m) for m in MANAGERS["managers"]]}
        if path == "/admin/chats":
            return {"chats": [], "employees": []}
        if path == "/admin/manager":
            return {"ok": True, "manager": body, "account": {"name": body["name"]}}
        if path == "/admin/stats":
            return STATS
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
    w.nav("mgrs")
    app.processEvents()
    yield w
    w.close()


def _edit_buttons(window) -> list[Button]:
    return [b for b in window.mgr_list.findChildren(Button) if b.text() == "изменить"]


def test_edit_fills_form_scrolls_and_keeps_direction_hint(app, window):
    page = window.pages["mgrs"]
    assert page.verticalScrollBar().value() == 0
    assert window.mgr_form_card.title.text() == MGR_FORM_TITLE
    _edit_buttons(window)[0].click()
    app.processEvents()
    assert window.mgr_tid.get() == "8539622577" and window.mgr_name.get() == "Рук ТЖ"
    assert window.mgr_direction.current() == 1
    assert "Таджикистан" in window.mgr_emp_hint.text(), "подсказка направления стёрлась"
    assert not window.mgr_emp_chips.isEnabled(), "у направленца галочки должны гаснуть"
    assert "Рук ТЖ" in window.mgr_form_card.title.text()
    bar = page.verticalScrollBar()
    assert bar.maximum() == 0 or bar.value() > 0, "к форме внизу страницы не прокрутило"
    assert "Форма заполнена" in window.toast_lbl.text()


def test_plain_manager_keeps_employee_chips(app, window):
    _edit_buttons(window)[1].click()
    app.processEvents()
    assert window.mgr_direction.current() == 0
    assert window.mgr_emp_chips.isEnabled()
    assert window.mgr_emp_hint.text() == "выбрано: 1"
    assert window.mgr_form["employees"] == {"Иванов И."}


def test_save_sends_direction_and_resets_form(app, window):
    _edit_buttons(window)[0].click()
    app.processEvents()
    window.calls.clear()
    window.save_manager()
    app.processEvents()
    posts = [c for c in window.calls if c[0] == "POST"]
    assert posts == [("POST", "/admin/manager",
                      {"telegram_id": 8539622577, "name": "Рук ТЖ",
                       "cities": ["Таджикистан"], "employees": [], "direction": "tj"})]
    assert ("GET", "/admin/managers", None) in window.calls, "после записи список перечитан"
    assert window.mgr_tid.get() == "" and window.mgr_form_card.title.text() == MGR_FORM_TITLE
    assert window.mgr_emp_chips.isEnabled() and window.mgr_emp_hint.text() == ""
