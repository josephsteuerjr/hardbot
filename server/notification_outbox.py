"""Durable очередь. После начала внешней отправки сбой означает неопределённость.

Pending переживает рестарт. Running после рестарта становится uncertain и
не повторяется автоматически: внешняя доставка не даёт exactly-once ключ.
"""
import asyncio
import contextvars
import json
import sqlite3
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import db

current = contextvars.ContextVar("notification_outbox", default=None)


def init_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS notification_outbox (
        id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
        started_at TEXT, finished_at TEXT, error TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS notification_attempts (
        id INTEGER PRIMARY KEY, outbox_id INTEGER NOT NULL, at TEXT NOT NULL,
        kind TEXT NOT NULL, target TEXT, status TEXT NOT NULL, error TEXT)""")


def enqueue(conn, key, payload):
    conn.execute("INSERT OR IGNORE INTO notification_outbox(event_key,payload,created_at) VALUES(?,?,?)",
                 (key, json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))


def record(conn, kind, target, status, error):
    oid = current.get()
    if oid:
        conn.execute("INSERT INTO notification_attempts(outbox_id,at,kind,target,status,error) VALUES(?,?,?,?,?,?)",
            (oid, datetime.now(timezone.utc).isoformat(), kind, str(target or ""), status, error))


def recover(before=None):
    with db.connect() as conn:
        conn.execute("UPDATE notification_outbox SET state='uncertain',error=? WHERE state='running'" +
                     (" AND started_at<?" if before else ""),
                     ("Процесс завершился во время отправки. Сверьте канал перед повтором.", *([before] if before else [])))
        conn.commit()


def claim(event_key=None):
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM notification_outbox WHERE state='pending' " +
                           ("AND event_key=? " if event_key else "") + "ORDER BY id LIMIT 1",
                           (event_key,) if event_key else ()).fetchone()
        if row:
            conn.execute("UPDATE notification_outbox SET state='running',started_at=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(), row["id"]))
            conn.commit()
    return dict(row) if row else None


def finish(oid, error=None):
    with db.connect() as conn:
        statuses = [r[0] for r in conn.execute("SELECT status FROM notification_attempts WHERE outbox_id=?", (oid,))]
        unknown = error or not statuses or any(s != db.PUSH_SENT and not s.startswith("skip") for s in statuses)
        state = "uncertain" if unknown else "delivered" if db.PUSH_SENT in statuses else "skipped"
        conn.execute("UPDATE notification_outbox SET state=?,finished_at=?,error=? WHERE id=?",
                     (state, datetime.now(timezone.utc).isoformat(),
                      str(error or "Сверьте результаты отдельных каналов") if unknown else None, oid))
        conn.commit()


async def deliver(app, event_key=None):
    limit = app.setdefault("notification_limit", asyncio.Semaphore(4))
    async with limit:
        return await _deliver(app, event_key)


async def _deliver(app, event_key=None):
    import web as handlers
    row = await asyncio.to_thread(claim, event_key)
    if not row:
        return False
    token = current.set(row["id"])
    error = None
    try:
        p = json.loads(row["payload"])
        await handlers._visit_notify(SimpleNamespace(app=app), p["row"], p["uid"],
            p.get("dist_m"), p.get("geo_far"), p.get("actor"), p.get("lat"), p.get("lon"),
            p["day"], p.get("pending"), p.get("evidence_ctx"))
    except Exception as exc:
        error = str(exc)
    except asyncio.CancelledError:
        error = "Отправка прервана. Результаты каналов требуют сверки."
        raise
    finally:
        try:
            await asyncio.to_thread(finish, row["id"], error)
        finally:
            current.reset(token)
    return True


async def lifecycle(app):
    started = datetime.now(timezone.utc).isoformat()
    async def work():
        try:
            await asyncio.to_thread(recover, started)
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            logging.info("Outbox schema unavailable; readiness requires migrations")
            return
        while True:
            if not await deliver(app):
                await asyncio.sleep(2)
    task = asyncio.create_task(work())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
