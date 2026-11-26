-- Execute as schema owner for the dedicated crm_access_admin login.
GRANT USAGE ON SCHEMA public TO crm_access_admin;
GRANT SELECT ON crm_meta,crm_actor_links,crm_employees,crm_memberships,
 crm_authority_scopes,crm_actor_access,crm_grants,crm_grant_employees,
 crm_grant_changes,crm_commands TO crm_access_admin;
GRANT INSERT, UPDATE ON crm_actor_access,crm_grants TO crm_access_admin;
GRANT INSERT, DELETE ON crm_grant_employees TO crm_access_admin;
GRANT INSERT ON crm_grant_changes,crm_commands,crm_audit TO crm_access_admin;
