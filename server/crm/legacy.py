"""Explicit single-writer cutover on an isolated pilot database."""

from contextlib import contextmanager
from contextvars import ContextVar

writing = ContextVar("crm_canonical_write", default=False)


@contextmanager
def projection():
    token = writing.set(True)
    try:
        yield
    finally:
        writing.reset(token)


def protect(conn):
    for table in ("clients", "calls", "visits"):
        for action in ("INSERT", "UPDATE", "DELETE"):
            name = "crm_guard_" + table + "_" + action.lower()
            clause = action
            if table == "clients" and action == "UPDATE":
                from fields import FIELDS

                clause += " OF " + ",".join('"' + f.key + '"' for f in FIELDS)
            conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS {name} BEFORE {clause} ON {table}
                WHEN crm_canonical_write()=0 BEGIN SELECT RAISE(ABORT,'CRM pilot: use canonical command endpoint'); END"""
            )
    conn.execute(
        "INSERT OR REPLACE INTO crm_meta VALUES('legacy_writer_guard','enabled')"
    )


def release(conn):
    # Operator rollback keeps all additive history and projections.
    for table in ("clients", "calls", "visits"):
        for action in ("insert", "update", "delete"):
            conn.execute("DROP TRIGGER IF EXISTS crm_guard_" + table + "_" + action)
    conn.execute(
        "INSERT OR REPLACE INTO crm_meta VALUES('legacy_writer_guard','disabled')"
    )
