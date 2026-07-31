"""Вход руководителя в приложение и слово «ссылка».

Руководителя НЕ привязывают к сотруднику — у него своя роль. Из-за этого раньше
получалось глухо: слово «ссылка» проверяло только привязку, владелец писал его
и не получал НИЧЕГО — ни ссылки, ни объяснения, просто обычную кнопку. А если бы
ссылку и выдали, приложение ответило бы 403 «not bound».

Теперь руководитель работает «от имени» одного из СВОИХ сотрудников и переключается
между ними, а отказ всегда объясняется.
"""
import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import handlers_main
import web
from config import ADMIN_ID

MGR = 555001
STRANGER = 555002
EMP_A, EMP_B, EMP_FOREIGN = "Алиев А.", "Бобоев Б.", "Чужой Ч."


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM managers WHERE telegram_id IN (?,?)", (MGR, STRANGER))
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'MA-%'")
        conn.execute("DELETE FROM settings WHERE key LIKE 'acting_employee:%'")
        conn.commit()
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    for i, emp in enumerate((EMP_A, EMP_B, EMP_FOREIGN)):
        grid.append([{"contract_id": f"MA-{i}", "client": f"Клиент {i}",
                      "responsible": emp}.get(f.key) for f in db.FIELDS])
    db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    db.set_manager(MGR, "Руководитель Р.", [], [EMP_A, EMP_B])


# --- кого руководитель вправе открыть -------------------------------------------
def test_manager_sees_only_his_employees():
    _seed()
    assert db.manager_employees(MGR) == [EMP_A, EMP_B]
    assert EMP_FOREIGN not in db.manager_employees(MGR)


def test_owner_without_role_sees_everyone():
    """Владелец без явной роли — это его контур, ограничивать его нечем."""
    _seed()
    everyone = db.manager_employees(ADMIN_ID, is_owner=True)
    assert {EMP_A, EMP_B, EMP_FOREIGN} <= set(everyone)


def test_plain_user_sees_nobody():
    _seed()
    assert db.manager_employees(STRANGER) == []
    assert db.manager_employees(STRANGER, is_owner=False) == []


# --- переключение портфеля ------------------------------------------------------
def test_default_acting_is_first_allowed():
    _seed()
    assert db.acting_employee(MGR, db.manager_employees(MGR)) == EMP_A


def test_switch_and_remember():
    _seed()
    allowed = db.manager_employees(MGR)
    assert db.set_acting_employee(MGR, EMP_B, allowed)
    assert db.acting_employee(MGR, allowed) == EMP_B


def test_switch_to_foreign_employee_refused():
    _seed()
    allowed = db.manager_employees(MGR)
    assert not db.set_acting_employee(MGR, EMP_FOREIGN, allowed)
    assert db.acting_employee(MGR, allowed) == EMP_A


def test_stale_choice_falls_back():
    """Сотрудника открепили или переименовали — сохранённый выбор не должен
    оставаться дырой в доступе."""
    _seed()
    db.set_acting_employee(MGR, EMP_B, db.manager_employees(MGR))
    db.set_manager(MGR, "Руководитель Р.", [], [EMP_A])       # B забрали
    assert db.acting_employee(MGR, db.manager_employees(MGR)) == EMP_A


# --- приложение пускает ---------------------------------------------------------
def test_app_resolves_manager_to_acting_employee():
    _seed()
    assert web._employee(MGR) == EMP_A
    db.set_acting_employee(MGR, EMP_B, db.manager_employees(MGR))
    assert web._employee(MGR) == EMP_B


def test_app_still_refuses_plain_user():
    _seed()
    assert web._employee(STRANGER) is None


def test_act_as_endpoint_checks_scope(monkeypatch):
    _seed()
    monkeypatch.setattr(web, "_uid", lambda _r: MGR)

    def call(employee):
        req = make_mocked_request("POST", "/api/act_as")

        async def _json():
            return {"employee": employee}
        req.json = _json
        return asyncio.run(web._act_as(req))

    assert json.loads(call(EMP_B).body.decode())["acting"] == EMP_B
    with pytest.raises(web.web.HTTPForbidden):
        call(EMP_FOREIGN)


def test_act_as_refused_for_non_manager(monkeypatch):
    _seed()
    monkeypatch.setattr(web, "_uid", lambda _r: STRANGER)
    req = make_mocked_request("POST", "/api/act_as")

    async def _json():
        return {"employee": EMP_A}
    req.json = _json
    with pytest.raises(web.web.HTTPForbidden):
        asyncio.run(web._act_as(req))


# --- слово «ссылка» -------------------------------------------------------------
def test_link_is_issued_to_manager(monkeypatch):
    _seed()
    monkeypatch.setenv("MINIAPP_URL", "https://example.invalid")
    assert handlers_main._may_open_app(MGR)
    assert handlers_main._applink_text(MGR, "ru")


def test_link_is_issued_to_owner(monkeypatch):
    _seed()
    monkeypatch.setenv("MINIAPP_URL", "https://example.invalid")
    assert handlers_main._may_open_app(ADMIN_ID)


def test_link_refused_to_stranger_but_with_a_reason(monkeypatch):
    """Главное здесь — не отказ, а то, что он ОБЪЯСНЁН."""
    _seed()
    monkeypatch.setenv("MINIAPP_URL", "https://example.invalid")
    assert not handlers_main._may_open_app(STRANGER)
    assert handlers_main._applink_text(STRANGER, "ru") is None
    reason = handlers_main._applink_refusal(STRANGER, "ru")
    assert reason and "закреп" in reason.lower()


def test_reason_names_missing_url(monkeypatch):
    _seed()
    monkeypatch.setenv("MINIAPP_URL", "")
    reason = handlers_main._applink_refusal(MGR, "ru")
    assert "не настроено" in reason


def test_word_must_be_exact():
    assert handlers_main._is_applink_request("ссылка")
    assert handlers_main._is_applink_request("  Ссылка  ")
    assert not handlers_main._is_applink_request("дай ссылку")
    assert not handlers_main._is_applink_request("ссылка!")


def test_act_as_route_registered():
    paths = {r.resource.canonical for r in web.build_app(None).router.routes()
             if getattr(r, "resource", None)}
    assert "/api/act_as" in paths
