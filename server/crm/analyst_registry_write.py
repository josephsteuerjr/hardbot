"""Versioned source-registry corrections before the coordinated CRM cutover."""

import json
import re

from crm.contract import CRMError, canonical, digest, field_value, fields, now
from crm.primary_fence import check_old_work
import workday


class RegistryConflict(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class Catalog:
    def __init__(self):
        protected = {
            "contract_id", "partner", "client", "responsible", "responsible_soft",
            "assign_date", "visits_count", "last_payment_sum", "last_payment_date",
        }
        self.labels = {
            row["key"]: row["title"] for row in fields()
            if row["group"] in {"accounting", "contact", "forecast"}
            and row["key"] not in protected and not row.get("readonly")
        }


def card(conn, catalog, row_id):
    row = conn.execute(
        "SELECT id, contract_id, partner, " + ", ".join(catalog.labels) + " FROM clients WHERE id=?",
        (row_id,),
    ).fetchone()
    if not row:
        raise RegistryConflict("Договор не найден.", 404)
    return row, digest(dict(row))


def change(conn, catalog, row_id, revision, operation, field, value, actor):
    """Keep the version comparison, field update and operation receipt in one transaction."""
    if not all(isinstance(item, str) for item in (revision, operation, field, value)):
        raise RegistryConflict("Не заполнена правка.", 400)
    if field not in catalog.labels or len(value) > 4096:
        raise RegistryConflict("Это поле нельзя изменить из карточки.", 400)
    if not re.fullmatch(r"[a-f0-9]{64}", revision) or not re.fullmatch(r"[a-f0-9]{32}", operation):
        raise RegistryConflict("Откройте карточку заново.", 400)
    try:
        revised = field_value(field, value.strip() or None, clear=True)
    except CRMError as exc:
        raise RegistryConflict(str(exc), 400) from exc
    event = "analyst-web:" + operation
    request_hash = digest([actor, row_id, revision, field, revised])
    prior = conn.execute("SELECT actor,detail FROM audit WHERE event_id=?", (event,)).fetchone()
    if prior:
        if prior["actor"] != actor or json.loads(prior["detail"])["request"] != request_hash:
            raise RegistryConflict("Эта операция уже использована для другой правки.")
        return False
    row, current = card(conn, catalog, row_id)
    if revision != current:
        raise RegistryConflict("Карточка изменилась. Откройте её заново и сравните значения.")
    try:
        check_old_work(conn, [row["contract_id"]])
    except CRMError as exc:
        raise RegistryConflict("Этот договор уже перенесён. Правка доступна в рабочей CRM.") from exc
    conn.execute(f'UPDATE clients SET "{field}"=?,updated_at=? WHERE id=?',
                 (revised, workday.today().isoformat(), row_id))
    conn.execute(
        "INSERT INTO audit(ts,actor,action,target,detail,event_id) VALUES (?,?,?,?,?,?)",
        (now(), actor, "analyst_web_registry_edit", row["contract_id"],
         canonical({"field": field, "previous": row[field], "value": revised, "request": request_hash}), event),
    )
    return True
