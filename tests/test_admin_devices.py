"""Устройства пульта: токен вместо мастер-ключа.

Смысл разделения: утёкший мастер-ключ перестаёт быть «загрузи что угодно откуда
угодно», а потерянный ноутбук отзывается по одному, не меняя ключ всем.
"""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

import db
import web

KEY = {"X-Admin-Key": "master"}


def _call(method, path, *, headers, json_body=None):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers, json=json_body)
            try:
                return resp.status, await resp.json()
            except Exception:  # noqa: BLE001 — 401 отдаёт текст, а не JSON
                return resp.status, {}
        finally:
            await client.close()

    return asyncio.run(scenario())


def _fresh(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM admin_devices")
        conn.execute("DELETE FROM admin_enroll_codes")
        conn.commit()
    web._ADMIN_FAILS.clear()   # счётчик неудач общий на процесс


def test_enroll_returns_token_that_then_works(monkeypatch):
    _fresh(monkeypatch)
    status, body = _call("POST", "/admin/device/enroll.json", headers=KEY,
                         json_body={"device_id": "fp-1", "name": "Ноут дома"})
    assert status == 200 and body["token"]
    token = body["token"]

    status, _ = _call("GET", "/admin/devices.json",
                      headers={"X-Device-Token": token, "X-Device-Id": "fp-1"})
    assert status == 200, "токен не пустили туда, куда пускает ключ"


def test_token_is_not_stored_in_clear(monkeypatch):
    """Дамп таблицы не должен давать вход."""
    _fresh(monkeypatch)
    _s, body = _call("POST", "/admin/device/enroll.json", headers=KEY,
                     json_body={"device_id": "fp-1", "name": "Ноут"})
    with db.connect() as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM admin_devices")]
    assert body["token"] not in stored
    assert all(len(h) == 64 for h in stored)


def test_revoked_token_stops_working(monkeypatch):
    _fresh(monkeypatch)
    _s, body = _call("POST", "/admin/device/enroll.json", headers=KEY,
                     json_body={"device_id": "fp-1", "name": "Потерянный"})
    token = body["token"]
    status, _ = _call("POST", "/admin/device/revoke.json", headers=KEY,
                      json_body={"id": body["id"]})
    assert status == 200
    status, _ = _call("GET", "/admin/devices.json",
                      headers={"X-Device-Token": token})
    assert status == 401


def test_device_token_cannot_enroll_or_revoke(monkeypatch):
    """Иначе укравший ноут запер бы владельца снаружи, а сам остался внутри."""
    _fresh(monkeypatch)
    _s, first = _call("POST", "/admin/device/enroll.json", headers=KEY,
                      json_body={"device_id": "fp-1", "name": "Первый"})
    tok = {"X-Device-Token": first["token"]}

    status, _ = _call("POST", "/admin/device/enroll.json", headers=tok,
                      json_body={"device_id": "fp-2", "name": "Чужой"})
    assert status == 401
    status, _ = _call("POST", "/admin/device/revoke.json", headers=tok,
                      json_body={"id": first["id"]})
    assert status == 401


def test_reenroll_replaces_old_token_of_the_same_machine(monkeypatch):
    """Переустановка пульта не должна плодить записи — иначе «отозвать это
    устройство» перестаёт что-либо значить."""
    _fresh(monkeypatch)
    _s, old = _call("POST", "/admin/device/enroll.json", headers=KEY,
                    json_body={"device_id": "fp-1", "name": "До"})
    _s, new = _call("POST", "/admin/device/enroll.json", headers=KEY,
                    json_body={"device_id": "fp-1", "name": "После"})
    assert new["token"] != old["token"]

    status, _ = _call("GET", "/admin/devices.json",
                      headers={"X-Device-Token": old["token"], "X-Device-Id": "fp-1"})
    assert status == 401, "старый токен машины пережил перепривязку"
    status, body = _call("GET", "/admin/devices.json",
                         headers={"X-Device-Token": new["token"], "X-Device-Id": "fp-1"})
    assert status == 200
    assert [d["name"] for d in body["devices"]] == ["После"]


def test_master_key_still_works_when_everything_is_revoked(monkeypatch):
    """Путь восстановления: отозвал все устройства — заходишь ключом и привязываешь
    заново. Без этого можно запереть себя окончательно."""
    _fresh(monkeypatch)
    _s, dev = _call("POST", "/admin/device/enroll.json", headers=KEY,
                    json_body={"device_id": "fp-1", "name": "Единственный"})
    _call("POST", "/admin/device/revoke.json", headers=KEY, json_body={"id": dev["id"]})
    status, body = _call("GET", "/admin/devices.json", headers=KEY)
    assert status == 200 and body["devices"] == []


def test_garbage_token_is_refused(monkeypatch):
    _fresh(monkeypatch)
    for bad in ("", "нет-такого", "a" * 64):
        status, _ = _call("GET", "/admin/devices.json",
                          headers={"X-Device-Token": bad})
        assert status == 401, bad


def test_token_alone_is_not_enough_without_the_fingerprint(monkeypatch):
    """Скопировали файл настроек на другую машину — токен там не работает.

    До этой правки отпечаток сверялся только при выдаче, а дальше токен был
    предъявительским: файл настроек пульта — обычный JSON на диске.
    """
    _fresh(monkeypatch)
    _s, dev = _call("POST", "/admin/device/enroll.json", headers=KEY,
                    json_body={"device_id": "fp-свой", "name": "Ноут"})
    tok = dev["token"]

    status, _ = _call("GET", "/admin/devices.json", headers={"X-Device-Token": tok})
    assert status == 401, "токен пустили вообще без отпечатка"

    status, _ = _call("GET", "/admin/devices.json",
                      headers={"X-Device-Token": tok, "X-Device-Id": "fp-чужой"})
    assert status == 401, "токен пустили с чужой машины"

    status, _ = _call("GET", "/admin/devices.json",
                      headers={"X-Device-Token": tok, "X-Device-Id": "fp-свой"})
    assert status == 200, "своя машина перестала работать"


def test_master_key_opens_data_only_until_the_first_device(monkeypatch):
    """Главное изменение: после первой же привязки ключ перестаёт давать данные.

    До неё он работает — иначе первый пульт неоткуда взять, а в свежей базе и
    данных ещё нет.
    """
    _fresh(monkeypatch)
    status, _ = _call("GET", "/admin/stats", headers=KEY)
    assert status == 200, "до первой привязки ключ должен работать"

    _call("POST", "/admin/device/enroll.json", headers=KEY,
          json_body={"device_id": "fp-1", "name": "Первый"})

    for path in ("/admin/stats", "/admin/export.json"):
        status, _ = _call("GET", path, headers=KEY)
        assert status == 401, f"мастер-ключ всё ещё открывает {path}"


def test_revoking_everything_does_not_give_the_key_its_power_back(monkeypatch):
    """Дыра, которую закрывает односторонний флаг.

    Иначе обладатель ключа отзывал бы все устройства (а отзыв ключу разрешён) и
    возвращал себе полный доступ — «один ключ открывает всё» вернулось бы через
    два шага и выглядело бы как штатное восстановление.
    """
    _fresh(monkeypatch)
    _s, dev = _call("POST", "/admin/device/enroll.json", headers=KEY,
                    json_body={"device_id": "fp-1", "name": "Единственный"})
    _call("POST", "/admin/device/revoke.json", headers=KEY, json_body={"id": dev["id"]})

    status, _ = _call("GET", "/admin/stats", headers=KEY)
    assert status == 401, "отзыв всех устройств вернул ключу доступ к данным"

    # Привязать заново ключ по-прежнему может — это и есть путь восстановления.
    status, body = _call("POST", "/admin/device/enroll.json", headers=KEY,
                         json_body={"device_id": "fp-1", "name": "Заново"})
    assert status == 200 and body["token"]


def test_master_key_check_endpoint_answers_only_the_key(monkeypatch):
    """Ротации ключа нужно место, где она убедится, что новый ключ принят.

    Раньше она проверяла это на /admin/stats — то есть на ручке с ПД.
    """
    _fresh(monkeypatch)
    status, body = _call("GET", "/admin/key_check.json", headers=KEY)
    assert status == 200 and body["ok"] is True
    status, _ = _call("GET", "/admin/key_check.json", headers={"X-Admin-Key": "не тот"})
    assert status == 401


def test_enroll_code_is_one_time_and_binds_to_the_machine(monkeypatch):
    """Обмен кода на токен — без мастер-ключа, и ровно один раз."""
    _fresh(monkeypatch)
    import db
    code = db.issue_enroll_code("Ноут дома")["code"]

    status, body = _call("POST", "/admin/device/redeem.json", headers={},
                         json_body={"code": code, "device_id": "fp-1", "name": "Ноут"})
    assert status == 200 and body["token"], "код не обменялся на токен"
    token = body["token"]

    status, _ = _call("GET", "/admin/devices.json",
                      headers={"X-Device-Token": token, "X-Device-Id": "fp-1"})
    assert status == 200, "выданный по коду токен не работает"

    status, body = _call("POST", "/admin/device/redeem.json", headers={},
                         json_body={"code": code, "device_id": "fp-2"})
    assert status == 400 and "использован" in body["error"], "код обменялся дважды"


def test_unknown_enroll_code_is_refused(monkeypatch):
    _fresh(monkeypatch)
    status, body = _call("POST", "/admin/device/redeem.json", headers={},
                         json_body={"code": "нет-такого", "device_id": "fp-1"})
    assert status == 400 and "error" in body


def test_existing_install_loses_the_key_power_on_upgrade(monkeypatch):
    """Обновление боевого сервера, где устройства привязаны давно.

    Строки в настройках там нет — она появилась только сейчас. Если бы флаг
    смотрел только на неё, обновлённый сервер молча сохранил бы прежнее
    поведение, и правка подействовала бы только на новые установки.
    """
    _fresh(monkeypatch)
    import db
    db.enroll_admin_device("fp-старый", "Привязан до обновления")
    # Убираем именно флаг, оставляя устройство: так выглядит база после апдейта.
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key=?", (db.ADMIN_BOOTSTRAP_KEY,))
        conn.commit()

    status, _ = _call("GET", "/admin/stats", headers=KEY)
    assert status == 401, "после обновления ключ всё ещё открывает данные"
    assert db.admin_bootstrap_done(), "флаг не проставился по факту устройств"
