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
from pathlib import Path

from .bootstrap import APP_DIR

SETTINGS_PATH = APP_DIR / "настройки пульта.json"

# Имя контура, в которое переезжает плоский старый файл. Адресов серверов
# в коде нет сознательно: сканер пакета для ИТ ищет именно их.
DEFAULT_PROFILE = "Прод"


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
        super().__init__(data or {})
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
            self.path.write_text(
                json.dumps(dict(self), ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            return False
        return True


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    return Settings.load(path)
