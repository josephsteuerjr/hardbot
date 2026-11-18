-- Apply as schema owner after schema migration. Identity/scope tables remain SELECT-only.
GRANT SELECT ON crm_primary_fences,crm_primary_pending,crm_runtime_status,crm_background_credentials,
 crm_background_authorities,crm_report_snapshots,crm_managed_contracts,crm_distribution_managers,
 crm_assignment_heads,crm_assignment_operations,crm_assignment_previews,crm_assignment_changes,
 crm_task_grant_revocations,crm_assignment_jobs,crm_assignment_packs,crm_effective_assignments TO crm_app;
GRANT INSERT ON crm_assignment_heads,crm_assignment_operations,crm_assignment_previews,crm_assignment_changes,
 crm_task_grant_revocations,crm_assignment_jobs,crm_assignment_packs,crm_assignments,
 crm_background_authorities,crm_report_snapshots,crm_primary_pending,crm_runtime_status,
 crm_background_credentials,crm_migration_issues TO crm_app;
GRANT UPDATE ON crm_assignment_heads,crm_assignment_jobs,crm_assignment_packs,crm_assignments,
 crm_background_authorities,crm_primary_pending,crm_runtime_status,crm_background_credentials TO crm_app;
GRANT DELETE ON crm_primary_pending TO crm_app;
GRANT UPDATE (name) ON crm_people TO crm_app;
GRANT USAGE ON SEQUENCE crm_assignment_changes_sequence_seq TO crm_app;
GRANT SELECT ON crm_logistics_staff,crm_logistics_cases,crm_logistics_calls TO crm_app;
GRANT INSERT, UPDATE ON crm_logistics_cases TO crm_app;
GRANT INSERT ON crm_logistics_calls TO crm_app;

-- The existing exchange role must advance each accepted source baseline and
-- reconcile an assignment within the same transaction as its audit record.
GRANT UPDATE (record_json,source_sha256,observed_at) ON crm_legacy_state TO crm_exchange;
GRANT SELECT ON crm_assignment_heads TO crm_exchange;
GRANT UPDATE (employee_uuid,revision,updated_at,operation_id,source,baseline_employee_uuid)
 ON crm_assignment_heads TO crm_exchange;
GRANT INSERT ON crm_assignment_changes,crm_task_grant_revocations TO crm_exchange;
GRANT USAGE ON SEQUENCE crm_assignment_changes_sequence_seq TO crm_exchange;
