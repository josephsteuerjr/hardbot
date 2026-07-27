"""Мобильная оболочка закреплена, а service worker обновлён вместе с ней."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "hardbot" / "miniapp" / "index.html").read_text("utf-8")
SW = (ROOT / "hardbot" / "miniapp" / "sw.js").read_text("utf-8")


def test_telegram_vertical_swipe_is_disabled():
    assert "TG.disableVerticalSwipes()" in INDEX
    assert 'TG.onEvent("viewportChanged"' in INDEX
    assert "TG.viewportStableHeight" in INDEX


def test_only_inner_content_scrolls_and_controls_do_not_translate():
    assert "html{width:100%;height:100%;overflow:hidden;overscroll-behavior:none" in INDEX
    assert "body{position:fixed" in INDEX
    assert ".wrap{flex:1 1 auto;min-height:0" in INDEX
    assert "overflow-y:auto;overscroll-behavior-y:contain" in INDEX
    assert ".pcard:active{background:var(--alt)}" in INDEX
    assert "transform:scale(.98)" not in INDEX
    assert "transform:translateY(4px)" not in INDEX


def test_shell_cache_was_bumped_for_new_index():
    assert 'const CACHE = "hardbot-shell-v12";' in SW

