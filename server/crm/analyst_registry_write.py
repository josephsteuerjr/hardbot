"""Versioned source-registry corrections before the coordinated CRM cutover."""

import json
import re

from crm.contract import CRMError, canonical, digest, field_value, fields, group, now, operation_id
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


def revisions(revision):
    return dict.fromkeys(("accounting", "contact", "forecast"), revision)


def save_rows(conn, catalog, body, actor):
    """Use the mobile editor's protected protocol on the working source registry."""
    patches = body.get("rows")
    if body.get("schema_version") != 1 or not isinstance(patches, list) or not 1 <= len(patches) <= 100:
        raise RegistryConflict("Некорректный пакет правок.", 400)
    try:
        operation = operation_id(body.get("operation_id"))
    except CRMError as exc:
        raise RegistryConflict(str(exc), 400) from exc
    marker = "analyst-pwa:" + operation
    proof = digest([actor, body])
    prior = conn.execute("SELECT actor,detail FROM audit WHERE event_id=?", (marker,)).fetchone()
    if prior:
        receipt = json.loads(prior["detail"])
        if prior["actor"] != actor or receipt["request"] != proof:
            raise RegistryConflict("Номер операции уже связан с другими правками.")
        return receipt["result"]
    prepared = {}
    for patch in patches:
        if not isinstance(patch, dict):
            raise RegistryConflict("Некорректная строка правки.", 400)
        row_id = patch.get("contract_uuid")
        values, clear, base = (patch.get(key) for key in ("values", "clear", "base_revisions"))
        if (not isinstance(row_id, str) or not re.fullmatch(r"[1-9][0-9]{0,17}", row_id)
                or row_id in prepared or not isinstance(values, dict) or not values
                or not isinstance(clear, list) or not isinstance(base, dict)
                or set(values) - catalog.labels.keys()):
            raise RegistryConflict("Состав или права полей изменились.", 400)
        if any(not isinstance(key, str) for key in clear) or set(clear) != {key for key, value in values.items() if value is None}:
            raise RegistryConflict("Пустое значение нужно очистить явно.", 400)
        if any(isinstance(value, str) and len(value) > 4096 for value in values.values()):
            raise RegistryConflict("В одном поле допустимо до 4096 символов.", 400)
        row, revision = card(conn, catalog, int(row_id))
        if any(base.get(group(key)) != revision for key in values):
            raise RegistryConflict("Карточка изменилась. Сверьте правки с реестром.")
        try:
            normalized = {key: field_value(key, value, clear=key in clear) for key, value in values.items()}
            check_old_work(conn, [row["contract_id"]])
        except CRMError as exc:
            raise RegistryConflict(str(exc), exc.status) from exc
        prepared[row_id] = (row, normalized, revision)
    if sum(len(values) for _, values, _ in prepared.values()) > 1000:
        raise RegistryConflict("За одну отправку можно изменить до 1000 полей.", 400)
    response = []
    for row_id, (row, values, previous_revision) in prepared.items():
        assignments = ",".join(f'"{key}"=?' for key in values)
        conn.execute(f"UPDATE clients SET {assignments},updated_at=? WHERE id=?",
                     (*values.values(), workday.today().isoformat(), int(row_id)))
        _, revision = card(conn, catalog, int(row_id))
        response.append(dict(contract_uuid=row_id, values=values, revisions=revisions(revision),
                             base_revisions=revisions(previous_revision)))
        conn.execute("INSERT INTO audit(ts,actor,action,target,detail,event_id) VALUES (?,?,?,?,?,?)",
                     (now(), actor, "analyst_web_registry_edit", row["contract_id"],
                      canonical(dict(previous={key: row[key] for key in values}, values=values)), f"{marker}:{row_id}"))
    result = dict(operation_id=operation, rows=response, projection_status="applied")
    conn.execute("INSERT INTO audit(ts,actor,action,target,detail,event_id) VALUES (?,?,?,?,?,?)",
                 (now(), actor, "analyst_pwa_receipt", "", canonical(dict(request=proof, result=result)), marker))
    return result
