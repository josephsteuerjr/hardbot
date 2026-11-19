"""Additive canonical tables; legacy IDs, accounts and documents are retained."""

VERSION = 9

DDL = """
CREATE TABLE IF NOT EXISTS crm_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_people(
 person_uuid TEXT PRIMARY KEY, name TEXT NOT NULL, identity_source TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_contracts(
 contract_uuid TEXT PRIMARY KEY, person_uuid TEXT NOT NULL REFERENCES crm_people,
 creditor_id TEXT NOT NULL, external_id TEXT NOT NULL, legacy_id TEXT UNIQUE,
 currency TEXT, baseline_json TEXT NOT NULL, fields_json TEXT NOT NULL,
 revisions_json TEXT NOT NULL, provenance_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(creditor_id,external_id));
CREATE INDEX IF NOT EXISTS crm_contract_person ON crm_contracts(person_uuid);
CREATE TABLE IF NOT EXISTS crm_employees(
 employee_uuid TEXT PRIMARY KEY, staff_id TEXT UNIQUE, name TEXT NOT NULL,
 source TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS crm_actor_links(
 actor_uid INTEGER PRIMARY KEY, employee_uuid TEXT NOT NULL REFERENCES crm_employees,
 evidence TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS crm_memberships(
 membership_id TEXT PRIMARY KEY, employee_uuid TEXT NOT NULL REFERENCES crm_employees,
 team_id TEXT NOT NULL, role TEXT NOT NULL, capabilities_json TEXT NOT NULL,
 timezone TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT);
CREATE INDEX IF NOT EXISTS crm_membership_employee ON crm_memberships(employee_uuid,starts_at,ends_at);
CREATE TABLE IF NOT EXISTS crm_assignments(
 assignment_id TEXT PRIMARY KEY, employee_uuid TEXT NOT NULL REFERENCES crm_employees,
 contract_uuid TEXT NOT NULL REFERENCES crm_contracts, capability TEXT NOT NULL,
 starts_at TEXT NOT NULL, ends_at TEXT, source TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS crm_assignment_scope ON crm_assignments(employee_uuid,contract_uuid,ends_at);
CREATE TABLE IF NOT EXISTS crm_events(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
 operation_id TEXT NOT NULL, request_hash TEXT NOT NULL, actor_uid INTEGER NOT NULL,
 employee_uuid TEXT NOT NULL, team_id TEXT NOT NULL, role TEXT NOT NULL,
 kind TEXT NOT NULL, origin TEXT NOT NULL, contracts_json TEXT NOT NULL,
 payload_json TEXT NOT NULL, occurred_at TEXT NOT NULL, received_at TEXT NOT NULL,
 time_quality TEXT NOT NULL, correction_of TEXT, reason TEXT,
 projection_status TEXT NOT NULL, legacy_source TEXT UNIQUE, client_version TEXT);
CREATE INDEX IF NOT EXISTS crm_events_employee ON crm_events(employee_uuid,received_at,kind);
CREATE INDEX IF NOT EXISTS crm_events_team ON crm_events(team_id,received_at);
CREATE TRIGGER IF NOT EXISTS crm_events_no_update BEFORE UPDATE ON crm_events
 BEGIN SELECT RAISE(ABORT,'CRM history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS crm_events_no_delete BEFORE DELETE ON crm_events
 BEGIN SELECT RAISE(ABORT,'CRM history is immutable'); END;
CREATE TABLE IF NOT EXISTS crm_event_contracts(
 event_id TEXT NOT NULL REFERENCES crm_events(event_id), contract_uuid TEXT NOT NULL REFERENCES crm_contracts,
 PRIMARY KEY(event_id,contract_uuid));
CREATE INDEX IF NOT EXISTS crm_history_contract ON crm_event_contracts(contract_uuid,event_id);
CREATE TABLE IF NOT EXISTS crm_commands(
 actor_uid INTEGER NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 result_json TEXT NOT NULL, received_at TEXT NOT NULL, PRIMARY KEY(actor_uid,operation_id));
CREATE TABLE IF NOT EXISTS crm_outbox(
 event_id TEXT PRIMARY KEY REFERENCES crm_events(event_id), state TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, processed_at TEXT);
CREATE TABLE IF NOT EXISTS crm_entities(
 entity_id TEXT PRIMARY KEY, kind TEXT NOT NULL, contract_uuid TEXT NOT NULL REFERENCES crm_contracts,
 event_id TEXT NOT NULL REFERENCES crm_events(event_id), data_json TEXT NOT NULL,
 revision INTEGER NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS crm_entities_contract ON crm_entities(contract_uuid,kind,status);
CREATE TABLE IF NOT EXISTS crm_route_policies(
 policy_id TEXT PRIMARY KEY, version INTEGER NOT NULL, team_id TEXT NOT NULL,
 kind TEXT NOT NULL, targets_json TEXT NOT NULL, active INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS crm_delivery_targets(
 event_id TEXT NOT NULL REFERENCES crm_events(event_id), target_id TEXT NOT NULL,
 policy_id TEXT NOT NULL, policy_version INTEGER NOT NULL, due_at TEXT,
 PRIMARY KEY(event_id,target_id));
CREATE TABLE IF NOT EXISTS crm_delivery_receipts(
 receipt_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES crm_events(event_id),
 target_id TEXT NOT NULL, export_id TEXT NOT NULL, file_sha256 TEXT NOT NULL,
 row_keys_json TEXT NOT NULL, field_keys_json TEXT NOT NULL, verified_at TEXT NOT NULL,
 actor_uid INTEGER NOT NULL, request_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS crm_delivery_event ON crm_delivery_receipts(event_id,target_id);
CREATE TABLE IF NOT EXISTS crm_exports(
 export_id TEXT PRIMARY KEY, actor_uid INTEGER NOT NULL, schema_version INTEGER NOT NULL,
 period TEXT NOT NULL, generated_at TEXT NOT NULL, scope_json TEXT NOT NULL,
 baseline_json TEXT NOT NULL, manifest_hash TEXT NOT NULL, target_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_import_previews(
 preview_id TEXT PRIMARY KEY, actor_uid INTEGER NOT NULL, export_id TEXT NOT NULL,
 request_hash TEXT NOT NULL, state_hash TEXT NOT NULL, plan_json TEXT NOT NULL,
 created_at TEXT NOT NULL, consumed_by TEXT);
CREATE TABLE IF NOT EXISTS crm_cohorts(
 cohort_id TEXT PRIMARY KEY, name TEXT NOT NULL, starts_at TEXT NOT NULL,
 metric_version INTEGER NOT NULL, thresholds_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_cohort_members(
 cohort_id TEXT NOT NULL REFERENCES crm_cohorts, employee_uuid TEXT NOT NULL REFERENCES crm_employees,
 access_at TEXT NOT NULL, ends_at TEXT, features_json TEXT NOT NULL,
 excluded_reason TEXT, PRIMARY KEY(cohort_id,employee_uuid));
CREATE TABLE IF NOT EXISTS crm_planned_shifts(
 plan_id TEXT PRIMARY KEY, employee_uuid TEXT NOT NULL REFERENCES crm_employees,
 starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, timezone TEXT NOT NULL,
 workday TEXT NOT NULL, state TEXT NOT NULL, source TEXT NOT NULL,
 UNIQUE(employee_uuid,starts_at));
CREATE INDEX IF NOT EXISTS crm_plan_window ON crm_planned_shifts(employee_uuid,starts_at,ends_at);
CREATE TABLE IF NOT EXISTS crm_shifts(
 shift_id TEXT PRIMARY KEY, employee_uuid TEXT NOT NULL REFERENCES crm_employees,
 starts_at TEXT NOT NULL, ends_at TEXT, timezone TEXT NOT NULL,
 state TEXT NOT NULL, revision INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS crm_one_open_shift ON crm_shifts(employee_uuid) WHERE ends_at IS NULL;
CREATE TABLE IF NOT EXISTS crm_shift_events(
 event_id TEXT PRIMARY KEY, shift_id TEXT NOT NULL REFERENCES crm_shifts,
 actor_uid INTEGER NOT NULL, kind TEXT NOT NULL, server_at TEXT NOT NULL,
 payload_json TEXT NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS crm_clock_anchors(
 anchor_id TEXT PRIMARY KEY, actor_uid INTEGER NOT NULL, installation_id TEXT NOT NULL,
 server_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_telemetry(
 event_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, actor_uid INTEGER NOT NULL,
 employee_uuid TEXT NOT NULL, team_id TEXT NOT NULL, installation_id TEXT NOT NULL,
 session_id TEXT NOT NULL, client_version TEXT NOT NULL, event_type TEXT NOT NULL,
 feature TEXT NOT NULL, operation_id TEXT, error_code TEXT, duration_ms INTEGER,
 occurred_at TEXT, received_at TEXT NOT NULL, dropped_count INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS crm_telemetry_window ON crm_telemetry(employee_uuid,received_at,event_type);
CREATE TABLE IF NOT EXISTS crm_metric_facts(
 event_id TEXT PRIMARY KEY REFERENCES crm_events(event_id), employee_uuid TEXT NOT NULL,
 team_id TEXT NOT NULL, kind TEXT NOT NULL, origin TEXT NOT NULL,
 activity_at TEXT NOT NULL, received_at TEXT NOT NULL, time_quality TEXT NOT NULL, client_version TEXT);
CREATE INDEX IF NOT EXISTS crm_metric_window ON crm_metric_facts(team_id,activity_at,employee_uuid);
CREATE TABLE IF NOT EXISTS crm_migration_issues(
 issue_id TEXT PRIMARY KEY, kind TEXT NOT NULL, legacy_key TEXT NOT NULL,
 detail_json TEXT NOT NULL, resolved_at TEXT, resolution_json TEXT);
CREATE TABLE IF NOT EXISTS crm_audit(
 audit_id INTEGER PRIMARY KEY AUTOINCREMENT, actor_uid INTEGER NOT NULL,
 kind TEXT NOT NULL, target_id TEXT NOT NULL, at TEXT NOT NULL, detail_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_documents(
 document_id TEXT PRIMARY KEY, contract_uuid TEXT NOT NULL REFERENCES crm_contracts,
 actor_uid INTEGER NOT NULL, filename TEXT NOT NULL, media_type TEXT NOT NULL,
 sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_scripts(
 team_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, draft_json TEXT,
 published_json TEXT, published_revision INTEGER, published_at TEXT, author_uid INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS crm_registry_sources(
 source_id TEXT PRIMARY KEY, workbook_sha256 TEXT NOT NULL, rows_sha256 TEXT NOT NULL,
 sheet TEXT NOT NULL, imported_at TEXT NOT NULL, row_count INTEGER NOT NULL, receipt_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_registry_rows(
 contract_uuid TEXT PRIMARY KEY REFERENCES crm_contracts,
 source_id TEXT NOT NULL REFERENCES crm_registry_sources, source_row INTEGER NOT NULL,
 record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_monthly_totals(
 contract_uuid TEXT NOT NULL REFERENCES crm_contracts,month TEXT NOT NULL,
 amount TEXT,currency TEXT,source_column TEXT NOT NULL,observed_at TEXT NOT NULL,
 source_sha256 TEXT NOT NULL,PRIMARY KEY(contract_uuid,month));
CREATE TABLE IF NOT EXISTS crm_authority_imports(
 source_sha256 TEXT PRIMARY KEY,captured_at TEXT NOT NULL,applied_at TEXT NOT NULL,
 plan_sha256 TEXT NOT NULL,before_sha256 TEXT NOT NULL,receipt_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_authority_rows(
 contract_uuid TEXT PRIMARY KEY REFERENCES crm_contracts,source_sha256 TEXT NOT NULL,
 record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_authority_scopes(
 actor_uid INTEGER PRIMARY KEY REFERENCES crm_actor_links,employees_json TEXT NOT NULL,
 all_contracts INTEGER NOT NULL,source_sha256 TEXT NOT NULL);
"""


def initialize(conn):
    from . import storage
    if storage.is_postgres(conn):
        from .postgres_schema import initialize as pg_initialize
        return pg_initialize(conn)
    # executescript would commit an enclosing legacy transaction; execute each
    # complete SQL statement instead, including trigger bodies.
    import sqlite3
    from .legacy_exchange import DDL as exchange_ddl

    statement = ""
    from .active_time import DDL as active_ddl
    from .assignment_schema import ddl as assignment_ddl
    from .runtime_schema import DDL as runtime_ddl
    from .logistics_schema import DDL as logistics_ddl
    from .withdrawals import DDL as withdrawal_ddl
    for line in (DDL + exchange_ddl + active_ddl + assignment_ddl() + runtime_ddl + logistics_ddl + withdrawal_ddl).splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    conn.execute(
        "INSERT INTO crm_meta VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(VERSION),)
    )
    import uuid
    conn.execute("INSERT INTO crm_meta VALUES('instance_id',?) ON CONFLICT(key) DO NOTHING",(str(uuid.uuid4()),))
    for table in ("crm_events", "crm_metric_facts"):
        if "client_version" not in {
            r[1] for r in conn.execute("PRAGMA table_info(" + table + ")")
        }:
            conn.execute("ALTER TABLE " + table + " ADD COLUMN client_version TEXT")
