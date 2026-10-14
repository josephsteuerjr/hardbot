"""Контуры серверов в пульте аналитика: миграция, изоляция токенов, ярлык.

Два живых сервера — прод и контур компании — и пульт обязан показывать,
к которому из них он сейчас обращён, не требуя похода в настройки. Токен
устройства у каждого контура свой: переключение не должно ни затирать чужой
токен, ни уносить свой на другой сервер.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", exc_type=ImportError,
    reason="нет работающего PySide6 (pip install -r requirements-dev.txt)")

from pult.app import _split_contour  # noqa: E402
from pult.core.settings import (DEFAULT_PROFILE, Settings, active_profile,  # noqa: E402
                                ensure_profiles)


# --- настройки без окна ------------------------------------------------------

def test_flat_file_migrates_into_default_profile(tmp_path):
    data = {"url": "https://a.example", "device_token": "tok-a", "sec": "dash"}
    Settings(data, tmp_path / "s.json").save()
    raw = dict(Settings.load(tmp_path / "s.json"))
    assert raw["profiles"][DEFAULT_PROFILE]["url"] == "https://a.example"
    assert raw["profiles"][DEFAULT_PROFILE]["device_token"] == "tok-a"
    assert raw["active"] == DEFAULT_PROFILE
    # Плоское зеркало — для командных помощников без контуров.
    assert raw["url"] == "https://a.example"
    assert raw["device_token"] == "tok-a"


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "s.json"
    Settings({"url": "https://a.example"}, path).save()
    first = json.loads(path.read_text(encoding="utf-8"))
    Settings.load(path).save()
    second = json.loads(path.read_text(encoding="utf-8"))
    assert first == second


def test_empty_file_gets_one_empty_profile():
    data = ensure_profiles({})
    assert list(data["profiles"]) == [DEFAULT_PROFILE]
    assert data["active"] == DEFAULT_PROFILE


def test_master_key_never_reaches_the_disk(tmp_path):
    path = tmp_path / "s.json"
    s = Settings({"url": "https://a.example"}, path)
    active_profile(s)["key"] = "super-secret"
    s["key"] = "super-secret"
    s.save()
    raw = path.read_text(encoding="utf-8")
    assert "super-secret" not in raw


def test_mirror_drops_stale_flat_token(tmp_path):
    path = tmp_path / "s.json"
    s = Settings({"url": "https://a.example", "device_token": "tok-a"}, path)
    s.save()
    active_profile(s).pop("device_token", None)
    s.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "device_token" not in raw or not raw["device_token"]


# --- окно -------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from pult.ui import theme
    theme.apply(application)
    return application


@pytest.fixture
def window(app, tmp_path):
    from pult.ui.main_window import Pult
    settings = Settings({}, tmp_path / "настройки пульта.json")
    settings["profiles"] = {
        "Прод": {"url": "https://prod.example", "device_token": "tok-prod"},
        "Компания": {"url": "https://company.example"},
    }
    settings["active"] = "Прод"
    w = Pult(settings)
    yield w
    w.close()


def test_starts_on_active_profile(window):
    assert window.profile == "Прод"
    assert window.url == "https://prod.example"
    assert window.device_token == "tok-prod"
    assert "Прод" in window.windowTitle()


def test_switch_keeps_tokens_apart(window):
    window.imp_selected.add("Кто-то")
    window.switch_profile("Компания")
    assert window.profile == "Компания"
    assert window.url == "https://company.example"
    assert window.device_token == ""
    assert not window.imp_selected, "выборы людей из прежнего контура пережили переключение"
    assert "Компания" in window.windowTitle()

    window.switch_profile("Прод")
    assert window.url == "https://prod.example"
    assert window.device_token == "tok-prod", "токен первого контура потерялся"


def test_header_names_the_contour_on_every_section(window):
    text = window.contour_lbl.text()
    assert "ПРОД" in text.upper() and "PROD.EXAMPLE" in text.upper()


def test_flush_writes_token_into_profile_not_flat_only(window):
    window.device_token = "tok-new"
    window._flush_profile()
    window._save_settings()
    saved = dict(Settings.load(window.settings.path))
    assert saved["profiles"]["Прод"]["device_token"] == "tok-new"
    assert saved["device_token"] == "tok-new"


def test_profile_management(window):
    assert window.profile_add("Тест", "https://test.example")
    assert not window.profile_add("Тест"), "дубль имени прошёл"
    assert window.profile_rename("Тест", "Тест-2")
    assert not window.profile_remove("Прод"), "активный контур удалился"
    assert window.profile_remove("Тест-2")
    assert window.profile_remove("Прод") is False


def test_unknown_contour_argument_is_ordinary_start(window):
    window.close()
    from pult.ui.main_window import Pult
    w = Pult(Settings({"profiles": {"Один": {"url": "https://x"}},
                       "active": "Один"}, window.settings.path), profile="НетТакого")
    assert w.profile == "Один"
    w.close()

    w = Pult(Settings({"profiles": {"Один": {"url": "https://x"},
                                    "Компания": {"url": "https://y"}},
                       "active": "Один"}, window.settings.path), profile="Компания")
    assert w.profile == "Компания" and w.url == "https://y"
    w.close()


# --- разбор аргументов запуска ----------------------------------------------

def test_split_contour_space_and_equals_forms():
    assert _split_contour(["--contour", "Компания"]) == ("Компания", [])
    assert _split_contour(["--contour=Компания"]) == ("Компания", [])
    assert _split_contour(["--contour"]) == ("", [])
    assert _split_contour([]) == ("", [])
    name, rest = _split_contour(["--contour", "Компания", "--foo"])
    assert name == "Компания" and rest == ["--foo"]


def test_failed_reenroll_keeps_working_token(window, monkeypatch):
    """Опечатка в мастер-ключе при перепривязке не должна стирать рабочий
    токен контура: следующее «Обновить» или закрытие окна пишет профиль на диск."""
    from pult.ui import dialogs
    monkeypatch.setattr(dialogs, "askstring", lambda *a, **k: "Ноутбук")
    monkeypatch.setattr(window, "bg_task",
                        lambda work, done, fail: fail(RuntimeError("401: неверный ключ")))
    monkeypatch.setattr(window, "toast", lambda *a, **k: None)
    window.key_entry.set("опечатка")
    window.device_enroll()
    assert window.device_token == "tok-prod"
    window._flush_profile()
    window._save_settings()
    saved = dict(Settings.load(window.settings.path))
    assert saved["profiles"]["Прод"]["device_token"] == "tok-prod"


def test_empty_answer_on_reenroll_keeps_working_token(window, monkeypatch):
    from pult.ui import dialogs
    monkeypatch.setattr(dialogs, "askstring", lambda *a, **k: "Ноутбук")
    monkeypatch.setattr(window, "bg_task", lambda work, done, fail: done({}))
    monkeypatch.setattr(window, "toast", lambda *a, **k: None)
    window.key_entry.set("ключ")
    window.device_enroll()
    assert window.device_token == "tok-prod"
