"""Догон фото: карточка визита ждёт снимок, который ещё едет.

Почему это отдельный механизм. Карточка «Выезд отработан» уходила руководителю
мгновенно по закрытию визита, а снимок ехал своим запросом — и обгонял её.
По журналу прода за 8–10.09.2026: из 293 карточек 48 ушли с «📸 без фото» при
том, что фото появлялось на сервере в среднем через 2 секунды (42 случая из 48
уложились в минуту). Руководитель видел «без фото» по визиту, у которого фото
есть, и переставал верить карточкам.

Проверяем ровно те правила, из-за которых механизм и написан:
  • есть обещанные снимки — ждём и уходим сразу, как только они появились;
  • «фото не будет» (photos_pending=0) — не ждём ни секунды;
  • обещанное фото так и не приехало — карточка всё равно уходит, по окну;
  • старая оболочка (поля нет) — ждём, только если фото за день нет вовсе.
"""
import asyncio
import time
from datetime import date

import db
import photostore
import web


def _day():
    return date.today().isoformat()


def _seed(cid="G-1"):
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO clients (contract_id, client, responsible) "
            "VALUES (?,?,?)", (cid, "Тестов Т.", "Пушкин Сотрудник"))
        conn.commit()
    d = photostore.PHOTO_DIR / cid
    if d.is_dir():
        for f in d.iterdir():
            f.unlink()
    return cid


def _add_photo(cid, name="1.jpg"):
    d = photostore.PHOTO_DIR / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"x")


def _wait(cid, pending, grace="5"):
    db.set_setting("photo_grace_s", grace)
    started = time.monotonic()
    asyncio.run(web._await_visit_photos(cid, _day(), pending))
    return time.monotonic() - started


def test_waits_for_promised_photo_and_leaves_early():
    """Обещан один снимок: ждём его и НЕ досиживаем окно до конца."""
    cid = _seed()

    async def scenario():
        db.set_setting("photo_grace_s", "5")
        task = asyncio.create_task(web._await_visit_photos(cid, _day(), 1))
        await asyncio.sleep(0.2)
        assert not task.done(), "карточка не должна уйти, пока фото не приехало"
        _add_photo(cid)
        await asyncio.wait_for(task, timeout=4)

    started = time.monotonic()
    asyncio.run(scenario())
    assert time.monotonic() - started < 4, "ушли по появлению фото, а не по окну"


def test_no_photos_expected_does_not_delay():
    """«Нет контакта, снимать нечего» — карточка уходит немедленно."""
    cid = _seed("G-2")
    assert _wait(cid, 0, grace="5") < 0.5


def test_gives_up_after_window():
    """Обещанное фото не доехало: карточка уходит по истечении окна, а не висит."""
    cid = _seed("G-3")
    spent = _wait(cid, 1, grace="1")
    assert 0.9 < spent < 3


def test_grace_off_by_setting():
    """settings['photo_grace_s']=0 полностью отключает ожидание."""
    cid = _seed("G-4")
    assert _wait(cid, 1, grace="0") < 0.5
    db.set_setting("photo_grace_s", "")


def test_old_shell_waits_only_without_photos():
    """Оболочка до v40 счётчик не шлёт. Фото за день уже есть — не ждём."""
    cid = _seed("G-5")
    _add_photo(cid)
    assert _wait(cid, None, grace="2") < 0.5


def test_old_shell_waits_when_nothing_yet():
    """Та же старая оболочка без единого снимка — ждём окно."""
    cid = _seed("G-6")
    assert _wait(cid, None, grace="1") > 0.9
