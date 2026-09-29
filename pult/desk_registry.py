"""Реестр и обогащение для пульта аналитика на движке Hélène: чтение книг,
предпросмотр и загрузка реестра, обогащение личных книг живым Excel.
Маршруты зовёт `pult.desk_backend.route`."""

from __future__ import annotations


def route(path: str, body: dict, *, admin, start_job, settings):
    raise KeyError(f"нет такого маршрута: {path}")
