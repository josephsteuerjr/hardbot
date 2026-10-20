"""Reported client versions, tied to authenticated account sessions.

These are diagnostic observations, never an access or binary-attestation gate.
PWA protocol versions and the Python bridge's compatibility header are kept
distinct from the installed desktop version. No bearer or user agent is stored.
"""
from datetime import datetime, timedelta, timezone
import re

import db


def init_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS account_client_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL, device_id INTEGER NOT NULL,
        token_table TEXT NOT NULL, token_id INTEGER NOT NULL,
        client_version TEXT NOT NULL, shell_version INTEGER NOT NULL,
        product TEXT NOT NULL, source TEXT NOT NULL,
        first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, login_at TEXT,
        UNIQUE(token_table,token_id,client_version,shell_version,product,source)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account_client_versions_device "
                 "ON account_client_versions(device_id,last_seen_at,id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account_client_versions_account "
                 "ON account_client_versions(account_id)")


def metadata(headers):
    version = headers.get('X-Client-Version', '')
    if not isinstance(version, str) or not re.fullmatch(
            r'[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:[-+][A-Za-z0-9.-]{1,24})?', version):
        version = ''
    raw_shell = headers.get('X-Shell-Version', '')
    shell = int(raw_shell) if isinstance(raw_shell, str) and re.fullmatch(r'[0-9]{1,5}', raw_shell) else 0
    if not version and not shell:
        return None
    product = headers.get('X-Work-Product', '')
    if product not in ('hard', 'soft', 'manager', 'analyst'):
        product = ''
    ua = headers.get('User-Agent', '')
    source = ('pwa' if shell else 'compatibility' if ua.startswith('Python-urllib/')
              else 'desktop' if 'Electron/' in ua else 'reported')
    return {'client_version': version, 'shell_version': shell, 'product': product, 'source': source}


def observe(token_table, token, info, *, login=False):
    """Observe a valid, non-revoked session, including pending approvals.

    New versions/sessions are recorded immediately; repeated observations of
    the same version touch last_seen_at at most once a minute. Missing headers
    leave previous observations intact rather than inventing a version.
    """
    if token_table not in ('agent_devices', 'manager_devices') or not token or not info:
        return
    key = db.app_token_key(token)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec='seconds')
    cutoff = (now_dt - timedelta(seconds=60)).isoformat(timespec='seconds')
    identity_sql = (f'SELECT t.id token_id,t.account_id,d.id device_id FROM {token_table} t '
                    'JOIN account_token_sessions s ON s.token_table=? AND s.token_id=t.id '
                    'JOIN account_devices d ON d.id=s.device_id AND d.account_id=t.account_id '
                    'JOIN accounts a ON a.id=d.account_id '
                    "WHERE t.token_hash=? AND t.revoked_at IS NULL AND d.status<>'revoked' "
                    "AND a.status<>'disabled'")
    values = tuple(info[k] for k in ('client_version', 'shell_version', 'product', 'source'))
    with db.connect() as conn:
        target = conn.execute(identity_sql, (token_table, key)).fetchone()
        if target is None:
            return
        previous = conn.execute(
            'SELECT last_seen_at,login_at FROM account_client_versions WHERE token_table=? '
            'AND token_id=? AND client_version=? AND shell_version=? AND product=? AND source=?',
            (token_table, target['token_id'], *values)).fetchone()
        if previous and previous['last_seen_at'] > cutoff and (not login or previous['login_at']):
            return
        conn.execute('BEGIN IMMEDIATE')
        # Recheck under the write lock so a concurrent revocation wins.
        target = conn.execute(identity_sql, (token_table, key)).fetchone()
        if target is None:
            return
        conn.execute('INSERT INTO account_client_versions '
                     '(account_id,device_id,token_table,token_id,client_version,shell_version,'
                     'product,source,first_seen_at,last_seen_at,login_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) '
                     'ON CONFLICT(token_table,token_id,client_version,shell_version,product,source) '
                     'DO UPDATE SET last_seen_at=excluded.last_seen_at,'
                     'login_at=COALESCE(account_client_versions.login_at,excluded.login_at)',
                     (target['account_id'], target['device_id'], token_table, target['token_id'],
                      *values, now, now, now if login else None))
        conn.commit()


def for_device(conn, device_id):
    """Safe administrative view; session ids and credentials stay internal."""
    rows = [dict(r) for r in conn.execute(
        'SELECT client_version,shell_version,product,source,first_seen_at,last_seen_at,login_at '
        'FROM account_client_versions WHERE device_id=? ORDER BY last_seen_at DESC,id DESC LIMIT 20',
        (device_id,))]
    current = next((r for r in rows if r['source'] != 'compatibility'), None)
    return {'client': current, 'client_versions': rows}
