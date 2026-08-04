"""Приложение должно обновляться само, а не по устной инструкции.

04.08.2026: выкатили сжатие фото и свой список вариантов, но людям в поле надо
было объяснить «закройте приложение из списка задач и откройте заново». До
половины это не дошло — выездник продолжал работать на старой оболочке и снова
упирался в то, что уже починено.

Теперь сервер отдаёт номер своей оболочки, приложение сравнивает со своим и
предлагает обновиться. Версия объявлена в ОДНОМ месте — sw.js.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "hardbot" / "miniapp" / "index.html").read_text(encoding="utf-8")
SW = (ROOT / "hardbot" / "miniapp" / "sw.js").read_text(encoding="utf-8")


def _sw_version() -> int:
    return int(re.search(r"hardbot-shell-v(\d+)", SW).group(1))


def _index_version() -> int:
    return int(re.search(r"const SHELL_VERSION=(\d+);", INDEX).group(1))


def test_shell_version_matches_between_index_and_sw():
    """Разъехались — приложение будет вечно предлагать обновление или молчать."""
    assert _index_version() == _sw_version(), (
        f"index.html={_index_version()} sw.js={_sw_version()} — синхронизируйте")


def test_server_reads_the_version_from_sw_and_does_not_duplicate_it():
    web = (ROOT / "hardbot" / "web.py").read_text(encoding="utf-8")
    assert "def _shell_version()" in web
    assert "hardbot-shell-v" in web, "сервер должен читать версию из sw.js"
    assert '"shellVersion": _shell_version()' in web


def test_update_never_interrupts_work():
    """Перезагрузка посреди заполнения формы стоила бы человеку работы."""
    i = INDEX.index("function noteShellVersion(")
    body = INDEX[i:i + 700]
    assert "draftDirty" in body, "не проверяется незаконченный черновик"
    assert "PHOTO_PREPARING.size" in body, "не проверяется готовящееся фото"
    assert "location.reload()" in body


def test_busy_user_gets_a_button_instead_of_a_reload():
    assert "reloadForUpdate" in INDEX
    assert "updready:" in INDEX and "updbtn:" in INDEX


def test_update_message_exists_in_all_three_languages():
    assert INDEX.count("updready:") == 3
    assert INDEX.count("updbtn:") == 3


def test_older_or_equal_server_version_does_nothing():
    """Сервер позади или вровень — обновляться не с чего."""
    i = INDEX.index("function noteShellVersion(")
    body = INDEX[i:i + 400]
    assert "srv<=SHELL_VERSION" in body
