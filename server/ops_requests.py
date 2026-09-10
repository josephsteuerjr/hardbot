"""Контракт между панелью инженера и помощником на хосте.

Панель живёт в контейнере и не имеет доступа к докеру — это сделано намеренно.
Чтобы контейнер мог перезапустить сам себя или снять копию базы, ему пришлось бы
отдать ``/var/run/docker.sock``, а это root на ВСЁМ сервере, включая соседние
проекты. Вместо этого панель кладёт заявку в общий каталог, а помощник на хосте
(``deploy/ops-agent.sh``) её исполняет — и умеет ровно шесть действий, ни одним
больше. Ни строки из заявки не попадают в оболочку: действие выбирается из
таблицы, параметры проверяются шаблоном.

Каталог общий через тот же том, где лежит база:

    data/ops/requests/<id>.json    заявка (пишет панель)
    data/ops/results/<id>.json     результат (пишет помощник)

Идентификатор заявки — случайный, а не по счётчику: иначе панель могла бы
прочитать чужой результат, просто угадав номер.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

# Шесть действий, и это ВЕСЬ список. Расширять его — отдельное решение владельца,
# а не побочный эффект удобства: каждое новое действие исполняется на хосте с
# правами, которых у контейнера нет.
ACTIONS = ("doctor", "logs", "backup", "restore", "restart", "update")

# Единицы, к которым действие применимо.
UNITS = ("core", "stt", "gps")

# Имя копии — это метка времени, которую делает backup.sh, и ничего кроме.
# Проверяем шаблоном, чтобы «../../etc» не приехало в путь.
BACKUP_NAME = re.compile(r"^[0-9]{8}T[0-9]{6}$")

_RESULT_MAX = 256 * 1024      # хвост вывода, а не весь журнал


def ops_dir() -> Path:
    """Каталог обмена внутри тома данных."""
    base = Path(os.getenv("APP_DATA") or "/data") / "ops"
    return base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate(action: str, params: dict | None) -> dict:
    """Проверить заявку ДО записи. Возвращает нормализованные параметры.

    Проверяем здесь, а не только в помощнике: заявка, которую нельзя исполнить,
    не должна вообще появляться на диске — иначе в каталоге копится мусор, по
    которому потом не понять, что было настоящей попыткой.
    """
    if action not in ACTIONS:
        raise ValueError(f"неизвестное действие: {action}")
    params = dict(params or {})
    clean: dict[str, str] = {}

    unit = str(params.get("unit") or "core").strip()
    if unit not in UNITS:
        raise ValueError(f"неизвестная единица: {unit}")
    clean["unit"] = unit

    if action == "restore":
        name = str(params.get("backup") or "").strip()
        if not BACKUP_NAME.fullmatch(name):
            raise ValueError("имя копии должно быть меткой времени вида 20260910T031500")
        clean["backup"] = name
        clean["photos"] = "1" if params.get("photos") else "0"
        if unit != "core":
            raise ValueError("восстановление есть только у единицы core")

    if action == "logs":
        # Число строк проверяем здесь И в помощнике: оно уходит аргументом
        # докеру, и «сколько угодно» означало бы выгрузку всего журнала разом.
        try:
            lines = int(params.get("lines") or 200)
        except (TypeError, ValueError):
            raise ValueError("число строк должно быть числом") from None
        clean["lines"] = str(max(10, min(2000, lines)))

    if action == "backup":
        clean["db_only"] = "1" if params.get("db_only") else "0"
        if unit != "core":
            raise ValueError("копия базы есть только у единицы core")

    return clean


def submit(action: str, params: dict | None = None, *, who: str = "") -> str:
    """Положить заявку и вернуть её идентификатор."""
    clean = validate(action, params)
    request_id = secrets.token_urlsafe(12)
    payload = {
        "schema": "hard.ops.request.v1",
        "id": request_id,
        "action": action,
        "params": clean,
        "who": str(who or "")[:80],
        "created_at": _now(),
    }
    requests = ops_dir() / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    tmp = requests / f".{request_id}.part"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    # Переименование атомарно: помощник никогда не увидит недописанный файл.
    tmp.replace(requests / f"{request_id}.json")
    return request_id


def result(request_id: str) -> dict | None:
    """Результат заявки или None, пока помощник её не исполнил.

    Статус и вывод лежат РАЗНЫМИ файлами: status.json появляется последним, а
    текст — в соседнем .out. Так помощнику не приходится паковать чужой журнал в
    JSON-строку прямо в баше; один раз это уже молча съело весь вывод.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(request_id or "")):
        return None
    results = ops_dir() / "results"
    path = results / f"{request_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8")[:_RESULT_MAX])
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    text_path = results / f"{request_id}.out"
    try:
        data["output"] = text_path.read_bytes()[:_RESULT_MAX].decode("utf-8", "replace")
    except OSError:
        data.setdefault("output", "")
    return data


def pending() -> list[dict]:
    """Заявки, которые помощник ещё не забрал. Пусто и когда каталога нет."""
    requests = ops_dir() / "requests"
    if not requests.is_dir():
        return []
    out = []
    for path in sorted(requests.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def inventory() -> dict:
    """Опись хозяйства, которую кладёт помощник: единицы, копии, место на диске.

    Панель видит из контейнера ровно один каталог — свои данные. Списка копий и
    соседних единиц оттуда не видно вовсе, поэтому опись приходит с хоста.
    """
    path = ops_dir() / "inventory.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")[:_RESULT_MAX])
    except (OSError, ValueError):
        return {"units": [], "disk_free": "", "at": ""}
    return data if isinstance(data, dict) else {"units": [], "disk_free": "", "at": ""}


def agent_alive() -> dict:
    """Жив ли помощник на хосте.

    Он касается файла «heartbeat» каждый круг. Без этого панель показывала бы
    заявки, которые никто никогда не исполнит, и выглядело бы это как зависшая
    операция, а не как «помощник не установлен».
    """
    beat = ops_dir() / "heartbeat"
    try:
        stamp = beat.stat().st_mtime
    except OSError:
        return {"alive": False, "seen": "", "reason": "помощник на хосте не запускался"}
    age = max(0.0, datetime.now(timezone.utc).timestamp() - stamp)
    seen = datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")
    if age > 120:
        return {"alive": False, "seen": seen,
                "reason": f"помощник молчит {int(age)} с — проверьте службу hard-ops-agent"}
    return {"alive": True, "seen": seen, "reason": ""}
