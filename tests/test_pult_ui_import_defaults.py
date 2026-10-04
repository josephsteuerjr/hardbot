"""Состояние галочек импорта на экране «Реестр»: что стоит с первого показа,
что об этом написано и что происходит, когда галочку снимают.

Решение владельца 16.09.2026: полный срез и возврат ручных снятий стоят по
умолчанию — это обычный прогон; очистка текущих комментариев остаётся снятой;
историчные комментарии галочки не имеют вовсе и грузятся по колонке файла.
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


def test_defaults_are_two_on_one_off_and_history_has_no_toggle(app, window):
    assert window.imp_full_var.get() is True
    assert window.imp_restore_manual_var.get() is True
    assert window.imp_replace_current_comments_var.get() is False
    assert not hasattr(window, "imp_import_historical_comments_var"), (
        "историчным комментариям галочка больше не нужна: забытая галочка "
        "оставляла выездника без истории вовсе"
    )


def test_checked_by_default_means_explained_by_default(app, window):
    """Включённая галочка без единого слова рядом — это и есть та тишина, из-за
    которой договоры снимаются «неожиданно». Текст стоит с первого показа."""
    warn = window.imp_warn.text()
    assert "ПОЛНЫЙ СРЕЗ" in warn
    assert "ВОЗВРАТ РУЧНЫХ СНЯТИЙ" in warn


def test_turning_one_toggle_off_keeps_the_other_warning_on_screen(app, window):
    """Поле предупреждений одно на все галочки. Раньше каждая писала своё
    поверх чужого, и самый опасный из режимов исчезал с экрана, стоило тронуть
    соседний."""
    window.imp_full_var.box.click()

    warn = window.imp_warn.text()
    assert window.imp_full_var.get() is False
    assert "ПОЛНЫЙ СРЕЗ" not in warn
    assert "ВОЗВРАТ РУЧНЫХ СНЯТИЙ" in warn


def test_new_book_returns_the_toggles_to_defaults(app, window):
    window.imp_full_var.set(False)
    window.imp_replace_current_comments_var.set(True)

    window._imp_vars_reset()

    assert window.imp_full_var.get() is True
    assert window.imp_restore_manual_var.get() is True
    assert window.imp_replace_current_comments_var.get() is False
    assert "ПОЛНЫЙ СРЕЗ" in window.imp_warn.text()


def test_python_profile_choice_changes_selection_and_invalidates_preview(app, window):
    import db
    from pult.core import registry_import as ri
    fields = db.FIELDS
    rows = [[f.title for f in fields] + ["ответственный Сотрудник СОФТ"],
            [f.source for f in fields] + ["софт"],
            [{"contract_id":"UI-PROFILE", "responsible":"Hard QA"}.get(f.key) for f in fields] + ["Soft QA"]]
    window.imp_book = {"grid":rows, "scope":ri.scope_from_grid(rows), "file_sha256":"synthetic"}
    ri.select_profile(window.imp_book, "hard")
    window.imp_selected = {"hard qa"}
    old_signature = window._imp_signature()
    window._imp_preview_signature = old_signature
    window.imp_profile.setCurrentIndex(1)
    assert window.imp_book["import_profile"] == "soft"
    assert window.imp_selected == {"soft qa"}
    assert window._imp_preview_signature is None
    assert window._imp_signature() != old_signature
