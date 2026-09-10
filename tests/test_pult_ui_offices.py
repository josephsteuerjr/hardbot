"""Офис сотрудника в пульте: колонка на «Сотрудниках» и охват на «Руководителях».

Офис — это где сидит САМ СОТРУДНИК, и вывести его из данных нельзя: город в
реестре означает, где живёт должник (душанбинский выездник ездит в Ёвон, Хисор
и Вахдат). Поэтому его проставляет оператор, и на нём держится состав
руководителя города.

Проверяем ровно то, ради чего экран и делался: офис виден и сохраняется сразу
по выбору, лишних запросов при этом нет; на «Руководителях» офисы — отдельный
блок от «городов-подписи», и сохранение уносит их явно.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from pult.core.settings import Settings  # noqa: E402
from pult.ui import dialogs, theme  # noqa: E402
from pult.ui.main_window import Pult  # noqa: E402
from pult.ui.people import OFFICE_NONE  # noqa: E402

EMPS = [{"employee": "Акназаров Карим", "contracts": 280},
        {"employee": "Рахимов Махмадулло", "contracts": 348}]
OFFICES = {"Акназаров Карим": "Худжанд", "Рахимов Махмадулло": "Душанбе"}
MANAGERS = {
    "managers": [{"telegram_id": 943628290, "name": "Старший спец", "cities": [],
                  "employees": [], "direction": "", "office_cities": ["Душанбе"],
                  "can_dm": 1, "spelling_mismatch": 0}],
    "cities": [], "employees": EMPS, "city_employees": {},
    "digest": {"time": "16:30", "enabled": True, "promise_time": "09:30",
               "promise_enabled": True, "visit_push": True, "delivery": {}},
}


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(application)
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    # Без ключа: окно с учётными данными само ставит таймер refresh(), который
    # выстрелил бы уже в чужом тесте — по удалённому окну.
    settings = Settings({"url": "https://127.0.0.1:9", "sec": "dash"},
                        tmp_path / "настройки пульта.json")
    w = Pult(settings)
    calls: list[tuple] = []
    offices = dict(OFFICES)

    def api(method, path, body=None, timeout=20):
        calls.append((method, path, body))
        if path == "/admin/employees":
            return {"employees": EMPS}
        if path == "/admin/employee_cities.json":
            for emp, city in ((body or {}).get("cities") or {}).items():
                offices[emp] = city if city else offices.pop(emp, None) and None
                if not city:
                    offices.pop(emp, None)
            return {"ok": True, "cities": dict(offices),
                    "employees": [e["employee"] for e in EMPS],
                    "known": sorted({c for c in offices.values() if c})}
        if path == "/admin/managers":
            return {**MANAGERS, "managers": [dict(m) for m in MANAGERS["managers"]]}
        if path == "/admin/guests":
            return {"guests": []}
        if path == "/admin/activity":
            return {"workers": [], "generated_at": ""}
        if path == "/admin/manager":
            return {"ok": True, "manager": body, "account": {"name": body["name"]}}
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


def _office_combos(window) -> list:
    """Комбобоксы офиса — те, где первым значением стоит «не задан»."""
    return [c for c in window.roles_frame.findChildren(QtWidgets.QComboBox)
            if c.count() and c.itemText(0) == OFFICE_NONE]


def _open_people(window, app):
    window.nav("people")
    window.load_roles()
    app.processEvents()


def test_office_column_shows_current_value(app, window):
    _open_people(window, app)
    combos = _office_combos(window)
    assert len(combos) == 2, "по комбобоксу офиса на каждого сотрудника"
    assert [c.currentText() for c in combos] == ["Худжанд", "Душанбе"]


def test_choosing_office_saves_at_once(app, window):
    _open_people(window, app)
    combo = _office_combos(window)[1]           # Рахимов, сейчас Душанбе
    combo.setCurrentText("Худжанд")
    window.calls.clear()
    window.set_office("Рахимов Махмадулло", combo)
    app.processEvents()
    assert [c for c in window.calls if c[0] == "POST"] == [
        ("POST", "/admin/employee_cities.json",
         {"cities": {"Рахимов Махмадулло": "Худжанд"}})]
    assert window._offices["Рахимов Махмадулло"] == "Худжанд"


def test_unchanged_office_does_not_post(app, window):
    """Комбобокс дёргает обработчик и просто при уходе фокуса — лишний POST на
    каждую строку превратил бы открытие вкладки в тридцать записей."""
    _open_people(window, app)
    window.calls.clear()
    window.set_office("Рахимов Махмадулло", _office_combos(window)[1])
    assert [c for c in window.calls if c[0] == "POST"] == []


def test_clearing_office_sends_empty_string(app, window):
    _open_people(window, app)
    combo = _office_combos(window)[1]
    combo.setCurrentText(OFFICE_NONE)
    window.calls.clear()
    window.set_office("Рахимов Махмадулло", combo)
    app.processEvents()
    assert ("POST", "/admin/employee_cities.json",
            {"cities": {"Рахимов Махмадулло": ""}}) in window.calls
    assert "Рахимов Махмадулло" not in window._offices


def test_manager_form_offers_offices_and_saves_them(app, window):
    window.nav("mgrs")
    window.load_managers()
    app.processEvents()
    # Штат офиса пульт знает и подписывает — оператор видит, кого получит.
    assert window.mgr_office_staff["Душанбе"] == ["Рахимов Махмадулло"]
    window.mgr_edit(943628290)
    app.processEvents()
    assert window.mgr_form["offices"] == {"Душанбе"}
    assert "Рахимов Махмадулло" in window.mgr_office_hint.text()
    window.calls.clear()
    window.save_manager()
    app.processEvents()
    body = next(c[2] for c in window.calls
                if c[0] == "POST" and c[1] == "/admin/manager")
    assert body["office_cities"] == ["Душанбе"]
    assert body["cities"] == [], "офис — не «город-подпись», это разные поля"


def test_office_alone_is_enough_to_save_a_manager(app, window):
    """Раньше сохранение требовало направление или поимённый список; офис —
    такое же полноценное основание, иначе экран не даёт завести старшего спеца."""
    window.nav("mgrs")
    window.load_managers()
    app.processEvents()
    window._mgr_form_reset()
    window.mgr_tid.set("943628290")
    window.mgr_name.set("Старший спец")
    window._mgr_toggle_office("Душанбе")
    window.calls.clear()
    window.save_manager()
    app.processEvents()
    assert any(c[0] == "POST" and c[1] == "/admin/manager" for c in window.calls)
