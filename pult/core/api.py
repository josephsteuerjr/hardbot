"""Клиент admin-API пульта. Дружелюбные ошибки вместо трейсбеков.

Каждый день ходим ТОКЕНОМ устройства; мастер-ключ идёт только если устройство
ещё не привязано (или его отозвали и надо привязать заново).
"""

from __future__ import annotations

import gzip
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

DEVICE_REVOKED_TEXT = ("Устройство не принято: токен отозван — привяжи заново "
                       "мастер-ключом")
BAD_KEY_TEXT = "Неверный мастер-ключ"


def auth_headers(key: str = "", device_token: str = "") -> dict[str, str]:
    """Чем представляемся серверу.

    К токену обязательно прикладывается отпечаток машины: сервер сверяет его на
    КАЖДОМ запросе, а не только при выдаче. Без этого файл настроек, скопированный
    на другую машину, продолжал бы работать — а это обычный JSON на диске.
    """
    if device_token:
        from .devices import device_fingerprint
        return {"X-Device-Token": device_token, "X-Device-Id": device_fingerprint()}
    return {"X-Admin-Key": key}


CERT_BAD_TEXT = ("Сертификат сервера не прошёл проверку — эта сеть подменяет "
                 "соединение (чужой Wi-Fi, прокси с подменой). Пульт ничего не "
                 "отправил: ни ключа, ни токена. Подключитесь к другой сети.")


def _read_json(resp):
    """Тело ответа как JSON, в том числе сжатое gzip: крупные ответы (выгрузка
    базы — 46 МБ) сервер сжимает, если его об этом попросили."""
    raw = resp.read()
    headers = getattr(resp, "headers", None) or {}
    if (headers.get("Content-Encoding") or "").lower() == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw)


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
    Сертификат не прошёл проверку — отказ словами, БЕЗ повтора «без проверки»:
    такой повтор отдал бы мастер-ключ или токен устройства тому, кто подменил
    связь. Серверы живут под настоящими сертификатами (Let's Encrypt).
    """
    urlopen = opener or urllib.request.urlopen
    url = (base_url or "").rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=auth_headers(key, device_token))
    # Просим сжатие: контур компании опубликован через туннель, и несжатая
    # выгрузка базы шла по нему минуты вместо секунд.
    req.add_header("Accept-Encoding", "gzip")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        try:
            with urlopen(req, timeout=timeout) as r:
                return _read_json(r)
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                raise RuntimeError(CERT_BAD_TEXT) from e
            raise
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError(DEVICE_REVOKED_TEXT if device_token else BAD_KEY_TEXT) from e
        if e.code == 404:
            raise RuntimeError(f"На сервере нет {path} — сервер старой версии?") from e
        if e.code == 409:
            try:
                payload = _read_json(e)
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
