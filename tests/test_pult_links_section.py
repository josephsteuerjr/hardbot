"""Раздел «Вход»: одна таблица, выдача в одно движение, QR под таблицей.

Контур компании работает без Telegram, ссылка из пульта — единственный путь
сотрудника в систему. Проверяется офскрин то, на что завтра смотрит живой
тест: в таблице видны все из реестра (даже когда ссылок нет ни у кого),
кнопки мертвы без выбранной строки, выдача уходит на /admin/app_link.json
вместе с числом открытий и сразу рисует панель с адресом, отзыв спрашивает и
метит строку, а старый сервер без /admin/app_links.json экран не роняет.
Окно ссылки живёт и без segno — QR тогда просто не показывают.
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", exc_type=ImportError,
    reason="нет работающего PySide6 (pip install -r requirements-dev.txt; на голом Linux Qt дополнительно требует системную libGL)")

from pult.core import links as lk  # noqa: E402
from pult.core.settings import Settings  # noqa: E402
from pult.ui import dialogs  # noqa: E402
from pult.ui.main_window import Pult  # noqa: E402
from pult.ui.nav import SECTIONS  # noqa: E402

BASE = "https://hard.example"
EMPS = [
    {"employee": "Иванов Иван", "contracts": 120},
    {"employee": "Петров Пётр", "contracts": 80, "kind": "courier"},
    {"employee": "Сидорова Анна", "contracts": 40, "telegram_id": 900001},
]
WORKERS = [
    {"employee": "Сидорова Анна", "kind": "agent", "entry": "pwa",
     "telegram_id": 900001, "portfolio_contracts": 40,
     "last_seen": "2026-09-20T09:15:00", "shell_version": 56},
    # Портфель приехал с прода вместе с users.last_seen: человек «активен» по
    # старой Телеграм-отметке, а в этом контуре не входил ни разу.
    {"employee": "Петров Пётр", "kind": "courier", "entry": "telegram",
     "telegram_id": 800002, "portfolio_contracts": 80,
     "last_seen": "2026-08-14T07:40:00", "shell_version": 54},
]


def link_row(name, tid, code, **kw):
    row = {"employee": name, "telegram_id": tid, "kind": None, "code": code,
           "created_at": "2026-09-21T10:00:00", "expires_at": "2026-09-28T10:00:00",
           "uses": 0, "max_uses": 2, "bonus_ios": 0, "last_use_at": None,
           "last_agent": None, "entered": False, "devices": 0, "alive": True}
    row.update(kw)
    return row


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
    copied: list[str] = []
    refreshed: list[bool] = []
    server = {"links": [], "no_links_api": False, "error": None, "next_tid": 700001}

    def api(method, path, body=None, timeout=20):
        calls.append((method, path, body))
        if path == "/admin/employees":
            return {"employees": [dict(e) for e in EMPS]}
        if path == "/admin/activity":
            return {"workers": [dict(x) for x in WORKERS], "generated_at": ""}
        if path == "/admin/app_links.json":
            if server["no_links_api"]:
                # как pult.core.api на 404: «сервер старой версии?»
                raise RuntimeError("На сервере нет /admin/app_links.json")
            return {"links": [dict(x) for x in server["links"]]}
        if path == "/admin/app_link.json":
            if server["error"]:
                return {"error": server["error"]}
            tid = (body or {}).get("telegram_id") or server["next_tid"]
            server["next_tid"] += 1
            uses = int((body or {}).get("uses") or 2)
            employee = (body or {}).get("employee") or ""
            code = "код-" + str(tid)
            server["links"] = [x for x in server["links"]
                               if x["employee"] != employee]
            server["links"].append(link_row(employee, tid, code, max_uses=uses,
                                            kind=(body or {}).get("kind")))
            return {"ok": True, "telegram_id": tid, "employee": employee,
                    "code": code, "url": f"{BASE}/i/{code}", "uses": uses,
                    "max_uses": uses, "days": 7,
                    "expires_at": "2026-09-28T10:00:00",
                    "local_identity": True, "created": True}
        if path == "/admin/app_revoke":
            tid = (body or {}).get("telegram_id")
            server["links"] = [x for x in server["links"] if x["telegram_id"] != tid]
            return {"ok": True, "revoked": 1}
        return {}

    def bg_task(fn, done=None, fail=None):
        result = fn()
        if done:
            done(result)

    w.api = api
    w.bg_task = bg_task
    w.calls = calls
    w.shown = shown
    w.copied = copied
    w.refreshed = refreshed
    w.server = server
    w.refresh = lambda: refreshed.append(True)
    monkeypatch.setattr("pult.ui.dialogs.show_link",
                        lambda title, url, note="", parent=None:
                        shown.append((title, url, note)))
    monkeypatch.setattr("pult.ui.dialogs.copy_text", copied.append)
    monkeypatch.setattr("pult.ui.dialogs.askyesno", lambda *a, **k: True)
    w.connected = True
    yield w
    w.close()


def states(window) -> dict[str, str]:
    """{ФИО: текст состояния} прямо из модели таблицы."""
    return {row[0]: row[2] for row in window.ln_tv.model_.rows}


def test_links_section_is_in_navigation(app, window):
    assert ("links", "Вход") in SECTIONS
    assert "links" in window.pages
    window.nav("links")
    assert window.stack.currentWidget() is window.pages["links"]
    assert window.sec_title.text() == "Вход сотрудников"


def test_table_lists_the_registry_before_any_link_is_issued(app, window):
    """Прежний экран показывал только привязанных и в первый день теста был
    пуст. Теперь строка есть у каждого из реестра — выдавать вход есть кому.

    Петров приехал с прода с чужой отметкой активности (entry «telegram»):
    «вошедшим» его считать нельзя — иначе первый же экран теста врёт, что
    выдавать вход некому."""
    window.load_links()
    assert states(window) == {"Иванов Иван": "не выдавали",
                              "Петров Пётр": "не выдавали",
                              "Сидорова Анна": "вошёл"}
    petrov = next(r for r in window.ln_tv.model_.rows if r[0] == "Петров Пётр")
    assert petrov[5] == "—"                     # «Последний вход» чужой датой не врёт
    assert "вошли 1 из 3" in window.ln_summary.text()
    chips = [c.text() for c in window.ln_filters.findChildren(QtWidgets.QPushButton)]
    assert "все · 3" in chips and "не выдавали · 2" in chips and "вошли · 1" in chips


def test_buttons_are_dead_until_a_row_is_chosen(app, window):
    window.load_links()
    for button in (window.ln_issue_btn, window.ln_again_btn, window.ln_qr_btn,
                   window.ln_copy_btn, window.ln_revoke_btn):
        assert not button.isEnabled()
    assert window.ln_pick.text() == ""
    assert window.ln_tv.select_key("Иванов Иван")
    assert window.ln_issue_btn.isEnabled()          # ссылки нет — выдать можно
    assert not window.ln_again_btn.isEnabled()      # перевыдавать нечего
    assert not window.ln_qr_btn.isEnabled()         # адреса ещё не существует
    assert not window.ln_revoke_btn.isEnabled()     # и отзывать нечего
    assert window.ln_pick.text() == "выбран: Иванов Иван"


def test_issue_sends_uses_and_draws_the_panel_at_once(app, window):
    window.load_links()
    window.ln_tv.select_key("Иванов Иван")
    window.ln_issue()

    posts = [c for c in window.calls if c[1] == "/admin/app_link.json"]
    assert posts and posts[-1][2] == {"employee": "Иванов Иван", "kind": "agent",
                                      "uses": 2}                  # два — по умолчанию
    # панель под таблицей: короткий адрес и подписи, без модального окна
    fields = window.ln_link_body.findChildren(QtWidgets.QLineEdit)
    assert fields and fields[0].text() == f"{BASE}/i/код-700001"
    texts = " ".join(lbl.text() for lbl in window.ln_link_body.findChildren(QtWidgets.QLabel))
    assert "Иванов Иван" in texts and "осталось открытий: 2" in texts
    assert not window.shown                       # окно не вылезает поверх работы
    # список перечитан точечно: строка на месте, весь пульт не перезагружался
    assert states(window)["Иванов Иван"] == "выдана, не открыта"
    assert window.ln_pick.text() == "выбран: Иванов Иван"
    assert not window.refreshed
    assert window.ln_again_btn.isEnabled() and window.ln_qr_btn.isEnabled()
    # публичный адрес приложения запомнен — по нему собираются ссылки из списка
    assert window.settings["miniapp"] == BASE


def test_uses_combo_decides_how_many_times_the_link_opens(app, window):
    window.load_links()
    window.ln_tv.select_key("Петров Пётр")
    window.ln_uses.setCurrentIndex(0)             # «1 открытие»
    window.ln_issue()
    body = [c for c in window.calls if c[1] == "/admin/app_link.json"][-1][2]
    assert body["uses"] == 1 and body["kind"] == "courier"
    assert lk.USES_CHOICES[lk.DEFAULT_USES_INDEX] == 2
    texts = " ".join(lbl.text() for lbl in window.ln_link_body.findChildren(QtWidgets.QLabel))
    assert "осталось открытий: 1" in texts
    assert "На экран „Домой“" in texts            # помощь с ярлыком нужна всегда
    assert "ещё раз" not in texts                 # но запасного открытия нет


def test_bound_person_keeps_one_identity(app, window):
    """У Сидоровой уже есть telegram_id: не послать его — сервер заведёт вторую
    служебную личность на то же ФИО, и портфель разъедется на два аккаунта."""
    window.load_links()
    window.ln_tv.select_key("Сидорова Анна")
    window.ln_issue()
    body = [c for c in window.calls if c[1] == "/admin/app_link.json"][-1][2]
    assert body["telegram_id"] == 900001


def test_show_qr_opens_the_same_link_in_a_window(app, window):
    window.load_links()
    window.ln_tv.select_key("Иванов Иван")
    window.ln_issue()
    window._ln_show_qr()
    title, url, note = window.shown[-1]
    assert "Иванов Иван" in title
    assert url == f"{BASE}/i/код-700001"
    assert "осталось открытий: 2" in note


def test_copy_takes_the_short_link_built_from_the_code(app, window):
    """В списке сервер отдаёт код, а не ссылку: адрес собирается из кода и
    запомненной базы — иначе после перезапуска пульта копировать будет нечего."""
    window.settings["miniapp"] = BASE
    window.server["links"] = [link_row("Иванов Иван", 700001, "код-700001")]
    window.load_links()
    window._ln_base = lk.base_of(BASE)
    window.ln_tv.select_key("Иванов Иван")
    window._ln_copy()
    assert window.copied == [f"{BASE}/i/код-700001"]


def test_revoke_asks_confirms_and_marks_the_row(app, window):
    window.load_links()
    window.ln_tv.select_key("Иванов Иван")
    window.ln_issue()
    window._ln_revoke()
    posts = [c for c in window.calls if c[1] == "/admin/app_revoke"]
    assert posts and posts[-1][2] == {"telegram_id": 700001, "scope": "all"}
    # сервер строку ссылки стёр — но экран не делает вид, что её не выдавали
    assert states(window)["Иванов Иван"] == "отозвана"
    assert window.ln_link_body.findChildren(QtWidgets.QLineEdit) == []


def test_filter_chip_narrows_the_table(app, window):
    window.load_links()
    window.ln_tv.select_key("Иванов Иван")
    window.ln_issue()
    window._ln_set_filter("issued")
    assert list(states(window)) == ["Иванов Иван"]
    window._ln_set_filter("entered")
    assert list(states(window)) == ["Сидорова Анна"]
    window._ln_set_filter("all")
    assert len(states(window)) == 3


def test_old_server_without_links_endpoint_does_not_break_the_screen(app, window):
    """Во время переезда пульт новый, а сервер ещё прежний: раздел обязан жить
    и выдавать вход, просто без состояний ссылок."""
    window.server["no_links_api"] = True
    window.load_links()
    assert not window.ln_note.isHidden()      # окно не показано — судим по флагу
    assert "старой версии" in window.ln_note.text()
    assert states(window) == {"Иванов Иван": "не выдавали",
                              "Петров Пётр": "не выдавали",
                              "Сидорова Анна": "вошёл"}
    window.ln_tv.select_key("Иванов Иван")
    window.ln_issue()
    assert [c for c in window.calls if c[1] == "/admin/app_link.json"]


def test_miniapp_url_error_stays_on_the_screen(app, window):
    """Единственная ошибка, которая на свежей машине встречает оператора
    гарантированно. Тост гаснет за три секунды — прочитать её он не успеет."""
    window.server["error"] = ("MINIAPP_URL не задан на сервере: без публичного "
                              "адреса ссылку входа не собрать")
    window.load_links()
    window.ln_tv.select_key("Иванов Иван")
    window.ln_issue()
    assert not window.ln_err.isHidden()
    assert "MINIAPP_URL" in window.ln_err.text()


def test_link_dialog_has_qr_and_url(app, window):
    pytest.importorskip("segno", reason="QR показывает только с segno")
    dialog = dialogs._build_link_dialog("Ссылка входа", f"{BASE}/i/abc",
                                        "проверка", parent=None)
    qr = dialog.findChild(QtWidgets.QLabel, "LinkQR")
    assert qr is not None and qr.pixmap() is not None
    field = [c for c in dialog.findChildren(QtWidgets.QLineEdit)][0]
    assert field.text() == f"{BASE}/i/abc"
    texts = " ".join(lbl.text() for lbl in dialog.findChildren(QtWidgets.QLabel))
    assert "проверка" in texts
    dialog.deleteLater()


def test_link_dialog_survives_missing_segno(app, window, monkeypatch):
    monkeypatch.setitem(sys.modules, "segno", None)      # import segno → ImportError
    dialog = dialogs._build_link_dialog("Ссылка входа", f"{BASE}/i/abc")
    qr = dialog.findChild(QtWidgets.QLabel, "LinkQR")
    assert qr is None                                    # без QR, но диалог жив
    texts = " ".join(lbl.text() for lbl in dialog.findChildren(QtWidgets.QLabel))
    assert "segno" in texts
    field = [c for c in dialog.findChildren(QtWidgets.QLineEdit)][0]
    assert field.text() == f"{BASE}/i/abc"               # ссылку всё ещё дадут
    dialog.deleteLater()


def test_embedded_panel_survives_missing_segno(app, window, monkeypatch):
    """Карточка с QR — главная на экране, и без segno она обязана не падать
    так же, как окно: адрес и подписи важнее картинки."""
    monkeypatch.setitem(sys.modules, "segno", None)
    window.load_links()
    window.ln_tv.select_key("Иванов Иван")
    window.ln_issue()
    fields = window.ln_link_body.findChildren(QtWidgets.QLineEdit)
    assert fields and fields[0].text().endswith("/i/код-700001")
    texts = " ".join(lbl.text() for lbl in window.ln_link_body.findChildren(QtWidgets.QLabel))
    assert "segno" in texts
