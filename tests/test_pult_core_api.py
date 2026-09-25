"""Клиент admin-API пульта: заголовки, дружелюбные ошибки, отказ на чужой сертификат
и на адрес без https."""

from __future__ import annotations

import http.server
import io
import json
import ssl
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from pult.core import api
from pult.core.settings import Settings, active_profile


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _opener(script):
    """script(req, kwargs) → bytes | raise. Записывает все вызовы."""
    calls = []

    def urlopen(req, **kwargs):
        calls.append((req, kwargs))
        return _Response(script(req, kwargs))
    urlopen.calls = calls
    return urlopen


def test_device_token_wins_over_master_key():
    opener = _opener(lambda req, kw: b'{"ok": true}')
    out = api.request("https://h/", "GET", "/admin/stats", key="k", device_token="t",
                      opener=opener)
    req = opener.calls[0][0]
    assert out == {"ok": True}
    assert req.full_url == "https://h/admin/stats"
    assert req.get_header("X-device-token") == "t"
    assert req.get_header("X-admin-key") is None
    assert req.get_header("Content-type") is None


def test_master_key_and_json_body_when_not_bound():
    opener = _opener(lambda req, kw: b'{"n": 1}')
    api.request("https://h", "POST", "/admin/bind", {"telegram_id": 1}, key="k",
                opener=opener)
    req = opener.calls[0][0]
    assert req.get_method() == "POST"
    assert req.get_header("X-admin-key") == "k"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"telegram_id": 1}
    assert opener.calls[0][1]["timeout"] == 20


def _http_error(code, payload=b""):
    return urllib.error.HTTPError("https://h/x", code, "msg", {}, io.BytesIO(payload))


def test_401_text_depends_on_how_we_authenticate():
    def boom(req, kw):
        raise _http_error(401)
    with pytest.raises(RuntimeError, match="токен отозван"):
        api.request("https://h", "GET", "/a", device_token="t", opener=_opener(boom))
    with pytest.raises(RuntimeError, match="Неверный мастер-ключ"):
        api.request("https://h", "GET", "/a", key="k", opener=_opener(boom))


def test_404_hints_old_server():
    def boom(req, kw):
        raise _http_error(404)
    with pytest.raises(RuntimeError, match="старой версии"):
        api.request("https://h", "GET", "/admin/new", key="k", opener=_opener(boom))


def test_409_with_error_payload_is_returned_not_raised():
    def boom(req, kw):
        raise _http_error(409, b'{"error": "scope_mismatch", "blocked": true}')
    out = api.request("https://h", "POST", "/admin/import.json", {}, key="k",
                      opener=_opener(boom))
    assert out["error"] == "scope_mismatch"


def test_409_without_payload_is_a_plain_error():
    def boom(req, kw):
        raise _http_error(409, b"not json")
    with pytest.raises(RuntimeError, match="409"):
        api.request("https://h", "GET", "/a", key="k", opener=_opener(boom))


def test_connection_failure_is_readable():
    def boom(req, kw):
        raise urllib.error.URLError("connection refused")
    with pytest.raises(RuntimeError, match="Нет связи с сервером"):
        api.request("https://h", "GET", "/a", key="k", opener=_opener(boom))


def test_bad_certificate_is_refused_without_unverified_retry():
    """Повтор без проверки TLS отдал бы мастер-ключ тому, кто подменил связь."""
    def script(req, kw):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("self signed"))
    opener = _opener(script)
    with pytest.raises(RuntimeError, match="Сертификат сервера не прошёл проверку"):
        api.request("https://h", "GET", "/a", key="k", opener=opener)
    assert len(opener.calls) == 1, "был повтор — ключ ушёл без проверки сертификата"


def test_admin_api_object_carries_credentials(monkeypatch):
    seen = {}

    def fake_request(url, method, path, body=None, **kw):
        seen.update(url=url, method=method, path=path, body=body, **kw)
        return {}
    monkeypatch.setattr(api, "request", fake_request)
    client = api.AdminApi("https://h", key="", device_token="tok")
    client.call("GET", "/admin/stats", timeout=5)
    assert seen["device_token"] == "tok" and seen["timeout"] == 5


def test_host_of():
    assert api.host_of("https://hardbot2.example.org/") == "hardbot2.example.org"
    assert api.host_of("") == "—"


def test_asks_for_gzip_and_unpacks_it():
    """Контур компании опубликован через туннель: несжатая выгрузка базы (46 МБ)
    шла по нему минуты. Пульт просит gzip и сам распаковывает ответ."""
    import gzip

    class _Gz(_Response):
        headers = {"Content-Encoding": "gzip"}

    seen = {}

    def urlopen(req, **kwargs):
        seen["enc"] = req.get_header("Accept-encoding")
        return _Gz(gzip.compress(json.dumps({"rows": [1, 2, 3]}).encode()))

    assert api.request("https://h", "GET", "/admin/export.json", key="k",
                       opener=urlopen) == {"rows": [1, 2, 3]}
    assert seen["enc"] == "gzip"


# --- только https (ревью 25.09.2026) ------------------------------------------
# Мастер-ключ, токен устройства и код привязки по http читает любой, кто видит
# сеть. Опечатка «http://» в адресе контура не должна отдать их никому.
PLAIN = ["http://hard.example", "HTTP://hard.example:443", "http://127.0.0.1.nip.io",
         "http://127.0.0.1@evil.example", "http://127.0.0.2:8080", "ftp://hard.example",
         "hard.example", "//hard.example", "https://", "http://[::1"]


@pytest.mark.parametrize("url", PLAIN)
def test_plain_http_is_refused_before_anything_is_sent(url):
    opener = _opener(lambda req, kw: b'{"token": "t"}')
    for creds in ({"key": "MASTER"}, {"device_token": "TOKEN"}, {}):
        # привязка мастер-ключом, обычный запрос токеном и обмен кода без них
        with pytest.raises(RuntimeError, match="должен начинаться с https://"):
            api.request(url, "POST", "/admin/device/redeem.json",
                        {"code": "ОДНОРАЗОВЫЙ", "device_id": "x"},
                        opener=opener, **creds)
    assert opener.calls == [], "запрос ушёл без https"


def test_empty_address_says_where_to_enter_it():
    opener = _opener(lambda req, kw: b"{}")
    for url in ("", "   ", None):
        with pytest.raises(RuntimeError, match="Не задан адрес сервера"):
            api.request(url, "GET", "/admin/stats", device_token="t", opener=opener)
    assert opener.calls == []


@pytest.mark.parametrize("url", ["https://hard.example", "HTTPS://Hard.Example/",
                                 "https://hard.example:8443", "http://127.0.0.1:8080",
                                 "http://localhost:1", "http://LOCALHOST", "http://[::1]:5000"])
def test_https_and_own_machine_pass(url):
    opener = _opener(lambda req, kw: b'{"ok": true}')
    assert api.request(url, "GET", "/admin/stats", device_token="t",
                       opener=opener) == {"ok": True}
    assert len(opener.calls) == 1


def test_https_profiles_are_untouched(tmp_path):
    """Контуры с https работают как раньше; http-контур отказывает сам, не
    трогая ни себя, ни соседей, ни файл настроек."""
    path = tmp_path / "настройки пульта.json"
    Settings({"profiles": {"Прод": {"url": "https://prod.example", "device_token": "P"},
                           "Компания": {"url": "http://10.0.0.5", "device_token": "K"}},
              "active": "Прод"}, path).save()
    before = path.read_bytes()
    settings = Settings.load(path)

    opener = _opener(lambda req, kw: b'{"ok": true}')
    prod = active_profile(settings)
    assert api.request(prod["url"], "GET", "/admin/stats",
                       device_token=prod["device_token"], opener=opener) == {"ok": True}
    assert opener.calls[0][0].get_header("X-device-token") == "P"

    company = settings["profiles"]["Компания"]
    with pytest.raises(RuntimeError, match="https://"):
        api.request(company["url"], "GET", "/admin/stats",
                    device_token=company["device_token"], opener=opener)
    assert len(opener.calls) == 1
    assert path.read_bytes() == before


def test_redirect_to_plain_http_is_not_followed():
    """urllib повторяет по новому адресу ВСЕ заголовки, ключ тоже."""
    handler = api._NoDowngradeRedirect()
    req = urllib.request.Request("https://hard.example/admin/stats",
                                 headers={"X-Admin-Key": "MASTER"})
    body = io.BytesIO(b"")
    with pytest.raises(RuntimeError, match="https://"):
        handler.redirect_request(req, body, 302, "Found", {},
                                 "http://hard.example/admin/stats")
    assert body.closed
    moved = handler.redirect_request(req, io.BytesIO(b""), 302, "Found", {},
                                     "https://hard.example/v2/stats")
    assert moved.full_url == "https://hard.example/v2/stats"
    # и этот обработчик стоит в двери, которой ходит пульт
    assert any(isinstance(h, api._NoDowngradeRedirect)
               for h in api._SECURE_OPENER.handlers)


def test_redirect_downgrade_end_to_end(monkeypatch):
    """Живой urllib: свой сервер (127.0.0.1) отвечает «иди на http://…» —
    ключ получает только он, по чужому адресу запроса нет. Прокси выключены,
    чтобы даже поломка не увела запрос за пределы машины."""
    seen = []

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — имя задаёт http.server
            seen.append(self.headers.get("X-Admin-Key"))
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/steal")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    monkeypatch.setattr(api, "_SECURE_OPENER", urllib.request.build_opener(
        urllib.request.ProxyHandler({}), api._NoDowngradeRedirect()))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RuntimeError, match="https://"):
            api.request(f"http://127.0.0.1:{server.server_address[1]}", "GET",
                        "/admin/stats", key="MASTER", timeout=5)
    finally:
        server.shutdown()
        server.server_close()
    assert seen == ["MASTER"]


def _no_network(*_args, **_kwargs):
    raise AssertionError("запрос ушёл без https")


def test_helper_scripts_refuse_plain_http_before_sending(monkeypatch):
    """Командные помощники пульта шлют ключ и токен мимо `request` — у каждого
    своя проверка до запроса."""
    import core.api as core_api        # помощники берут `core`, а не `pult.core`
    import daily_cycle
    import repair_comments
    import repair_false_enrichment
    import restore_from_bot

    monkeypatch.setattr(core_api, "_SECURE_OPENER", SimpleNamespace(open=_no_network))
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    calls = [
        lambda: repair_comments.api_export("http://hard.example", "MASTER"),
        lambda: repair_false_enrichment._request_json(
            "http://hard.example/admin/export.json", "MASTER", device_token="T"),
        lambda: daily_cycle.api({"url": "http://hard.example", "device_token": "T"},
                                "GET", "/admin/export.json"),
        lambda: restore_from_bot.fetch_rows({"url": "http://hard.example",
                                             "key": "MASTER"}, print),
    ]
    for call in calls:
        with pytest.raises(RuntimeError, match="https://"):
            call()
    with pytest.raises(RuntimeError, match="Не задан адрес сервера"):
        daily_cycle.api({"url": ""}, "GET", "/admin/export.json")
