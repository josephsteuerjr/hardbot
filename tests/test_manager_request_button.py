"""Кнопка «Запрос для руководителей» должна быть видна тому, для кого сделана.

03.08.2026 руководитель написал боту и не увидел кнопки. Причина: `uid` у
построителя клавиатуры был необязательным, и ровно те два вызова, через которые
проходит НОВЫЙ человек — `/start` и ответ на любой текст, — его не передавали.
Кнопка зависит от uid, поэтому её просто не рисовало. Молча.
"""

import ast
import pathlib

import handlers_main as hm

SRC = pathlib.Path(hm.__file__).read_text(encoding="utf-8")


def test_uid_is_required_not_optional():
    """Значение по умолчанию здесь — способ потерять кнопку и не заметить."""
    tree = ast.parse(SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_open_app_kb")
    assert fn.args.defaults == [], "у _open_app_kb снова появился необязательный uid"
    assert [a.arg for a in fn.args.args] == ["lang", "uid"]


def test_every_caller_passes_uid():
    """AST, а не глазами: вызовов четыре, и забыть аргумент легко."""
    calls = []
    for path in pathlib.Path(hm.__file__).parent.glob("handlers_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_open_app_kb"):
                calls.append((path.name, node.lineno, len(node.args)))
    assert calls, "вызовы _open_app_kb не найдены — тест перестал что-либо проверять"
    bad = [c for c in calls if c[2] < 2]
    assert not bad, f"вызовы без uid: {bad}"


def _kb(monkeypatch, uid, *, manager=False, binding=None):
    monkeypatch.setenv("MINIAPP_URL", "https://example.invalid")
    monkeypatch.setattr(hm.db, "is_manager", lambda _u: manager)
    monkeypatch.setattr(hm.db, "get_binding", lambda _u: binding)
    kb = hm._open_app_kb("ru", uid)
    return [b.text for row in kb.inline_keyboard for b in row]


def test_stranger_sees_the_request_button(monkeypatch):
    assert hm.BTN_MANAGER_REQ in _kb(monkeypatch, 555)


def test_bound_employee_does_not_see_it(monkeypatch):
    """Выезднику она не нужна — он уже в системе."""
    assert hm.BTN_MANAGER_REQ not in _kb(monkeypatch, 555, binding="Егор")


def test_existing_manager_does_not_see_it(monkeypatch):
    assert hm.BTN_MANAGER_REQ not in _kb(monkeypatch, 555, manager=True)


def test_owner_does_not_see_it(monkeypatch):
    """Владельцу просить роль не у кого."""
    assert hm.BTN_MANAGER_REQ not in _kb(monkeypatch, hm.ADMIN_ID)


def test_db_failure_does_not_break_the_keyboard(monkeypatch):
    """Кнопка «Открыть приложение» важнее заявки: падение БД не должно оставить
    человека вообще без клавиатуры."""
    monkeypatch.setenv("MINIAPP_URL", "https://example.invalid")

    def boom(_u):
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr(hm.db, "is_manager", boom)
    monkeypatch.setattr(hm.db, "get_binding", boom)
    kb = hm._open_app_kb("ru", 555)
    assert kb is not None and kb.inline_keyboard
