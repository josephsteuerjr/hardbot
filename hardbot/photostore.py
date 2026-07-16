"""Файловое хранилище фото визитов: PHOTO_DIR/<contract_id>/<op|ts>.jpg.

Пишет web.py (/api/photo из миниаппа), читает дайджест руководителя (альбомы
в чат бота). В БД — только счётчик clients.photos_count; принадлежность фото
к визиту не хранится, поэтому «фото за день» отбираем по mtime файла (момент
загрузки на сервер: миниапп шлёт фото сразу при съёмке, оффлайн-очередь может
дослать позже — тогда фото попадёт в день досыла, это осознанный компромисс).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

PHOTO_DIR = Path(os.getenv("HARDBOT_DATA", Path(__file__).parent)) / "photos"


def contract_photos(contract_id: str) -> list[Path]:
    """Все фото договора, новые сверху."""
    d = PHOTO_DIR / str(contract_id)
    try:
        files = [p for p in d.iterdir() if p.is_file()]
    except OSError:  # папки нет = фото нет
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


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
