"""Настройки пульта — тот же файл и те же ключи, что и раньше, чтобы у людей
ничего не потерялось при обновлении.

Файл: `pult/настройки пульта.json`. Ключи: `url`, `key` (только пока
устройство не привязано), `device_token`, `sec` (открытый раздел),
`scatterBase`, `scatterSource`, `enrich_file`.
"""

from __future__ import annotations

import json
from pathlib import Path

from .bootstrap import APP_DIR

SETTINGS_PATH = APP_DIR / "настройки пульта.json"


class Settings(dict):
    """Словарь с памятью о своём файле."""

    def __init__(self, data: dict | None = None, path: Path = SETTINGS_PATH) -> None:
        super().__init__(data or {})
        self.path = Path(path)

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
        try:
            self.path.write_text(
                json.dumps(dict(self), ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            return False
        return True


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    return Settings.load(path)
