"""Массовая выдача входа из пульта аналитика: старт пробы в один клик.

Проба начинается с того, что вход нужен всем сразу: шестидесяти выездникам и
десятку руководителей. По одному — шестьдесят кликов и шестьдесят шансов
промахнуться строкой. Поэтому: галочки в «Входе» (или «отметить показанных»
после чипа «не выдавали»), одна кнопка, и итог списком «ФИО — ссылка»,
который копируется целиком или уходит в Excel. Руководителям — одна кнопка в
«Руководителях»; тем, у кого нет команды, вход не выдаётся.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", exc_type=ImportError,
    reason="нет работающего PySide6 (pip install -r requirements-dev.txt)")

from PySide6.QtCore import Qt  # noqa: E402

from pult.core import links as lk  # noqa: E402
from pult.core.settings import Settings  # noqa: E402
from pult.ui import dialogs  # noqa: E402
from pult.ui.main_window import Pult  # noqa: E402

BASE = "https://hard.example"
EMPS = [
    {"employee": "Иванов Иван", "contracts": 120},
    {"employee": "Петров Пётр", "contracts": 80, "kind": "courier"},
    {"employee": "Сидорова Анна", "contracts": 40, "telegram_id": 900001},
]
MANAGERS = [
    {"telegram_id": 501, "name": "Рук Направления", "direction": "tj",
     "employees": [], "office_cities": [], "cities": []},
    {"telegram_id": 502, "name": "Рук Офиса", "direction": None,
     "employees": [], "office_cities": ["Худжанд"], "cities": []},
    {"telegram_id": 503, "name": "Алексей", "direction": None,
     "employees": [], "office_cities": [], "cities": []},
]


# --- логика без Qt ---------------------------------------------------------

def test_bulk_items_carry_bound_id_like_single_issue():
    people = {e["employee"]: dict(e) for e in EMPS}
    items = lk.bulk_items(people, ["Сидорова Анна", "Петров Пётр", "Нет Такого"])
    assert items == [
        {"employee": "Сидорова Анна", "kind": "agent", "telegram_id": 900001},
        {"employee": "Петров Пётр", "kind": "courier"},
    ]


def test_manager_items_skip_those_without_team_and_send_only_id():
    items, skipped = lk.manager_items(MANAGERS)
    assert items == [{"telegram_id": 501}, {"telegram_id": 502}]
    assert skipped == ["Алексей"]
    assert all(set(i) == {"telegram_id"} for i in items), \
        "с ФИО в запросе сервер сделал бы руководителя выездником"
    assert "Алексей" in lk.manager_bulk_text(2, skipped)


def test_bulk_rows_sort_and_text():
    results = [
        {"employee": "Яковлев Я.", "telegram_id": 1, "url": f"{BASE}/i/a",
         "max_uses": 2, "expires_at": "2026-10-01T10:00:00"},
        {"employee": "Рук Направления", "telegram_id": 501, "url": f"{BASE}/i/b",
         "max_uses": 2, "expires_at": "2026-10-01T10:00:00"},
        {"employee": "Ошибкин О.", "telegram_id": 777, "error": "id 777 никому не привязан"},
    ]
    rows = lk.bulk_rows(results, manager_ids=[501])
    assert [r["employee"] for r in rows] == ["Рук Направления", "Яковлев Я.", "Ошибкин О."]
    assert rows[0]["who"] == lk.MANAGER_WHO and rows[1]["who"] == lk.WORKER_WHO
    assert rows[1]["expires"] == "01.10"
    assert lk.bulk_text(rows) == (f"Рук Направления — {BASE}/i/b\n"
                                  f"Яковлев Я. — {BASE}/i/a")
    summary = lk.bulk_summary(rows)
    assert "2 из 3" in summary and "Ошибка" in summary


def test_bulk_xlsx_round_trip(tmp_path):
    from openpyxl import load_workbook

    rows = lk.bulk_rows([{"employee": "Иванов Иван", "telegram_id": 1,
                          "url": f"{BASE}/i/x", "max_uses": 2,
                          "expires_at": "2026-10-01T10:00:00"}])
    path = tmp_path / "ссылки.xlsx"
    lk.write_bulk_xlsx(path, rows)
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]] == list(lk.BULK_HEADERS)
    assert ws["A2"].value == "Иванов Иван" and ws["C2"].value == f"{BASE}/i/x"
    assert ws["C2"].hyperlink.target == f"{BASE}/i/x"


def test_confirm_text_speaks_russian_numbers():
    assert "по 1 открытию" in lk.bulk_confirm_text(3, 1)
    assert "по 2 открытия" in lk.bulk_confirm_text(3, 2)
    assert "по 5 открытий" in lk.bulk_confirm_text(3, 5)
    assert "не вылетит" in lk.bulk_confirm_text(3, 2, entered=1)


# --- окно ------------------------------------------------------------------

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
    server = {"bulk_missing": False, "next_tid": 700001}

    def issue_one(body):
        tid = body.get("telegram_id") or server["next_tid"]
        server["next_tid"] += 1
        name = body.get("employee") or next(
            (m["name"] for m in MANAGERS if m["telegram_id"] == tid), f"id{tid}")
        return {"ok": True, "telegram_id": tid, "employee": name,
                "code": f"код-{tid}", "url": f"{BASE}/i/код-{tid}",
                "max_uses": int(body.get("uses") or 2),
                "expires_at": "2026-10-01T10:00:00"}

    def api(method, path, body=None, timeout=20):
        calls.append((method, path, body))
        if path == "/admin/employees":
            return {"employees": [dict(e) for e in EMPS]}
        if path == "/admin/app_links.json":
            return {"links": []}
        if path == "/admin/activity":
            return {"workers": []}
        if path == "/admin/app_links_bulk.json":
            if server["bulk_missing"]:
                raise RuntimeError("На сервере нет /admin/app_links_bulk.json — "
                                   "сервер старой версии?")
            return {"ok": True, "results": [
                issue_one(dict(i, uses=body["uses"])) for i in body["items"]]}
        if path == "/admin/app_link.json":
            return issue_one(body)
        if path == "/admin/managers":
            return {"managers": [dict(m) for m in MANAGERS]}
        return {}

    def bg_task(fn, done=None, fail=None):
        result = fn()
        if done:
            done(result)

    w.api = api
    w.bg_task = bg_task
    w.calls = calls
    w.shown = shown
    w.server = server
    w.connected = True
    monkeypatch.setattr("pult.ui.dialogs.show_link_list",
                        lambda title, rows, parent=None: shown.append((title, rows)))
    monkeypatch.setattr("pult.ui.dialogs.askyesno", lambda *a, **k: True)
    w.load_links()
    yield w
    w.close()


def _check(window, name, on=True):
    model = window.ln_tv.model_
    row = next(i for i, r in enumerate(model.rows) if r[0] == name)
    idx = model.index(row, 0)
    state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
    assert model.setData(idx, state, Qt.ItemDataRole.CheckStateRole)


def test_checkboxes_live_in_first_column_and_survive_filters(app, window):
    model = window.ln_tv.model_
    idx = model.index(0, 0)
    assert model.flags(idx) & Qt.ItemFlag.ItemIsUserCheckable
    assert not (model.flags(model.index(0, 1)) & Qt.ItemFlag.ItemIsUserCheckable)
    assert window.ln_bulk_btn.isEnabled() is False
    _check(window, "Петров Пётр")
    assert window.ln_tv.checked_keys() == ["Петров Пётр"]
    assert "(1)" in window.ln_bulk_btn.text() and window.ln_bulk_btn.isEnabled()
    # перечитывание списка отметку не теряет
    window.load_links()
    assert window.ln_tv.checked_keys() == ["Петров Пётр"]


def test_mark_visible_respects_search(app, window):
    window.ln_search.set("Сидор")
    window.ln_tv.set_filter("Сидор")
    window.ln_tv.check_visible(True)
    assert window.ln_tv.checked_keys() == ["Сидорова Анна"]
    window.ln_tv.set_filter("")
    window.ln_tv.check_visible(True)
    assert len(window.ln_tv.checked_keys()) == 3
    window.ln_tv.set_checked(())
    assert window.ln_tv.checked_keys() == [] and not window.ln_bulk_btn.isEnabled()


def test_bulk_issue_sends_one_request_and_shows_list(app, window):
    window.ln_tv.check_visible(True)
    window.ln_bulk_issue()
    bulk = [c for c in window.calls if c[1] == "/admin/app_links_bulk.json"]
    assert len(bulk) == 1, "массовая выдача должна идти одним запросом"
    items = bulk[0][2]["items"]
    assert {i["employee"] for i in items} == {e["employee"] for e in EMPS}
    assert next(i for i in items if i["employee"] == "Сидорова Анна")["telegram_id"] == 900001
    assert not [c for c in window.calls if c[1] == "/admin/app_link.json"]
    title, rows = window.shown[-1]
    assert "3" in title and len(rows) == 3 and all(r["url"] for r in rows)
    assert window.ln_tv.checked_keys() == [], "после выдачи отметки снимаются"


def test_old_server_without_bulk_falls_back_to_one_by_one(app, window):
    window.server["bulk_missing"] = True
    window.ln_tv.check_visible(True)
    window.ln_bulk_issue()
    singles = [c for c in window.calls if c[1] == "/admin/app_link.json"]
    assert len(singles) == 3
    assert len(window.shown[-1][1]) == 3


def test_managers_bulk_skips_those_without_team(app, window):
    window.load_managers()
    window.mgr_bulk_issue()
    bulk = [c for c in window.calls if c[1] == "/admin/app_links_bulk.json"]
    assert bulk[-1][2]["items"] == [{"telegram_id": 501}, {"telegram_id": 502}]
    _title, rows = window.shown[-1]
    assert [r["who"] for r in rows] == [lk.MANAGER_WHO, lk.MANAGER_WHO]


def test_result_dialog_copies_whole_list(app, monkeypatch):
    copied = []
    monkeypatch.setattr("pult.ui.dialogs.copy_text", copied.append)
    rows = lk.bulk_rows([
        {"employee": "Иванов Иван", "telegram_id": 1, "url": f"{BASE}/i/x", "max_uses": 2},
        {"employee": "Ошибкин О.", "telegram_id": 2, "error": "нет"}])
    dialog = dialogs._build_link_list_dialog("Ссылки", rows)
    assert dialog.table.rowCount() == 2
    dialog.copy_btn.click()
    assert copied == [f"Иванов Иван — {BASE}/i/x"]
    empty = dialogs._build_link_list_dialog("Ссылки", lk.bulk_rows([{"error": "x"}]))
    assert not empty.copy_btn.isEnabled() and not empty.save_btn.isEnabled()
