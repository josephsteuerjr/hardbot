"""Additive actor-grant tables; legacy identity tables remain read-only here."""


def ddl(postgres=False):
    integer = "BIGINT" if postgres else "INTEGER"
    triggers = ""
    if postgres:
        triggers = """
DROP TRIGGER IF EXISTS crm_grant_changes_immutable ON crm_grant_changes;
DROP TRIGGER IF EXISTS crm_grant_changes_no_truncate ON crm_grant_changes;
CREATE TRIGGER crm_grant_changes_immutable BEFORE UPDATE OR DELETE ON crm_grant_changes
 FOR EACH ROW EXECUTE FUNCTION crm_reject_history_change();
CREATE TRIGGER crm_grant_changes_no_truncate BEFORE TRUNCATE ON crm_grant_changes
 FOR EACH STATEMENT EXECUTE FUNCTION crm_reject_history_change();
"""
    else:
        triggers = """
CREATE TRIGGER IF NOT EXISTS crm_grant_changes_no_update BEFORE UPDATE ON crm_grant_changes
 BEGIN SELECT RAISE(ABORT,'CRM grant audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS crm_grant_changes_no_delete BEFORE DELETE ON crm_grant_changes
 BEGIN SELECT RAISE(ABORT,'CRM grant audit is immutable'); END;
"""
    return f"""
CREATE TABLE IF NOT EXISTS crm_actor_access(
 actor_uid {integer} PRIMARY KEY REFERENCES crm_actor_links,
 access_model {integer} NOT NULL DEFAULT 1 CHECK(access_model IN(1,2)),
 policy_revision {integer} NOT NULL DEFAULT 0 CHECK(policy_revision>=0));
CREATE TABLE IF NOT EXISTS crm_grants(
 grant_id TEXT PRIMARY KEY,
 actor_uid {integer} NOT NULL REFERENCES crm_actor_links,
 role TEXT NOT NULL,
 territory TEXT NOT NULL CHECK(territory IN('self','employees','team','all')),
 capabilities_json TEXT NOT NULL,
 all_contracts {integer} NOT NULL DEFAULT 0,
 read_all_contracts {integer} NOT NULL DEFAULT 0,
 is_default {integer} NOT NULL DEFAULT 0 CHECK(is_default IN(0,1)),
 revision {integer} NOT NULL CHECK(revision>=1),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 revoked_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS crm_grant_actor_role_territory
 ON crm_grants(actor_uid,role,territory) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS crm_grant_one_default
 ON crm_grants(actor_uid) WHERE is_default=1 AND revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_grant_actor_active
 ON crm_grants(actor_uid,revoked_at,role);
CREATE TABLE IF NOT EXISTS crm_grant_employees(
 grant_id TEXT NOT NULL REFERENCES crm_grants,
 employee_uuid TEXT NOT NULL REFERENCES crm_employees,
 PRIMARY KEY(grant_id,employee_uuid));
CREATE INDEX IF NOT EXISTS crm_grant_employee_scope
 ON crm_grant_employees(employee_uuid,grant_id);
CREATE TABLE IF NOT EXISTS crm_grant_changes(
 change_id TEXT PRIMARY KEY,
 actor_uid {integer} NOT NULL REFERENCES crm_actor_links,
 grant_id TEXT REFERENCES crm_grants,
 revision {integer} NOT NULL,
 action TEXT NOT NULL,
 before_json TEXT NOT NULL,
 after_json TEXT NOT NULL,
 changed_by_uid {integer},
 changed_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS crm_grant_change_actor
 ON crm_grant_changes(actor_uid,revision,change_id);
{triggers}"""
