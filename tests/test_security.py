"""Тесты безопасности O3: аппрув привязки, аудит, auth движка."""
import itertools

import pytest

import db

_uid = itertools.count(700000)


@pytest.fixture(autouse=True, scope="module")
def _init():
    db.init_db()


# --- Аппрув привязки выездника ---------------------------------------------
def test_pending_binding_has_no_access():
    uid = next(_uid)
    db.bind_agent(uid, "Сотрудник А", approved=False)
    assert db.get_binding(uid) is None          # доступа нет, пока не одобрят
    assert db.pending_binding(uid) is not None


def test_approve_grants_access():
    uid = next(_uid)
    db.bind_agent(uid, "Сотрудник Б", approved=False)
    emp = db.approve_binding(uid)
    assert emp == "Сотрудник Б"
    assert db.get_binding(uid) == "Сотрудник Б"
    assert db.pending_binding(uid) is None


def test_reject_removes_pending():
    uid = next(_uid)
    db.bind_agent(uid, "Сотрудник В", approved=False)
    db.reject_binding(uid)
    assert db.pending_binding(uid) is None
    assert db.get_binding(uid) is None
    # после отклонения можно выбрать себя заново
    assert db.bind_agent(uid, "Сотрудник Г", approved=False) is True


def test_direct_bind_active_when_approval_off():
    uid = next(_uid)
    db.bind_agent(uid, "Сотрудник Д", approved=True)
    assert db.get_binding(uid) == "Сотрудник Д"


# --- Аудит-лог -------------------------------------------------------------
def test_audit_appends():
    uid = next(_uid)
    before = len(db.audit_tail(1000))
    db.audit_access(uid, "view_card", "ДГ-1", "тест")
    tail = db.audit_tail(1000)
    assert len(tail) == before + 1
    assert tail[0]["action"] == "view_card" and tail[0]["actor"] == uid


# --- Auth API движка -------------------------------------------------------
def test_engine_auth_open_for_synthetic():
    import app.main as engine_main
    engine_main.ENGINE_API_TOKEN = ""
    engine_main._REAL_DB = False
    assert engine_main._engine_auth("") is None      # демо без ПД — открыто


def test_engine_auth_requires_token_for_real_db():
    import app.main as engine_main
    from fastapi import HTTPException
    engine_main.ENGINE_API_TOKEN = "s3cret"
    engine_main._REAL_DB = True
    with pytest.raises(HTTPException):
        engine_main._engine_auth("wrong")
    assert engine_main._engine_auth("s3cret") is None


def test_all_api_routes_require_engine_token():
    """Токен-auth висит на ВСЕХ /api/* движка (никто не забыт)."""
    import app.main as engine_main
    api_routes = [r for r in engine_main.app.routes
                  if getattr(r, "path", "").startswith("/api/")]
    assert api_routes, "нет /api/* маршрутов?"
    for r in api_routes:
        calls = [d.call for d in r.dependant.dependencies]
        assert engine_main._engine_auth in calls, f"{r.path} без _engine_auth!"


def test_startup_guard_blocks_real_db_without_token(monkeypatch):
    import app.main as engine_main
    monkeypatch.setattr(engine_main, "_REAL_DB", True)
    monkeypatch.setattr(engine_main, "ENGINE_API_TOKEN", "")
    with pytest.raises(RuntimeError):
        engine_main._enforce_startup_safety()


def test_startup_guard_blocks_public_bind(monkeypatch):
    import app.main as engine_main
    monkeypatch.setattr(engine_main, "_REAL_DB", True)
    monkeypatch.setattr(engine_main, "ENGINE_API_TOKEN", "tok")
    monkeypatch.setenv("ENGINE_HOST", "0.0.0.0")
    with pytest.raises(RuntimeError):
        engine_main._enforce_startup_safety()


def test_startup_guard_ok_for_synthetic(monkeypatch):
    import app.main as engine_main
    monkeypatch.setattr(engine_main, "_REAL_DB", False)
    engine_main._enforce_startup_safety()   # синтетика — без ограничений, не падает
