"""Настройки пульта: тот же файл, те же ключи, терпимость к мусору."""

from __future__ import annotations

import json

from pult.core.settings import SETTINGS_PATH, Settings


def test_default_path_is_the_historical_file():
    assert SETTINGS_PATH.name == "настройки пульта.json"
    assert SETTINGS_PATH.parent.name == "pult"


def test_missing_or_broken_file_gives_empty_settings(tmp_path):
    assert dict(Settings.load(tmp_path / "нет.json")) == {}
    broken = tmp_path / "битые.json"
    broken.write_text("{не json", encoding="utf-8")
    assert dict(Settings.load(broken)) == {}
    listing = tmp_path / "список.json"
    listing.write_text("[1, 2]", encoding="utf-8")
    assert dict(Settings.load(listing)) == {}


def test_roundtrip_keeps_keys_and_cyrillic(tmp_path):
    path = tmp_path / "настройки пульта.json"
    s = Settings.load(path)
    s.update(url="https://h", device_token="t", sec="registry",
             scatterBase=r"U:\rbr\Развитие СНГ", enrich_file="")
    assert s.save() is True
    raw = path.read_text(encoding="utf-8")
    assert "Развитие СНГ" in raw and "\\u" not in raw     # ensure_ascii=False
    assert raw.startswith("{\n \"")                        # indent=1, как раньше
    again = Settings.load(path)
    assert again["scatterBase"] == r"U:\rbr\Развитие СНГ"
    assert again.path == path


def test_save_failure_is_quiet(tmp_path):
    s = Settings({"a": 1}, tmp_path / "нет такой папки" / "x.json")
    assert s.save() is False


def test_settings_is_a_plain_dict_for_the_window():
    s = Settings({"url": "u"})
    assert s.get("url") == "u"
    s.pop("url")
    assert json.dumps(dict(s)) == "{}"
