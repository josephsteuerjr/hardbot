"""Operational status and sign-in inventory for the designated server operator."""
from . import primary_fence, storage
from .contract import CRMError, instant, now


def status(principal):
    if not principal.server_operator or principal.role != 'manager':
        raise CRMError('Сводка сервера доступна оператору.', 'forbidden', 403)

    checked_at = now()
    with storage.connect(readonly=True) as conn:
        state = {row[0]: row[1] for row in conn.execute(
            "SELECT key,value FROM crm_meta WHERE key IN ('schema_version','distribution_mode')"
        )}
        worker = conn.execute(
            "SELECT heartbeat_at,ok FROM crm_runtime_status WHERE worker='scheduler'"
        ).fetchone()
        pending = conn.execute("SELECT count(*) FROM crm_primary_pending").fetchone()[0]
        migrated = conn.execute("SELECT count(*) FROM crm_primary_fences").fetchone()[0]

    with primary_fence.connect(readonly=True) as legacy:
        # The account summary and device counts describe the same snapshot.
        legacy.execute("BEGIN")
        entries = [dict(row) for row in legacy.execute("""
            SELECT a.id,a.employee,a.username,a.role,a.status,
              (SELECT max(at) FROM account_logins l WHERE l.account_id=a.id AND l.kind='ok') last_login
            FROM accounts a ORDER BY a.employee,a.id
        """)]
        devices = [dict(row) for row in legacy.execute("""
            SELECT account_id,kind,status,count(*) total FROM account_devices
            WHERE status IN ('active','pending') GROUP BY account_id,kind,status
        """)]
        recent = [dict(row) for row in legacy.execute("""
            SELECT a.employee,a.username,l.at,l.kind,l.device_kind
            FROM (SELECT account_id,at,kind,device_kind FROM account_logins
                  ORDER BY id DESC LIMIT 120) l JOIN accounts a ON a.id=l.account_id
            ORDER BY l.at DESC
        """)]

    return {
        'checked_at': checked_at,
        'schema_version': state.get('schema_version'),
        'cutover': state.get('distribution_mode') == 'cutover',
        'pending': pending,
        'migrated': migrated,
        'scheduler': {
            'ok': bool(worker and worker['ok'] and
                       (instant(checked_at) - instant(worker['heartbeat_at'])).total_seconds() < 90),
            'heartbeat': worker['heartbeat_at'] if worker else None,
        },
        'entries': entries,
        'devices': devices,
        'recent': recent,
    }
