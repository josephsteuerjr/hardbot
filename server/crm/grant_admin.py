"""Isolated actor-grant administration with optimistic, idempotent apply."""

import json
import re

from . import access, grants, storage, store
from .contract import CRMError, canonical, digest, now, operation_id
from .roles import GRANT_ROLE_CAPABILITIES

SCHEMA_VERSION = 1
RESERVED_ROLES = frozenset({"owner", "server_operator", "root"})
ROLE_TITLES = {
    "employee": "Сотрудник",
    "manager": "Руководитель",
    "analyst": "Аналитик",
    "observer": "Наблюдатель",
    "courier": "Курьер",
    "logistic": "Логист",
    "owner": "Владелец",
}
TERRITORY_TITLES = {
    "self": "только сам сотрудник",
    "employees": "выбранные сотрудники",
    "team": "команда",
    "all": "глобально",
}
GRANT_KEYS = {
    "grant_id",
    "role",
    "territory",
    "capabilities",
    "employees",
    "all_contracts",
    "read_all_contracts",
    "is_default",
}
BUSINESS_KEYS = GRANT_KEYS - {"grant_id"}


def _error(message, code="grant_invalid", status=400, details=None):
    return CRMError(message, code, status, details)


def _environment(conn):
    rows = dict(
        conn.execute(
            "SELECT key,value FROM crm_meta WHERE key IN "
            "('synthetic_candidate','distribution_mode')"
        )
    )
    if "synthetic_candidate" not in rows and rows.get("distribution_mode") != "isolated":
        raise _error(
            "Управление grants доступно только в изолированном проверочном контуре.",
            "grant_admin_unavailable",
            403,
        )


def _require_admin(principal):
    if principal.owner:
        return
    if principal.role != "analyst" or "access_manage" not in principal.capabilities:
        raise _error(
            "Требуется владелец или отдельное полномочие access_manage Аналитика.",
            "forbidden",
            403,
        )


def _refresh_admin(conn, principal):
    refreshed = access.refresh_write(conn, principal)
    _require_admin(refreshed)
    return refreshed


def _revision(conn, actor_uid):
    row = conn.execute(
        "SELECT access_model,policy_revision FROM crm_actor_access WHERE actor_uid=?",
        (actor_uid,),
    ).fetchone()
    return (
        (int(row["access_model"]), int(row["policy_revision"]))
        if row
        else (grants.LEGACY_MODEL, 0)
    )


def _actor(conn, actor_uid):
    if type(actor_uid) is not int or not 0 < abs(actor_uid) < 2**63:
        raise _error("Некорректный actor_uid.")
    at = now()
    row = conn.execute(
        "SELECT l.actor_uid,l.employee_uuid,e.name,e.active,m.team_id,m.role,"
        "m.capabilities_json FROM crm_actor_links l "
        "JOIN crm_employees e USING(employee_uuid) "
        "LEFT JOIN crm_memberships m ON m.employee_uuid=l.employee_uuid "
        "AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) "
        "WHERE l.actor_uid=? AND l.active=1 "
        "ORDER BY m.starts_at DESC LIMIT 1",
        (at, at, actor_uid),
    ).fetchone()
    if not row or not row["active"]:
        raise _error("Действующий actor не найден.", "actor_not_found", 404)
    return row


def _active_team(conn, team_id):
    at = now()
    if team_id is None:
        return frozenset()
    return frozenset(
        row[0]
        for row in conn.execute(
            "SELECT e.employee_uuid FROM crm_employees e "
            "JOIN crm_memberships m USING(employee_uuid) "
            "WHERE e.active=1 AND m.team_id=? AND m.starts_at<=? "
            "AND (m.ends_at IS NULL OR m.ends_at>?)",
            (team_id, at, at),
        )
    )


def _target_ceiling(conn, actor):
    if actor["team_id"] is None or actor["role"] not in GRANT_ROLE_CAPABILITIES:
        raise _error("У actor нет действующего кадрового потолка.", "grant_escalation", 403)
    capabilities = frozenset(json.loads(actor["capabilities_json"]))
    scope = frozenset({actor["employee_uuid"]})
    if actor["role"] in {"manager", "analyst", "observer"}:
        scope = _active_team(conn, actor["team_id"])
        policy = conn.execute(
            "SELECT employees_json FROM crm_authority_scopes WHERE actor_uid=?",
            (actor["actor_uid"],),
        ).fetchone()
        if policy:
            scope = frozenset(json.loads(policy["employees_json"])) | {
                actor["employee_uuid"]
            }
    return capabilities, scope


def _authorize_target(principal, actor):
    if principal.owner:
        return
    if actor["employee_uuid"] not in principal.employee_scope:
        raise _error("Actor вне области администратора.", "grant_escalation", 403)


def _global(grant):
    return (
        grant["role"] == "owner"
        or grant["territory"] == "all"
        or grant["all_contracts"]
        or grant["read_all_contracts"]
    )


def _authorize_grant(conn, principal, actor, grant, ceiling=None):
    if grant["role"] in {"server_operator", "root"} or "access_manage" in grant["capabilities"]:
        raise _error("Служебную роль нельзя выдать через grant-admin.", "grant_escalation", 403)
    if grant["role"] not in GRANT_ROLE_CAPABILITIES or grant["territory"] not in grants.TERRITORIES:
        raise _error("Неизвестная роль или территория grant.")
    if not principal.owner and (grant["role"] in RESERVED_ROLES or _global(grant)):
        raise _error("Глобальные и служебные grants выдаёт только владелец.", "grant_escalation", 403)
    if principal.owner:
        return
    target_capabilities, target_scope = ceiling or _target_ceiling(conn, actor)
    if not set(grant["capabilities"]) <= target_capabilities:
        raise _error("Grant превышает кадровый потолок actor.", "grant_escalation", 403)
    if not set(grant["capabilities"]) <= (set(principal.capabilities) - {"access_manage"}):
        raise _error("Grant превышает область прав администратора.", "grant_escalation", 403)
    if grant["territory"] == "self":
        requested_scope = frozenset({actor["employee_uuid"]})
    elif grant["territory"] == "employees":
        requested_scope = frozenset(grant["employees"])
    elif grant["territory"] == "team":
        requested_scope = _active_team(conn, actor["team_id"])
    else:
        requested_scope = frozenset()
    if not requested_scope <= target_scope or not requested_scope <= principal.employee_scope:
        raise _error("Территория grant превышает область actor или администратора.", "grant_escalation", 403)


def _boolean(value, key):
    if type(value) is not bool:
        raise _error("Поле " + key + " должно быть boolean.")
    return value


def _strings(value, key):
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) > 100
    ):
        raise _error("Некорректный список " + key + ".")
    return sorted(set(value))


def _business(grant):
    return {key: grant[key] for key in sorted(BUSINESS_KEYS)}


def _normalize(raw_grants, current, actor_uid, operation):
    if not isinstance(raw_grants, list) or len(raw_grants) > 50:
        raise _error("Передайте до 50 grants.")
    existing = {grant.grant_id: grant for grant in current}
    normalized = []
    for raw in raw_grants:
        if not isinstance(raw, dict) or set(raw) - GRANT_KEYS - {"actor_uid", "revision"}:
            raise _error("Неизвестные поля grant.")
        if "actor_uid" in raw and raw["actor_uid"] != actor_uid:
            raise _error("Grant принадлежит другому actor.")
        role = raw.get("role")
        territory = raw.get("territory")
        if not isinstance(role, str) or not isinstance(territory, str):
            raise _error("Укажите роль и территорию grant.")
        grant_id = raw.get("grant_id")
        if grant_id is not None and (
            not isinstance(grant_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", grant_id)
        ):
            raise _error("Некорректный grant_id.")
        previous = existing.get(grant_id) if grant_id is not None else None
        value = dict(
            role=role,
            territory=territory,
            capabilities=_strings(raw.get("capabilities"), "capabilities"),
            employees=_strings(raw.get("employees", []), "employees"),
            all_contracts=_boolean(raw.get("all_contracts", False), "all_contracts"),
            read_all_contracts=_boolean(
                raw.get("read_all_contracts", False), "read_all_contracts"
            ),
            is_default=_boolean(raw.get("is_default", False), "is_default"),
        )
        if previous and (previous.role != role or previous.territory != territory):
            previous = None
            grant_id = None
        generated = grants.identifier(
            "grant-admin",
            str(actor_uid) + ":" + operation + ":" + canonical(value),
        )
        if grant_id is None:
            grant_id = generated
        elif previous is None and grant_id != generated:
            raise _error("Grant не принадлежит текущему actor или уже отозван.")
        if "revision" in raw and (
            type(raw["revision"]) is not int
            or raw["revision"] != (previous.revision if previous else 0)
        ):
            raise _error("Некорректная revision grant.")
        normalized.append(
            dict(
                grant_id=grant_id,
                actor_uid=actor_uid,
                revision=previous.revision if previous else 0,
                **value,
            )
        )
    identities = [(item["role"], item["territory"]) for item in normalized]
    if len(set(identities)) != len(identities):
        raise _error("Роль и территория должны быть уникальны внутри actor.")
    if sum(item["is_default"] for item in normalized) > 1:
        raise _error("У actor может быть только один default grant.")
    normalized.sort(key=lambda item: (item["role"], item["territory"], item["grant_id"]))
    return normalized


def _validate_normalized(conn, principal, actor, normalized, ceiling=None):
    for grant in normalized:
        _authorize_grant(conn, principal, actor, grant, ceiling)
        capabilities, employees = grants._validate(
            grant["actor_uid"],
            grant["role"],
            grant["territory"],
            grant["capabilities"],
            grant["employees"],
            grant["all_contracts"],
        )
        grant["capabilities"] = sorted(capabilities)
        grant["employees"] = sorted(employees)


def _description(grant):
    capabilities = ", ".join(grant["capabilities"]) or "без capabilities"
    text = (
        ROLE_TITLES.get(grant["role"], grant["role"])
        + " / "
        + TERRITORY_TITLES.get(grant["territory"], grant["territory"])
        + "; "
        + capabilities
    )
    if grant["territory"] == "employees":
        text += "; сотрудники: " + ", ".join(grant["employees"])
    flags = []
    if grant["all_contracts"]:
        flags.append("все договоры")
    if grant["read_all_contracts"]:
        flags.append("чтение всех договоров")
    if grant["is_default"]:
        flags.append("default")
    return text + ("; " + ", ".join(flags) if flags else "")


def _diff(current, normalized, access_model):
    before = {grant.grant_id: grant.to_dict() for grant in current}
    after = {grant["grant_id"]: grant for grant in normalized}
    result = []
    for grant_id in sorted(before.keys() - after.keys()):
        result.append(
            dict(
                action="revoke",
                grant_id=grant_id,
                text="Отозвать: " + _description(before[grant_id]),
                before=before[grant_id],
                after=None,
            )
        )
    for grant_id in sorted(after):
        desired = after[grant_id]
        if grant_id not in before:
            result.append(
                dict(
                    action="create",
                    grant_id=grant_id,
                    text="Добавить: " + _description(desired),
                    before=None,
                    after=desired,
                )
            )
            continue
        previous = before[grant_id]
        changes = [
            key
            for key in sorted(BUSINESS_KEYS)
            if previous[key] != desired[key]
        ]
        if changes:
            result.append(
                dict(
                    action="update",
                    grant_id=grant_id,
                    text="Изменить: "
                    + _description(previous)
                    + " → "
                    + _description(desired)
                    + "; поля: "
                    + ", ".join(changes),
                    before=previous,
                    after=desired,
                    fields=changes,
                )
            )
    if access_model != grants.GRANTS_MODEL:
        result.append(
            dict(
                action="activate",
                grant_id=None,
                text="Включить actor-scoped grants (access_model=2).",
                before={"access_model": access_model},
                after={"access_model": grants.GRANTS_MODEL},
            )
        )
    return result


def _warnings(normalized, access_model):
    result = []
    if access_model != grants.GRANTS_MODEL:
        result.append("После применения legacy fallback будет заменён указанными grants.")
    if normalized and not any(grant["is_default"] for grant in normalized):
        result.append("Без default grant старым клиентам может потребоваться явный выбор grant.")
    if not normalized:
        result.append("Все действующие grants будут отозваны; вход по модели 2 потеряет доступ.")
    return result


def _preview_payload(actor_uid, base_revision, normalized, diff, warnings, operation):
    return dict(
        schema_version=SCHEMA_VERSION,
        actor_uid=actor_uid,
        base_revision=base_revision,
        grants=normalized,
        diff=diff,
        warnings=warnings,
        operation_id=operation,
    )


def _build(conn, principal, body):
    actor = _actor(conn, body["actor_uid"])
    _authorize_target(principal, actor)
    access_model, base_revision = _revision(conn, actor["actor_uid"])
    if body["base_revision"] != base_revision:
        raise _error(
            "Версия полномочий изменилась. Повторите предпросмотр.",
            "policy_revision_conflict",
            409,
            {"policy_revision": base_revision},
        )
    current = grants.load(conn, actor["actor_uid"])
    ceiling = None if principal.owner else _target_ceiling(conn, actor)
    if not principal.owner:
        for grant in current:
            _authorize_grant(conn, principal, actor, grant.to_dict(), ceiling)
    normalized = _normalize(body["grants"], current, actor["actor_uid"], body["operation_id"])
    _validate_normalized(conn, principal, actor, normalized, ceiling)
    changes = _diff(current, normalized, access_model)
    warnings = _warnings(normalized, access_model)
    payload = _preview_payload(
        actor["actor_uid"], base_revision, normalized, changes, warnings, body["operation_id"]
    )
    return actor, current, payload, dict(
        payload, preview_sha256=digest(payload), access_model=access_model
    )


def _common(body, operation):
    if not isinstance(body, dict) or body.get("operation") != operation:
        raise _error("Некорректная операция grant-admin.")
    if operation == "preview":
        allowed = {"operation", "actor_uid", "base_revision", "grants", "operation_id"}
    else:
        allowed = {
            "operation",
            "actor_uid",
            "base_revision",
            "grants",
            "operation_id",
            "preview_sha256",
            "diff",
            "warnings",
            "schema_version",
            "access_model",
        }
    if set(body) - allowed:
        raise _error("Неизвестные параметры grant-admin.")
    if type(body.get("base_revision")) is not int or body["base_revision"] < 0:
        raise _error("Некорректный base_revision.")
    if "schema_version" in body and body["schema_version"] != SCHEMA_VERSION:
        raise _error("Неизвестная версия grant-admin.")
    body = dict(body, operation_id=operation_id(body.get("operation_id")))
    if operation == "apply" and (
        not isinstance(body.get("preview_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", body["preview_sha256"])
    ):
        raise _error("Некорректный preview_sha256.")
    return body


def catalog(principal):
    with storage.connect(readonly=True) as conn:
        _environment(conn)
        _require_admin(principal)
        roles = []
        for role in sorted(GRANT_ROLE_CAPABILITIES):
            if role in RESERVED_ROLES and role != "owner":
                continue
            if role == "owner" and not principal.owner:
                continue
            roles.append(
                dict(
                    role=role,
                    title=ROLE_TITLES.get(role, role),
                    capabilities=sorted(GRANT_ROLE_CAPABILITIES[role]),
                    owner_only=role == "owner",
                )
            )
        return dict(
            schema_version=SCHEMA_VERSION,
            roles=roles,
            territories=[
                dict(territory=key, title=TERRITORY_TITLES[key], owner_only=key == "all")
                for key in sorted(grants.TERRITORIES)
            ],
            admin_capability="access_manage",
            global_owner_only=True,
            isolated=True,
        )


def list_state(principal):
    with storage.connect(readonly=True) as conn:
        _environment(conn)
        _require_admin(principal)
        rows = list(
            conn.execute(
                "SELECT l.actor_uid,l.employee_uuid,e.name,m.team_id,m.role "
                "FROM crm_actor_links l JOIN crm_employees e USING(employee_uuid) "
                "LEFT JOIN crm_memberships m USING(employee_uuid) "
                "WHERE l.active=1 AND e.active=1 "
                "ORDER BY e.name,l.actor_uid,m.starts_at DESC"
            )
        )
        actors = []
        seen = set()
        for row in rows:
            if row["actor_uid"] in seen:
                continue
            seen.add(row["actor_uid"])
            actor = _actor(conn, row["actor_uid"])
            if not principal.owner and actor["employee_uuid"] not in principal.employee_scope:
                continue
            access_model, revision = _revision(conn, actor["actor_uid"])
            current = grants.load(conn, actor["actor_uid"])
            manageable = True
            reason = ""
            if not principal.owner:
                try:
                    ceiling = _target_ceiling(conn, actor)
                    for grant in current:
                        _authorize_grant(conn, principal, actor, grant.to_dict(), ceiling)
                except CRMError as exc:
                    manageable = False
                    reason = str(exc)
            actors.append(
                dict(
                    actor_uid=actor["actor_uid"],
                    employee_uuid=actor["employee_uuid"],
                    name=actor["name"],
                    team_id=actor["team_id"],
                    role=actor["role"],
                    access_model=access_model,
                    base_revision=revision,
                    grants=[grant.to_dict() for grant in current],
                    manageable=manageable,
                    reason=reason,
                )
            )
        return dict(schema_version=SCHEMA_VERSION, actors=actors, isolated=True)


def preview(principal, body):
    body = _common(body, "preview")
    with storage.connect(readonly=True) as conn:
        _environment(conn)
        _require_admin(principal)
        return _build(conn, principal, body)[3]


def apply(principal, body):
    body = _common(body, "apply")
    with storage.connect() as conn:
        storage.begin(conn)
        _environment(conn)
        principal = _refresh_admin(conn, principal)
        actor = _actor(conn, body["actor_uid"])
        _authorize_target(principal, actor)
        grants._begin(conn, actor["actor_uid"])
        current = grants.load(conn, actor["actor_uid"])
        if not principal.owner:
            ceiling = _target_ceiling(conn, actor)
            for grant in current:
                _authorize_grant(conn, principal, actor, grant.to_dict(), ceiling)
        previous = store.replay(conn, principal, body["operation_id"], body)
        if previous is not None:
            return previous
        _, current, payload, checked = _build(conn, principal, body)
        if body["preview_sha256"] != checked["preview_sha256"]:
            raise _error(
                "Предпросмотр не совпадает с запросом. Повторите проверку.",
                "preview_mismatch",
                409,
            )
        for key in ("diff", "warnings", "access_model"):
            if key in body and body[key] != checked[key]:
                raise _error(
                    "Содержимое предпросмотра изменилось.",
                    "preview_mismatch",
                    409,
                )
        desired = {grant["grant_id"]: grant for grant in payload["grants"]}
        before = {grant.grant_id: grant for grant in current}
        for grant_id in sorted(before.keys() - desired.keys()):
            grants.revoke(
                conn,
                actor["actor_uid"],
                grant_id,
                changed_by_uid=principal.actor_uid,
            )
        for grant_id, grant in desired.items():
            grants.upsert(
                conn,
                actor["actor_uid"],
                grant_id=grant_id,
                role=grant["role"],
                territory=grant["territory"],
                capabilities=grant["capabilities"],
                employees=grant["employees"],
                all_contracts=grant["all_contracts"],
                read_all_contracts=grant["read_all_contracts"],
                is_default=grant["is_default"],
                changed_by_uid=principal.actor_uid,
            )
        access_model, _ = _revision(conn, actor["actor_uid"])
        if access_model != grants.GRANTS_MODEL:
            grants.set_model(
                conn,
                actor["actor_uid"],
                grants.GRANTS_MODEL,
                changed_by_uid=principal.actor_uid,
            )
        _, policy_revision = _revision(conn, actor["actor_uid"])
        applied_at = now()
        result = dict(
            checked,
            access_model=grants.GRANTS_MODEL,
            status="applied",
            changed=bool(payload["diff"]),
            policy_revision=policy_revision,
            result_grants=[grant.to_dict() for grant in grants.load(conn, actor["actor_uid"])],
            applied_at=applied_at,
            production_access_changed=False,
        )
        store.acknowledge(
            conn, principal, body["operation_id"], body, result, applied_at
        )
        store.audit(
            conn,
            principal,
            "grant_admin_applied",
            str(actor["actor_uid"]),
            dict(
                operation_id=body["operation_id"],
                base_revision=body["base_revision"],
                policy_revision=policy_revision,
                preview_sha256=checked["preview_sha256"],
                diff=payload["diff"],
            ),
        )
        return result


def command(principal, body):
    if not isinstance(body, dict):
        raise _error("Ожидается объект grant-admin.")
    operation = body.get("operation")
    if operation == "list":
        if set(body) != {"operation"}:
            raise _error("List не принимает дополнительные параметры.")
        return list_state(principal)
    if operation == "preview":
        return preview(principal, body)
    if operation == "apply":
        return apply(principal, body)
    raise _error("Неизвестная операция grant-admin.")
