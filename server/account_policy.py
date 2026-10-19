"""Opt-in UTC cutover with a durable one-way activation latch.

The configured time does not change production by default. Once reached, a
settings row preserves denial across restart or an accidentally removed env key;
rollback requires deliberate database recovery, never deleting the variable.
The database is read on every gate: one process must not miss a latch another
process or CLI writer activated after its own first request.
"""
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def cutover_at() -> datetime | None:
    raw = os.getenv("ACCOUNT_CUTOVER_AT_UTC", "").strip()
    if not raw:
        return None
    if not (raw.endswith("Z") or raw.endswith("+00:00")):
        raise ValueError("ACCOUNT_CUTOVER_AT_UTC must be an ISO-8601 UTC timestamp with Z or +00:00")
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid ACCOUNT_CUTOVER_AT_UTC timestamp") from exc
    if at.utcoffset() != timezone.utc.utcoffset(at):
        raise ValueError("ACCOUNT_CUTOVER_AT_UTC must be UTC")
    return at


def activate(*, connection: sqlite3.Connection | None = None) -> bool:
    """Activate at each auth boundary, sharing a caller's SQLite transaction.

    Password login and approvals already hold BEGIN IMMEDIATE. Opening another
    writer there would deadlock the first request crossing the UTC boundary.
    A supplied connection owns both the policy decision and its durable latch;
    its caller is responsible for committing or rolling back the transaction.
    """
    import db
    at = cutover_at()  # invalid operator configuration never degrades to legacy
    due = at is not None and datetime.now(timezone.utc) >= at
    path = Path(db.DB_PATH)
    if connection is None and not path.is_file():
        if due:
            raise RuntimeError("Cutover database is not initialized")
        return False
    if connection is None:
        with closing(db.connect()) as conn, conn:
            return _activate_connection(conn, due)
    return _activate_connection(connection, due)


def _activate_connection(conn: sqlite3.Connection, due: bool) -> bool:
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?",
                           ("account_cutover_activated_at_utc",)).fetchone()
        latched = bool(row and row[0])
    except sqlite3.OperationalError as exc:
        if "no such table: settings" not in str(exc):
            raise
        # A partial synthetic fixture can build its HTTP app before init_db.
        # An EXISTING accounts table without settings, or an already due
        # cutover, is instead damaged authority and must fail closed.
        has_accounts = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'").fetchone())
        if has_accounts or due:
            raise RuntimeError("Cutover settings table is unavailable") from exc
        return False
    if due and not latched:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     ("account_cutover_activated_at_utc",
                      datetime.now(timezone.utc).isoformat(timespec="seconds")))
        return True
    return latched


def strict(*, connection: sqlite3.Connection | None = None) -> bool:
    return activate(connection=connection)
