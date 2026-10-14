"""Версия прав и явное ограничение офлайн-чтения сохранённых данных."""
import hashlib
import json

import db

OFFLINE_READ_SECONDS = 8 * 60 * 60


def version(team, role="", caps=None):
    names = sorted(set(team))
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
