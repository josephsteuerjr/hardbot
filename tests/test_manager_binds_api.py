"""Перепривязку сотрудника делает руководитель — из приложения.

14.09.2026 три человека подряд зашли под чужим ФИО: самовыбор одноразовый,
список строится из реестра, соседние фамилии похожи. До починки человек работает
в ЧУЖОМ портфеле, а починить это мог только владелец за пультом — то есть не в
поле и не в тот же час. Правит теперь руководитель, и ровно в своей команде:
и ФИО, и сам аккаунт обязаны в неё входить, иначе со своего экрана можно было бы
снять устройство человека из чужой команды, зная только его telegram_id.
"""
import asyncio
import json
import re
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web

ROOT = Path(__file__).resolve().parent.parent
SHELL = (ROOT / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")

MGR, STRANGER = 571000, 571999
DEV_OLD, DEV_NEW, DEV_ALIEN = 571001, 571002, 571003
# Второй руководитель, который сам возит под ФИО из команды MGR.
MGR_DRIVER = 571500
IDS = (MGR, STRANGER, DEV_OLD, DEV_NEW, DEV_ALIEN, MGR_DRIVER)
_IN_IDS = ",".join("?" * len(IDS))
EMP_A, EMP_B, EMP_ALIEN = "Ктоаров А.", "Ктобеков Б.", "Ктовалиев В."


def _wipe():
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'KTO-%'")
        conn.execute(f"DELETE FROM managers WHERE telegram_id IN ({_IN_IDS})", IDS)
        conn.execute(f"DELETE FROM agent_bind WHERE telegram_id IN ({_IN_IDS})", IDS)
        conn.execute(f"DELETE FROM users WHERE telegram_id IN ({_IN_IDS})", IDS)
        conn.execute(f"DELETE FROM app_tokens WHERE telegram_id IN ({_IN_IDS})", IDS)
        conn.execute(f"DELETE FROM app_links WHERE telegram_id IN ({_IN_IDS})", IDS)
        conn.execute("DELETE FROM agent_bind WHERE employee IN (?,?,?)",
                     (EMP_A, EMP_B, EMP_ALIEN))
        conn.commit()


def _seed():
    db.init_db()
    _wipe()
    with db.connect() as conn:
        for cid, emp in (("KTO-1", EMP_A), ("KTO-2", EMP_B), ("KTO-3", EMP_ALIEN)):
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "partner, debt_today, overall_result, country) VALUES (?,?,?,?,?,?,?,?)",
                (cid, f"Клиент {cid}", emp, "1990-01-01", "ЗК", "1 000",
                 "Не обработан", "Таджикистан"))
        conn.commit()
    db.set_manager(MGR, "Рук", [], [EMP_A, EMP_B])
    db.reset_role_caches()
    # Как в жизни: у EMP_A работает телефон, а рядом висит заявка чужого человека
    # на то же ФИО — самовыбор его не пустил (одно ФИО — одно устройство).
    db.touch_guest(DEV_OLD, "karim_a", "Карим Ктоаров")
    db.touch_guest(DEV_NEW, "bahrom_s", "Bahrom Sharifov")
    db.set_bind(DEV_OLD, EMP_A)
    db.bind_agent(DEV_NEW, EMP_A, "agent", approved=False)
    db.set_bind(DEV_ALIEN, EMP_ALIEN)


@pytest.fixture(autouse=True)
def _cleanup():
    _seed()
    yield
    _wipe()
    db.reset_role_caches()


def _call(method, body, monkeypatch, uid=MGR):
    monkeypatch.setattr(web, "_uid", lambda _r: uid)
    req = make_mocked_request(method, "/api/manager/binds", app={"bot": None})
    if body is not None:
        async def _json():
            return body
        req.json = _json
    return json.loads(asyncio.run(web._manager_binds(req)).body.decode())


def _accounts(out, employee):
    row = next(e for e in out["employees"] if e["employee"] == employee)
    return row["accounts"]


def _token(uid: int) -> str:
    return db.redeem_app_link(db.create_app_link(uid))


# --- чтение ------------------------------------------------------------------
def test_list_shows_only_own_team_with_its_accounts(monkeypatch):
    out = _call("GET", None, monkeypatch)
    assert out["ok"] and out["screen"] == "binds"
    assert [e["employee"] for e in out["employees"]] == [EMP_A, EMP_B]
    assert [a["telegram_id"] for a in _accounts(out, EMP_A)] == [DEV_OLD]
    assert _accounts(out, EMP_B) == [], "устройства нет — и это видно"


def test_profile_name_is_what_gives_a_stranger_away(monkeypatch):
    """По голому telegram_id не видно ничего; «Bahrom Sharifov» на ФИО
    «Ктоаров А.» видно сразу — ради этого имя профиля тут и есть."""
    out = _call("GET", None, monkeypatch)
    req = out["pending"][0]
    assert req["telegram_id"] == DEV_NEW and req["employee"] == EMP_A
    assert req["profile"] == "Bahrom Sharifov" and req["username"] == "bahrom_s"
    assert _accounts(out, EMP_A)[0]["profile"] == "Карим Ктоаров"


def test_a_local_login_is_not_shown_as_a_telegram_id(monkeypatch):
    uid, _ = db.local_identity_for(EMP_B)
    try:
        out = _call("GET", None, monkeypatch)
        assert _accounts(out, EMP_B)[0]["local"] is True
    finally:
        db.unbind(uid)
        with db.connect() as conn:
            conn.execute("DELETE FROM users WHERE telegram_id = ?", (uid,))
            conn.commit()


def test_outsiders_cannot_read_or_write(monkeypatch):
    with pytest.raises(web.web.HTTPForbidden):
        _call("GET", None, monkeypatch, uid=STRANGER)
    with pytest.raises(web.web.HTTPForbidden):
        _call("POST", {"action": "unbind", "telegram_id": DEV_OLD}, monkeypatch,
              uid=STRANGER)


# --- перестановка на верное ФИО ----------------------------------------------
def test_wrong_pick_is_fixed_without_touching_the_victim(monkeypatch):
    """Тот самый случай 14.09: человек выбрал соседнюю фамилию. Его аккаунт
    переставляют на своё ФИО, а тот, под кого он зашёл, остаётся работать."""
    out = _call("POST", {"action": "reassign", "telegram_id": DEV_NEW,
                         "employee": EMP_B}, monkeypatch)
    assert out["ok"] and out["pending"] == [], "заявки больше нет"
    assert db.get_binding(DEV_NEW) == EMP_B
    assert db.get_binding(DEV_OLD) == EMP_A, "пострадавшего не тронули"


def test_reassign_refuses_a_name_that_already_has_a_device(monkeypatch):
    """Вытеснение чужого устройства — осознанное действие с отдельной кнопкой,
    молча из списка ФИО оно происходить не должно."""
    db.set_bind(DEV_NEW, EMP_B)
    out = _call("POST", {"action": "reassign", "telegram_id": DEV_NEW,
                         "employee": EMP_A}, monkeypatch)
    assert out["ok"] is False and EMP_A in out["error"]
    assert db.get_binding(DEV_NEW) == EMP_B and db.get_binding(DEV_OLD) == EMP_A


def test_neither_a_foreign_name_nor_a_foreign_account_can_be_touched(monkeypatch):
    with pytest.raises(web.web.HTTPNotFound):       # чужое ФИО как цель
        _call("POST", {"action": "reassign", "telegram_id": DEV_OLD,
                       "employee": EMP_ALIEN}, monkeypatch)
    with pytest.raises(web.web.HTTPNotFound):       # чужой аккаунт как объект
        _call("POST", {"action": "unbind", "telegram_id": DEV_ALIEN}, monkeypatch)
    with pytest.raises(web.web.HTTPNotFound):       # аккаунт без привязки вовсе
        _call("POST", {"action": "unbind", "telegram_id": 571777}, monkeypatch)
    assert db.get_binding(DEV_ALIEN) == EMP_ALIEN and db.get_binding(DEV_OLD) == EMP_A


# --- чужой руководитель в своей команде (ревью 25.09, #8) ---------------------
def _driving_manager():
    """MGR_DRIVER — руководитель (строка в managers) и сам возит под EMP_B,
    которое входит в команду MGR. Ссылочные ручки такой вход не трогают;
    теперь не трогает и эта."""
    db.set_manager(MGR_DRIVER, "Рук-водитель", [], [])
    db.reset_role_caches()
    db.set_bind(MGR_DRIVER, EMP_B)
    return _token(MGR_DRIVER)


@pytest.mark.parametrize("body", [
    {"action": "unbind", "telegram_id": MGR_DRIVER},
    {"action": "reassign", "telegram_id": MGR_DRIVER, "employee": EMP_A},
    {"action": "approve", "telegram_id": MGR_DRIVER},
])
def test_another_managers_login_is_not_touched(monkeypatch, body):
    tok = _driving_manager()
    assert web._foreign_privileged(MGR, MGR_DRIVER)
    with pytest.raises(web.web.HTTPNotFound):
        _call("POST", body, monkeypatch)
    assert db.get_binding(MGR_DRIVER) == EMP_B, "коллегу сняли или переставили"
    assert db.uid_for_app_token(tok) == MGR_DRIVER, "у коллеги отняли вход"


def test_a_strangers_request_does_not_push_out_another_manager(monkeypatch):
    """Обход через заявку: одобрение чужой заявки на ФИО, под которым возит
    другой руководитель, вытеснило бы его устройство и токены."""
    tok = _driving_manager()
    db.bind_agent(STRANGER, EMP_B, "agent", approved=False)
    out = _call("POST", {"action": "approve", "telegram_id": STRANGER}, monkeypatch)
    assert out["ok"] is False and "другого руководителя" in out["error"]
    assert db.get_binding(MGR_DRIVER) == EMP_B
    assert db.uid_for_app_token(tok) == MGR_DRIVER
    assert db.get_binding(STRANGER) is None, "заявку одобрили"


def test_own_login_is_still_the_managers_own_business(monkeypatch):
    """Себя руководитель по-прежнему правит сам: «чужой» — только другой."""
    db.set_bind(MGR, EMP_B)
    out = _call("POST", {"action": "unbind", "telegram_id": MGR}, monkeypatch)
    assert out["ok"] and db.get_binding(MGR) is None


# --- одобрение заявки = смена устройства -------------------------------------
def test_approve_moves_the_name_to_the_new_device_and_closes_the_old(monkeypatch):
    """«Одно ФИО — одно устройство» остаётся: у прежнего телефона забираем и
    привязку, и вход в приложение — иначе девайс-токен жил бы ещё 30 дней."""
    tok = _token(DEV_OLD)
    assert db.uid_for_app_token(tok) == DEV_OLD
    out = _call("POST", {"action": "approve", "telegram_id": DEV_NEW}, monkeypatch)
    assert out["ok"]
    assert db.get_binding(DEV_NEW) == EMP_A
    assert db.get_binding(DEV_OLD) is None
    assert db.uid_for_app_token(tok) is None, "прежнее устройство осталось в системе"
    assert [a["telegram_id"] for a in _accounts(out, EMP_A)] == [DEV_NEW]


def test_approving_a_working_account_is_refused_not_repeated(monkeypatch):
    out = _call("POST", {"action": "approve", "telegram_id": DEV_OLD}, monkeypatch)
    assert out["ok"] is False and "заявки нет" in out["error"]


# --- снятие -------------------------------------------------------------------
def test_unbind_frees_the_name_and_closes_the_app(monkeypatch):
    tok = _token(DEV_OLD)
    out = _call("POST", {"action": "unbind", "telegram_id": DEV_OLD}, monkeypatch)
    assert out["ok"] and _accounts(out, EMP_A) == []
    assert db.get_binding(DEV_OLD) is None
    assert db.uid_for_app_token(tok) is None
    assert db.employee_taken_by(EMP_A) is None, "ФИО свободно — настоящий выберет себя"


def test_unbind_is_also_how_a_request_is_rejected(monkeypatch):
    out = _call("POST", {"action": "unbind", "telegram_id": DEV_NEW}, monkeypatch)
    assert out["ok"] and out["pending"] == []
    assert db.binding_of(DEV_NEW) is None
    assert db.get_binding(DEV_OLD) == EMP_A, "работающего это не касается"


# --- мусор и следы ------------------------------------------------------------
def test_garbage_is_400(monkeypatch):
    for body in ({"action": "удалить всех", "telegram_id": DEV_OLD},
                 {"action": "unbind"},
                 {"action": "unbind", "telegram_id": "нет"}):
        with pytest.raises(web.web.HTTPBadRequest):
            _call("POST", body, monkeypatch)
    assert db.get_binding(DEV_OLD) == EMP_A


def test_every_change_leaves_a_trace(monkeypatch):
    _call("POST", {"action": "reassign", "telegram_id": DEV_NEW,
                   "employee": EMP_B}, monkeypatch)
    row = db.audit_tail(5)[0]
    assert row["actor"] == MGR and row["action"] == "mgr_rebind"
    assert row["target"] == str(DEV_NEW) and f"{EMP_A} -> {EMP_B}" in row["detail"]


def test_two_devices_on_one_name_are_shown_not_hidden(monkeypatch):
    """Аппрув из бота прежнее устройство не вытеснял — такие пары в базе есть,
    и прятать от руководителя надо не их, а повод их прятать."""
    db.bind_agent(DEV_NEW, EMP_A, "agent", approved=True)
    out = _call("GET", None, monkeypatch)
    assert {a["telegram_id"] for a in _accounts(out, EMP_A)} == {DEV_OLD, DEV_NEW}


# --- счётчик на сводке --------------------------------------------------------
def test_summary_says_how_many_requests_are_waiting(monkeypatch):
    monkeypatch.setattr(web, "_uid", lambda _r: MGR)
    req = make_mocked_request("GET", "/api/manager/stats?preset=today")
    out = json.loads(asyncio.run(web._manager_stats(req)).body.decode())
    assert out["bind_requests"] == 1


# --- экран --------------------------------------------------------------------
def _screen() -> str:
    return SHELL[SHELL.index("function dgBindsScreen("):SHELL.index("const DG_METRIC_TITLE")]


def test_summary_has_a_way_into_the_screen():
    assert "dgGo('binds')" in SHELL and "👤 Кто есть кто" in SHELL
    assert "bind_requests" in SHELL, "число висящих заявок видно ещё со сводки"


def test_screen_asks_before_closing_someones_access():
    body = _screen()
    assert body.count("confirm(") == 2, "снятие и вытеснение спрашиваем вслух"
    assert "прежнее потеряет доступ" in body
    assert "не сохранилось" in body, "отказ обязан быть виден"


def test_screen_carries_only_numeric_indices_into_onclick():
    """esc не экранирует апостроф, а он в ФИО на узбекской латинице норма."""
    for m in re.finditer(r'onclick="([^"]*)"', _screen()):
        assert "${e." not in m.group(1) and "${a." not in m.group(1), m.group(1)


def test_occupied_names_are_visible_but_not_tappable():
    body = _screen()
    i = body.index("function dgBindList(")
    picker = body[i:body.index("function dgBindPick(", i)]
    assert "занято:" in picker and "if(busy.length) return" in picker
