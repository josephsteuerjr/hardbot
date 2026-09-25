"""Панель обслуживания: роли, данные не видны, страница открыта.

Главное свойство: панель обслуживания НЕ ВИДИТ должников, а инженер панели не
попадает в данные пульта. Обслуживание открыто обеим ролям устройств — инженер
входит веб-страницей, аналитик пультом, — но роли остаются раздельными для
отзыва, и граница с данными держится отдельно.
"""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

import db
import web


def _call(method, path, *, headers, json_body=None):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers, json=json_body)
            body = {}
            try:
                body = await resp.json()
            except Exception:  # noqa: BLE001 — страница отдаёт html, 401 отдаёт текст
                body = {"text": await resp.text()}
            return resp.status, body
        finally:
            await client.close()

    return asyncio.run(scenario())


def _fresh(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM admin_devices")
        conn.execute("DELETE FROM admin_enroll_codes")
        conn.execute("DELETE FROM settings WHERE key=?", (db.ADMIN_BOOTSTRAP_KEY,))
        conn.commit()
    web._ADMIN_FAILS.clear()


def _device(role, fingerprint):
    made = db.enroll_admin_device(fingerprint, f"тест {role}", role)
    return {"X-Device-Token": made["token"], "X-Device-Id": fingerprint}


OPS_PATHS = ("/ops/state.json", "/ops/devices.json")


def test_operator_token_opens_the_panel(monkeypatch):
    """Пульт аналитика — тоже консоль обслуживания («гипотетический инженер»
    переехал в пульт). Без этого владелец зависел бы от того, кто держит SSH."""
    _fresh(monkeypatch)
    pult = _device("operator", "fp-pult")
    for path in OPS_PATHS:
        status, body = _call("GET", path, headers=pult)
        assert status == 200, f"пульт не пустили в {path}"


def test_engineer_token_cannot_see_debtors(monkeypatch):
    """И обратно: панель не видит ни одной ручки с должниками.

    Это причина, по которой роль отдельная, а не флаг у оператора.
    """
    _fresh(monkeypatch)
    engineer = _device("engineer", "fp-eng")
    for path in ("/admin/stats", "/admin/export.json", "/admin/devices.json"):
        status, _ = _call("GET", path, headers=engineer)
        assert status == 401, f"панель пустили к данным: {path}"


def test_engineer_token_opens_the_panel(monkeypatch):
    _fresh(monkeypatch)
    engineer = _device("engineer", "fp-eng")
    status, body = _call("GET", "/ops/state.json", headers=engineer)
    assert status == 200
    assert "agent" in body and "inventory" in body and "buttons" in body


def test_master_key_does_not_open_the_panel(monkeypatch):
    """Панель умеет перезапускать сервер. Открывать её строкой из .env значило бы
    вернуть ровно то, что мы убрали из админского доступа."""
    _fresh(monkeypatch)
    for path in OPS_PATHS:
        status, _ = _call("GET", path, headers={"X-Admin-Key": "master"})
        assert status == 401, f"мастер-ключ открыл {path}"


def test_panel_page_is_open_but_carries_no_data(monkeypatch):
    """Страница отдаётся без токена — иначе её нельзя открыть в браузере вовсе.

    Браузер не шлёт заголовки на переход по ссылке. Поэтому страница пустая:
    разметка и скрипт, который сперва просит код. Данные — только в ручках.
    """
    _fresh(monkeypatch)
    status, body = _call("GET", "/ops/", headers={})
    assert status == 200
    page = body.get("text", "")
    assert "Вход" in page and "admin_access.py issue" in page
    for leak in ("hardbot.db", "clients", "должник", "ADMIN_API_KEY"):
        assert leak not in page, f"на открытой странице оказалось «{leak}»"


def test_engineer_issues_no_codes_at_all(monkeypatch):
    """Инженер не выдаёт кодов вовсе — ни пульту, ни панели.

    Код пульта открывает должников. Раньше инженер выдавал его себе, обменивал
    без мастер-ключа и в два запроса становился оператором (ревью 25.09, PoC
    poc_eng_escalate.py): граница «панель не видит должников» стиралась.
    """
    _fresh(monkeypatch)
    engineer = _device("engineer", "fp-eng")
    for role in ("operator", "engineer"):
        status, body = _call("POST", "/ops/code.json", headers=engineer,
                             json_body={"name": "Себе же", "role": role})
        assert status == 403, role
        assert body["error"] == "код выдаёт оператор или команда на сервере"
    assert db.list_enroll_codes(include_used=True) == [], "код всё-таки выписан"


def test_engineer_cannot_climb_to_operator_through_a_code(monkeypatch):
    """Вся цепочка из PoC: код → обмен → /admin/stats. Рвётся на первом шаге."""
    _fresh(monkeypatch)
    engineer = _device("engineer", "fp-eng")
    status, body = _call("POST", "/ops/code.json", headers=engineer,
                         json_body={"name": "promote", "role": "operator"})
    assert status == 403 and not body.get("code")
    status, _ = _call("POST", "/admin/device/redeem.json", headers={},
                      json_body={"code": body.get("code") or "", "device_id": "eng-2"})
    assert status == 400
    status, _ = _call("GET", "/admin/stats", headers=engineer)
    assert status == 401


def test_operator_issues_codes_only_for_the_pult(monkeypatch):
    """Аналитик из пульта выдаёт коды только пультам: вторую ПАНЕЛЬ не
    заводит никто, кроме того, кто у сервера."""
    _fresh(monkeypatch)
    pult = _device("operator", "fp-pult")
    status, body = _call("POST", "/ops/code.json", headers=pult,
                         json_body={"name": "Ноут второго аналитика",
                                    "role": "operator"})
    assert status == 200 and body["role"] == "operator"

    status, body = _call("POST", "/ops/code.json", headers=pult,
                         json_body={"name": "Панель инженера", "role": "engineer"})
    assert status == 400 and "командой на сервере" in body["error"]


def test_operator_cannot_revoke_the_device_he_works_from(monkeypatch):
    """Само-отзыв закрыт и пульту: «пульт сломался» лечится только кодом
    с сервера, и это осознанно."""
    _fresh(monkeypatch)
    made = db.enroll_admin_device("fp-pult", "Пульт", "operator")
    headers = {"X-Device-Token": made["token"], "X-Device-Id": "fp-pult"}
    status, body = _call("POST", "/ops/device/revoke.json", headers=headers,
                         json_body={"id": made["id"]})
    assert status == 400 and "из которого вы работаете" in body["error"]
    assert db.admin_device_by_token(made["token"], "fp-pult"), "устройство всё-таки отозвано"


def test_engineer_cannot_revoke_the_device_he_works_from(monkeypatch):
    """Отзыв самого себя выглядит как «панель сломалась» и лечится только
    доступом к серверу — тем самым, которого мы инженера и лишаем."""
    _fresh(monkeypatch)
    made = db.enroll_admin_device("fp-eng", "Панель", "engineer")
    headers = {"X-Device-Token": made["token"], "X-Device-Id": "fp-eng"}
    status, body = _call("POST", "/ops/device/revoke.json", headers=headers,
                         json_body={"id": made["id"]})
    assert status == 400 and "из которого вы работаете" in body["error"]
    assert db.admin_device_by_token(made["token"], "fp-eng"), "устройство всё-таки отозвано"


def test_operator_and_engineer_do_not_revoke_each_other(monkeypatch):
    """Роли гасят только свои устройства.

    Иначе укравший токен одной роли отзывал бы законные устройства другой, а
    своё оставлял. Чужую роль отзывает мастер-ключ.
    """
    _fresh(monkeypatch)
    pult = db.enroll_admin_device("fp-pult", "Пульт", "operator")
    panel = db.enroll_admin_device("fp-eng", "Панель", "engineer")
    as_pult = {"X-Device-Token": pult["token"], "X-Device-Id": "fp-pult"}
    as_panel = {"X-Device-Token": panel["token"], "X-Device-Id": "fp-eng"}

    status, body = _call("POST", "/ops/device/revoke.json", headers=as_pult,
                         json_body={"id": panel["id"]})
    assert status == 403 and "мастер-ключом" in body["error"]
    status, body = _call("POST", "/ops/device/revoke.json", headers=as_panel,
                         json_body={"id": pult["id"]})
    assert status == 403
    assert db.admin_device_by_token(pult["token"], "fp-pult")
    assert db.admin_device_by_token(panel["token"], "fp-eng")


def test_a_role_revokes_its_own_kind(monkeypatch):
    _fresh(monkeypatch)
    first = _device("engineer", "fp-eng-1")
    second = db.enroll_admin_device("fp-eng-2", "Вторая панель", "engineer")
    status, body = _call("POST", "/ops/device/revoke.json", headers=first,
                         json_body={"id": second["id"]})
    assert status == 200 and body["ok"] is True
    assert db.admin_device_by_token(second["token"], "fp-eng-2") is None


def test_panel_page_never_inserts_markup(monkeypatch):
    """Имя устройства задаёт тот, кто обменял код, — строкой в теле обмена.

    Страница вставляла его разметкой: имя вида <img onerror=…> исполнялось бы в
    панели инженера и уносило его токен из localStorage. Теперь данные ставятся
    только текстом, и HTML-вставок на странице нет вовсе.
    """
    _fresh(monkeypatch)
    status, body = _call("GET", "/ops/", headers={})
    page = body.get("text", "")
    assert status == 200
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in page, f"на странице осталось {sink}"
    assert 'node("td", "", name)' in page, "имя устройства ставится не текстом"
    assert "/ops/code.json" not in page, "кнопка кода осталась, а инженеру её не выдают"


def test_unknown_action_is_refused_before_the_queue(monkeypatch):
    _fresh(monkeypatch)
    engineer = _device("engineer", "fp-eng")
    status, body = _call("POST", "/ops/run.json", headers=engineer,
                         json_body={"action": "shell", "params": {"unit": "core"}})
    # 400 — действие не из списка; 409 — помощник не запущен. Оба честные отказы,
    # и оба означают, что заявка в очередь не легла.
    assert status in (400, 409), status


def test_panel_refuses_work_when_the_agent_is_silent(monkeypatch):
    """Отказываем сразу, а не кладём заявку, которую никто не заберёт.

    Иначе инженер смотрит на «выполняется» и ждёт службу, которая не запущена.
    """
    _fresh(monkeypatch)
    engineer = _device("engineer", "fp-eng")
    status, body = _call("POST", "/ops/run.json", headers=engineer,
                         json_body={"action": "doctor", "params": {"unit": "core"}})
    assert status == 409 and "помощник" in body["error"]
