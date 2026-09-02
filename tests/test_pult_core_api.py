"""Клиент admin-API пульта: заголовки, дружелюбные ошибки, TLS-fallback."""

from __future__ import annotations

import io
import json
import ssl
import urllib.error

import pytest

from pult.core import api


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


def test_self_signed_certificate_falls_back_to_unverified_tls():
    def script(req, kw):
        if "context" not in kw:
            raise urllib.error.URLError(ssl.SSLCertVerificationError("self signed"))
        return b'{"ok": 1}'
    opener = _opener(script)
    assert api.request("https://h", "GET", "/a", key="k", opener=opener) == {"ok": 1}
    assert len(opener.calls) == 2
    assert isinstance(opener.calls[1][1]["context"], ssl.SSLContext)


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
