"""Canonical team adapter for the reviewed draft/publish call-script workflow."""

import json, re, uuid
from . import storage
from call_scripts import _content, ScriptError
from . import store
from .contract import CRMError, canonical, digest, now, operation_id


def _state(conn, p):
    row = conn.execute(
        "SELECT * FROM crm_scripts WHERE team_id=?", (p.team_id,)
    ).fetchone()
    editing = p.role in {"owner", "manager"} and (
        p.owner or "analytics" in p.capabilities
    )
    published = json.loads(row["published_json"]) if row and row["published_json"] else None
    return dict(
        revision=row["revision"] if row else 0,
        script=(
            json.loads(row["published_json"]) if row and row["published_json"] else None
        ),
        published_revision=row["published_revision"] if row else None,
        published_at=row["published_at"] if row else None,
        draft=(
            json.loads(row["draft_json"])
            if editing and row and row["draft_json"]
            else None
        ),
        can_edit=editing,
        script_ref=(dict(team_id=p.team_id, published_revision=row["published_revision"],
                         script_hash=digest(published)) if published else None),
    )


def validate_reference(conn, principal, reference):
    """Accept a known publication, including one pinned before an offline call."""
    if (not isinstance(reference, dict)
        or set(reference) != {"team_id", "published_revision", "script_hash"}
        or reference.get("team_id") != principal.team_id
        or type(reference.get("published_revision")) is not int
        or reference["published_revision"] < 1
        or not isinstance(reference.get("script_hash"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", reference["script_hash"])):
        raise CRMError("Некорректная ссылка на версию подсказки.")
    for row in conn.execute("SELECT payload_json FROM crm_events WHERE team_id=? AND kind='script_publish'", (principal.team_id,)):
        publication = json.loads(row[0])
        if (publication.get("script_revision") == reference["published_revision"]
            and publication.get("script_hash") == reference["script_hash"]):
            return
    raise CRMError("Версия подсказки не подтверждена публикацией.", "script_reference_unknown", 409)


def state(p):
    with storage.connect() as conn:
        return _state(conn, p)


def mutate(p, body):
    if p.role not in {"owner", "manager"} or (
        not p.owner and "analytics" not in p.capabilities
    ):
        raise CRMError("Скрипт редактирует руководитель команды.", "forbidden", 403)
    if set(body) - {"operation_id", "action", "base_revision", "content"} or body.get(
        "action"
    ) not in {"save", "publish", "withdraw"}:
        raise CRMError("Некорректное изменение скрипта.")
    operation = operation_id(body.get("operation_id"))
    at = now()
    action = body["action"]
    content = None
    if action == "save":
        try:
            content = _content(body.get("content"))
        except ScriptError as exc:
            raise CRMError(str(exc))
    elif "content" in body:
        raise CRMError("Публикуется только сохранённый черновик.")
    with storage.connect() as conn:
        store.begin(conn, [storage.operation_lock(p, operation), "script:" + p.team_id])
        prior = store.replay(conn, p, operation, body)
        if prior is not None:
            return prior
        previous = conn.execute(
            "SELECT * FROM crm_scripts WHERE team_id=?", (p.team_id,)
        ).fetchone()
        revision = previous["revision"] if previous else 0
        if body.get("base_revision") != revision:
            raise CRMError(
                "Скрипт изменился. Сохраните свои правки и обновите версию.",
                "revision_conflict",
                409,
            )
        if action == "save":
            conn.execute(
                """INSERT INTO crm_scripts VALUES(?,?,?,NULL,NULL,NULL,?) ON CONFLICT(team_id)
                DO UPDATE SET revision=excluded.revision,draft_json=excluded.draft_json,author_uid=excluded.author_uid""",
                (p.team_id, revision + 1, canonical(content), p.actor_uid),
            )
        else:
            if not previous or not previous["draft_json"]:
                raise CRMError("Сначала сохраните черновик.")
            conn.execute(
                "UPDATE crm_scripts SET revision=?,published_json=?,published_revision=?,published_at=?,author_uid=? WHERE team_id=?",
                (
                    revision + 1,
                    previous["draft_json"] if action == "publish" else None,
                    revision + 1 if action == "publish" else None,
                    at if action == "publish" else None,
                    p.actor_uid,
                    p.team_id,
                ),
            )
        if action == "publish":
            event = str(uuid.uuid4())
            payload = canonical(
                {
                    "script_revision": revision + 1,
                    "script_hash": digest(json.loads(previous["draft_json"])),
                }
            )
            conn.execute(
                """INSERT INTO crm_events(event_id,operation_id,request_hash,actor_uid,employee_uuid,team_id,role,kind,origin,contracts_json,payload_json,occurred_at,received_at,time_quality,projection_status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event,
                    operation,
                    digest(body),
                    p.actor_uid,
                    p.employee_uuid,
                    p.team_id,
                    p.role,
                    "script_publish",
                    "app",
                    "[]",
                    payload,
                    at,
                    at,
                    "server",
                    "applied",
                ),
            )
            conn.execute("INSERT INTO crm_outbox(event_id) VALUES(?)", (event,))
        result = _state(conn, p)
        store.acknowledge(conn, p, operation, body, result, at)
        store.audit(conn, p, "script_" + action, p.team_id, {"revision": revision + 1})
        conn.commit()
        return result
