"""CRM storage boundary: local SQLite demo or explicitly enabled PostgreSQL.

The legacy application's db.connect() is never redirected. Qmark statements
remain parameterized; the adapter translates placeholders, not SQL values.
"""
import hashlib
import os
from pathlib import Path


class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    names = [column.name for column in cursor.description or ()]
    return lambda values: Row(zip(names, values))


def parameters(sql):
    """Translate qmarks outside SQL literals; escape psycopg's percent syntax."""
    result, quote, index = [], None, 0
    while index < len(sql):
        char = sql[index]
        if char == '%':
            result.append('%%')
        elif quote:
            result.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    result.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in ("'", '"'):
            quote = char
            result.append(char)
        elif char == '?':
            result.append('%s')
        else:
            result.append(char)
        index += 1
    return ''.join(result)


def _lock_key(value):
    return int.from_bytes(hashlib.sha256(('crm-v1:' + value).encode()).digest()[:8],
                          'big', signed=True)


class Postgres:
    dialect = 'postgresql'

    def __init__(self, dsn, *, readonly=False):
        import psycopg
        self.raw = psycopg.connect(dsn, row_factory=_row_factory, connect_timeout=10,
                                  application_name='crm-pilot')
        if readonly:
            self.raw.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            self.raw.read_only = True

    def execute(self, sql, args=None):
        return self.raw.execute(parameters(sql) if args is not None else sql, args)

    def executemany(self, sql, values):
        cursor = self.raw.cursor()
        cursor.executemany(parameters(sql), values)
        return cursor

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.rollback() if kind else self.commit()
        finally:
            self.close()


def postgres_enabled():
    return os.environ.get('HARD_CRM_SHARED') == '1'


def connect(*, readonly=False):
    if postgres_enabled():
        dsn = os.environ.get('HARD_CRM_DATABASE_URL', '')
        filename = os.environ.get('HARD_CRM_DATABASE_URL_FILE')
        if filename:
            dsn = Path(filename).read_text().strip()
        if not dsn.startswith(('postgresql://', 'postgres://')):
            raise RuntimeError('Shared CRM requires its own PostgreSQL DSN')
        return Postgres(dsn, readonly=readonly)
    import db
    return db.connect()


def is_postgres(conn):
    return getattr(conn, 'dialect', None) == 'postgresql'


def begin(conn, keys=None):
    if not is_postgres(conn):
        conn.execute('BEGIN IMMEDIATE')
        return
    # All writers cooperate with the maintenance lock. Normal commands take a
    # shared lock and ordered resource locks, allowing independent contracts to
    # progress concurrently. Bulk imports/rebuilds use an exclusive lock.
    conn.execute("SET LOCAL lock_timeout='15s'")
    conn.execute("SET LOCAL statement_timeout='120s'")
    mode = 'pg_advisory_xact_lock' if keys is None else 'pg_advisory_xact_lock_shared'
    conn.execute('SELECT ' + mode + '(?)', (_lock_key('maintenance'),))
    if keys is not None:
        for key in sorted({_lock_key(str(value)) for value in keys}):
            conn.execute('SELECT pg_advisory_xact_lock(?)', (key,))


def operation_lock(principal, operation):
    return 'operation:' + str(principal.actor_uid) + ':' + operation


def try_maintenance_barrier(conn):
    """Take the exclusive snapshot barrier immediately, or report an active writer."""
    return conn.execute("SELECT pg_try_advisory_xact_lock(?)",
                        (_lock_key('maintenance'),)).fetchone()[0]


def document_root():
    if postgres_enabled():
        value = os.environ.get('HARD_CRM_DOCUMENTS')
        if not value or not Path(value).is_absolute():
            raise RuntimeError('Shared CRM requires its own absolute document directory')
        path = Path(value)
    else:
        import db
        path = Path(db.DB_PATH).resolve().parent / 'crm-documents'
    path.mkdir(parents=True, exist_ok=True)
    return path
