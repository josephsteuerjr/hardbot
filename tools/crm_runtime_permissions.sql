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
-- The import may enroll an active courier for deliveries. The runtime cannot
-- administer the general roster or write any other kind of membership.
DO $permissions$
DECLARE app_schema name := current_schema();
BEGIN
    EXECUTE format($function$
        CREATE OR REPLACE FUNCTION %1$I.crm_enroll_delivery_courier(region text, employee text)
        RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $body$
            INSERT INTO %1$I.crm_logistics_staff(country,work,employee_uuid)
            SELECT region,'delivery',e.employee_uuid
            FROM %1$I.crm_employees e
            WHERE region IN ('TJ','UZ') AND e.employee_uuid=employee AND e.active=1
                AND EXISTS (SELECT 1 FROM %1$I.crm_actor_links a WHERE a.employee_uuid=e.employee_uuid AND a.active=1)
                AND EXISTS (SELECT 1 FROM %1$I.crm_memberships m WHERE m.employee_uuid=e.employee_uuid
                    AND m.role='courier' AND m.starts_at::timestamptz<=statement_timestamp()
                    AND (m.ends_at IS NULL OR m.ends_at::timestamptz>statement_timestamp())
                    AND jsonb_exists(m.capabilities_json::jsonb,'delivery'))
            ON CONFLICT DO NOTHING
        $body$
    $function$, app_schema);
    EXECUTE format('REVOKE ALL ON FUNCTION %I.crm_enroll_delivery_courier(text,text) FROM PUBLIC', app_schema);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %I.crm_enroll_delivery_courier(text,text) TO crm_app', app_schema);
END
$permissions$;
GRANT INSERT, UPDATE ON crm_logistics_cases TO crm_app;
GRANT INSERT ON crm_logistics_calls TO crm_app;
GRANT SELECT ON crm_contact_status,crm_contact_withdrawals TO crm_app;
GRANT SELECT ON crm_contact_withdrawals TO crm_exchange;
GRANT SELECT ON crm_actor_access,crm_grants,crm_grant_employees,crm_grant_changes TO crm_app;
GRANT SELECT ON crm_actor_access,crm_grants,crm_grant_employees TO crm_exchange;

-- The existing exchange role must advance each accepted source baseline and
-- reconcile an assignment within the same transaction as its audit record.
GRANT UPDATE (record_json,source_sha256,observed_at) ON crm_legacy_state TO crm_exchange;
GRANT SELECT ON crm_assignment_heads,crm_primary_fences TO crm_exchange;
GRANT UPDATE (employee_uuid,revision,updated_at,operation_id,source,baseline_employee_uuid)
 ON crm_assignment_heads TO crm_exchange;
GRANT INSERT ON crm_assignment_changes,crm_task_grant_revocations TO crm_exchange;
GRANT USAGE ON SEQUENCE crm_assignment_changes_sequence_seq TO crm_exchange;
GRANT SELECT, INSERT ON crm_monthly_totals TO crm_exchange;
GRANT UPDATE (amount,currency,source_column,observed_at,source_sha256) ON crm_monthly_totals TO crm_exchange;
GRANT INSERT ON crm_people,crm_contracts,crm_authority_rows TO crm_exchange;
GRANT UPDATE (legacy_id) ON crm_contracts TO crm_exchange;
