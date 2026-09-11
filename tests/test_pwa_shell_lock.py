"""Мобильная оболочка закреплена, а service worker обновлён вместе с ней."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "server" / "miniapp" / "index.html").read_text("utf-8")
SW = (ROOT / "server" / "miniapp" / "sw.js").read_text("utf-8")


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
    """Правишь оболочку — поднимай версию кэша, иначе у поставивших на экран
    останется старая: service worker отдаст своё, и починка до них не доедет.

    Проверяем СВЯЗЬ, а не константу: обе строки ниже придётся обновить руками,
    и «случайно» пройти тест нельзя. Раньше здесь была прибита только версия —
    её приходилось править, ни на что не глядя, и смысл проверки терялся.
    """
    import hashlib

    # Хэшируем СОДЕРЖИМОЕ, а не байты: git переписывает переводы строк при
    # коммите (core.autocrlf), и проверка по сырым байтам падала ложно после
    # каждого коммита, ничего не говоря о самой оболочке.
    text = (ROOT / "server" / "miniapp" / "index.html").read_text("utf-8")
    index_sha = hashlib.sha256(text.replace(chr(13) + chr(10), chr(10)).encode("utf-8")).hexdigest()
    assert index_sha == (
        "d179f47faf9c7e446ff01e55a691bce9bb415f8c159a0502981dc15afeb1bc94"
    ), "оболочка изменилась — обнови этот хэш И версию CACHE в sw.js"
    assert 'const CACHE = "hard-shell-v44";' in SW
