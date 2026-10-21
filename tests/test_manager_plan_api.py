"""Нормы выездов правит руководитель — из приложения.

Офис делит выездников на группы по фактической выработке (8, 10 и 12 выездов в
операционный день). Состав групп меняется чаще, чем выходят выкладки, поэтому
норма живёт в настройке и правится с экрана — не в коде и не через нас.
"""
import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web

ROOT = Path(__file__).resolve().parent.parent
SHELL = (ROOT / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")

MGR, STRANGER = 565001, 565999
EMP_A, EMP_B, EMP_ALIEN = "Нрмиров Н.", "Нрмбеков Н.", "Чужой Ч."


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'NR-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id IN (?,?)", (MGR, STRANGER))
        conn.execute("DELETE FROM settings WHERE key='visit_plan_by_employee'")
        for cid, emp in (("NR-1", EMP_A), ("NR-2", EMP_B), ("NR-3", EMP_ALIEN)):
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "partner, debt_today, overall_result, country) VALUES (?,?,?,?,?,?,?,?)",
                (cid, f"Клиент {cid}", emp, "1990-01-01", "ЗК", "1 000",
                 "Не обработан", "Таджикистан"))
        conn.commit()
    db.set_manager(MGR, "Рук", [], [EMP_A, EMP_B])
    db.reset_role_caches()


@pytest.fixture(autouse=True)
def _cleanup():
    _seed()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'NR-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id IN (?,?)", (MGR, STRANGER))
        conn.execute("DELETE FROM settings WHERE key='visit_plan_by_employee'")
        conn.commit()
    db.reset_role_caches()


def _call(method, body, monkeypatch, uid=MGR):
    monkeypatch.setattr(web, "_uid", lambda _r: uid)
    req = make_mocked_request(method, "/api/manager/plan")
    if body is not None:
        async def _json():
            return body
        req.json = _json
    resp = asyncio.run(web._manager_plan(req))
    return json.loads(resp.body.decode())


# --- чтение ------------------------------------------------------------------
def test_list_shows_common_norm_until_a_personal_one_is_set(monkeypatch):
    out = _call("GET", None, monkeypatch)
    assert out["ok"] and out["common"] == 10 and out["groups"] == [8, 10, 12]
    by_name = {e["employee"]: e for e in out["employees"]}
    assert set(by_name) == {EMP_A, EMP_B}, "только своя команда"
    assert _norm(by_name[EMP_A]) == {"employee": EMP_A, "plan": 10, "personal": False}
    # Рядом с ручной нормой — подсказка по расчёту (workload.py); у команды
    # без адресов на карте её нет, но ключ в ответе есть всегда.
    assert "suggested" in by_name[EMP_A] and "why" in by_name[EMP_A]


def _norm(e: dict) -> dict:
    """Ручная часть строки нормы — без подсказки по расчёту."""
    return {k: e[k] for k in ("employee", "plan", "personal")}


# --- запись ------------------------------------------------------------------
def test_manager_sets_a_personal_norm(monkeypatch):
    out = _call("POST", {"plan": {EMP_A: 12}}, monkeypatch)
    by_name = {e["employee"]: e for e in out["employees"]}
    assert by_name[EMP_A]["plan"] == 12 and by_name[EMP_A]["personal"] is True
    assert by_name[EMP_B]["plan"] == 10, "соседа не трогаем"
    assert db.visit_plan_for(EMP_A) == 12


def test_zero_returns_a_person_to_the_common_norm(monkeypatch):
    _call("POST", {"plan": {EMP_A: 8}}, monkeypatch)
    out = _call("POST", {"plan": {EMP_A: 0}}, monkeypatch)
    by_name = {e["employee"]: e for e in out["employees"]}
    assert _norm(by_name[EMP_A]) == {"employee": EMP_A, "plan": 10, "personal": False}
    assert db.visit_plan_map() == {}


def test_a_stranger_from_another_team_is_skipped_not_fatal(monkeypatch):
    """Состав команды мог измениться, пока руководитель держал экран открытым."""
    out = _call("POST", {"plan": {EMP_A: 8, EMP_ALIEN: 12}}, monkeypatch)
    assert out["ok"]
    assert db.visit_plan_for(EMP_A) == 8
    assert db.visit_plan_for(EMP_ALIEN) == 10, "чужому норму не поставили"


def test_garbage_norm_does_not_break_the_map(monkeypatch):
    _call("POST", {"plan": {EMP_A: 13}}, monkeypatch)
    with pytest.raises(web.web.HTTPBadRequest):
        _call("POST", {"plan": {EMP_A: "двенадцать"}}, monkeypatch)
    assert db.visit_plan_map() == {EMP_A:13}, "ошибка ввода не стирает сохранённую норму"


def test_bad_body_is_400(monkeypatch):
    with pytest.raises(web.web.HTTPBadRequest):
        _call("POST", {"plan": [8, 10]}, monkeypatch)


def test_outsiders_cannot_read_or_write(monkeypatch):
    with pytest.raises(web.web.HTTPForbidden):
        _call("GET", None, monkeypatch, uid=STRANGER)


def test_change_leaves_a_trace(monkeypatch):
    _call("POST", {"plan": {EMP_A: 12}}, monkeypatch)
    row = db.audit_tail(5)[0]
    assert row["action"] == "visit_plan" and "changed=1" in (row["detail"] or "")


# --- экран -------------------------------------------------------------------
def test_screen_saves_on_tap_without_a_separate_button():
    """Кнопка «сохранить» на каждой из тридцати строк — тридцать поводов забыть."""
    body = SHELL[SHELL.index("function dgPlanScreen("):SHELL.index("const DG_METRIC_TITLE")]
    assert "dgSetPlan(" in body and "/api/manager/plan" in body
    assert "не сохранилось" in body, "отказ обязан быть виден"
    assert "общая " in body, "должен быть возврат к общей норме"


def test_summary_has_a_way_into_the_norms():
    assert "dgGo('plan')" in SHELL and "\"Нормы\",\"dgGo('plan')\"" in SHELL
