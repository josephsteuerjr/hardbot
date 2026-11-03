"""Server-clock attendance declarations; never infer work from an open window."""

import json
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from . import storage
from . import access, store
from .contract import CRMError, canonical, instant, now, operation_id


def command(principal, body):
    if set(body) - {
        "operation_id",
        "action",
        "shift_id",
        "revision",
        "reason",
        "starts_at",
        "ends_at",
        "employee_uuid",
    }:
        raise CRMError("Неизвестные поля смены.")
    action = body.get("action")
    operation = operation_id(body.get("operation_id"))
    at = now()
    if action not in {"start", "break", "resume", "finish", "correct"}:
        raise CRMError("Неизвестное действие смены.")
    employee = body.get("employee_uuid", principal.employee_uuid)
    if employee != principal.employee_uuid and action != "correct":
        raise CRMError("Смена другого сотрудника недоступна.", "forbidden", 403)
    with storage.connect() as conn:
        store.begin(conn, [storage.operation_lock(principal, operation), "shift:" + str(employee)])
        prior = store.replay(conn, principal, operation, body)
        if prior is not None:
            return prior
        access.employee(
            conn, principal, employee, "shift_correct" if action == "correct" else None
        )
        if action == "start":
            if any(k in body for k in ("starts_at", "ends_at", "shift_id", "reason")):
                raise CRMError("Начало фиксируется сервером.")
            if conn.execute(
                "SELECT 1 FROM crm_shifts WHERE employee_uuid=? AND ends_at IS NULL",
                (employee,),
            ).fetchone():
                raise CRMError("Смена уже открыта.", "shift_open", 409)
            identifier = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO crm_shifts VALUES(?,?,?,?,?,?,?)",
                (identifier, employee, at, None, principal.timezone, "working", 1),
            )
        else:
            row = conn.execute(
                "SELECT * FROM crm_shifts WHERE shift_id=? AND employee_uuid=?",
                (body.get("shift_id"), employee),
            ).fetchone()
            if not row:
                raise CRMError("Смена не найдена.", "not_found", 404)
            identifier = row["shift_id"]
            if body.get("revision") != row["revision"]:
                raise CRMError("Состояние смены изменилось.", "revision_conflict", 409)
            if action == "correct":
                reason = body.get("reason")
                if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
                    raise CRMError("Нужна причина исправления.")
                start = instant(body.get("starts_at"))
                end = instant(body.get("ends_at"))
                if (
                    not start < end <= instant(at)
                    or (end - start).total_seconds() > 48 * 3600
                ):
                    raise CRMError("Некорректные границы смены.")
                overlaps = conn.execute(
                    "SELECT starts_at,ends_at FROM crm_shifts WHERE employee_uuid=? AND shift_id<>?",
                    (employee, identifier),
                )
                if any(
                    start < instant(r["ends_at"] or at)
                    and end > instant(r["starts_at"])
                    for r in overlaps
                ):
                    raise CRMError(
                        "Исправление пересекается с другой сменой.",
                        "shift_overlap",
                        409,
                    )
                conn.execute(
                    "UPDATE crm_shifts SET starts_at=?,ends_at=?,state='corrected',revision=revision+1 WHERE shift_id=?",
                    (start.isoformat(), end.isoformat(), identifier),
                )
            else:
                if any(k in body for k in ("starts_at", "ends_at", "reason")):
                    raise CRMError("Время действия фиксируется сервером.")
                if row["ends_at"] is not None:
                    raise CRMError("Смена закрыта.", "shift_closed", 409)
                expected = (
                    "working"
                    if action == "break"
                    else "break" if action == "resume" else row["state"]
                )
                if row["state"] != expected:
                    raise CRMError("Недопустимый переход смены.", "shift_state", 409)
                conn.execute(
                    "UPDATE crm_shifts SET state=?,ends_at=?,revision=revision+1 WHERE shift_id=?",
                    (
                        (
                            "finished"
                            if action == "finish"
                            else "break" if action == "break" else "working"
                        ),
                        at if action == "finish" else None,
                        identifier,
                    ),
                )
        conn.execute(
            "INSERT INTO crm_shift_events VALUES(?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                identifier,
                principal.actor_uid,
                action,
                at,
                canonical(body),
                body.get("reason"),
            ),
        )
        result = dict(
            conn.execute(
                "SELECT * FROM crm_shifts WHERE shift_id=?", (identifier,)
            ).fetchone()
        )
        store.acknowledge(conn, principal, operation, body, result, at)
        conn.commit()
        return result


def summary(principal, employee=None):
    employee = employee or principal.employee_uuid
    at = now()
    with storage.connect() as conn:
        access.employee(conn, principal, employee)
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM crm_shifts WHERE employee_uuid=? ORDER BY starts_at DESC LIMIT 90",
                (employee,),
            )
        ]
        for row in rows:
            events = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM crm_shift_events WHERE shift_id=? ORDER BY server_at,event_id",
                    (row["shift_id"],),
                )
            ]
            start, end = instant(row["starts_at"]), instant(row["ends_at"] or at)
            pause = None
            seconds = 0
            for event in events:
                when = instant(event["server_at"])
                if event["kind"] == "break":
                    pause = when
                elif event["kind"] in {"resume", "finish"} and pause:
                    seconds += max(
                        0, (min(when, end) - max(pause, start)).total_seconds()
                    )
                    pause = None
            if pause:
                seconds += max(0, (end - max(pause, start)).total_seconds())
            duration = max(0, (end - start).total_seconds())
            row.update(
                elapsed_seconds=int(duration),
                break_seconds=min(int(seconds), int(duration)),
                declared_seconds=max(0, int(duration - seconds)),
                unclosed=row["ends_at"] is None,
                workday=start.astimezone(ZoneInfo(row["timezone"])).date().isoformat(),
                events=events,
            )
        return dict(
            shifts=rows,
            as_of=at,
            meaning="Заявленная длительность смены; не табель и не расчёт оплаты.",
        )


def set_plan(principal, body):
    access.require(principal, "shift_correct")
    if set(body) - {"operation_id", "employee_uuid", "shifts"}:
        raise CRMError("Неизвестные параметры графика.")
    operation = operation_id(body.get("operation_id"))
    items = body.get("shifts")
    at = now()
    if not isinstance(items, list) or not 1 <= len(items) <= 366:
        raise CRMError("Укажите до 366 смен.")
    parsed = []
    for item in items:
        if set(item) != {"starts_at", "ends_at", "timezone", "state", "source"}:
            raise CRMError("Не задан источник или время смены.")
        try:
            zone = ZoneInfo(item["timezone"])
        except (ZoneInfoNotFoundError, TypeError):
            raise CRMError("Неизвестный часовой пояс.")
        start, end = instant(item["starts_at"]), instant(item["ends_at"])
        if not start < end or (end - start).total_seconds() > 48 * 3600:
            raise CRMError("Некорректное окно смены.")
        if (
            item["state"] not in {"planned", "leave", "sick", "off", "suspended"}
            or not isinstance(item["source"], str)
            or not 1 <= len(item["source"]) <= 300
        ):
            raise CRMError("Неизвестный статус или источник графика.")
        if any(start < other[1] and end > other[0] for other in parsed):
            raise CRMError("Плановые смены пересекаются.")
        parsed.append((start, end, item, start.astimezone(zone).date().isoformat()))
    with storage.connect() as conn:
        store.begin(conn)
        prior = store.replay(conn, principal, operation, body)
        if prior is not None:
            return prior
        access.employee(conn, principal, body.get("employee_uuid"))
        for start, end, item, workday in parsed:
            overlaps = conn.execute(
                "SELECT starts_at,ends_at FROM crm_planned_shifts WHERE employee_uuid=? AND starts_at<>?",
                (body["employee_uuid"], start.isoformat()),
            )
            if any(
                start < instant(r["ends_at"]) and end > instant(r["starts_at"])
                for r in overlaps
            ):
                raise CRMError("План пересекается с существующей сменой.")
            conn.execute(
                """INSERT INTO crm_planned_shifts VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(employee_uuid,starts_at)
                DO UPDATE SET ends_at=excluded.ends_at,timezone=excluded.timezone,workday=excluded.workday,state=excluded.state,source=excluded.source""",
                (
                    str(uuid.uuid4()),
                    body["employee_uuid"],
                    start.isoformat(),
                    end.isoformat(),
                    item["timezone"],
                    workday,
                    item["state"],
                    item["source"],
                ),
            )
        result = dict(ok=True, planned=len(parsed))
        store.acknowledge(conn, principal, operation, body, result, at)
        store.audit(
            conn, principal, "plan", body["employee_uuid"], {"count": len(parsed)}
        )
        conn.commit()
        return result
