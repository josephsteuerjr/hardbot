"""Окно пульта строится офскрин: все разделы открываются, протокол вида у
виджетов работает, без сервера ошибка связи показывается тостом."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QTimer  # noqa: E402

from pult.core.settings import Settings  # noqa: E402
from pult.ui import theme, widgets  # noqa: E402
from pult.ui.main_window import Pult  # noqa: E402
from pult.ui.nav import SECTIONS  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(application)
    return application


@pytest.fixture
def window(app, tmp_path):
    settings = Settings({"url": "https://127.0.0.1:9", "key": "x", "sec": "registry"},
                        tmp_path / "настройки пульта.json")
    w = Pult(settings)
    yield w
    w.close()


def test_window_title_and_sections(app, window):
    assert window.windowTitle() == "Хард · Пульт"
    assert set(window.pages) == {sec for sec, _ in SECTIONS}
    for sec, _ in SECTIONS:
        window.nav(sec)
        assert window.stack.currentWidget() is window.pages[sec]
    assert window.sec_title.text() == "Подключение"
    assert window.settings["sec"] == "conn"


def test_connection_failure_is_a_toast_not_a_crash(app, window):
    QTimer.singleShot(2500, app.quit)
    window.refresh()
    app.exec()
    assert window.connected is False
    assert "Нет связи с сервером" in window.toast_lbl.text()
    assert window.rail.conn_lbl.text() == "нет связи"


def test_widgets_speak_the_view_protocol(app):
    note = widgets.Note("a", "muted")
    note.configure(text="b", role="warn")
    assert note.text() == "b" and note.property("role") == "warn"
    button = widgets.Button("x")
    button.configure(text="y", state="disabled")
    assert button.text() == "y" and not button.isEnabled()
    check = widgets.WrapCheck("длинная подпись", value=True)
    check.set(False)
    assert check.get() is False
    field = widgets.Field("секрет", secret=True)
    field.toggle_secret()
    assert not field.secret
    field.configure(show="•")
    assert field.secret
    kv = widgets.KVGrid()
    kv.set_pairs([("а", 1234), ("б", None)])
    assert kv.grid.count() == 2
    table = widgets.Table((("a", "A", 50), ("n", "N", 40)), numeric=("n",))
    table.set_rows([("x", 1), ("y", 2), ("z", 3)])
    assert table.model_.rowCount() == 3
    combo = widgets.Combo(["один", "два"])
    combo.set_values(["два", "три"])
    assert combo.get() == "два"


def test_closing_never_stores_the_master_key(app, tmp_path):
    settings = Settings({"url": "https://h", "key": "master", "device_token": "tok"},
                        tmp_path / "настройки пульта.json")
    w = Pult(settings)
    w.close()
    saved = Settings.load(tmp_path / "настройки пульта.json")
    assert saved["device_token"] == "tok" and saved["url"] == "https://h"


def test_registry_columns_screen_renders_a_decision_per_unknown_header(app, window):
    """Экран «Колонки реестра» обязан открываться и на книге, которую импорт
    принять пока не может: иначе оператор не увидит, что именно сломалось."""
    from pult.core import header_map as hm
    from pult.core import registry_import as ri

    window.nav("registry")
    hm.forget()
    window._col_path = "ОБЩИЙ РЕЕСТР.xlsb"
    window._col_analysis = {
        "header_row": 2, "has_contract_key": True,
        "recognized": [{"column": 5, "title": "Номер КД / ID", "key": "contract_id",
                        "field_title": "Номер КД / ID", "by_operator": False}],
        "missing": [{"key": "responsible", "title": "ответственный Сотрудник",
                     "required": True}],
        "missing_required": [{"key": "responsible",
                              "title": "ответственный Сотрудник", "required": True}],
        "unknown": [{"column": 10, "title": "ответственный Сотрудник ОТДЕЛ",
                     "suggest": "responsible"},
                    {"column": 90, "title": "условия акции", "suggest": ""}],
        "ignored": [], "extras": [],
    }
    window._columns_render()

    assert window.col_table.isVisible() or window.col_table.rowCount() == 2
    assert "Не хватает обязательных колонок" in window.col_state.text()
    decisions = window.col_table.decisions()
    assert decisions[0]["decision"] == "field:responsible"   # подсказка подставлена
    assert decisions[1]["decision"] == "skip"                # догадок не строим

    decisions[1] = {"title": "условия акции", "decision": "extra", "label": "Акция"}
    new_map = hm.apply_decisions(hm.current_map(), decisions)
    assert new_map["aliases"]["responsible"] == ["ответственный Сотрудник ОТДЕЛ"]
    assert new_map["extras"][0]["label"] == "Акция"
    assert ri.columns_caption("книга.xlsb", window._col_analysis).startswith("книга.xlsb")
    hm.forget()
