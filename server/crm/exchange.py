"""Versioned snapshots, server-held baselines, three-way imports and exact receipts."""

import json
import re
import uuid
from datetime import timedelta
from . import storage
from . import access, store
from .contract import (
    CRMError,
    canonical,
    digest,
    fields,
    field_value,
    group,
    instant,
    now,
    operation_id,
    DELIVERY_FIELDS,
)

IMMUTABLE = {
    "contract_id",
    "client",
    "partner",
    "pool",
    "responsible",
    "responsible_soft",
    "country",
    "city",
}
IMMUTABLE.update(DELIVERY_FIELDS)


def _rows(conn, principal, ids=None, *, state_only=False):
    allowed = access.scoped_contracts(conn, principal)
    if ids is not None and (
        not isinstance(ids, list)
        or len(ids) != len(set(ids))
        or not set(ids) <= allowed
    ):
        raise CRMError("Состав выгрузки недоступен.", "scope_changed", 403)
    chosen = allowed if ids is None else set(ids)
    columns = 'contract_uuid,fields_json,revisions_json' if state_only else '*'
    return [
        dict(r)
        for r in conn.execute("SELECT " + columns + " FROM crm_contracts ORDER BY contract_uuid")
        if r["contract_uuid"] in chosen
    ]


def export(principal, body):
    access.require(principal, "exchange")
    if set(body) - {"operation_id", "period", "target_id", "contract_ids"}:
        raise CRMError("Неизвестные параметры выгрузки.")
    period = body.get("period")
    target = body.get("target_id")
    if not isinstance(period, str) or not re.fullmatch(r"\d{4}-\d{2}", period):
        raise CRMError("Укажите месяц выгрузки.")
    if not isinstance(target, str) or not re.fullmatch(
        r"[A-Za-z0-9:_-]{1,120}", target
    ):
        raise CRMError("Не задан маршрут реестра.")
    operation = operation_id(body.get("operation_id"))
    at = now()
    with storage.connect() as conn:
        store.begin(conn)
        prior = store.replay(conn, principal, operation, body)
        if prior is not None:
            # An idempotent export contains private data, not only an ACK.
            # Revocation must apply even when returning a stored response.
            if not {row['contract_uuid'] for row in prior['rows']} <= access.scoped_contracts(conn, principal):
                raise CRMError("Состав выгрузки недоступен.", "scope_changed", 403)
            return prior
        rows = _rows(conn, principal, body.get("contract_ids"))
        baseline = {}
        output = []
        if len(rows) > 100000:
            raise CRMError("Разделите выгрузку по командам.")
        from .delivery import latest
        deliveries=latest(conn,[row['contract_uuid'] for row in rows])
        for row in rows:
            cid = row["contract_uuid"]
            values = json.loads(row["fields_json"])
            values.update(deliveries.get(cid,{}))
            baseline[cid] = dict(
                values=values,
                revisions=json.loads(row["revisions_json"]),
                provenance=json.loads(row["provenance_json"]),
            )
            output.append(
                dict(
                    contract_uuid=cid,
                    person_uuid=row["person_uuid"],
                    creditor_id=row["creditor_id"],
                    external_id=row["external_id"],
                    currency=row["currency"],
                    values=values,
                    revisions=baseline[cid]["revisions"],
                    provenance=baseline[cid]["provenance"],
                )
            )
        identifier = str(uuid.uuid4())
        scope = sorted(baseline)
        manifest = dict(
            schema_version=1,
            export_id=identifier,
            period=period,
            target_id=target,
            generated_at=at,
            scope_hash=digest(scope),
            baseline_hash=digest(baseline),
            field_keys=[f["key"] for f in fields()],
        )
        manifest["manifest_hash"] = digest(manifest)
        conn.execute(
            "INSERT INTO crm_exports VALUES(?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                principal.actor_uid,
                1,
                period,
                at,
                canonical(scope),
                canonical(baseline),
                manifest["manifest_hash"],
                target,
            ),
        )
        result = dict(manifest=manifest, fields=fields(), rows=output)
        store.acknowledge(conn, principal, operation, body, result, at)
        store.audit(
            conn,
            principal,
            "export",
            identifier,
            {"rows": len(rows), "target_id": target},
        )
        conn.commit()
        return result


def _snapshot(conn, principal, body):
    if set(body) - {
        "export_id",
        "manifest_hash",
        "schema_version",
        "file_sha256",
        "rows",
    }:
        raise CRMError("Неизвестные данные импорта.")
    if body.get("schema_version") != 1:
        raise CRMError("Версия реестра не поддерживается.", "schema_mismatch", 409)
    if not isinstance(body.get("file_sha256"), str) or not re.fullmatch(
        "[a-f0-9]{64}", body["file_sha256"]
    ):
        raise CRMError("Нужен SHA-256 выбранного файла.")
    snapshot = conn.execute(
        "SELECT * FROM crm_exports WHERE export_id=?", (body.get("export_id"),)
    ).fetchone()
    if not snapshot or snapshot["manifest_hash"] != body.get("manifest_hash"):
        raise CRMError("Экспорт или его манифест не найден.", "baseline_missing", 409)
    snapshot = dict(snapshot)
    baseline = json.loads(snapshot.pop("baseline_json"))
    snapshot.pop("scope_json", None)
    rows = body.get("rows")
    if not isinstance(rows, list) or len(rows) > 100000:
        raise CRMError("Некорректные строки реестра.")
    seen = set()
    current = {r["contract_uuid"]: r for r in _rows(conn, principal, state_only=True)}
    changes = []
    unchanged = 0
    titles = {f['key']:f['title'] for f in fields()}
    definitions = set(titles)
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"contract_uuid", "values", "clear"}:
            raise CRMError("Некорректная строка реестра.")
        cid = row.get("contract_uuid")
        if cid in seen:
            raise CRMError("Договор повторён в реестре.", "duplicate_row", 409)
        seen.add(cid)
        if cid not in baseline:
            raise CRMError("Строка не входит в исходную выгрузку.", "foreign_row", 409)
        if cid not in current:
            raise CRMError(
                "Назначение или доступ к строке изменились.", "scope_changed", 403
            )
        values = row.get("values")
        cleared = row.get("clear", [])
        if (
            not isinstance(values, dict)
            or not isinstance(cleared, list)
            or not set(cleared) <= set(values)
            or not set(values) <= definitions
        ):
            raise CRMError("Неизвестные ключи или очистка поля.")
        live = json.loads(current[cid]["fields_json"])
        base = baseline[cid]["values"]
        for key, value in values.items():
            # Blank Excel cells are unspecified unless explicitly marked as clear.
            if value is None and key not in cleared:
                continue
            if value == base.get(key):
                unchanged += 1
                continue
            desired = field_value(key, value, clear=key in cleared)
            if desired == live.get(key):
                unchanged += 1
                continue
            if key in IMMUTABLE:
                raise CRMError(
                    "Идентичность и назначения меняются отдельной операцией.",
                    "immutable_field",
                    403,
                    {"key": key},
                )
            capability = {
                "call": "call",
                "visit": "visit",
                "contact": "contact",
                "forecast": "promise",
                "accounting": "accounting",
            }[group(key)]
            try:
                access.contracts(conn, principal, [cid], capability=capability)
            except CRMError as error:
                if error.status!=403:raise
                raise CRMError(f'Нет права изменять поле «{titles[key]}».',error.code,403,
                    {'key':key,'capability':capability}) from error
            changes.append(
                dict(
                    contract_uuid=cid,
                    key=key,
                    baseline=base.get(key),
                    current=live.get(key),
                    incoming=desired,
                    conflict=live.get(key) != base.get(key),
                    group=group(key),
                )
            )
    state = {
        cid: dict(fields=r["fields_json"], revisions=r["revisions_json"])
        for cid, r in current.items()
        if cid in seen
    }
    return (
        snapshot,
        changes,
        dict(
            unchanged=unchanged,
            missing_rows=len(set(baseline) - seen),
            missing_rows_action="none",
        ),
        digest(state),
    )


def preview(principal, body):
    access.require(principal, "exchange")
    with storage.connect() as conn:
        store.begin(conn)
        snapshot, changes, counts, state_hash = _snapshot(conn, principal, body)
        identifier = str(uuid.uuid4())
        plan = dict(input=body, changes=changes, counts=counts)
        conn.execute(
            "INSERT INTO crm_import_previews VALUES(?,?,?,?,?,?,?,?)",
            (
                identifier,
                principal.actor_uid,
                snapshot["export_id"],
                digest(body),
                state_hash,
                canonical(plan),
                now(),
                None,
            ),
        )
        conn.commit()
    return dict(
        preview_id=identifier,
        file_sha256=body["file_sha256"],
        changes=changes,
        conflicts=sum(c["conflict"] for c in changes),
        **counts
    )


def apply(principal, body):
    access.require(principal, "exchange")
    if set(body) - {"operation_id", "preview_id", "file_sha256", "resolutions"}:
        raise CRMError("Неизвестные параметры применения.")
    operation = operation_id(body.get("operation_id"))
    at = now()
    with storage.connect() as conn:
        store.begin(conn)
        prior = store.replay(conn, principal, operation, body)
        if prior is not None:
            return prior
        principal=access.refresh_write(conn,principal)
        access.require(principal,'exchange')
        item = conn.execute(
            "SELECT * FROM crm_import_previews WHERE preview_id=? AND actor_uid=?",
            (body.get("preview_id"), principal.actor_uid),
        ).fetchone()
        if not item or item["consumed_by"]:
            raise CRMError(
                "Предпросмотр уже использован или недоступен.", "preview_expired", 409
            )
        if instant(item["created_at"]) < instant(at) - timedelta(hours=24):
            raise CRMError("Выполните новую сверку.", "preview_expired", 409)
        plan = json.loads(item["plan_json"])
        if body.get("file_sha256") != plan["input"]["file_sha256"]:
            raise CRMError("Выбран другой файл.", "file_changed", 409)
        _, changes, counts, state_hash = _snapshot(conn, principal, plan["input"])
        if state_hash != item["state_hash"]:
            raise CRMError(
                "Данные изменились после предпросмотра. Повторите сверку.",
                "preview_stale",
                409,
            )
        resolutions = body.get("resolutions", {})
        if not isinstance(resolutions, dict):
            raise CRMError("Некорректные решения конфликтов.")
        conflict_keys = {
            c["contract_uuid"] + ":" + c["key"] for c in changes if c["conflict"]
        }
        if set(resolutions) != conflict_keys or any(
            v not in {"server", "excel"} for v in resolutions.values()
        ):
            raise CRMError(
                "Для каждого конфликта нужен явный выбор.", "unresolved", 409
            )
        selected = {}
        for change in changes:
            key = change["contract_uuid"] + ":" + change["key"]
            if change["conflict"] and resolutions[key] == "server":
                continue
            selected.setdefault(change["contract_uuid"], {})[change["key"]] = change[
                "incoming"
            ]
        events = []
        for cid, values in selected.items():
            event = str(uuid.uuid4())
            row = dict(
                conn.execute(
                    "SELECT * FROM crm_contracts WHERE contract_uuid=?", (cid,)
                ).fetchone()
            )
            payload = dict(
                values=values,
                export_id=item["export_id"],
                file_sha256=body["file_sha256"],
            )
            conn.execute(
                """INSERT INTO crm_events(event_id,operation_id,request_hash,actor_uid,employee_uuid,team_id,role,kind,origin,
                contracts_json,payload_json,occurred_at,received_at,time_quality,projection_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event,
                    operation,
                    digest(body),
                    principal.actor_uid,
                    principal.employee_uuid,
                    principal.team_id,
                    principal.role,
                    "manual_edit",
                    "excel",
                    canonical([cid]),
                    canonical(payload),
                    at,
                    at,
                    "server",
                    "applied",
                ),
            )
            conn.execute("INSERT INTO crm_event_contracts VALUES(?,?)", (event, cid))
            conn.execute("INSERT INTO crm_outbox(event_id) VALUES(?)", (event,))
            store.project(conn, row, values, event, at, "excel")
            events.append(event)
        result = dict(
            ok=True,
            operation_id=operation,
            events=events,
            changed_rows=len(selected),
            changed_cells=sum(len(v) for v in selected.values()),
            **counts
        )
        conn.execute(
            "UPDATE crm_import_previews SET consumed_by=? WHERE preview_id=?",
            (operation, item["preview_id"]),
        )
        store.acknowledge(conn, principal, operation, body, result, at)
        store.audit(conn, principal, "import", operation, {"rows": len(selected)})
        conn.commit()
        return result


def receipt(principal, body):
    access.require(principal, "exchange")
    if set(body) - {
        "receipt_id",
        "export_id",
        "manifest_hash",
        "target_id",
        "file_sha256",
        "cells",
    }:
        raise CRMError("Неизвестные поля квитанции.")
    identifier = operation_id(body.get("receipt_id"))
    at = now()
    if not isinstance(body.get("file_sha256"), str) or not re.fullmatch(
        "[a-f0-9]{64}", body["file_sha256"]
    ):
        raise CRMError("Некорректный SHA-256.")
    with storage.connect() as conn:
        store.begin(conn)
        prior = conn.execute(
            "SELECT * FROM crm_delivery_receipts WHERE receipt_id=?", (identifier,)
        ).fetchone()
        if prior:
            if prior["actor_uid"] != principal.actor_uid or prior[
                "request_hash"
            ] != digest(body):
                raise CRMError(
                    "ID квитанции занят другими данными.", "receipt_conflict", 409
                )
            return dict(ok=True, receipt_id=identifier, deduplicated=True)
        snapshot = conn.execute(
            "SELECT * FROM crm_exports WHERE export_id=?", (body.get("export_id"),)
        ).fetchone()
        if (
            not snapshot
            or snapshot["manifest_hash"] != body.get("manifest_hash")
            or snapshot["target_id"] != body.get("target_id")
        ):
            raise CRMError("Маршрут или экспорт не совпадают.")
        baseline = json.loads(snapshot["baseline_json"])
        cells = body.get("cells")
        if not isinstance(cells, list) or len(cells) > 100000:
            raise CRMError("Некорректное чтение результата.")
        proven = {}
        seen = set()
        for cell in cells:
            if not isinstance(cell, dict) or set(cell) != {
                "contract_uuid",
                "key",
                "value",
                "event_id",
            }:
                raise CRMError("Некорректное подтверждение ячейки.")
            cid, key = cell["contract_uuid"], cell["key"]
            pair = (cid, key)
            if pair in seen:
                raise CRMError("Ячейка повторена.")
            seen.add(pair)
            access.contracts(conn, principal, [cid])
            base = baseline.get(cid, {})
            source = base.get("provenance", {}).get("_fields", {}).get(key, {})
            if (
                key not in base.get("values", {})
                or base["values"][key] != cell["value"]
                or source.get("event_id") != cell["event_id"]
            ):
                raise CRMError(
                    "Чтение не подтверждает исходное событие.", "readback_mismatch", 409
                )
            proven.setdefault(cell["event_id"], []).append(cell)
        recorded = []
        for event, candidates in proven.items():
            original = conn.execute(
                "SELECT * FROM crm_events WHERE event_id=?", (event,)
            ).fetchone()
            if not original or original["projection_status"] != "applied":
                continue
            target = conn.execute(
                "SELECT 1 FROM crm_delivery_targets WHERE event_id=? AND target_id=?",
                (event, body["target_id"]),
            ).fetchone()
            if not target:
                continue
            values = json.loads(original["payload_json"]).get("values", {})
            required = {
                (cid, key)
                for cid in json.loads(original["contracts_json"])
                for key in values
            }
            actual = {(c["contract_uuid"], c["key"]) for c in candidates}
            if not required or not required <= actual:
                continue
            rid = identifier if not recorded else identifier + "-" + str(len(recorded))
            conn.execute(
                "INSERT INTO crm_delivery_receipts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    event,
                    body["target_id"],
                    snapshot["export_id"],
                    body["file_sha256"],
                    canonical(sorted({p[0] for p in required})),
                    canonical(sorted(values)),
                    at,
                    principal.actor_uid,
                    digest(body),
                ),
            )
            recorded.append(event)
        if not recorded:
            raise CRMError(
                "Полная доставка ни одного ожидаемого события не подтверждена.",
                "incomplete_receipt",
                409,
            )
        conn.commit()
        return dict(ok=True, receipt_id=identifier, event_ids=recorded)
