"""Выдача входов — работа руководителя: ссылки своей команде и свои рубильники.

Контракт доступа после переезда на контур компании (решение 23.09.2026):
ключ к сервису рождает только держатель логина сервера, а ссылки входа выдаёт
каждый руководитель — сам и своей команде. Граница команды проверяется на
сервере: чужое ФИО через ручку руководителя не проходит.
"""

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web

ROOT = Path(__file__).resolve().parent.parent

MGR, STRANGER, EMP_TID = 573000, 573999, 573001
EMP_A, EMP_B, EMP_ALIEN = "Линков А.", "Линкова Б.", "Чужоев В."


def _wipe():
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'LNK-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id IN (?,?,?)",
                     (MGR, STRANGER, EMP_TID))
        conn.execute("DELETE FROM agent_bind WHERE telegram_id IN (?,?,?)",
                     (MGR, STRANGER, EMP_TID))
        conn.execute("DELETE FROM agent_bind WHERE employee IN (?,?,?)",
                     (EMP_A, EMP_B, EMP_ALIEN))
        conn.execute("DELETE FROM app_tokens WHERE telegram_id IN (?,?,?)",
                     (MGR, STRANGER, EMP_TID))
        conn.execute("DELETE FROM app_links WHERE telegram_id IN (?,?,?)",
                     (MGR, STRANGER, EMP_TID))
        conn.commit()


def _seed():
    db.init_db()
    _wipe()
    with db.connect() as conn:
        for cid, emp in (("LNK-1", EMP_A), ("LNK-2", EMP_B), ("LNK-3", EMP_ALIEN)):
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "partner, debt_today, overall_result, country) VALUES (?,?,?,?,?,?,?,?)",
                (cid, f"Клиент {cid}", emp, "1990-01-01", "ЗК", "1 000",
                 "Не обработан", "Таджикистан"))
        conn.commit()
    db.set_manager(MGR, "Рук Линков", [], [EMP_A, EMP_B])
    db.reset_role_caches()


@pytest.fixture(autouse=True)
def _clean():
    _seed()
    yield
    _wipe()
    db.reset_role_caches()


def _call(handler, method, body, monkeypatch, uid=MGR):
    monkeypatch.setattr(web, "_uid", lambda _r: uid)
    req = make_mocked_request(method, "/api/manager/x", app={"bot": None})
    if body is not None:
        async def _json():
            return body
        req.json = _json
    out = asyncio.run(handler(req))
    return out.status, json.loads(out.body.decode())


MINIAPP = "https://entry.example"


@pytest.fixture
def miniapp(monkeypatch):
    monkeypatch.setenv("MINIAPP_URL", MINIAPP)


# --- ссылки команды -----------------------------------------------------------

def test_links_list_has_team_only_and_never_issued_rows(monkeypatch, miniapp):
    status, out = _call(web._manager_app_links, "GET", None, monkeypatch)
    assert status == 200
    names = [r["employee"] for r in out["links"]]
    assert names == [EMP_A, EMP_B], "чужой попал в список или свои потерялись"
    assert all(not r["code"] and not r["entered"] for r in out["links"])


def test_manager_issues_link_to_own_employee(monkeypatch, miniapp):
    status, out = _call(web._manager_app_link, "POST",
                        {"employee": EMP_A}, monkeypatch)
    assert status == 200 and out["ok"]
    assert out["url"].startswith(MINIAPP + "/i/")
    # ссылка появилась в списке команды
    _, state = _call(web._manager_app_links, "GET", None, monkeypatch)
    row = next(r for r in state["links"] if r["employee"] == EMP_A)
    assert row["code"] == out["code"] and row["url"] == out["url"]


def test_manager_cannot_issue_for_stranger(monkeypatch, miniapp):
    status, out = _call(web._manager_app_link, "POST",
                        {"employee": EMP_ALIEN}, monkeypatch)
    assert status == 403 and "своей команде" in out["error"]


def test_revoke_kills_issued_link_of_own_employee(monkeypatch, miniapp):
    _, made = _call(web._manager_app_link, "POST", {"employee": EMP_A}, monkeypatch)
    tid = made["telegram_id"]
    token = db.redeem_app_link(made["code"])          # человек вошёл
    assert db.uid_for_app_token(token) == tid
    status, out = _call(web._manager_app_revoke, "POST",
                        {"employee": EMP_A}, monkeypatch)
    assert status == 200 and out["revoked"] >= 1
    assert not db.uid_for_app_token(token), "токен пережил отзыв"


def test_revoke_refuses_stranger(monkeypatch, miniapp):
    status, out = _call(web._manager_app_revoke, "POST",
                        {"employee": EMP_ALIEN}, monkeypatch)
    assert status == 403


# --- рубильники ---------------------------------------------------------------

def test_manager_reads_and_flips_pushes(monkeypatch):
    status, out = _call(web._manager_pushes, "GET", None, monkeypatch)
    assert status == 200 and out["pushes"]["push_digest"] is True

    status, out = _call(web._manager_pushes, "POST",
                        {"push_digest": False, "push_visits": False}, monkeypatch)
    assert status == 200
    assert out["pushes"] == {"push_visits": False, "push_digest": False,
                             "push_promises": True}
    row = db.get_manager(MGR)
    assert row["push_visits"] is False and row["push_digest"] is False


def test_pushes_refuse_unknown_keys_and_strangers(monkeypatch):
    status, out = _call(web._manager_pushes, "POST", {"push_nonsense": True},
                        monkeypatch)
    assert status == 400

    # чужой не руководитель: гейт поднимает 403 (в aiohttp это и есть отказ)
    from aiohttp import web as aio_web
    with pytest.raises(aio_web.HTTPForbidden):
        _call(web._manager_pushes, "GET", None, monkeypatch, uid=STRANGER)


def test_audit_carries_manager_name(monkeypatch, miniapp):
    """Провенанс: в журнале — имя руководителя, а не только номер."""
    _call(web._manager_app_link, "POST", {"employee": EMP_A}, monkeypatch)
    tail = db.audit_tail(20)
    row = next(r for r in tail if r["action"] == "mgr_app_link")
    assert "Рук Линков" in row["detail"], row["detail"]
    assert row["target"] == EMP_A


def test_manager_issues_link_to_himself(monkeypatch):
    """«Себе» — тоже можно: руководитель, который сам возит, входит в
    приложение своей ссылкой, даже когда его ФИО в составе команды нет."""
    monkeypatch.setenv("MINIAPP_URL", "https://entry.example")
    db.set_manager(MGR, "Рук Линков", [], [EMP_A])   # команда без EMP_B
    db.set_bind(MGR, EMP_B)                          # а сам он — EMP_B
    db.reset_role_caches()

    # себе — можно
    status, out = _call(web._manager_app_link, "POST", {"employee": EMP_B},
                        monkeypatch)
    assert status == 200 and out["employee"] == EMP_B
    assert out["url"].startswith("https://entry.example/i/")

    # своя строка видна в списке команды
    status, out = _call(web._manager_app_links, "GET", None, monkeypatch)
    assert {r["employee"] for r in out["links"]} == {EMP_A, EMP_B}

    # чужому по-прежнему нельзя
    status, out = _call(web._manager_app_link, "POST", {"employee": EMP_ALIEN},
                        monkeypatch)
    assert status == 403
