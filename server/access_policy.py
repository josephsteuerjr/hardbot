"""Версия прав и явное ограничение офлайн-чтения сохранённых данных."""
import hashlib
import json

import db

OFFLINE_READ_SECONDS = 8 * 60 * 60


def version(team, role="", caps=None):
    names = set(team)
    # Identity, bootstrap and card warmup must agree even when one person's
    # two products use explicit different portfolio labels. This changes only
    # the cache version, never the permission checks of those endpoints.
    import staff_roster
    with db.connect() as conn:
        keys = {db._name_key(n) for n in names}
        roster = (staff_roster.rows(conn) if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='staff_roster'").fetchone() else [])
        for row in roster:
            aliases = {row['employee'], *json.loads(row['product_names']).values()}
            if keys & {db._name_key(n) for n in aliases}:
                names.update(aliases)
    names = sorted(names)
    assignments = []
    with db.connect() as conn:
        for start in range(0, len(names), 250):
            part = names[start:start + 250]
            marks = ",".join("?" * len(part))
            assignments.extend(tuple(r) for r in conn.execute(
                f"SELECT contract_id,responsible,responsible_soft FROM clients WHERE responsible IN ({marks}) OR responsible_soft IN ({marks}) ORDER BY contract_id",
                (*part, *part)))
    payload = json.dumps([names, role, caps or {}, sorted(set(assignments))], ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
