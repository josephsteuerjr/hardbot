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

# Куда секрет можно отдать по открытому http: только на эту же машину — там
# нет сети, которую подслушивают. Всё остальное — только https.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

NO_URL_TEXT = ("Не задан адрес сервера — впишите его в пульте, раздел "
               "«Подключение» (вид: https://сервер).")
NOT_HTTPS_TEXT = ("Адрес сервера должен начинаться с https://. По такому адресу "
                  "мастер-ключ, токен устройства и код привязки ушли бы по сети "
                  "открытым текстом — пульт ничего не отправил. Исправьте адрес "
                  "в пульте, раздел «Подключение» (http годится только для "
                  "127.0.0.1 и localhost).")


def require_https(url: str) -> None:
    """Отказ словами ДО запроса, если секрет поехал бы по сети открытым текстом.

    Мастер-ключ, токен устройства и одноразовый код привязки едут заголовками и
    телом обычного HTTP-запроса; по http их читает любой, кто видит сеть (чужой
    Wi-Fi, прокси). Опечатка «http://» в адресе контура молча отдавала бы ключ —
    поэтому отказ ещё до того, как собран первый байт запроса. Исключение —
    своя машина (127.0.0.1, localhost, ::1): там сети нет.
    """
    text = (url or "").strip()
    if not text:
        raise RuntimeError(NO_URL_TEXT)
    try:
        parts = urllib.parse.urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise RuntimeError(NOT_HTTPS_TEXT) from None
    if parts.scheme == "https" and host:
        return
    if parts.scheme == "http" and host in LOOPBACK_HOSTS:
        return
    raise RuntimeError(NOT_HTTPS_TEXT)


class _NoDowngradeRedirect(urllib.request.HTTPRedirectHandler):
    """Переадресация не уводит запрос с секретом на http.

    urllib повторяет по новому адресу ВСЕ заголовки, в том числе ключ и токен.
    Сервер «Харда» админ-ручки не переадресует; ответ «иди на http://…» может
    дать только чья-то настройка перед ним (прокси, балансировщик) — и ключ по
    такому ответу не поедет.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            require_https(newurl)
        except RuntimeError:
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SECURE_OPENER = urllib.request.build_opener(_NoDowngradeRedirect())


def open_secure(req: urllib.request.Request, timeout: float):
    """`urlopen` для запросов с секретом: адрес и переадресация — только https
    (или своя машина). Им ходят и командные помощники пульта."""
    require_https(req.full_url)
    return _SECURE_OPENER.open(req, timeout=timeout)


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


GZIP_BODY_FROM = 64 * 1024     # байт JSON, с которых тело запроса сжимается


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
    Адрес не https (и не своя машина) — отказ до запроса (`require_https`):
    это касается любого запроса, и привязки по коду тоже — код в теле.
    """
    require_https(base_url)
    urlopen = opener or open_secure
    url = base_url.strip().rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    # Крупное тело — сжатым (опыт 30.09.2026): реестр на 50 тыс. строк — это
    # 45–57 МБ JSON, и предпросмотр с применением гнали его по туннелю дважды;
    # в gzip — 3–6 МБ. aiohttp распаковывает тело сам и лимит client_max_size
    # меряет по распакованному — «бомба» не пройдёт.
    gz = data is not None and len(data) > GZIP_BODY_FROM
    if gz:
        data = gzip.compress(data, 6)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=auth_headers(key, device_token))
    if gz:
        req.add_header("Content-Encoding", "gzip")
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


def download(base_url: str, path: str, *, device_token: str = "", timeout: float = 90,
             opener=None) -> bytes:
    """Файл с admin-API (книга Excel для рассылки): те же заголовки и та же
    проверка https, что у `request`, тело — байтами."""
    require_https(base_url)
    urlopen = opener or open_secure
    req = urllib.request.Request(base_url.strip().rstrip("/") + path, method="GET",
                                 headers=auth_headers("", device_token))
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError(DEVICE_REVOKED_TEXT) from e
        if e.code == 404:
            raise RuntimeError(f"На сервере нет {path.split('?')[0]} — сервер старой версии?") from e
        raise RuntimeError(f"Сервер ответил {e.code} на {path.split('?')[0]}") from e
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            raise RuntimeError(CERT_BAD_TEXT) from e
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
