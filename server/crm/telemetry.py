"""Allowlisted technical diagnostics; no free text or debtor identifiers."""

import re
from datetime import timedelta
from . import storage
from . import store, access
from .contract import CRMError, canonical, digest, instant, now, operation_id

EVENTS = {
    "login_ok",
    "client_ready",
    "feature_opened",
    "workflow_started",
    "command_queued",
    "command_rejected",
    "sync_state_changed",
    "sync_failed",
    "script_shown",
    "script_step_used",
    "boot_ok",
    "boot_error",
    "render_error",
    "shell_update",
    "queue_gap",
    "report_viewed",
}
FEATURES = {
    "shell",
    "portfolio",
    "card",
    "call",
    "visit",
    "note",
    "contact",
    "promise",
    "task",
    "reported_payment",
    "document",
    "script",
    "shift",
    "exchange",
    "adoption",
}
ERRORS = {
    "offline",
    "timeout",
    "auth",
    "forbidden",
    "scope_changed",
    "revision_conflict",
    "operation_conflict",
    "file_locked",
    "file_changed",
    "baseline_missing",
    "schema_mismatch",
    "unresolved",
    "server",
    "render",
    "update",
    "queue_full",
    "unknown",
}
KEYS = {
    "schema_version",
    "event_id",
    "event_type",
    "producer",
    "installation_id",
    "session_id",
    "client_version",
    "feature",
    "operation_id",
    "occurred_at",
    "error_code",
    "duration_ms",
    "dropped_count",
}


def ingest(principal, body):
    if (
        not isinstance(body, dict)
        or set(body) != {"events"}
        or not isinstance(body["events"], list)
        or not 1 <= len(body["events"]) <= 100
        or len(canonical(body).encode()) > 65536
    ):
        raise CRMError("Пакет диагностики превышает пределы.", "telemetry_invalid", 413)
    at = now()
    items = []
    for raw in body["events"]:
        if (
            not isinstance(raw, dict)
            or set(raw) - KEYS
            or raw.get("schema_version") != 1
            or raw.get("producer") != "desktop"
        ):
            raise CRMError("Неизвестная схема диагностики.")
        if raw.get("event_type") not in EVENTS or raw.get("feature") not in FEATURES:
            raise CRMError("Неизвестное диагностическое событие.")
        for key in ("event_id", "installation_id", "session_id"):
            operation_id(raw.get(key))
        version = raw.get("client_version")
        if (
            not isinstance(version, str)
            or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-z0-9.]+)?", version)
            or len(version) > 40
        ):
            raise CRMError("Некорректная версия клиента.")
        if raw.get("operation_id") is not None:
            operation_id(raw["operation_id"])
        if raw.get("error_code") is not None and raw["error_code"] not in ERRORS:
            raise CRMError("Неизвестный код диагностики.")
        duration = raw.get("duration_ms")
        dropped = raw.get("dropped_count", 0)
        if duration is not None and (
            type(duration) is not int or not 0 <= duration <= 86400000
        ):
            raise CRMError("Некорректная длительность.")
        if type(dropped) is not int or not 0 <= dropped <= 1000000:
            raise CRMError("Некорректное число пропусков.")
        if dropped and raw["event_type"] != "queue_gap":
            raise CRMError("Пропуски указываются отдельным событием.")
        if raw.get("occurred_at"):
            instant(
                raw["occurred_at"]
            )  # Untrusted: never used as attendance or business time.
        items.append(raw)
    with storage.connect() as conn:
        store.begin(conn, ["telemetry:" + str(principal.actor_uid)])
        cutoff = (instant(at) - timedelta(minutes=1)).isoformat()
        count = conn.execute(
            "SELECT COUNT(*) FROM crm_telemetry WHERE actor_uid=? AND received_at>=?",
            (principal.actor_uid, cutoff),
        ).fetchone()[0]
        novel = 0
        for raw in items:
            prior = conn.execute(
                "SELECT request_hash,actor_uid FROM crm_telemetry WHERE event_id=?",
                (raw["event_id"],),
            ).fetchone()
            if prior:
                if (
                    prior["request_hash"] != digest(raw)
                    or prior["actor_uid"] != principal.actor_uid
                ):
                    raise CRMError(
                        "ID диагностики содержит другой запрос.",
                        "operation_conflict",
                        409,
                    )
                continue
            novel += 1
            if count + novel > 300:
                raise CRMError(
                    "Слишком частая диагностика. Повторите позже.",
                    "telemetry_rate",
                    429,
                )
            conn.execute(
                "INSERT INTO crm_telemetry VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    raw["event_id"],
                    digest(raw),
                    principal.actor_uid,
                    principal.employee_uuid,
                    principal.team_id,
                    raw["installation_id"],
                    raw["session_id"],
                    raw["client_version"],
                    raw["event_type"],
                    raw["feature"],
                    raw.get("operation_id"),
                    raw.get("error_code"),
                    raw.get("duration_ms"),
                    raw.get("occurred_at"),
                    at,
                    raw.get("dropped_count", 0),
                ),
            )
        conn.commit()
    return dict(
        ok=True, accepted_ids=[r["event_id"] for r in items], new=novel, received_at=at
    )


def prune(principal, days):
    if not principal.owner:
        raise CRMError("Настройка хранения доступна владельцу.", "forbidden", 403)
    if type(days) is not int or not 30 <= days <= 365:
        raise CRMError("Срок сырых технических событий: от 30 до 365 дней.")
    cutoff = (instant(now()) - timedelta(days=days)).isoformat()
    with storage.connect() as conn:
        store.begin(conn)
        count = conn.execute(
            "DELETE FROM crm_telemetry WHERE received_at<?", (cutoff,)
        ).rowcount
        conn.execute(
            "INSERT INTO crm_meta VALUES('telemetry_retained_since',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (cutoff,),
        )
        store.audit(
            conn,
            principal,
            "telemetry_retention",
            "telemetry",
            {"days": days, "deleted": count, "cutoff": cutoff},
        )
        conn.commit()
    return dict(deleted=count, retained_since=cutoff)
