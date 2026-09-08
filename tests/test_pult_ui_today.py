"""Новый пульт офскрин: разделы, экран «Сегодня», таблица сотрудников с
сортировкой и поиском, «Каналы» отдельно от «Руководителей»."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from pult.core import activity as act  # noqa: E402
from pult.core import dashboard as dash  # noqa: E402
from pult.core.settings import Settings  # noqa: E402
from pult.ui import theme, widgets  # noqa: E402
from pult.ui.main_window import LAZY, Pult  # noqa: E402
from pult.ui.nav import SECTIONS  # noqa: E402

TODAY = {
    "date": "2026-09-09",
    "days": [{"date": f"2026-09-0{i}", "day": i, "label": f"{i} сен", "weekend": i in (5, 6),
              "visits": v} for i, v in zip(range(1, 10), (3, 38, 38, 25, 0, 0, 3, 195, 12))],
    "kpi": {"visits_today": 12, "visits_yesterday": 195, "visits_7d": 273,
            "employees_total": 29, "employees_active": 4, "paid_today": 1,
            "promised_today": 3, "with_photo_today": 0, "overdue": 198},
    "rows": [], "silent": [
        {"employee": "Молчун М.", "portfolio": 387, "entry": "none",
         "last_activity": None, "idle_days": None},
        {"employee": "Пауза П.", "portfolio": 120, "entry": "pwa",
         "last_activity": "2026-09-07", "idle_days": 2}],
    "delivery": {"visit": {"sent": 40, "failed": 0, "skipped": 1},
                 "digest": {"sent": 7, "failed": 0, "skipped": 0},
                 "promise": {"sent": 6, "failed": 0, "skipped": 0},
                 "report_chat": {"sent": 0, "failed": 0, "skipped": 40},
                 "blocked": [], "last_error": None},
    "enrichment": {"ledger_cells": 4085, "ledger_contracts": 474,
                   "last_delivered_at": "2026-09-08T22:02:55"},
    "registry": {"clients": 14192, "visited": 1392, "assigned": 9142,
                 "unassigned": {"absent_from_registry": 2845, "manual": 1295, "recalled": 910},
                 "employees": 29},
    "managers": {"total": 7, "no_dm": []},
}
WORKERS = [
    {"employee": "Актив А.", "kind": "agent", "telegram_id": 1, "visits_7d": 12, "visits_30d": 40,
     "markers_7d": 0, "markers_30d": 0, "last_activity": "2026-09-09", "idle_days": 0,
     "orphaned": False, "last_seen": "2026-09-09T10:15:00", "shell_version": 38, "entry": "pwa",
     "visits_today": 5, "photos_7d": 7, "portfolio_contracts": 387, "portfolio_persons": 116},
    {"employee": "Спящий С.", "kind": "agent", "telegram_id": 2, "visits_7d": 0, "visits_30d": 0,
     "markers_7d": 0, "markers_30d": 0, "last_activity": None, "idle_days": None,
     "orphaned": False, "last_seen": None, "shell_version": None, "entry": "none",
     "visits_today": 0, "photos_7d": 0, "portfolio_contracts": 200, "portfolio_persons": 80},
    {"employee": "Старый О.", "kind": "courier", "telegram_id": 3, "visits_7d": 2, "visits_30d": 9,
     "markers_7d": 1, "markers_30d": 3, "last_activity": "2026-09-05", "idle_days": 4,
     "orphaned": True, "last_seen": "2026-09-05T18:00:00", "shell_version": 37,
     "entry": "telegram", "visits_today": 0, "photos_7d": 1, "portfolio_contracts": 0,
     "portfolio_persons": 0},
]


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(application)
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def window(app, tmp_path):
    settings = Settings({"url": "https://127.0.0.1:9", "sec": "dash"},
                        tmp_path / "настройки пульта.json")
    w = Pult(settings)
    w.bg_task = lambda fn, done=None, fail=None: done(fn()) if done else fn()
    w.api = lambda method, path, body=None, timeout=20: (
        TODAY if path == "/admin/today" else {"workers": WORKERS} if path == "/admin/activity"
        else {"guests": []} if path == "/admin/guests" else {"employees": []})
    w.connected = True
    w.show()
    yield w
    w.close()


def test_sections_are_reorganised(app, window):
    keys = [s for s, _ in SECTIONS]
    assert keys == ["dash", "people", "mgrs", "channels", "registry", "prio", "conn"]
    assert "activity" not in keys, "«Активность» слилась с «Сотрудниками»"
    assert set(window.pages) == set(keys)
    assert LAZY["dash"] == "load_today" and LAZY["channels"] == "load_channels"
    window.nav("channels")
    assert window.sec_title.text() == "Каналы"
    assert hasattr(window, "chats_list") and hasattr(window, "dig_time")
    window.nav("mgrs")
    assert not hasattr(window, "geo_days") or window.pages["conn"] is not None


def test_today_screen_renders_day_payload(app, window):
    window.nav("dash")
    app.processEvents()
    assert window.tiles["visits_today"].text() == "12"
    assert window.tiles["employees_active"].text() == "4 / 29"
    assert window.silent_tv.model_.rowCount() == 2
    assert "2 из 29" in window.silent_note.text()
    assert "ни разу не открывали" in window.silent_note.text()
    assert window.delivery_kv.grid.count() == 4
    assert dash.delivery_pairs(TODAY)[0] == ("карточки визитов", "ушло 40 · упало 0")
    assert "нет в срезе реестра 2 845" in window.registry_note.text()
    assert "4 085 ячеек" in window.registry_note.text()
    assert len(window.day_bars._items) == 9


def test_employees_table_filters_and_sorts(app, window):
    window.nav("people")
    app.processEvents()
    tv = window.act_tv
    assert tv.model_.rowCount() == 3
    assert "ни разу не заходили 1" in window.act_summary.text()
    assert "на старой оболочке 1 (свежая v38)" in window.act_summary.text()
    # фильтр «ни разу не заходили»
    window._act_set_filter("never")
    assert tv.model_.rowCount() == 1 and tv.model_.rows[0][0] == "Спящий С."
    window._act_set_filter("old")
    assert tv.model_.rows[0][0] == "Старый О."
    window._act_set_filter("all")
    # поиск по любой колонке
    tv.set_filter("курьер")
    assert tv.visible_count() == 1
    tv.set_filter("")
    assert tv.visible_count() == 3
    # сортировка по портфелю: числа, а не строки
    col = [c[0] for c in act.ACTIVITY_COLUMNS].index("contracts")
    from PySide6.QtCore import Qt
    tv.sortByColumn(col, Qt.SortOrder.DescendingOrder)
    first = tv.proxy.index(0, 0).data()
    assert first == "Актив А."


def test_sort_key_treats_formatted_numbers_as_numbers():
    assert widgets.sort_key("1 234") < widgets.sort_key("9 999")
    assert widgets.sort_key("12%") < widgets.sort_key("100%")
    assert widgets.sort_key("") > widgets.sort_key("я"), "пустые — в конец"
    assert widgets.sort_key("абв")[0] == 1 and widgets.sort_key(5)[0] == 0


def test_dashboard_helpers_speak_russian():
    assert dash.tile_values(TODAY)["employees_active"] == "4 / 29"
    assert dash.silent_row(TODAY["silent"][0])[2] == "не заходил"
    assert "Сбоев доставки сегодня нет" in dash.delivery_note(TODAY)
    assert dash.registry_pairs(TODAY)[2] == ("без ответственного", 5050)
    row = act.activity_row(WORKERS[2])
    assert row[2] == "Telegram" and row[3] == 37 and row[-1].startswith("⚠ ФИО нет в реестре")
