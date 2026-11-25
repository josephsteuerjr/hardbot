"""Rebuildable facts and cohort denominators independent of diagnostics."""

from datetime import timedelta
import json
import math
import uuid
from . import storage
from . import access, store
from .contract import CRMError, USEFUL, canonical, digest, instant, now, operation_id

METRIC_VERSION = 1
USEFUL_KINDS = USEFUL | {"script_publish", "manager_review"}


def aggregate(limit=500, *, rebuild=False):
    with storage.connect() as conn:
        store.begin(conn, None if rebuild else ["metrics"])
        if rebuild:
            conn.execute("DELETE FROM crm_metric_facts")
            conn.execute("UPDATE crm_outbox SET state='pending',processed_at=NULL")
        rows = conn.execute(
            "SELECT e.* FROM crm_outbox o JOIN crm_events e USING(event_id) WHERE o.state='pending' ORDER BY e.sequence LIMIT ?",
            (min(max(limit, 1), 2000),),
        ).fetchall()
        for row in rows:
            # Unanchored wall-clock times are explicitly reported as uncertain;
            # use receipt time for daily adoption, never fabricate a past shift.
            activity = (
                row["occurred_at"]
                if row["time_quality"] in {"server", "anchored"}
                else row["received_at"]
            )
            conn.execute(
                "INSERT INTO crm_metric_facts VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING",
                (
                    row["event_id"],
                    row["employee_uuid"],
                    row["team_id"],
                    row["kind"],
                    row["origin"],
                    activity,
                    row["received_at"],
                    row["time_quality"],
                    row["client_version"],
                ),
            )
            conn.execute(
                "UPDATE crm_outbox SET state='done',attempts=attempts+1,processed_at=? WHERE event_id=?",
                (now(), row["event_id"]),
            )
        conn.commit()
        return len(rows)


def configure_cohort(principal, body):
    access.require(principal, "analytics")
    if principal.role not in {"owner", "manager"}:
        raise CRMError("Редактирование группы недоступно.", "forbidden", 403)
    if set(body) - {
        "operation_id",
        "cohort_id",
        "name",
        "starts_at",
        "members",
        "thresholds",
    }:
        raise CRMError("Неизвестные параметры группы.")
    operation = operation_id(body.get("operation_id"))
    cohort = operation_id(body.get("cohort_id"))
    at = now()
    name = body.get("name")
    start = instant(body.get("starts_at")).isoformat()
    members = body.get("members")
    thresholds = body.get("thresholds", {})
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 150
        or not isinstance(members, list)
        or len(members) > 1000
    ):
        raise CRMError("Некорректная группа.")
    if not isinstance(thresholds, dict) or set(thresholds) - {
        "activation",
        "regularity",
        "delivery",
        "minimum_workdays",
        "minimum_coverage",
    }:
        raise CRMError("Неизвестные пороги.")
    for key, value in thresholds.items():
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= (366 if key == "minimum_workdays" else 1)
        ):
            raise CRMError("Некорректный порог.")
    with storage.connect() as conn:
        store.begin(conn)
        prior = store.replay(conn, principal, operation, body)
        if prior is not None:
            return prior
        existing = conn.execute(
            "SELECT * FROM crm_cohorts WHERE cohort_id=?", (cohort,)
        ).fetchone()
        if existing:
            raise CRMError(
                "Создайте новую версию группы; исходный состав сохранён.",
                "cohort_exists",
                409,
            )
        seen = set()
        conn.execute(
            "INSERT INTO crm_cohorts VALUES(?,?,?,?,?)",
            (cohort, name, start, METRIC_VERSION, canonical(thresholds)),
        )
        for member in members:
            if set(member) - {
                "employee_uuid",
                "access_at",
                "ends_at",
                "features",
                "excluded_reason",
            }:
                raise CRMError("Неизвестные поля участника.")
            eid = member.get("employee_uuid")
            access.employee(conn, principal, eid)
            if eid in seen:
                raise CRMError("Сотрудник повторён.")
            seen.add(eid)
            access_at = instant(member.get("access_at")).isoformat()
            end = (
                instant(member["ends_at"]).isoformat()
                if member.get("ends_at")
                else None
            )
            if end and end <= access_at:
                raise CRMError("Некорректный период участия.")
            features = member.get("features")
            if (
                not isinstance(features, list)
                or not features
                or not set(features) <= USEFUL_KINDS | {"report"}
            ):
                raise CRMError("Не заданы применимые функции.")
            reason = member.get("excluded_reason")
            if reason is not None and reason not in {
                "test",
                "service",
                "dismissed",
                "suspended",
            }:
                raise CRMError("Неизвестная причина исключения.")
            conn.execute(
                "INSERT INTO crm_cohort_members VALUES(?,?,?,?,?,?)",
                (cohort, eid, access_at, end, canonical(features), reason),
            )
        store.audit(
            conn,
            principal,
            "cohort_created",
            cohort,
            {"members": len(seen), "thresholds": thresholds},
        )
        result = dict(ok=True, cohort_id=cohort, members=len(seen))
        store.acknowledge(conn, principal, operation, body, result, at)
        conn.commit()
        return result


def ratio(n, d):
    return dict(numerator=n, denominator=d, value=n / d if d else None)


def quantiles(values):
    data = sorted(max(0, v) for v in values)
    return dict(
        count=len(data),
        p50=data[math.ceil(len(data) * 0.5) - 1] if data else None,
        p95=data[math.ceil(len(data) * 0.95) - 1] if data else None,
    )


def report(principal, body):
    access.require(principal, "analytics")
    if set(body) - {
        "cohort_id",
        "since",
        "until",
        "team_id",
        "feature",
        "version",
        "role",
    }:
        raise CRMError("Неизвестный фильтр аналитики.")
    if body.get("role") and body["role"] not in {
        "employee",
        "manager",
        "observer",
        "owner",
    }:
        raise CRMError("Неизвестная роль.")
    start, end = instant(body.get("since")), instant(body.get("until"))
    as_of = instant(now())
    if not start < end or (end - start).days > 366:
        raise CRMError("Выберите период до 366 дней.")
    if body.get("feature") and body["feature"] not in USEFUL_KINDS | {"report"}:
        raise CRMError("Неизвестная функция.")
    with storage.connect() as conn:
        cohort = conn.execute(
            "SELECT * FROM crm_cohorts WHERE cohort_id=?", (body.get("cohort_id"),)
        ).fetchone()
        if not cohort:
            raise CRMError("Группа внедрения не найдена.", "not_found", 404)
        members = [
            dict(r)
            for r in conn.execute(
                "SELECT m.*,e.name FROM crm_cohort_members m JOIN crm_employees e USING(employee_uuid) WHERE cohort_id=?",
                (cohort["cohort_id"],),
            )
            if principal.owner or r["employee_uuid"] in principal.employee_scope
        ]
        if not members and not principal.owner:
            raise CRMError("Группа вне доступного охвата.", "forbidden", 403)
        rows = []
        totals = dict(
            eligible=0,
            activated=0,
            planned=0,
            active=0,
            unknown=0,
            not_started=0,
            excluded=0,
            observed=0,
            with_gaps=0,
        )
        funnel = {
            key: set()
            for key in ("access", "login", "ready", "work", "return", "delivered")
        }
        origins = {}
        feature_stats = {}
        versions = {}
        trend = {}
        errors = {}
        allfacts = []
        telemetry_times = []
        receive_delays = []
        for member in members:
            eid = member["employee_uuid"]
            features = set(json.loads(member["features_json"]))
            if body.get("feature") and body["feature"] not in features:
                continue
            a = max(start, instant(member["access_at"]), instant(cohort["starts_at"]))
            b = min(
                end, instant(member["ends_at"]) if member["ends_at"] else end, as_of
            )
            memberships = [
                r
                for r in conn.execute(
                    "SELECT * FROM crm_memberships WHERE employee_uuid=?", (eid,)
                )
            ]
            selected_role = body.get("role")
            if selected_role and not any(
                r["role"] == selected_role
                and instant(r["starts_at"]) < b
                and (not r["ends_at"] or instant(r["ends_at"]) > a)
                for r in memberships
            ):
                continue

            def in_team(when, team=None):
                selected = body.get("team_id")
                role_matches = not selected_role or any(
                    r["role"] == selected_role
                    and instant(r["starts_at"]) <= when
                    and (not r["ends_at"] or when < instant(r["ends_at"]))
                    for r in memberships
                )
                return role_matches and (
                    not selected
                    or (
                        team == selected
                        if team is not None
                        else any(
                            r["team_id"] == selected
                            and instant(r["starts_at"]) <= when
                            and (not r["ends_at"] or when < instant(r["ends_at"]))
                            for r in memberships
                        )
                    )
                )

            if body.get("team_id") and not any(
                r["team_id"] == body["team_id"]
                and instant(r["starts_at"]) < b
                and (not r["ends_at"] or instant(r["ends_at"]) > a)
                for r in memberships
            ):
                continue
            excluded = member["excluded_reason"] is not None or a >= b
            plans = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM crm_planned_shifts WHERE employee_uuid=? ORDER BY starts_at",
                    (eid,),
                )
                if instant(r["starts_at"]) < b
                and instant(r["ends_at"]) > a
                and in_team(instant(r["starts_at"]))
            ]
            planned = [
                p
                for p in plans
                if p["state"] == "planned" and a <= instant(p["starts_at"]) < b
            ]
            facts = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM crm_metric_facts WHERE employee_uuid=? AND activity_at>=? AND activity_at<?",
                    (eid, a.isoformat(), b.isoformat()),
                )
                if r["kind"] in features
                and r["kind"] in USEFUL_KINDS
                and in_team(instant(r["activity_at"]), r["team_id"])
            ]
            if body.get("feature"):
                facts = [r for r in facts if r["kind"] == body["feature"]]
            tech = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM crm_telemetry WHERE employee_uuid=? AND received_at>=? AND received_at<?",
                    (eid, a.isoformat(), b.isoformat()),
                )
                if in_team(instant(r["received_at"]), r["team_id"])
            ]
            if body.get("version"):
                # Keep denominator: missing version is unknown, not an exclusion.
                tech = [r for r in tech if r["client_version"] == body["version"]]
                facts = [r for r in facts if r["client_version"] == body["version"]]
            useful_ids = {f["event_id"] for f in facts}
            active = [
                p
                for p in planned
                if any(
                    instant(p["starts_at"])
                    <= instant(f["activity_at"])
                    < instant(p["ends_at"])
                    for f in facts
                )
            ]
            working = set(
                f["event_id"]
                for f in facts
                if any(
                    instant(p["starts_at"])
                    <= instant(f["activity_at"])
                    < instant(p["ends_at"])
                    for p in planned
                )
            )
            observed = bool(tech)
            gaps = sum(r["dropped_count"] for r in tech)
            future = conn.execute(
                "SELECT 1 FROM crm_planned_shifts WHERE employee_uuid=? AND state='planned' AND starts_at>=? LIMIT 1",
                (eid, b.isoformat()),
            ).fetchone()
            eligible = bool(planned) and not excluded
            reason = (
                "Исключён из группы"
                if excluded
                else (
                    "График не задан"
                    if not plans and not future
                    else (
                        "Первая смена ещё не наступила"
                        if not plans and future
                        else (
                            "Нет плановых рабочих смен"
                            if not planned
                            else (
                                "Недостаточно технических данных"
                                if not observed or gaps
                                else "Нет принятой работы" if not facts else None
                            )
                        )
                    )
                )
            )
            if excluded:
                totals["excluded"] += 1
            else:
                funnel["access"].add(eid)
                totals["eligible"] += eligible
                totals["activated"] += bool(eligible and facts)
                totals["planned"] += len(planned)
                totals["active"] += len(active)
                totals["unknown"] += not plans and not future
                totals["not_started"] += not plans and bool(future)
                totals["observed"] += observed
                totals["with_gaps"] += bool(gaps)
                if any(t["event_type"] == "login_ok" for t in tech):
                    funnel["login"].add(eid)
                if any(t["event_type"] == "client_ready" for t in tech):
                    funnel["ready"].add(eid)
                if facts:
                    funnel["work"].add(eid)
                if len(active) > 1:
                    funnel["return"].add(eid)
                allfacts.extend(facts)
                for fact in facts:
                    bucket = origins.setdefault(
                        fact["kind"], dict(app=0, excel=0, bot=0, unknown=0)
                    )
                    bucket[
                        fact["origin"] if fact["origin"] in bucket else "unknown"
                    ] += 1
                    if fact["time_quality"] in {"server", "anchored"}:
                        receive_delays.append(
                            (
                                instant(fact["received_at"])
                                - instant(fact["activity_at"])
                            ).total_seconds()
                        )
                for p in planned:
                    bucket = trend.setdefault(p["workday"], dict(planned=0, active=0))
                    bucket["planned"] += 1
                    bucket["active"] += p in active
                for t in tech:
                    version = versions.setdefault(
                        t["client_version"], dict(employees=set(), installations=set())
                    )
                    version["employees"].add(eid)
                    version["installations"].add(t["installation_id"])
                    if t["feature"] in features or t["feature"] in {
                        "shell",
                        "portfolio",
                        "card",
                        "script",
                        "shift",
                        "exchange",
                        "adoption",
                    }:
                        bucket = feature_stats.setdefault(t["feature"], {})
                        bucket.setdefault(t["event_type"], set()).add(eid)
                    if t["error_code"]:
                        errors[t["error_code"]] = errors.get(t["error_code"], 0) + 1
                    if (
                        t["event_type"] == "client_ready"
                        and t["duration_ms"] is not None
                    ):
                        telemetry_times.append(t["duration_ms"])
            first = min((f["received_at"] for f in facts), default=None)
            rows.append(
                dict(
                    employee_uuid=eid,
                    name=member["name"],
                    eligible=eligible,
                    activated=bool(eligible and facts),
                    planned_shifts=len(planned),
                    active_shifts=len(active),
                    extra_shift_actions=len(useful_ids - working),
                    last_work=max((f["received_at"] for f in facts), default=None),
                    first_work=first,
                    time_to_work_from_access_seconds=(
                        max(
                            0,
                            (
                                instant(first) - instant(member["access_at"])
                            ).total_seconds(),
                        )
                        if first
                        else None
                    ),
                    versions=sorted({t["client_version"] for t in tech}),
                    telemetry_observed=observed,
                    dropped_events=gaps,
                    reason=reason,
                    uncertain_time=sum(
                        f["time_quality"] not in {"server", "anchored"} for f in facts
                    ),
                    excluded=excluded,
                )
            )
        expected = 0
        complete = 0
        future_count = 0
        no_sla = 0
        overdue = 0
        pending = 0
        delays = []
        oldest = None
        for fact in allfacts:
            targets = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM crm_delivery_targets WHERE event_id=?",
                    (fact["event_id"],),
                )
            ]
            if not targets:
                continue
            receipts = {
                r["target_id"]: r["verified_at"]
                for r in conn.execute(
                    "SELECT * FROM crm_delivery_receipts WHERE event_id=? ORDER BY verified_at DESC",
                    (fact["event_id"],),
                )
            }
            pending += any(t["target_id"] not in receipts for t in targets)
            if any(t["due_at"] is None for t in targets):
                no_sla += 1
                continue
            due = max(instant(t["due_at"]) for t in targets)
            if due > as_of:
                future_count += 1
                continue
            expected += 1
            done = all(t["target_id"] in receipts for t in targets)
            if done:
                complete += 1
                funnel["delivered"].add(fact["employee_uuid"])
                delays.append(
                    (
                        max(instant(receipts[t["target_id"]]) for t in targets)
                        - instant(fact["received_at"])
                    ).total_seconds()
                )
            else:
                overdue += 1
                oldest = min(
                    oldest or instant(fact["received_at"]), instant(fact["received_at"])
                )
        for row in rows:
            facts = [f for f in allfacts if f["employee_uuid"] == row["employee_uuid"]]
            row["pending_delivery"] = sum(
                bool(
                    conn.execute(
                        "SELECT 1 FROM crm_delivery_targets t WHERE t.event_id=? AND NOT EXISTS(SELECT 1 FROM crm_delivery_receipts r WHERE r.event_id=t.event_id AND r.target_id=t.target_id)",
                        (f["event_id"],),
                    ).fetchone()
                )
                for f in facts
            )
        origin_coverage = {
            r["key"][16:]: r["value"] == "complete"
            for r in conn.execute(
                "SELECT * FROM crm_meta WHERE key LIKE 'origin_coverage:%'"
            )
        }
        origin_report = {
            kind: dict(
                counts=counts,
                app_share=(
                    ratio(counts["app"], sum(counts.values()))
                    if origin_coverage.get(kind) and not counts["unknown"]
                    else None
                ),
                reason=(
                    None
                    if origin_coverage.get(kind) and not counts["unknown"]
                    else "Доля неизвестна: внешняя работа наблюдается не полностью"
                ),
            )
            for kind, counts in origins.items()
        }
        scoped_employees = {r["employee_uuid"] for r in members}
        backlog = sum(
            r["employee_uuid"] in scoped_employees
            for r in conn.execute(
                "SELECT e.employee_uuid FROM crm_outbox o JOIN crm_events e USING(event_id) WHERE o.state='pending'"
            )
        )
        retained = conn.execute(
            "SELECT value FROM crm_meta WHERE key='telemetry_retained_since'"
        ).fetchone()
        coverage = ratio(totals["observed"], sum(not r["excluded"] for r in rows))
        result = dict(
            metric_version=METRIC_VERSION,
            cohort=dict(
                cohort_id=cohort["cohort_id"],
                name=cohort["name"],
                thresholds=json.loads(cohort["thresholds_json"]),
            ),
            as_of=as_of.isoformat(),
            since=start.isoformat(),
            until=end.isoformat(),
            activation=ratio(totals["activated"], totals["eligible"]),
            regularity=ratio(totals["active"], totals["planned"]),
            coverage=coverage,
            counts=totals,
            funnel={k: len(v) for k, v in funnel.items()},
            origins=origin_report,
            delivery=dict(
                **ratio(complete, expected),
                overdue=overdue,
                pending=pending,
                future=future_count,
                without_sla=no_sla,
                oldest_pending_seconds=(
                    max(0, (as_of - oldest).total_seconds()) if oldest else None
                ),
                latency_seconds=quantiles(delays)
            ),
            reception_seconds=quantiles(receive_delays),
            screen_ready_ms=quantiles(telemetry_times),
            versions={
                k: dict(
                    employees=len(v["employees"]), installations=len(v["installations"])
                )
                for k, v in versions.items()
            },
            features={
                k: {event: len(ids) for event, ids in v.items()}
                for k, v in feature_stats.items()
            },
            trend=[dict(workday=k, **v) for k, v in sorted(trend.items())],
            errors=errors,
            employees=rows,
            projection_pending=backlog,
            last_received=max((f["received_at"] for f in allfacts), default=None),
            technical_retained_since=retained["value"] if retained else None,
            completeness=(
                "Недостаточно данных"
                if backlog
                or totals["unknown"]
                or totals["with_gaps"]
                or coverage["value"] != 1
                or (retained and instant(retained["value"]) > start)
                else "Наблюдаемые данные за период"
            ),
            success=None,
        )
        store.audit(
            conn,
            principal,
            "analytics_view",
            cohort["cohort_id"],
            {
                "since": start.isoformat(),
                "until": end.isoformat(),
                "employees": len(rows),
            },
        )
        conn.commit()
        return result
