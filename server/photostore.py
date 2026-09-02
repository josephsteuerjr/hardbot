"""Файловое хранилище фото визитов: PHOTO_DIR/<contract_id>/<op|ts>.jpg.

Пишет web.py (/api/photo из миниаппа), читает дайджест руководителя (альбомы
в чат бота). В БД — только счётчик clients.photos_count; принадлежность фото
к визиту не хранится, поэтому «фото за день» отбираем по mtime файла (момент
загрузки на сервер: миниапп шлёт фото сразу при съёмке, оффлайн-очередь может
дослать позже — тогда фото попадёт в день досыла, это осознанный компромисс).
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

PHOTO_DIR = Path(os.getenv("APP_DATA") or os.getenv("HARDBOT_DATA") or Path(__file__).parent) / "photos"


def contract_photos(contract_id: str, root: Path | None = None) -> list[Path]:
    """Все фото договора, новые сверху. Времянки идущей загрузки (~…part,
    web._photo пишет атомарно через rename) — не фото, в альбомы не попадают.
    root — переопределение корня (тесты подменяют web.PHOTO_DIR)."""
    d = Path(PHOTO_DIR if root is None else root) / str(contract_id)
    try:
        files = [p for p in d.iterdir() if p.is_file()
                 and not p.name.startswith("~") and not p.name.endswith(".part")]
    except OSError:  # папки нет = фото нет
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def photo_key(path: Path, root: Path | None = None) -> str:
    """«КД/имя файла» — тот же ключ, что в photo_geo.file.

    Считается ОТ КОРНЯ (relative_to), а не по parent.name: для КД со слэшем
    в номере каталог вложенный, и parent.name дал бы другую строку, чем
    записанная в photo_geo — снимок двоился бы в выдаче."""
    base = Path(PHOTO_DIR if root is None else root)
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return f"{path.parent.name}/{path.name}"


# Ключ = 1..4 сегментов (КД со слэшем даёт вложенный каталог), каждый без
# служебных символов и не «.»/«..».
_KEY_RE = re.compile(r"^[^/\\\x00]{1,120}(?:/[^/\\\x00]{1,120}){1,3}$")


def photo_path(key: str, root: Path | None = None) -> Path | None:
    """«КД/имя» → путь внутри PHOTO_DIR. None на всё подозрительное.
    Единственный путь из внешнего ввода к файлу на диске."""
    if not isinstance(key, str) or not _KEY_RE.match(key):
        return None
    parts = key.split("/")
    if any(seg in (".", "..") for seg in parts):
        return None
    name = parts[-1]
    if name.startswith("~") or name.endswith(".part"):
        return None
    base = Path(PHOTO_DIR if root is None else root)
    target = base.joinpath(*parts)
    if any(p.is_symlink() for p in (target, *target.parents)
           if str(p).startswith(str(base))):
        return None
    try:
        resolved = target.resolve()
        base_resolved = base.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(base_resolved):
        return None
    return resolved


def person_photos(contracts) -> list[Path]:
    """Фото человека — по всем его договорам, новые сверху."""
    out: list[Path] = []
    for c in contracts:
        out.extend(contract_photos(c["contract_id"]))
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def day_photos(contracts, on_date: str) -> list[Path]:
    """Фото человека, загруженные в дату on_date (ISO, локальное время сервера)."""
    return [p for p in person_photos(contracts)
            if date.fromtimestamp(p.stat().st_mtime).isoformat() == on_date]


def photos_between(contracts, date_from: str, date_to: str) -> list[Path]:
    """Фото человека за диапазон дат [date_from..date_to] (ISO, сравнение строк)."""
    return [p for p in person_photos(contracts)
            if date_from <= date.fromtimestamp(p.stat().st_mtime).isoformat() <= date_to]
