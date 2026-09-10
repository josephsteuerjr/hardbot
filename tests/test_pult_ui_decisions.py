"""Таблица решений по конфликтам в окне пульта (офскрин)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", exc_type=ImportError,
    reason="нет работающего PySide6 (pip install -r requirements-dev.txt; на голом Linux Qt дополнительно требует системную libGL)")

from pult.core import enrichment as en  # noqa: E402
from pult.core.settings import Settings  # noqa: E402
from pult.ui import theme  # noqa: E402
from pult.ui.main_window import Pult  # noqa: E402


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


CONFLICTS = [
    {"contract_id": "A", "key": "call_status", "column": "Общий результат", "row": 2,
     "file": "Правка офиса", "server": "Отказ", "delivered": "", "visit_date": "2026-08-20",
     "kind": "no_history", "employee": "Иванов"},
    {"contract_id": "B", "key": "call_status", "column": "Общий результат", "row": 3,
     "file": "Ещё правка", "server": "Обещание", "delivered": "Отказ",
     "visit_date": "2026-08-21", "kind": "both_changed", "employee": "Иванов"},
]


def test_decision_table_defaults_bulk_and_per_row(app, window):
    table = window.enr_decisions
    assert table.isHidden(), "до проверки таблицы решений не видно"
    table.set_conflicts(CONFLICTS, [en.conflict_row(c) for c in CONFLICTS])
    assert table.rowCount() == 2
    assert table.decisions() == {("A", "call_status"): "file", ("B", "call_status"): "file"}, \
        "по умолчанию безопасное «оставить как в книге»"
    table.set_all(en.DECISION_SERVER)
    assert set(table.decisions().values()) == {"server"}
    # по одной строке: вторую вернуть к книге
    combo = table.cellWidget(1, table.columnCount() - 1)
    combo.setCurrentIndex(en.DECISION_ORDER.index(en.DECISION_FILE))
    assert table.decisions() == {("A", "call_status"): "server", ("B", "call_status"): "file"}
    assert table.item(0, 3).text() == "Правка офиса" and table.item(1, 4).text() == "Обещание"
    table.clear()
    assert table.rowCount() == 0 and table.decisions() == {}


def test_decisions_are_used_only_for_the_checked_selection(app, window):
    window.enr_decisions.set_conflicts(CONFLICTS, [en.conflict_row(c) for c in CONFLICTS])
    picked = [{"key": "ivanov", "employee": "Иванов"}]
    window._enr_preview = {"keys": frozenset({"ivanov"}), "since": "01.08.2026", "until": ""}
    assert window._enr_decisions_for(picked, "01.08.2026", "") == {
        ("A", "call_status"): "file", ("B", "call_status"): "file"}
    # другое окно дат или другой набор — решения не переносятся
    assert window._enr_decisions_for(picked, "02.08.2026", "") == {}
    assert window._enr_decisions_for(picked + [{"key": "petrov", "employee": "Петров"}],
                                     "01.08.2026", "") == {}
    assert "Записать" in window.enr_apply_sel_btn.text()
    assert "Проверить конфликты" in window.enr_check_btn.text()
