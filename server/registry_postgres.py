"""Database used by the ordinary registry import, exports and Excel delivery receipts."""
from pathlib import Path

from crm import storage
from crm.storage import Postgres


REGISTRY_LOCK = 1836264771


def active(conn):
    if not storage.is_postgres(conn):
        return False
    receipt = conn.execute("SELECT to_regclass('crm_registry.migration_receipt')").fetchone()[0]
    return bool(receipt and conn.execute("SELECT active FROM crm_registry.migration_receipt").fetchone()[0])


class RegistryPostgres(Postgres):
    def __init__(self, dsn, *, readonly=False):
        super().__init__(dsn, readonly=readonly)
        try:
            self.execute("SET search_path TO crm_registry, pg_catalog")
            if not active(self):
                raise RuntimeError("Рабочий реестр PostgreSQL ещё не включён.")
            self.commit()
        except BaseException:
            self.close()
            raise

    def execute(self, query, args=None):
        if query == "BEGIN IMMEDIATE":
            return super().execute("SELECT pg_advisory_xact_lock(?)", (REGISTRY_LOCK,))
        return super().execute(query, args)


def connect(secret_file, *, readonly=False):
    return RegistryPostgres(Path(secret_file).read_text(encoding="utf-8").strip(), readonly=readonly)



def begin_import(conn):
    # Take locks in the same order as canonical writers, before any source rows are changed.
    conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
    storage.begin(conn, keys=())
    conn.execute("SELECT pg_advisory_xact_lock(?)", (REGISTRY_LOCK,))


def stage_clients(conn):
    # A full import touches thousands of rows. Keep its intermediate writes off
    # the employee's live row locks until the final, set-based publication.
    conn.execute("CREATE TEMP TABLE clients (LIKE crm_registry.clients INCLUDING DEFAULTS INCLUDING INDEXES) ON COMMIT DROP")
    conn.execute("ALTER TABLE pg_temp.clients ALTER COLUMN id SET DEFAULT nextval('crm_registry.clients_id_seq')")
    conn.execute("INSERT INTO pg_temp.clients SELECT * FROM crm_registry.clients")
    conn.execute("SET LOCAL search_path TO pg_temp, crm_registry, pg_catalog")


def publish_clients(conn):
    from psycopg import sql
    columns = [r[0] for r in conn.execute("SELECT attname FROM pg_attribute WHERE attrelid="
               "'crm_registry.clients'::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum")]
    assignments = sql.SQL(", ").join(sql.SQL("{}=excluded.{}").format(sql.Identifier(k), sql.Identifier(k))
                                     for k in columns if k not in {"id", "contract_id"})
    new_ids = [r[0] for r in conn.execute("SELECT d.contract_id FROM pg_temp.clients d LEFT JOIN crm_registry.clients r "
                                         "ON d.contract_id=r.contract_id WHERE r.id IS NULL")]
    conn.raw.execute(sql.SQL("""
        INSERT INTO crm_registry.clients
        SELECT draft.* FROM pg_temp.clients draft
        LEFT JOIN crm_registry.clients prior USING(contract_id)
        WHERE prior.id IS NULL OR draft IS DISTINCT FROM prior
        ORDER BY draft.contract_id
        ON CONFLICT(contract_id) DO UPDATE SET {}
        """).format(assignments))
    if new_ids:
        conn.execute("SELECT crm_registry.include_new_contracts(ARRAY(SELECT c.contract_uuid "
                     "FROM public.crm_contracts c JOIN crm_registry.clients r ON c.legacy_id=r.contract_id "
                     "AND c.creditor_id='registry-partner:' || trim(r.partner) WHERE r.contract_id=ANY(?)))", (new_ids,))


def protected_assignments(conn, grouped, fields):
    """A manager owns each slot they have explicitly distributed, including a cleared slot."""
    protected = {}
    rows = conn.execute("""
        SELECT c.legacy_id, h.contract_uuid, h.capability, h.revision, r.responsible, r.responsible_soft
          FROM public.crm_assignment_heads h JOIN public.crm_contracts c USING(contract_uuid)
          JOIN crm_registry.clients r ON r.contract_id=c.legacy_id
               AND c.creditor_id='registry-partner:' || trim(r.partner)
         WHERE h.source='native-distribution'
        """)
    for row in rows:
        cid = row["legacy_id"]
        field = "responsible_soft" if row["capability"] == "call" else "responsible"
        if field not in fields:
            continue
        protected[cid, field] = (row["contract_uuid"], row["revision"])
        data = grouped.get(cid)
        if data is None or field not in data:
            continue
        incoming, current = str(data[field] or "").strip(), str(row[field] or "").strip()
        if incoming.casefold() != current.casefold():
            note_assignment_conflict(conn, protected[cid, field], field, incoming, current)
        data[field] = current
    return protected


def note_assignment_conflict(conn, head, field, incoming, current):
    from crm.contract import canonical, digest
    cid, revision = head
    key = cid + ":" + field
    detail = dict(field=field, source_value=incoming, current_value=current, revision=revision)
    conn.execute("INSERT INTO public.crm_migration_issues VALUES(?,?,?,?,NULL,NULL) ON CONFLICT(issue_id) DO NOTHING",
                 ("registry:" + digest([key, revision, incoming]), "primary_assignment_conflict", key, canonical(detail)))


def register_portfolio_owners(conn, snapshot):
    from collections import defaultdict
    from crm.contract import identifier
    from crm.primary_fence import _label, NO_ASSIGNEE
    used = {_label(row.get(field)) for row in snapshot["clients"] for field in ("responsible", "responsible_soft")} - NO_ASSIGNEE
    employees = list(conn.execute("SELECT staff_id,name FROM public.crm_employees"))
    existing_ids = {r["staff_id"] for r in employees}
    existing_names = {_label(r["name"]) for r in employees}
    candidates = defaultdict(set)
    for row in snapshot["employee_identities"]:
        candidates[_label(row["display_name"])].add(row["id"])
    for row in snapshot["employee_aliases"]:
        candidates[_label(row["name"])].add(row["employee_id"])
    for label in sorted(used - existing_names):
        ids = candidates[label]
        if len(ids) != 1:
            continue
        source_id = next(iter(ids))
        staff_id = "authority-staff:" + source_id
        if staff_id not in existing_ids:
            conn.execute("SELECT crm_registry.register_portfolio_owner(?, ?::uuid)", (source_id, identifier("staff", staff_id)))
            existing_ids.add(staff_id)


def publish_import(conn, contract_ids=None):
    from crm import legacy_exchange
    from crm.contract import now
    snapshot = dict(source=legacy_exchange.SOURCE, captured_at=now(), calls=[], visits=[], mirror_links=[])
    where = "" if contract_ids is None else " WHERE contract_id=ANY(?)"
    args = None if contract_ids is None else (sorted(set(contract_ids)),)
    snapshot["clients"] = [dict(r) for r in conn.execute(
        "SELECT * FROM clients" + where + " ORDER BY contract_id", args)]
    for table, key in (("employee_identities", "id"), ("employee_aliases", "name")):
        snapshot[table] = [dict(r) for r in conn.execute("SELECT * FROM crm_registry." + table + " ORDER BY " + key)]
    register_portfolio_owners(conn, snapshot)
    snapshot["monthly"] = [dict(r) for r in conn.execute(
        "SELECT contract_id,month,amount,currency,source_column,observed_at "
        "FROM crm_registry.registry_monthly_totals" + where + " ORDER BY contract_id,month", args)]
    conn.execute("SET LOCAL search_path TO public, pg_catalog")
    try:
        return legacy_exchange.apply_registry_transaction(conn, snapshot)
    finally:
        # A failed PostgreSQL transaction can only be rolled back by the caller.
        if conn.raw.info.transaction_status.name != "INERROR":
            conn.execute("SET LOCAL search_path TO crm_registry, pg_catalog")


def project_event(conn, event_id):
    """Update the spreadsheet-facing rows in the transaction that accepted the CRM action."""
    if not active(conn):
        return
    import json
    from datetime import timezone
    from zoneinfo import ZoneInfo
    from psycopg import sql
    from crm.contract import CRMError, canonical, instant
    from crm.legacy_mirror import _call_snapshot
    import db

    event = conn.execute("SELECT * FROM public.crm_events WHERE event_id=?", (event_id,)).fetchone()
    if event["projection_status"] != "applied" or event["origin"] == "legacy":
        return
    payload = json.loads(event["payload_json"])
    kind = payload.get("target_kind") if event["kind"] == "correction" else event["kind"]
    if kind not in {"call", "visit", "promise", "contact", "manual_edit", "registry_edit"}:
        return
    ids = json.loads(event["contracts_json"])
    rows = list(conn.execute("""
        SELECT r.* FROM public.crm_contracts c JOIN crm_registry.clients r
          ON r.contract_id=c.legacy_id AND c.creditor_id='registry-partner:' || trim(r.partner)
         WHERE c.contract_uuid=ANY(?) ORDER BY c.contract_uuid FOR UPDATE OF r
        """, (ids,)))
    if len(rows) != len(ids):
        raise CRMError("Договор не сопоставлен с рабочим реестром.", "cutover_identity_changed", 409)
    actor = conn.execute("""
        SELECT e.name, m.timezone FROM public.crm_employees e JOIN public.crm_memberships m USING(employee_uuid)
         WHERE e.employee_uuid=? AND m.team_id=? AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?)
         ORDER BY m.starts_at DESC, m.membership_id LIMIT 1
        """, (event["employee_uuid"], event["team_id"], event["received_at"], event["received_at"])).fetchone()
    if not actor:
        raise CRMError("Не определено подразделение автора операции.", "author_unmapped", 409)
    at = instant(event["occurred_at"])
    day = at.astimezone(ZoneInfo(actor["timezone"])).date().isoformat()
    happened = at.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="milliseconds")
    received = instant(event["received_at"]).replace(tzinfo=None).isoformat(timespec="milliseconds")
    by_contract = {row["contract_id"]: row for row in rows}
    ordered = {r[0]: r[1] for r in conn.execute("SELECT contract_uuid,legacy_id FROM public.crm_contracts WHERE contract_uuid=ANY(?)", (ids,))}
    raw_ids = [ordered[cid] for cid in ids]
    values = payload.get("values", {})
    # The existing snapshot builders use unqualified registry tables.
    conn.execute("SET LOCAL search_path TO crm_registry, pg_catalog")
    try:
        if event["kind"] in {"call", "visit"}:
            table = "calls" if kind == "call" else "visits"
            cols = dict(contract_id=raw_ids[0],agent_id=event["actor_uid"],created_at=day,
                        payload="CRM "+event_id,op_id="crm:"+event_id,received_at=received,
                        saved_at=happened,employee=actor["name"],values_json=canonical(values),
                        contracts_json=canonical(raw_ids))
            if kind == "visit":
                cols["request_hash"] = event["request_hash"]
            conn.raw.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
                sql.SQL(", ").join(sql.Placeholder() for _ in cols)), list(cols.values()))
        for cid, row in by_contract.items():
            known = {key: value for key, value in values.items() if key in row}
            if not known:
                continue
            conn.raw.execute(sql.SQL("UPDATE clients SET {} WHERE contract_id=%s").format(
                sql.SQL(", ").join(sql.SQL("{}=%s").format(sql.Identifier(key)) for key in known)),
                [*known.values(), cid])
            if kind == "call":
                _call_snapshot(conn, row, known, happened, day)
            elif kind == "visit":
                previous = db.bot_visit_snapshot(row) or {}
                db.record_bot_visit(cid, {**previous, **known}, day, happened, _conn=conn, _contracts=[row])
                conn.execute("UPDATE clients SET app_visit_edited_at=? WHERE contract_id=?", (happened, cid))
    finally:
        if conn.raw.info.transaction_status.name != "INERROR":
            conn.execute("SET LOCAL search_path TO public, pg_catalog")


def project_assignment(conn, cid, work, employee):
    if not active(conn):
        return False
    from crm.contract import CRMError
    field, product = {"call": ("responsible_soft", "soft"), "visit": ("responsible", "hard")}[work]
    name = ""
    if employee:
        row = conn.execute("""
            SELECT e.name, r.product_names FROM public.crm_employees e
             LEFT JOIN crm_registry.staff_roster r ON e.staff_id='authority-staff:' || r.employee_id
             WHERE e.employee_uuid=?
            """, (employee,)).fetchone()
        if not row:
            raise CRMError("Ответственный не найден.", "cutover_identity_changed", 409)
        import json
        name = json.loads(row["product_names"] or "{}").get(product) or row["name"]
    changed = conn.execute("UPDATE crm_registry.clients r SET " + field + "=? FROM public.crm_contracts c "
                           "WHERE c.contract_uuid=? AND r.contract_id=c.legacy_id "
                           "AND c.creditor_id='registry-partner:' || trim(r.partner)", (name, cid))
    if changed.rowcount != 1:
        raise CRMError("Договор не сопоставлен с рабочим реестром.", "cutover_identity_changed", 409)
    return True
