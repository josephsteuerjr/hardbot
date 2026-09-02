"""Клиент admin-API пульта. Дружелюбные ошибки вместо трейсбеков.

Каждый день ходим ТОКЕНОМ устройства; мастер-ключ идёт только если устройство
ещё не привязано (или его отозвали и надо привязать заново).
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

DEVICE_REVOKED_TEXT = ("Устройство не принято: токен отозван — привяжи заново "
                       "мастер-ключом")
BAD_KEY_TEXT = "Неверный мастер-ключ"


def auth_headers(key: str = "", device_token: str = "") -> dict[str, str]:
    return {"X-Device-Token": device_token} if device_token else {"X-Admin-Key": key}


def host_of(url: str) -> str:
    """Хост для подписи в рейке: «—», если адрес не разбирается."""
    try:
        return urllib.parse.urlsplit(url).netloc or "—"
    except ValueError:
        return "—"


def request(base_url: str, method: str, path: str, body: dict | None = None, *,
            key: str = "", device_token: str = "", timeout: float = 20,
            opener=None):
    """Один запрос к admin-API.

    401 — два разных текста (токен отозван / неверный ключ); 404 — «сервер
    старой версии?»; 409 с JSON `{"error": …}` возвращается как есть — это
    валидационный конфликт импорта, сервер гарантированно ничего не записал, и
    экрану нужен структурированный отчёт, а не безликий HTTP 409.
    Самоподписанный или прокси-сертификат — повтор без проверки TLS.
    """
    urlopen = opener or urllib.request.urlopen
    url = (base_url or "").rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=auth_headers(key, device_token))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        try:
            with urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                ctx = ssl._create_unverified_context()  # noqa: S323 — осознанно
                with urlopen(req, timeout=timeout, context=ctx) as r:
                    return json.load(r)
            raise
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError(DEVICE_REVOKED_TEXT if device_token else BAD_KEY_TEXT) from e
        if e.code == 404:
            raise RuntimeError(f"На сервере нет {path} — сервер старой версии?") from e
        if e.code == 409:
            try:
                payload = json.load(e)
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict) and payload.get("error"):
                return payload
        raise RuntimeError(f"Сервер ответил {e.code} на {path}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Нет связи с сервером ({e.reason})") from e


class AdminApi:
    """Адрес и учётные данные в одном месте; `call` — как прежний `Pult.api`."""

    def __init__(self, url: str = "", key: str = "", device_token: str = "") -> None:
        self.url = url
        self.key = key
        self.device_token = device_token

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 20):
        return request(self.url, method, path, body, key=self.key,
                       device_token=self.device_token, timeout=timeout)
