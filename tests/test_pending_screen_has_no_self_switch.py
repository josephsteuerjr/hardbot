"""На экране ожидания нельзя сменить выбранное ФИО самому.

Решение владельца 14.09.2026: самовыбор одноразовый, чужое ФИО правит
руководитель. Кнопка «Выбрать другого сотрудника» этому противоречила и при этом
не работала — на экране ожидания сервер список сотрудников не отдаёт вовсе
(_bootstrap возвращает pending без employees). Люди жали мёртвую кнопку и шли
писать в чат.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHELL = (ROOT / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")
WEB = (ROOT / "server" / "web.py").read_text(encoding="utf-8")


def _pending_screen() -> str:
    start = SHELL.index("function renderPending(")
    return SHELL[start:SHELL.index("\n}", start)]


def test_no_button_to_pick_another_employee():
    body = _pending_screen()
    assert "restartOnboarding" not in body, "самовыбор одноразовый"
    # Accepted mobile login no longer requires a GPS exception workflow.
    import re
    assert set(re.findall(r'onclick="([^"]+)"',body)) == {'checkPending()'}
    assert 'kind==="gps"' in body and '<select' not in body


def test_dead_handler_is_gone_entirely():
    assert "restartOnboarding" not in SHELL, "вызов без функции — та же мёртвая кнопка"


def test_screen_says_who_fixes_a_wrong_choice():
    assert "pendingwrong" in _pending_screen()
    assert SHELL.count("pendingwrong:") == 3, "на всех трёх языках"


def test_server_still_hides_the_roster_from_pending_users():
    """Список ФИО на этом экране не отдаётся — и это не баг, а то же правило."""
    i = WEB.index('if pend:')
    block = WEB[i:i + 400]
    assert '"pending": True' in block
    assert "employees" not in block
