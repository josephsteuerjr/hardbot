"""Explicit offline migration; no production migration at import or server boot."""

import json, uuid
from zoneinfo import ZoneInfo
import db
from . import schema, store
from .contract import (
    CRMError,
    canonical,
    digest,
    fields,
    field_value,
    identifier,
    instant,
    now,
)


def preview(mapping):
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        histories = {
            table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            for table in ("calls", "visits")
        }
    return dict(
        contracts=count,
        legacy_events=histories,
        explicit_employees=len(mapping.get("employees", [])),
        explicit_person_links=len(mapping.get("contracts", {})),
        mapping_hash=digest(mapping),
        applied=False,
    )


def apply(mapping):
    if (
        not isinstance(mapping, dict)
        or set(mapping) - {"employees", "contracts", "source"}
        or not mapping.get("source")
    ):
        raise CRMError("Нужен явный источник сопоставления.")
    at = now()
    mapping_hash = digest(mapping)
    definitions = {f["key"]: f for f in fields()}
    with db.connect() as conn:
        store.begin(conn)
        schema.initialize(conn)
        prior = conn.execute(
            "SELECT value FROM crm_meta WHERE key='migration_mapping_hash'"
        ).fetchone()
        if prior:
            if prior[0] != mapping_hash:
                raise CRMError(
                    "Миграция уже выполнена с другой картой. Нужна отдельная версия.",
                    "migration_conflict",
                    409,
                )
            return json.loads(
                conn.execute(
                    "SELECT value FROM crm_meta WHERE key='migration_receipt'"
                ).fetchone()[0]
            )
        if conn.execute("SELECT COUNT(*) FROM crm_events").fetchone()[0]:
            raise CRMError("Миграция разрешена только до начала работы CRM.")
        legacy = [dict(r) for r in conn.execute("SELECT * FROM clients ORDER BY id")]
        mapped = {}
        issues = 0

        def issue(kind, key, detail):
            nonlocal issues
            conn.execute(
                "INSERT OR IGNORE INTO crm_migration_issues VALUES(?,?,?,?,?,?)",
                (
                    identifier("issue", kind + ":" + key),
                    kind,
                    key,
                    canonical(detail),
                    None,
                    None,
                ),
            )
            issues += 1

        for row in legacy:
            old = str(row["contract_id"])
            entry = mapping.get("contracts", {}).get(old, {})
            creditor = entry.get("creditor_id") or "legacy-partner:" + str(
                row.get("partner") or "unknown"
            )
            if not entry.get("creditor_id"):
                issue(
                    "creditor_requires_confirmation",
                    old,
                    {"partner": row.get("partner")},
                )
            external = entry.get("external_id") or old
            cid = identifier("contract", canonical([creditor, external]))
            person = identifier(
                "person", entry.get("person_ref") or "legacy-contract:" + old
            )
            if entry.get("person_ref") and not entry.get("evidence"):
                raise CRMError("Объединение клиента требует подтверждённого источника.")
            if not entry.get("person_ref"):
                issue(
                    "person_not_merged",
                    old,
                    {"reason": "No authoritative cross-contract person ID"},
                )
            conn.execute(
                "INSERT OR IGNORE INTO crm_people VALUES(?,?,?,?)",
                (
                    person,
                    str(row.get("client") or ""),
                    entry.get("evidence") or "legacy-contract-separate",
                    at,
                ),
            )
            values = {}
            for key, definition in definitions.items():
                value = row.get(key)
                if value is None or value == "":
                    values[key] = value
                    continue
                try:
                    if definition["type"] == "date":
                        parsed = db._as_date(value)
                        if parsed is None:
                            raise CRMError("Unknown date")
                        value = (
                            parsed.date().isoformat()
                            if hasattr(parsed, "date")
                            else parsed.isoformat()
                        )
                    elif definition["type"] in {"money", "integer"}:
                        value = (
                            str(value)
                            .replace("\u00a0", "")
                            .replace(" ", "")
                            .replace(",", ".")
                        )
                    values[key] = field_value(key, value)
                except (CRMError, ValueError):
                    values[key] = row.get(key)
                    issue(
                        "legacy_value_untyped",
                        old + ":" + key,
                        {"key": key, "type": definition["type"]},
                    )
            conn.execute(
                "INSERT INTO crm_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cid,
                    person,
                    creditor,
                    external,
                    old,
                    entry.get("currency"),
                    canonical(values),
                    canonical(values),
                    "{}",
                    "{}",
                    at,
                ),
            )
            mapped[old] = cid
        actors = {}
        staff = {}
        for employee in mapping.get("employees", []):
            required = {
                "staff_id",
                "name",
                "team_id",
                "role",
                "capabilities",
                "timezone",
                "starts_at",
                "actor_uids",
                "assignments",
                "evidence",
            }
            if set(employee) != required or not employee["evidence"]:
                raise CRMError("Кадровое соответствие неполное.")
            eid = identifier("staff", employee["staff_id"])
            start = instant(employee["starts_at"]).isoformat()
            ZoneInfo(employee["timezone"])
            if employee["staff_id"] in staff:
                raise CRMError("Кадровый ID повторён.")
            staff[employee["staff_id"]] = eid
            conn.execute(
                "INSERT INTO crm_employees VALUES(?,?,?,?,?)",
                (eid, employee["staff_id"], employee["name"], employee["evidence"], 1),
            )
            conn.execute(
                "INSERT INTO crm_memberships VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    eid,
                    employee["team_id"],
                    employee["role"],
                    canonical(employee["capabilities"]),
                    employee["timezone"],
                    start,
                    None,
                ),
            )
            for uid in employee["actor_uids"]:
                if type(uid) is not int or uid in actors:
                    raise CRMError("Учётная запись сопоставлена неоднозначно.")
                actors[uid] = eid
                conn.execute(
                    "INSERT INTO crm_actor_links VALUES(?,?,?,?)",
                    (uid, eid, employee["evidence"], 1),
                )
            for assignment in employee["assignments"]:
                if (
                    set(assignment) != {"legacy_id", "capability"}
                    or assignment["legacy_id"] not in mapped
                    or assignment["capability"] not in {"call", "visit"}
                ):
                    raise CRMError("Назначение требует явного договора и функции.")
                conn.execute(
                    "INSERT INTO crm_assignments VALUES(?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        eid,
                        mapped[assignment["legacy_id"]],
                        assignment["capability"],
                        start,
                        None,
                        employee["evidence"],
                    ),
                )
        imported = 0
        for table, kind in [("calls", "call"), ("visits", "visit")]:
            for raw in conn.execute("SELECT * FROM " + table + " ORDER BY id"):
                old = dict(raw)
                source = table + ":" + str(old["id"])
                event = identifier("legacy-event", source)
                try:
                    ids = json.loads(old.get("contracts_json") or "[]")
                except (ValueError, TypeError):
                    ids = []
                if not ids:
                    ids = [old.get("contract_id")]
                contracts = sorted(
                    {mapped[str(key)] for key in ids if str(key) in mapped}
                )
                if not contracts:
                    issue("history_contract_missing", source, {"unlinked": True})
                try:
                    values = json.loads(old.get("values_json") or "{}")
                except (ValueError, TypeError):
                    values = {}
                uid = int(old.get("agent_id") or 0)
                employee = actors.get(uid, "legacy-actor:" + str(uid))
                payload = dict(
                    values=values,
                    legacy_payload=old.get("payload"),
                    legacy_created_at=old.get("created_at"),
                    legacy_received_at=old.get("received_at"),
                    legacy_employee=old.get("employee"),
                    legacy_contract_ids=ids,
                )
                # Preserve raw source timestamps; they cannot establish trustworthy
                # historical adoption or a more recent field projection.
                conn.execute(
                    """INSERT INTO crm_events(event_id,operation_id,request_hash,actor_uid,employee_uuid,team_id,role,kind,origin,contracts_json,payload_json,occurred_at,received_at,time_quality,projection_status,legacy_source)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event,
                        old.get("op_id") or source,
                        digest(old),
                        uid,
                        employee,
                        "legacy-unknown",
                        "legacy",
                        kind,
                        "unknown",
                        canonical(contracts),
                        canonical(payload),
                        at,
                        at,
                        "legacy_unknown",
                        "historical_only",
                        source,
                    ),
                )
                conn.executemany(
                    "INSERT INTO crm_event_contracts VALUES(?,?)",
                    [(event, cid) for cid in contracts],
                )
                imported += 1
        result = dict(
            stage="verified",
            contracts=len(legacy),
            events=imported,
            employees=len(staff),
            issues=issues,
            mapping_hash=mapping_hash,
            legacy_rows_unchanged=True,
            applied_at=at,
        )
        for key, value in [
            ("migration_mapping_hash", mapping_hash),
            ("migration_receipt", canonical(result)),
            ("reliable_collection_since", at),
        ]:
            conn.execute("INSERT INTO crm_meta VALUES(?,?)", (key, value))
        from .legacy import protect

        protect(conn)
        conn.commit()
        return result
