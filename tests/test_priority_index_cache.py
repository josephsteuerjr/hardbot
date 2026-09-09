"""Открытие приложения не должно ждать пересчёта приоритета.

Что здесь охраняется. Индекс приоритета кэшируется по версии данных, а версия
поднимается на КАЖДУЮ запись в карточку — в том числе на автосохранение
черновика, которое приложение шлёт само раз в 20 секунд у любого выездника.
На боевом портфеле (14 тысяч договоров) пересчёт стоит около двух секунд, и
раньше он шёл прямо в обработчике `/api/bootstrap`. Цикл событий в aiohttp
один на всех, поэтому эти две секунды ждал не только тот, кто открыл
приложение, а вообще все запросы сервера.

Замер 09.09.2026 на рабочей базе: холодный расчёт 1900 мс (1161 мс выборка +
806 мс счёт), тёплый — 0,03 мс. В журнале за день у семи человек стартовый
запрос занимал 1,8–5,7 с, у остальных 30–120 мс — разница только в том, успел
ли кто-то сохранить карточку между заходами.

Решение: отдавать предыдущий индекс немедленно, а свежий считать в отдельном
потоке. Приоритет отстаёт максимум на один пересчёт; список от этого не
ломается — порядок обновится на следующем открытии.
"""
import asyncio

import pytest

import scoring
import web


@pytest.fixture(autouse=True)
def _clean_cache():
    scoring._PRIORITY_CACHE.clear()
    scoring._PRIORITY_LAST.clear()
    web._priority_refresh_task = None
    yield
    scoring._PRIORITY_CACHE.clear()
    scoring._PRIORITY_LAST.clear()
    web._priority_refresh_task = None


def _fake_items(n=3):
    return [{"rep": str(i), "client": f"К{i}", "birth": "1990-01-01",
             "city": "Худжанд", "debt": 1000 * (i + 1), "visits": 0,
             "result": "", "location": "", "who": ""} for i in range(n)]


def test_fresh_index_is_reused(monkeypatch):
    calls = []
    monkeypatch.setattr(scoring, "_items_from_db",
                        lambda: calls.append(1) or _fake_items())
    first = scoring.priority_index()
    second = scoring.priority_index()
    assert first is second and len(calls) == 1
    assert scoring.priority_index_is_fresh()


def test_stale_index_is_served_instead_of_recomputing(monkeypatch):
    """Главное свойство: после чужой записи открытие приложения не считает.

    Без этого каждое автосохранение черновика у одного выездника заставляло
    следующего ждать полного пересчёта по всей базе.
    """
    import db

    calls = []
    monkeypatch.setattr(scoring, "_items_from_db",
                        lambda: calls.append(1) or _fake_items())
    warm = scoring.priority_index()
    assert len(calls) == 1

    db._bump_version()                       # чья-то запись в карточку
    assert not scoring.priority_index_is_fresh()

    stale = scoring.priority_index(allow_stale=True)
    assert stale == warm
    assert len(calls) == 1, "устаревший индекс не должен вызывать пересчёт"

    # Без allow_stale поведение прежнее: считаем честно.
    fresh = scoring.priority_index()
    assert len(calls) == 2 and fresh == warm


def test_first_ever_call_computes_even_with_allow_stale(monkeypatch):
    """Предыдущего индекса ещё нет — отдавать нечего, приходится считать."""
    calls = []
    monkeypatch.setattr(scoring, "_items_from_db",
                        lambda: calls.append(1) or _fake_items())
    idx = scoring.priority_index(allow_stale=True)
    assert idx and len(calls) == 1


def test_handler_helper_schedules_one_background_refresh(monkeypatch):
    """Обработчик отдаёт индекс сразу и заводит РОВНО один пересчёт в фоне."""
    import db

    calls = []
    monkeypatch.setattr(scoring, "_items_from_db",
                        lambda: calls.append(1) or _fake_items())

    async def scenario():
        scoring.priority_index()             # прогрели
        db._bump_version()                   # чужая запись
        first = web._priority_index_nonblocking()
        second = web._priority_index_nonblocking()
        assert first == second
        task = web._priority_refresh_task
        assert task is not None
        await task
        return first

    asyncio.run(scenario())
    # Один прогрев + один фоновый пересчёт: второй вызов не завёл своего.
    assert len(calls) == 2
    assert scoring.priority_index_is_fresh()


def test_background_refresh_runs_outside_the_event_loop(monkeypatch):
    """Пересчёт обязан идти в отдельном потоке, иначе он снова остановит сервер."""
    import db
    import threading

    main_thread = threading.get_ident()
    seen = []
    monkeypatch.setattr(
        scoring, "_items_from_db",
        lambda: seen.append(threading.get_ident()) or _fake_items())

    async def scenario():
        scoring.priority_index()
        db._bump_version()
        web._priority_index_nonblocking()
        await web._priority_refresh_task

    asyncio.run(scenario())
    assert seen[0] == main_thread              # прогрев — синхронный
    assert seen[1] != main_thread, "фоновый пересчёт остался в цикле событий"
