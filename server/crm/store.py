"""Transactional event journal and deterministic current projections."""

from datetime import timedelta
import json
import uuid

import db
from . import access
from .contract import (
    CRMError,
    CALL_KEYS,
    VISIT_KEYS,
    CONTACT_KEYS,
    PROMISE_KEYS,
    KINDS,
    canonical,
    currency,
    digest,
    field_value,
    group,
    instant,
    money,
    now,
    operation_id,
)


def begin(conn):
    conn.execute("BEGIN IMMEDIATE")


def replay(conn, principal, operation, body):
    operation_id(operation)
    row = conn.execute(
        "SELECT * FROM crm_commands WHERE actor_uid=? AND operation_id=?",
        (principal.actor_uid, operation),
    ).fetchone()
    if row:
        if row["request_hash"] != digest(body):
            raise CRMError(
                "Этот ID уже использован для другого запроса.",
                "operation_conflict",
                409,
            )
        return json.loads(row["result_json"])
    return None


def acknowledge(conn, principal, operation, body, result, at):
    conn.execute(
        "INSERT INTO crm_commands VALUES(?,?,?,?,?)",
        (principal.actor_uid, operation, digest(body), canonical(result), at),
    )


def audit(conn, principal, kind, target, detail=None):
    conn.execute(
        "INSERT INTO crm_audit(actor_uid,kind,target_id,at,detail_json) VALUES(?,?,?,?,?)",
        (principal.actor_uid, kind, target, now(), canonical(detail or {})),
    )


def anchor(principal, installation):
    if not isinstance(installation, str) or not 12 <= len(installation) <= 100:
        raise CRMError("Некорректный идентификатор установки.")
    at = now()
    identifier = str(uuid.uuid4())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO crm_clock_anchors VALUES(?,?,?,?)",
            (identifier, principal.actor_uid, installation, at),
        )
        conn.commit()
    return dict(anchor_id=identifier, server_at=at, installation_id=installation)


def event_time(conn, principal, body, received):
    clock = body.get("clock") or {}
    if clock:
        if not isinstance(clock, dict) or set(clock) - {
            "anchor_id",
            "installation_id",
            "elapsed_ms",
        }:
            raise CRMError("Некорректная привязка времени.")
        row = conn.execute(
            "SELECT * FROM crm_clock_anchors WHERE anchor_id=? AND actor_uid=? AND installation_id=?",
            (clock.get("anchor_id"), principal.actor_uid, clock.get("installation_id")),
        ).fetchone()
        elapsed = clock.get("elapsed_ms")
        if not row or type(elapsed) is not int or not 0 <= elapsed <= 31 * 86400 * 1000:
            raise CRMError(
                "Время устройства не подтверждено серверной точкой отсчёта.",
                "clock_invalid",
                409,
            )
        happened = instant(row["server_at"]) + timedelta(milliseconds=elapsed)
        if happened > instant(received) + timedelta(minutes=5) or happened < instant(
            received
        ) - timedelta(days=31):
            raise CRMError("Время действия вне допустимого окна.", "clock_invalid", 409)
        return happened.isoformat(timespec="milliseconds"), "anchored"
    claimed = body.get("occurred_at")
    if claimed:
        at = instant(claimed)
        if at > instant(received) + timedelta(minutes=5) or at < instant(
            received
        ) - timedelta(days=365):
            raise CRMError("Некорректное время действия.")
        return at.isoformat(timespec="milliseconds"), "unverified"
    return received, "server"


def allowed_fields(kind):
    return {
        "call": CALL_KEYS,
        "visit": VISIT_KEYS,
        "contact": CONTACT_KEYS,
        "promise": PROMISE_KEYS,
    }.get(kind, set())


def _payload(kind, raw):
    if not isinstance(raw, dict):
        raise CRMError("Некорректные поля действия.")
    allowed = {
        "values",
        "clear",
        "text",
        "amount",
        "currency",
        "due_at",
        "title",
        "entity_id",
        "status",
        "source_id",
        "source",
        "document_id",
        "sha256",
        "filename",
        "media_type",
        "related_id",
        "target_kind",
        "entity_revision",
    }
    if set(raw) - allowed:
        raise CRMError("Неизвестные поля действия.")
    values = raw.get("values") or {}
    cleared = raw.get("clear") or []
    if (
        not isinstance(values, dict)
        or not isinstance(cleared, list)
        or not set(cleared) <= set(values)
    ):
        raise CRMError("Некорректная явная очистка.")
    target = raw.get("target_kind") if kind == "correction" else kind
    if target not in KINDS - {"correction"}:
        raise CRMError("Некорректный тип исправляемого действия.")
    if set(values) - allowed_fields(target):
        raise CRMError("Поле недоступно для этого действия.", "forbidden", 403)
    normalized = {
        key: field_value(key, value, clear=key in cleared)
        for key, value in values.items()
    }
    if target in {"call", "visit", "contact"} and not normalized:
        raise CRMError("Укажите результат действия.")
    if target == "note" and not str(raw.get("text") or "").strip():
        raise CRMError("Введите текст заметки.")
    if target == "task" and not str(raw.get("title") or "").strip():
        raise CRMError("Укажите задачу.")
    result = {**raw, "values": normalized, "clear": sorted(cleared)}
    for key in (
        "text",
        "title",
        "filename",
        "source",
        "source_id",
        "document_id",
        "related_id",
        "entity_id",
    ):
        if key in raw and (
            not isinstance(raw[key], str)
            or len(raw[key]) > (32700 if key == "text" else 300)
        ):
            raise CRMError("Некорректный текст действия.")
    if target in {"promise", "reported_payment", "payment_confirmed"}:
        result["amount"] = money(raw.get("amount"))
        result["currency"] = currency(raw.get("currency"))
        if result["amount"].startswith("-") and kind != "correction":
            raise CRMError("Отрицательная сумма допускается только в корректировке.")
    if target in {"promise", "task"}:
        result["due_at"] = instant(raw.get("due_at")).isoformat()
    if target == "payment_confirmed" and (
        not raw.get("source_id") or not raw.get("source")
    ):
        raise CRMError("Для поступления нужен идентификатор учётного источника.")
    if target == "document":
        import re

        if not raw.get("document_id") or not re.fullmatch(
            "[a-f0-9]{64}", str(raw.get("sha256", ""))
        ):
            raise CRMError("Документ должен быть сохранён и проверен по SHA-256.")
    if target == "promise" and normalized:
        if not {"promise_date", "forecast_sum"} <= set(normalized):
            raise CRMError("Дата и сумма обещания изменяются вместе.")
        if normalized["forecast_sum"] != result["amount"]:
            raise CRMError("Суммы обещания должны совпадать.")
    return result, target


def command(principal, body, *, origin="app", client_version=None):
    import re

    if client_version is not None and (
        not isinstance(client_version, str)
        or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-z0-9.]+)?", client_version)
        or len(client_version) > 40
    ):
        raise CRMError("Некорректная версия клиента.")
    if not isinstance(body, dict) or set(body) - {
        "operation_id",
        "kind",
        "contract_ids",
        "payload",
        "base_revisions",
        "occurred_at",
        "clock",
        "correction_of",
        "reason",
        "schema_version",
    }:
        raise CRMError("Неизвестный формат операции.")
    if body.get("schema_version") != 1:
        raise CRMError(
            "Обновите приложение: другая схема операции.", "schema_mismatch", 409
        )
    kind = body.get("kind")
    if kind not in KINDS:
        raise CRMError("Неизвестное действие.")
    operation = operation_id(body.get("operation_id"))
    received = now()
    payload, target = _payload(kind, body.get("payload", {}))
    capability = (
        "correct"
        if kind == "correction"
        else (
            "accounting"
            if kind == "payment_confirmed"
            else "task" if kind == "task_complete" else kind
        )
    )
    with db.connect() as conn:
        begin(conn)
        previous = replay(conn, principal, operation, body)
        if previous is not None:
            return previous
        rows = access.contracts(
            conn, principal, body.get("contract_ids"), capability=capability
        )
        if len({r["person_uuid"] for r in rows}) != 1:
            raise CRMError(
                "Для группового действия выберите договоры одного подтверждённого клиента."
            )
        if (
            target
            in {
                "promise",
                "task",
                "reported_payment",
                "payment_confirmed",
                "document",
                "task_complete",
            }
            and len(rows) != 1
        ):
            raise CRMError(
                "Для суммы, задачи или документа выберите конкретный договор."
            )
        if target == "promise":
            from zoneinfo import ZoneInfo

            if rows[0]["currency"] and payload["currency"] != rows[0]["currency"]:
                raise CRMError(
                    "Валюта обещания отличается от валюты договора. Пересчёт автоматически не выполняется."
                )
            payload["values"] = {
                **payload["values"],
                "forecast_sum": payload["amount"],
                "promise_date": instant(payload["due_at"])
                .astimezone(ZoneInfo(principal.timezone))
                .date()
                .isoformat(),
            }
        if target == "document":
            attachment = conn.execute(
                "SELECT * FROM crm_documents WHERE document_id=? AND contract_uuid=?",
                (payload["document_id"], rows[0]["contract_uuid"]),
            ).fetchone()
            if not attachment or attachment["sha256"] != payload["sha256"]:
                raise CRMError(
                    "Файл документа не сохранён или не совпадает по SHA-256.",
                    "document_missing",
                    409,
                )
            from .documents import verify

            verify(attachment)
        correction = body.get("correction_of")
        reason = body.get("reason")
        if kind == "correction":
            old = conn.execute(
                "SELECT * FROM crm_events WHERE event_id=?", (correction,)
            ).fetchone()
            if (
                not old
                or not isinstance(reason, str)
                or not 1 <= len(reason.strip()) <= 1000
            ):
                raise CRMError("Исправление требует исходного события и причины.")
            if old[
                "employee_uuid"
            ] != principal.employee_uuid and principal.role not in {"manager", "owner"}:
                raise CRMError(
                    "Можно исправить только своё действие.", "forbidden", 403
                )
            if old["kind"] != target or set(json.loads(old["contracts_json"])) != set(
                body["contract_ids"]
            ):
                raise CRMError("Исправление не может изменить вид и охват события.")
        elif correction or reason:
            raise CRMError("Причина и ссылка используются только для исправления.")
        old_entity = None
        if target in {
            "promise",
            "task",
            "reported_payment",
            "payment_confirmed",
            "document",
        }:
            if kind == "correction":
                old_entity = conn.execute(
                    "SELECT * FROM crm_entities WHERE event_id=?", (correction,)
                ).fetchone()
                if not old_entity:
                    raise CRMError("Исправляемая запись не найдена.", "not_found", 404)
                payload["entity_id"] = old_entity["entity_id"]
            else:
                payload["entity_id"] = str(uuid.uuid4())
        happened, quality = event_time(conn, principal, body, received)
        event = str(uuid.uuid4())
        groups = {group(k) for k in payload["values"]}
        expected = body.get("base_revisions") or {}
        if not isinstance(expected, dict):
            raise CRMError("Некорректные версии полей.")
        conflicts = []
        for row in rows:
            revisions = json.loads(row["revisions_json"])
            provenance = json.loads(row["provenance_json"])
            for field_group in groups:
                if expected.get(row["contract_uuid"], {}).get(
                    field_group
                ) != revisions.get(field_group, 0):
                    conflicts.append(
                        dict(
                            contract_uuid=row["contract_uuid"],
                            group=field_group,
                            reason="revision_changed",
                        )
                    )
                elif provenance.get(field_group, {}).get("occurred_at", "") > happened:
                    conflicts.append(
                        dict(
                            contract_uuid=row["contract_uuid"],
                            group=field_group,
                            reason="older_event",
                        )
                    )
        if conflicts and target not in {"call", "visit", "note"}:
            raise CRMError(
                "Карточка изменилась. Сверьте введённые данные.",
                "revision_conflict",
                409,
                conflicts,
            )
        status = "needs_review" if conflicts else "applied"
        if target == "payment_confirmed":
            duplicate = conn.execute(
                "SELECT data_json FROM crm_entities WHERE kind='payment_confirmed'"
            ).fetchall()
            if any(
                (d := json.loads(r[0])).get("source") == payload["source"]
                and d.get("source_id") == payload["source_id"]
                and d.get("entity_id") != payload.get("entity_id")
                for r in duplicate
            ):
                raise CRMError(
                    "Поступление с таким идентификатором уже учтено.",
                    "payment_duplicate",
                    409,
                )
        conn.execute(
            """INSERT INTO crm_events(event_id,operation_id,request_hash,actor_uid,employee_uuid,team_id,role,
            kind,origin,contracts_json,payload_json,occurred_at,received_at,time_quality,correction_of,reason,projection_status,client_version)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event,
                operation,
                digest(body),
                principal.actor_uid,
                principal.employee_uuid,
                principal.team_id,
                principal.role,
                kind,
                origin,
                canonical(sorted(body["contract_ids"])),
                canonical(payload),
                happened,
                received,
                quality,
                correction,
                reason,
                status,
                client_version,
            ),
        )
        conn.executemany(
            "INSERT INTO crm_event_contracts VALUES(?,?)",
            [(event, r["contract_uuid"]) for r in rows],
        )
        conn.execute("INSERT INTO crm_outbox(event_id) VALUES(?)", (event,))
        if not conflicts:
            for row in rows:
                project(conn, row, payload["values"], event, happened, origin)
            if target in {
                "promise",
                "task",
                "reported_payment",
                "payment_confirmed",
                "document",
            }:
                entity = payload["entity_id"]
                if kind == "correction":
                    conn.execute(
                        "UPDATE crm_entities SET event_id=?,data_json=?,revision=revision+1,updated_at=? WHERE entity_id=?",
                        (event, canonical(payload), received, entity),
                    )
                else:
                    conn.execute(
                        "INSERT INTO crm_entities VALUES(?,?,?,?,?,?,?,?)",
                        (
                            entity,
                            target,
                            rows[0]["contract_uuid"],
                            event,
                            canonical(payload),
                            1,
                            "open",
                            received,
                        ),
                    )
            if target == "task_complete":
                entity = conn.execute(
                    "SELECT * FROM crm_entities WHERE entity_id=? AND kind='task' AND contract_uuid=?",
                    (payload.get("entity_id"), rows[0]["contract_uuid"]),
                ).fetchone()
                if not entity:
                    raise CRMError("Задача не найдена.", "not_found", 404)
                if (
                    entity["status"] != "open"
                    or payload.get("entity_revision") != entity["revision"]
                ):
                    raise CRMError(
                        "Задача уже изменена или завершена.", "revision_conflict", 409
                    )
                conn.execute(
                    "UPDATE crm_entities SET status='done',revision=revision+1,updated_at=? WHERE entity_id=?",
                    (received, entity["entity_id"]),
                )
        for policy in conn.execute(
            "SELECT * FROM crm_route_policies WHERE active=1 AND team_id=? AND kind=?",
            (principal.team_id, target),
        ):
            for route in json.loads(policy["targets_json"]):
                due = (
                    (
                        instant(received) + timedelta(seconds=route["delay_seconds"])
                    ).isoformat()
                    if route.get("delay_seconds") is not None
                    else None
                )
                conn.execute(
                    "INSERT INTO crm_delivery_targets VALUES(?,?,?,?,?)",
                    (
                        event,
                        route["target_id"],
                        policy["policy_id"],
                        policy["version"],
                        due,
                    ),
                )
        result = dict(
            ok=True,
            event_id=event,
            operation_id=operation,
            status="accepted",
            projection_status=status,
            conflicts=conflicts,
            received_at=received,
            occurred_at=happened,
            time_quality=quality,
            entity_id=payload.get("entity_id"),
        )
        acknowledge(conn, principal, operation, body, result, received)
        conn.commit()
    return result


def rebuild(principal):
    if not principal.owner:
        raise CRMError("Пересборка доступна владельцу.", "forbidden", 403)
    with db.connect() as conn:
        begin(conn)
        conn.execute(
            "UPDATE crm_contracts SET fields_json=baseline_json,revisions_json='{}',provenance_json='{}'"
        )
        conn.execute("DELETE FROM crm_entities")
        count = 0
        for event in conn.execute(
            "SELECT * FROM crm_events WHERE projection_status='applied' ORDER BY sequence"
        ).fetchall():
            payload = json.loads(event["payload_json"])
            ids = json.loads(event["contracts_json"])
            kind = event["kind"]
            target = payload.get("target_kind") if kind == "correction" else kind
            for cid in ids:
                row = dict(
                    conn.execute(
                        "SELECT * FROM crm_contracts WHERE contract_uuid=?", (cid,)
                    ).fetchone()
                )
                if payload.get("values"):
                    project(
                        conn,
                        row,
                        payload["values"],
                        event["event_id"],
                        event["occurred_at"],
                        event["origin"],
                    )
            if target in {
                "promise",
                "task",
                "reported_payment",
                "payment_confirmed",
                "document",
            }:
                entity = payload.get("entity_id")
                if not entity:
                    raise CRMError(
                        "Событие не содержит постоянный ID сущности; пересборка остановлена.",
                        "incomplete_event",
                        409,
                    )
                if kind == "correction":
                    if (
                        conn.execute(
                            "UPDATE crm_entities SET event_id=?,data_json=?,revision=revision+1,updated_at=? WHERE entity_id=?",
                            (
                                event["event_id"],
                                canonical(payload),
                                event["received_at"],
                                entity,
                            ),
                        ).rowcount
                        != 1
                    ):
                        raise CRMError(
                            "Отсутствует исходная сущность.", "incomplete_event", 409
                        )
                else:
                    conn.execute(
                        "INSERT INTO crm_entities VALUES(?,?,?,?,?,?,?,?)",
                        (
                            entity,
                            target,
                            ids[0],
                            event["event_id"],
                            canonical(payload),
                            1,
                            "open",
                            event["received_at"],
                        ),
                    )
            if target == "task_complete":
                conn.execute(
                    "UPDATE crm_entities SET status='done',revision=revision+1,updated_at=? WHERE entity_id=?",
                    (event["received_at"], payload["entity_id"]),
                )
            count += 1
        audit(conn, principal, "projection_rebuild", "crm", {"events": count})
        conn.commit()
        return dict(ok=True, events=count)


def project(conn, row, values, event, happened, origin):
    current = json.loads(row["fields_json"])
    revisions = json.loads(row["revisions_json"])
    provenance = json.loads(row["provenance_json"])
    current.update(values)
    for field_group in {group(key) for key in values}:
        revisions[field_group] = revisions.get(field_group, 0) + 1
        provenance[field_group] = dict(
            event_id=event, occurred_at=happened, origin=origin
        )
    for key in values:
        provenance.setdefault("_fields", {})[key] = dict(
            event_id=event, occurred_at=happened, origin=origin
        )
    conn.execute(
        "UPDATE crm_contracts SET fields_json=?,revisions_json=?,provenance_json=? WHERE contract_uuid=?",
        (
            canonical(current),
            canonical(revisions),
            canonical(provenance),
            row["contract_uuid"],
        ),
    )
    # Canonical project() is the only writer during the explicit pilot cutover.
    # Keep legacy IDs and readers valid; legacy mutation routes are guarded.
    if row.get("legacy_id") and values:
        from fields import BY_KEY

        valid = {key: value for key, value in values.items() if key in BY_KEY}
        if valid:
            sql = ",".join('"' + key + '"=?' for key in valid)
            from .legacy import projection

            with projection():
                conn.execute(
                    "UPDATE clients SET " + sql + " WHERE contract_id=?",
                    (*valid.values(), row["legacy_id"]),
                )


def card(principal, person_uuid):
    with db.connect() as conn:
        allowed = access.scoped_contracts(conn, principal)
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM crm_contracts WHERE person_uuid=? ORDER BY creditor_id,external_id",
                (person_uuid,),
            )
            if r["contract_uuid"] in allowed
        ]
        if not rows:
            raise CRMError("Клиент недоступен.", "not_found", 404)
        person = dict(
            conn.execute(
                "SELECT * FROM crm_people WHERE person_uuid=?", (person_uuid,)
            ).fetchone()
        )
        ids = {r["contract_uuid"] for r in rows}
        events = {}
        for cid in ids:
            for event in conn.execute(
                "SELECT e.*,staff.name AS author FROM crm_events e JOIN crm_event_contracts c USING(event_id) LEFT JOIN crm_employees staff ON staff.employee_uuid=e.employee_uuid WHERE c.contract_uuid=? ORDER BY sequence DESC LIMIT 500",
                (cid,),
            ):
                item = dict(event)
                item["payload"] = json.loads(item.pop("payload_json"))
                item["contract_ids"] = [
                    i for i in json.loads(item.pop("contracts_json")) if i in ids
                ]
                events[item["event_id"]] = item
        entities = []
        for cid in ids:
            for entity in conn.execute(
                "SELECT * FROM crm_entities WHERE contract_uuid=? ORDER BY updated_at DESC",
                (cid,),
            ):
                item = dict(entity)
                item["data"] = json.loads(item.pop("data_json"))
                entities.append(item)
        for row in rows:
            row["fields"] = json.loads(row.pop("fields_json"))
            row["revisions"] = {
                key: 0 for key in ("call", "visit", "contact", "forecast", "accounting")
            }
            row["revisions"].update(json.loads(row.pop("revisions_json")))
            row["provenance"] = json.loads(row.pop("provenance_json"))
            row.pop("baseline_json")
        return dict(
            person=person,
            contracts=rows,
            history=sorted(events.values(), key=lambda x: x["sequence"], reverse=True),
            entities=entities,
        )


def portfolio(principal, search="", limit=200):
    from decimal import Decimal

    with db.connect() as conn:
        allowed = access.scoped_contracts(conn, principal)
        people = {}
        for row in conn.execute(
            "SELECT c.*,p.name FROM crm_contracts c JOIN crm_people p USING(person_uuid) ORDER BY p.name,c.external_id"
        ):
            if row["contract_uuid"] not in allowed:
                continue
            values = json.loads(row["fields_json"])
            if (
                search
                and search.casefold()
                not in (
                    " ".join(
                        [
                            row["name"],
                            row["external_id"],
                            str(values.get("phone") or ""),
                            str(values.get("address") or ""),
                        ]
                    )
                ).casefold()
            ):
                continue
            item = people.setdefault(
                row["person_uuid"],
                dict(
                    person_uuid=row["person_uuid"],
                    name=row["name"],
                    contracts=0,
                    currencies={},
                    unknown_amounts=0,
                ),
            )
            item["contracts"] += 1
            value = values.get("debt_today")
            if value in (None, ""):
                value = values.get("debt_total")
            try:
                code = currency(row["currency"])
                exact = money(value)
                item["currencies"][code] = format(
                    Decimal(item["currencies"].get(code, "0")) + Decimal(exact), ".2f"
                )
            except CRMError:
                item["unknown_amounts"] += 1
        items = list(people.values())
        return dict(
            people=items[: min(max(int(limit), 1), 500)],
            total_people=len(items),
            total_contracts=sum(x["contracts"] for x in items),
            truncated=len(items) > limit,
        )
