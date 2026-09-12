"""Кнопка голоса видна всегда, но заморожена без службы речи.

Речь — отдельная единица, которой может не быть (deploy/README.md: «без неё
сотрудники вводят текст руками»). Раньше кнопки в таком контуре не было вовсе, и
человек в поле не мог отличить «у меня другое приложение» от «сломалось».
"""
from pathlib import Path

import web

ROOT = Path(__file__).resolve().parent.parent
SHELL = (ROOT / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")


def test_voice_off_is_the_default_on_the_server():
    """Пустой VOICE_BACKEND — это «голоса нет», а не «включим на всякий случай»."""
    assert web.VOICE_BACKEND in ("off", *web.VOICE_BACKENDS)
    assert "off" not in web.VOICE_BACKENDS


def test_meta_tells_the_shell_whether_speech_exists():
    body = (ROOT / "server" / "web.py").read_text(encoding="utf-8")
    assert '"voice": _voice_configured()' in body


def test_button_is_rendered_frozen_instead_of_hidden():
    i = SHELL.index('id="micbtn"')
    block = SHELL[i - 400:i + 500]
    assert "frozen" in block and "disabled" in block
    assert "voiceoff" in block, "рядом должно быть объяснение"


def test_frozen_style_is_visible_but_dead():
    assert ".btn.frozen{opacity:.45;cursor:default}" in SHELL


def test_explanation_exists_in_all_three_languages():
    assert SHELL.count("voiceoff:") == 3
