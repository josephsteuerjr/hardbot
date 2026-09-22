"""Настройки пульта руководителя: адрес «Харда», токен доступа и имя владельца.

Файл отдельный от настроек пульта аналитика и лежит в папке своего приложения:
у аналитика в его настройках админ-ключ сервера, и класть рядом токен
руководителя — значит однажды скопировать человеку не тот файл. Устроено всё
ровно так же, как там (JSON, тот же порядок «загрузили — поправили — сохранили»),
чтобы человек, знающий один пульт, узнавал и второй.

ТОКЕН ЖИВЁТ ТОЛЬКО ЗДЕСЬ. Ни в адресе страницы, ни в JSON, который уходит в
браузер (и тем более в тоннель), его нет: адрес тоннеля видит cloudflare, а
история браузера переживает и пульт, и сам тоннель.

Серверов может быть несколько (прод и контур компании): известные адреса лежат
в `servers`, у каждого свой токен. Вставленная ссылка сама называет сервер —
пульт заводит запись под этот адрес и делает его активным; «Выйти из пульта»
забывает токен только активного.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "настройки пульта руководителя.json"

# Ключи файла:
#   url      — активный сервер («https://…»); берётся из ссылки входа;
#   servers  — известные серверы: {адрес: {token, days, manager}};
#   days     — на сколько суток выдан токен (для строки «ссылка действует до …»);
#   manager  — {name, scope}: кем пульт видел владельца в прошлый раз. Нужен,
#              чтобы экран «Я и телефон» не был пустым до первого ответа сервера.


def active_url(data: dict) -> str:
    return str(data.get("url") or "").rstrip("/")


def ensure_servers(data: dict) -> dict:
    """Гарантировать `servers` и осмысленный `url`. Плоский старый файл (один
    сервер) переезжает в карту серверов при первой загрузке."""
    servers = data.get("servers")
    if not isinstance(servers, dict):
        servers = {}
    if "servers" not in data and data.get("url"):
        flat = {k: data[k] for k in ("token", "days", "manager") if k in data}
        if flat:
            servers[active_url(data)] = flat
        for k in ("token", "days", "manager"):
            data.pop(k, None)
    servers = {str(u).rstrip("/"): (v if isinstance(v, dict) else {})
               for u, v in servers.items() if str(u).rstrip("/")}
    data["servers"] = servers
    url = active_url(data)
    if url:
        servers.setdefault(url, {})
    elif servers:
        with_token = [u for u, v in servers.items() if v.get("token")]
        data["url"] = with_token[0] if with_token else next(iter(servers))
    return data


def server_entry(data: dict) -> dict:
    """Запись активного сервера (токен, days, manager)."""
    ensure_servers(data)
    url = active_url(data)
    if not url:
        return {}
    return data["servers"].setdefault(url, {})


class Settings(dict):
    """Словарь, помнящий свой файл. Тот же приём, что в пульте аналитика."""

    def __init__(self, data: dict | None = None, path: Path = SETTINGS_PATH) -> None:
        super().__init__(data or {})
        self.path = Path(path)
        ensure_servers(self)

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
        """Записать. Ошибку не поднимаем: пульт обязан работать и с диска,
        на который не пускают, — просто в следующий раз спросит ссылку снова."""
        try:
            self.path.write_text(
                json.dumps(dict(self), ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            return False
        # Файл с токеном не должен быть общим для всех пользователей машины.
        # На Windows прав POSIX нет — там это тихо ничего не делает.
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return True

    def forget(self) -> bool:
        """Забыть доступ АКТИВНОГО сервера («Выйти из пульта»). Адрес остаём:
        человек почти всегда возвращается на тот же «Хард», и следующая ссылка
        войдёт без вопросов. Токены ДРУГИХ серверов не трогаем."""
        entry = server_entry(self)
        for key in ("token", "days", "manager"):
            entry.pop(key, None)
        return self.save()


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    return Settings.load(path)
