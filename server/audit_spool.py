"""Persistent access audit fallback while the registry writer owns SQLite."""
import asyncio
import sqlite3
from contextlib import closing

import db


def path():
    return db.DB_PATH.with_name("audit-spool.sqlite3")


def save(event):
    with closing(sqlite3.connect(path(), timeout=3)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,ts TEXT,actor INTEGER,action TEXT,target TEXT,detail TEXT)")
        conn.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)", event)
        conn.commit()


def drain():
    if not path().exists():
        return 0
    with closing(sqlite3.connect(path(), timeout=.05)) as spool:
        try:
            events = spool.execute("SELECT * FROM events ORDER BY rowid LIMIT 100").fetchall()
        except sqlite3.Error:
            return 0
        if not events:
            return 0
        try:
            with closing(sqlite3.connect(db.DB_PATH, timeout=.05)) as main:
                main.executemany("INSERT OR IGNORE INTO audit(event_id,ts,actor,action,target,detail) VALUES(?,?,?,?,?,?)", events)
                main.commit()
            spool.executemany("DELETE FROM events WHERE id=?", [(r[0],) for r in events])
            spool.commit()
        except sqlite3.OperationalError:
            return 0
        return len(events)


async def lifecycle(app):
    async def work():
        while True:
            await asyncio.to_thread(drain)
            await asyncio.sleep(1)
    task = asyncio.create_task(work())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
