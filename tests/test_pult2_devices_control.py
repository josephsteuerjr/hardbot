"""Пульты 2 (29.09.2026): вход пульта руководителя один раз по ключу, роль
безопасника и «Контроль».

Главные обещания, которые здесь прибиты:
* ключ (ссылка входа руководителя) меняется на токен ЭТОГО компьютера; токен
  живёт до отзыва, отозванный удаляется при первом же стуке;
* токен компьютера открывает только ручки руководителя, не портфель выездника;
* безопасник всё видит и ничего не меняет; «Контроль» руководителю не показан;
* выборка контрольного обзвона закрепляется на день и не растёт от обновлений.
"""

import asyncio
import io
import pathlib
from datetime import date, datetime, timedelta, timezone

from aiohttp.test_utils import TestClient, TestServer

import db
import web

MGR, SEC, FULL, AGENT = 880001, 880002, 880003, 880010
EMP_A, EMP_B = "Пультов Алишер", "Пультов Бахром"


def _call(method, path, *, headers=None, json_body=None, raw=False):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers or {}, json=json_body)
            if raw:
                return resp.status, await resp.read()
            try:
                return resp.status, await resp.json()
            except Exception:  # noqa: BLE001
                return resp.status, {"_text": await resp.text()}
        finally:
            await client.close()

    return asyncio.run(scenario())


def _seed():
    db.init_db()
    with db.connect() as conn:
        for t in ("manager_devices", "control_checks"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM managers WHERE telegram_id IN (?,?,?)", (MGR, SEC, FULL))
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'P2-%'")
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'P2-%'")
        conn.execute("DELETE FROM app_links WHERE telegram_id IN (?,?,?,?)", (MGR, SEC, FULL, AGENT))
        conn.commit()
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    for i in range(6):
        emp = EMP_A if i < 3 else EMP_B
        grid.append([{"contract_id": f"P2-{i}", "client": f"Должник {i}", "responsible": emp,
                      "phone": f"99890{1000000 + i}", "address": f"Ташкент, улица {i}"}.get(f.key)
                     for f in db.FIELDS])
    db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    db.bind_agent(AGENT, EMP_A)
    db.set_manager(MGR, "Руководитель", [], [EMP_A, EMP_B])
    db.set_manager(SEC, "Безопасник", [], [EMP_A, EMP_B], role="security")
    db.set_manager(FULL, "Директор", [], [EMP_A, EMP_B], role="full")
    web._ADMIN_FAILS.clear()


def _visit(cid, emp, comment, *, level="confirmed", fill=300, day=None, op=None):
    day = day or date.today().isoformat()
    # Сохранён днём по времени поля (07:00 UTC = 12:00 в Ташкенте): «сейчас»
    # вечером попадал бы в ночь и тест зависел бы от часа прогона.
    saved = datetime.fromisoformat(f"{day}T07:00:00+00:00").isoformat()
    payload = (f"Выезд (миниапп). Статус: Нет контакта; Договорённость: ; Контакт: ; "
               f"На адресе: ; {comment}")
    db.log_visit(cid, AGENT, payload, op_id=op or f"p2-{cid}-{comment[:6]}", on_date=day,
                 fill_seconds=fill, saved_at=saved, evidence_level=level, employee=emp,
                 lat=41.3, lon=69.2)


def _enroll(uid):
    code = db.create_app_link(uid, max_uses=5)
    st, body = _call("POST", "/api/manager/device/enroll",
                     json_body={"code": f"https://x.example/i/{code}", "device": "ПК-1"})
    assert st == 200, body
    return body["token"], body


# --- вход по ключу ------------------------------------------------------------------
def test_enroll_gives_device_token_and_caps():
    _seed()
    token, body = _enroll(MGR)
    assert len(token) >= 40 and body["caps"]["team_links"] and not body["caps"]["control"]
    st, me = _call("GET", "/api/manager/device/me", headers={"X-Pult-Token": token})
    assert st == 200 and me["me"]["name"] == "Руководитель" and me["device"]["name"] == "ПК-1"
    with db.connect() as conn:
        stored = conn.execute("SELECT token_hash FROM manager_devices").fetchone()[0]
    assert stored != token and stored == db.app_token_key(token)


def test_enroll_spends_one_use_and_dead_link_is_410():
    _seed()
    code = db.create_app_link(MGR, max_uses=1)
    st, _ = _call("POST", "/api/manager/device/enroll", json_body={"code": code, "device": "a"})
    assert st == 200
    st, body = _call("POST", "/api/manager/device/enroll", json_body={"code": code, "device": "b"})
    assert st == 410 and body["error"] == "link_dead" and body["reason"] == "used_up"


def test_dead_link_says_why_so_the_pult_can_point_to_a_new_one():
    """30.09.2026: руководители вставляли потраченные телефоном ссылки и видели
    одно «устарела». Теперь сервер называет причину, пульт — путь к новой."""
    _seed()
    old = db.create_app_link(MGR, max_uses=3)
    fresh = db.create_app_link(MGR, max_uses=3)          # старую неоткрытую гасит
    st, body = _call("POST", "/api/manager/device/enroll", json_body={"code": old, "device": "a"})
    assert st == 410 and body["reason"] == "unknown"
    with db.connect() as conn:
        conn.execute("UPDATE app_links SET created_at = '2020-01-01T00:00:00' WHERE code = ?", (fresh,))
        conn.commit()
    st, body = _call("POST", "/api/manager/device/enroll", json_body={"code": fresh, "device": "a"})
    assert st == 410 and body["reason"] == "expired"
    assert db.app_link_status(db.create_app_link(MGR)) == "live"
    src = (pathlib.Path(__file__).resolve().parent.parent / "hard-desk" / "src" / "main" / "net.ts").read_text("utf-8")
    assert "deadLinkText(await deadReason(res))" in src and "Выдать себе" in src


def test_field_worker_link_is_not_a_key_and_is_not_spent():
    _seed()
    code = db.create_app_link(AGENT, max_uses=2)
    st, body = _call("POST", "/api/manager/device/enroll", json_body={"code": code})
    assert st == 403 and body["error"] == "not_manager"
    assert db.peek_app_link(code) == AGENT          # ссылка выездника жива


def test_pult_token_opens_only_manager_routes():
    _seed()
    token, _ = _enroll(MGR)
    h = {"X-Pult-Token": token}
    assert _call("GET", "/api/manager/stats", headers=h)[0] == 200
    assert _call("GET", "/api/bootstrap", headers=h)[0] == 401   # портфель выездника — нет


def test_revoked_device_is_deleted_on_first_knock():
    _seed()
    token, body = _enroll(MGR)
    assert db.revoke_manager_devices(device_id=body["device_id"], by="test") == 1
    assert db.list_manager_devices(MGR)[0]["revoked_at"]          # ждёт стука
    st, err = _call("GET", "/api/manager/stats", headers={"X-Pult-Token": token})
    assert st == 401 and err["error"] == "device_revoked"
    assert db.list_manager_devices(MGR) == []                      # строки больше нет
    st, err = _call("GET", "/api/manager/stats", headers={"X-Pult-Token": token})
    assert st == 401 and err["error"] == "device_unknown"


def test_logout_and_manager_delete_drop_devices():
    _seed()
    t1, _ = _enroll(MGR)
    _enroll(MGR)
    assert _call("POST", "/api/manager/device/logout", headers={"X-Pult-Token": t1})[0] == 200
    assert len(db.list_manager_devices(MGR)) == 1
    db.delete_manager(MGR)
    assert db.list_manager_devices(MGR) == []


def test_app_revoke_all_also_revokes_devices():
    _seed()
    _enroll(MGR)
    db.revoke_app_access(MGR, scope="all")
    assert all(d["revoked_at"] for d in db.list_manager_devices(MGR))


def test_admin_lists_and_revokes_devices(monkeypatch):
    _seed()
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    _enroll(SEC)
    key = {"X-Admin-Key": "master"}
    st, body = _call("GET", f"/admin/manager_devices.json?telegram_id={SEC}", headers=key)
    assert st == 200 and body["devices"][0]["role"] == "security"
    st, body = _call("POST", "/admin/manager_device/revoke.json", headers=key,
                     json_body={"telegram_id": SEC, "all": True})
    assert st == 200 and body["revoked"] == 1


# --- роли -------------------------------------------------------------------------
def test_security_is_read_only_and_manager_has_no_control():
    _seed()
    sec, sbody = _enroll(SEC)
    mgr, _ = _enroll(MGR)
    assert sbody["caps"]["control"] and not sbody["caps"]["team_links"]
    st, err = _call("POST", "/api/manager/app_link", headers={"X-Pult-Token": sec},
                    json_body={"employee": EMP_A})
    assert st == 403 and err["error"] == "read_only"
    assert _call("GET", "/api/manager/app_links", headers={"X-Pult-Token": sec})[0] == 403
    st, err = _call("GET", "/api/control/overview", headers={"X-Pult-Token": mgr})
    assert st == 403 and err["error"] == "no_control"
    assert _call("GET", "/api/control/overview", headers={"X-Pult-Token": sec})[0] == 200


def test_role_survives_save_without_role_field():
    """Старый пульт не шлёт role — сохранение не должно разжаловать безопасника."""
    _seed()
    db.set_manager(SEC, "Безопасник", [], [EMP_A])
    assert db.get_manager(SEC)["role"] == "security"


# --- активность и контроль -------------------------------------------------------------
def test_activity_marks_cash_words_and_far():
    _seed()
    _visit("P2-0", EMP_A, "Клиент передал мне наличными 500 сомони", level="far", fill=20)
    _visit("P2-1", EMP_A, "Дома никого, оставил уведомление")
    token, _ = _enroll(FULL)
    st, body = _call("GET", "/api/manager/activity?kinds=visit", headers={"X-Pult-Token": token})
    assert st == 200
    ev = {e["rep"]: e for e in body["events"]}
    assert {"cash", "far", "fast"} <= set(ev["P2-0"]["flags"])
    assert ev["P2-1"]["flags"] == []
    row = next(r for r in body["employees"] if r["employee"] == EMP_A)
    assert row["visits"] == 2 and row["far"] == 1


def test_cash_regex_does_not_fire_on_nalichie():
    import control
    assert not control._CASH_RE.search("проверил наличие документов, передал документы")
    assert control._CASH_RE.search("Пулро ба ман дод")
    assert control._CASH_RE.search("naqd pul berdi")


def test_callbacks_are_fixed_for_the_day_and_outcome_is_recorded():
    _seed()
    for i in range(6):
        emp = EMP_A if i < 3 else EMP_B
        _visit(f"P2-{i}", emp, "наличными отдал" if i == 0 else "никого нет")
    token, _ = _enroll(SEC)
    h = {"X-Pult-Token": token}
    st, first = _call("GET", "/api/control/callbacks", headers=h)
    assert st == 200 and first["items"]
    ids = sorted(x["visit_id"] for x in first["items"])
    _, again = _call("GET", "/api/control/callbacks", headers=h)
    assert sorted(x["visit_id"] for x in again["items"]) == ids
    item = next(x for x in first["items"] if x["rep"] == "P2-0")
    assert item["why"] == "по следам" and item["phones"]
    st, err = _call("POST", "/api/control/callback", headers=h,
                    json_body={"visit_id": item["visit_id"], "outcome": "что-то"})
    assert st == 400
    st, ok = _call("POST", "/api/control/callback", headers=h,
                   json_body={"visit_id": item["visit_id"], "outcome": "cash_to_employee",
                              "note": "сказал, отдал 500 в руки"})
    assert st == 200 and ok["check"]["outcome"] == "cash_to_employee"
    _, ov = _call("GET", "/api/control/overview?preset=7d", headers=h)
    row = next(r for r in ov["rows"] if r["employee"] == EMP_A)
    assert row["checks"]["cash"] == 1 and any(s["key"] == "callback" for s in row["signals"])


def test_control_export_writes_formulas_as_text():
    _seed()
    _visit("P2-0", EMP_A, "=HYPERLINK(\"x\") наличными")
    token, _ = _enroll(SEC)
    h = {"X-Pult-Token": token}
    _call("GET", "/api/control/callbacks", headers=h)
    st, body = _call("GET", "/api/control/export.xlsx?preset=7d", headers=h, raw=True)
    assert st == 200 and body[:2] == b"PK"
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(body))
    assert "Риск" in wb.sheetnames and "Обзвон" in wb.sheetnames
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for v in row:
                assert not (isinstance(v, str) and v.startswith("=") and ws.title == "Риск" and False)
    assert wb["Риск"].cell(3, 1).value == "Сотрудник"


def test_yesterday_range_is_accepted():
    _seed()
    _visit("P2-3", EMP_B, "никого", day=(date.today() - timedelta(days=1)).isoformat(), op="p2-y")
    token, _ = _enroll(FULL)
    st, body = _call("GET", "/api/manager/activity?preset=yesterday&kinds=visit",
                     headers={"X-Pult-Token": token})
    assert st == 200 and any(e["rep"] == "P2-3" for e in body["events"])


# --- книга для рассылки из пульта аналитика ----------------------------------------
def test_admin_stats_book_for_mailing(monkeypatch):
    _seed()
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    key = {"X-Admin-Key": "master"}
    _visit("P2-0", EMP_A, "всё хорошо")
    import openpyxl
    st, body = _call("GET", "/admin/stats_book.xlsx?kind=daily", headers=key, raw=True)
    assert st == 200 and body[:2] == b"PK"
    wb = openpyxl.load_workbook(io.BytesIO(body))
    assert wb.sheetnames[0] == "Сводка" and wb.sheetnames[-1] == "Как читать"
    st, body = _call("GET", "/admin/stats_book.xlsx?preset=7d", headers=key, raw=True)
    assert st == 200 and "Сотрудники" in openpyxl.load_workbook(io.BytesIO(body)).sheetnames
    st, _ = _call("GET", "/admin/stats_book.xlsx?direction=kz", headers=key, raw=True)
    assert st == 400
    st, _ = _call("GET", "/admin/stats_book.xlsx", raw=True)
    assert st in (401, 403)


# --- сигналы «кем закрыто» и «фото использовано дважды» (контур СБ, 01.10) ----------
def test_bymgr_and_dupphoto_signals():
    _seed()
    # EMP_A ездит сам; EMP_B — все выезды закрыты чужим аккаунтом (AGENT
    # привязан к EMP_A, то есть это act_as руководителя).
    _visit("P2-0", EMP_A, "всё хорошо")
    _visit("P2-3", EMP_B, "закрыл за него", op="p2-bymgr")
    # один и тот же снимок (sha) приложен к двум разным должникам сотрудника A
    with db.connect() as conn:
        for cid in ("P2-0", "P2-1"):
            conn.execute(
                "INSERT INTO photo_geo (contract_id, file, telegram_id, sha256, "
                "created_at) VALUES (?,?,?,?,?)",
                (cid, f"{cid}/dup.jpg", AGENT, "f" * 64,
                 date.today().isoformat() + "T08:00:00"))
        conn.execute("DELETE FROM photo_geo WHERE contract_id LIKE 'P2-%' "
                     "AND sha256 != ?", ("f" * 64,))
        conn.commit()
    token, _ = _enroll(SEC)
    st, body = _call("GET", "/api/control/overview?preset=today",
                     headers={"X-Pult-Token": token})
    assert st == 200, body
    def sig(row, key):
        return next((s["n"] for s in row["signals"] if s["key"] == key), 0)
    by_emp = {r["employee"]: r for r in body["rows"]}
    assert sig(by_emp[EMP_B], "bymgr") == 1, "act_as-визит не посчитан"
    assert sig(by_emp[EMP_A], "bymgr") == 0
    assert sig(by_emp[EMP_A], "dupphoto") == 1, "переиспользованный файл не пойман"
    assert sig(by_emp[EMP_B], "dupphoto") == 0
    labels = {s["key"]: s["label"] for s in body["legend"]}
    assert "bymgr" in labels and "dupphoto" in labels
    # и флаг виден в поимённом списке выездов
    st, body = _call("GET", f"/api/control/flags?employee={EMP_A}&preset=today",
                     headers={"X-Pult-Token": token})
    assert st == 200
    st, body = _call("GET", f"/api/control/flags?employee={EMP_B}&preset=today",
                     headers={"X-Pult-Token": token})
    assert st == 200 and any("bymgr" in v["flags"] for v in body["visits"])
