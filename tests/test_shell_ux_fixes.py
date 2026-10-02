"""Правки оболочки 02.10: прокрутка экранов и нормы.

1. Все экраны делят ОДИН скроллер (#app): без сброса прокрутка формы визита
   прилетала в список должников, и «Мои клиенты» открывались глубоко внизу.
2. Расчётная норма (workload) — ЭКСПЕРИМЕНТ для сравнения: применение её одним
   тапом как плана убрано с экрана «Нормы» (на проде нажимали — и Penetration
   воронки уезжал вместе с расчётом). Руководитель ставит норму сам: по
   отдельности или всей команде сразу.
"""
from pathlib import Path

INDEX = (Path(__file__).resolve().parent.parent
         / "server" / "miniapp" / "index.html").read_text("utf-8")


def _body_after(anchor: str, size: int = 4000) -> str:
    i = INDEX.index(anchor)
    return INDEX[i:i + size]


def test_every_screen_entry_resets_the_shared_scroller():
    assert "function resetScroll()" in INDEX
    for anchor in ("function renderList(", "function renderDetail()",
                   "function setTab(", "async function dgEnter(",
                   "async function dgLoad(", "async function renderMarks(",
                   "async function renderNav("):
        assert "resetScroll()" in _body_after(anchor), \
            f"{anchor} не сбрасывает прокрутку общего скроллера"


def test_suggested_norm_cannot_be_applied_with_one_tap():
    body = _body_after("function dgPlanScreen(")
    assert "dgSetPlan(${i},${e.suggested})" not in body, \
        "расчётная норма снова применяется тапом как план"
    assert "эксперимент, план не меняет" in body, \
        "подсказка расчёта не помечена как эксперимент"


def test_plan_screen_sets_whole_team_at_once():
    body = _body_after("function dgPlanScreen(")
    assert "Всем сразу" in body and "dgSetPlanAll(" in body
    setter = _body_after("async function dgSetPlanAll(")
    assert "plan[e.employee]=norm" in setter, \
        "массовая норма должна уходить одной картой на всю команду"
