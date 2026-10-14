"""Настройки пульта — тот же файл, что и раньше; контуры серверов добавились.

Файл: `pult/настройки пульта.json`. Ключи:

- `profiles` — контуры серверов: `{имя: {url, device_token}}`. У каждого
  сервера свой адрес и свой токен устройства, переключение не перезатирает
  чужой контур. `key` (мастер-ключ) в профиле не хранится: он нужен ровно на
  одну операцию привязки и живёт, пока окно открыто.
- `active` — имя контура, с которым пульт работает сейчас.
- Плоские `url` / `device_token` остаются в файле как зеркало АКТИВНОГО
  контура: по ним работают командные помощники (`daily_cycle`,
  `restore_from_bot`), которым контуры ни к чему.
- `sec` (открытый раздел), `scatterBase`, `scatterSource`, `enrich_file`,
  `miniapp` — как раньше, общие на все контуры.

Старый плоский файл переезжает в контур «Прод» при первой загрузке; адрес
второго контура в исходники не вшивается — его добавляют руками в «Подключении».
"""

from __future__ import annotations

import json
import os
import base64
import copy
from pathlib import Path

from .bootstrap import APP_DIR

# HARD_PULT_SETTINGS — другой файл настроек (дев-стенд пультов, снимки): так
# проверка окна не ходит в рабочий контур ни на чтение, ни тем более на запись.
SETTINGS_PATH = (Path(os.environ["HARD_PULT_SETTINGS"]) if os.environ.get("HARD_PULT_SETTINGS")
                 else APP_DIR / "настройки пульта.json")

# Имя контура, в которое переезжает плоский старый файл. Адресов серверов
# в коде нет сознательно: сканер пакета для ИТ ищет именно их.
DEFAULT_PROFILE = "Прод"

TOKEN_PREFIX = "dpapi-v1:"


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    """Windows user DPAPI, available in the frozen runtime without extra DLL packages."""
    if os.name != "nt":
        raise OSError("Windows DPAPI недоступен")
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ctypes.WinError(ctypes.get_last_error())
    free = ctypes.WinDLL("kernel32").LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        free(outgoing.data)


def decode_tokens(data: dict) -> dict:
    """Accept old plaintext settings and protected settings without re-enrollment."""
    result = copy.deepcopy(data)
    records = [result, *result.get("profiles", {}).values()] if isinstance(result.get("profiles", {}), dict) else [result]
    for record in records:
        if not isinstance(record, dict):
            continue
        token = record.get("device_token")
        if isinstance(token, str) and token.startswith(TOKEN_PREFIX):
            record["device_token"] = _dpapi(base64.b64decode(token[len(TOKEN_PREFIX):], validate=True), decrypt=True).decode("utf-8")
    return result


def ensure_profiles(data: dict) -> dict:
    """Гарантировать `profiles` и `active`. Возвращает те же данные.

    Плоский `url`/`device_token` переезжают в контур «Прод» один раз — при
    первом запуске новой версии. Дальше правит только profiles; плоские ключи
    остаются зеркалом активного контура (см. `Settings.save`).
    """
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    if "profiles" not in data:
        profiles[DEFAULT_PROFILE] = {
            "url": str(data.get("url") or ""),
            "device_token": str(data.get("device_token") or ""),
        }
    profiles = {str(k): (v if isinstance(v, dict) else {}) for k, v in profiles.items()}
    if not profiles:
        profiles[DEFAULT_PROFILE] = {"url": "", "device_token": ""}
    data["profiles"] = profiles
    if not isinstance(data.get("active"), str) or data["active"] not in profiles:
        names = list(profiles)
        data["active"] = (DEFAULT_PROFILE if DEFAULT_PROFILE in names else names[0])
    return data


def active_profile(data: dict) -> dict:
    """Словарь активного контура (создавая структуру, если её ещё нет)."""
    ensure_profiles(data)
    return data["profiles"][data["active"]]


class Settings(dict):
    """Словарь с памятью о своём файле."""

    def __init__(self, data: dict | None = None, path: Path = SETTINGS_PATH) -> None:
        incoming = data or {}
        self.protect_tokens = (os.name == "nt" or os.environ.get("HARD_PULT_PROTECT_TOKENS") == "1"
                               or incoming.get("token_protection") == "windows-dpapi")
        super().__init__(decode_tokens(incoming))
        self.path = Path(path)
        ensure_profiles(self)

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "Settings":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        return cls(data, path)

    def save(self) -> bool:
        # Мастер-ключа на диске не бывает вовсе — ни в профиле, ни в зеркале.
        for profile in self.get("profiles", {}).values():
            profile.pop("key", None)
        self.pop("key", None)
        # Зеркало активного контура для командных помощников без контуров.
        ensure_profiles(self)
        current = active_profile(self)
        self["url"] = current.get("url", "")
        if current.get("device_token"):
            self["device_token"] = current.get("device_token")
        else:
            self.pop("device_token", None)
        try:
            payload = copy.deepcopy(dict(self))
            if self.protect_tokens:
                payload["token_protection"] = "windows-dpapi"
                for record in [payload, *payload["profiles"].values()]:
                    token = record.get("device_token")
                    if token:
                        record["device_token"] = TOKEN_PREFIX + base64.b64encode(_dpapi(str(token).encode("utf-8"))).decode("ascii")
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            return False
        return True


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    return Settings.load(path)
