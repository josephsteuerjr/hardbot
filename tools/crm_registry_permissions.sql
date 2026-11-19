-- Execute as owner in the database that holds both the registry and public CRM schema.
GRANT USAGE ON SCHEMA crm_registry TO crm_app, crm_exchange;
GRANT SELECT ON crm_registry.migration_receipt, crm_registry.clients, crm_registry.staff_roster TO crm_app;
GRANT UPDATE ON crm_registry.clients TO crm_app;
GRANT SELECT, INSERT ON crm_registry.calls, crm_registry.visits TO crm_app;
GRANT USAGE ON SEQUENCE crm_registry.calls_id_seq, crm_registry.visits_id_seq TO crm_app;
GRANT SELECT ON ALL TABLES IN SCHEMA crm_registry TO crm_exchange;
GRANT INSERT, UPDATE ON crm_registry.clients, crm_registry.registry_payments, crm_registry.registry_monthly_totals,
    crm_registry.enrichment_ledger, crm_registry.enrichment_book_ledger, crm_registry.import_receipts,
    crm_registry.import_source_heads, crm_registry.settings TO crm_exchange;
GRANT INSERT ON crm_registry.audit, crm_registry.assignment_events, crm_registry.enrichment_receipts,
    crm_registry.employee_identities, crm_registry.employee_aliases TO crm_exchange;
GRANT INSERT, UPDATE, DELETE ON crm_registry.client_extra TO crm_exchange;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA crm_registry TO crm_exchange;


-- A registry label can own a portfolio before its holder has a CRM login.
-- The exchange role can only create a dormant identity from an existing registry row.
CREATE OR REPLACE FUNCTION crm_registry.register_portfolio_owner(identity_id text, canonical_id uuid)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    INSERT INTO public.crm_employees(employee_uuid, staff_id, name, source, active)
    SELECT canonical_id::text, 'authority-staff:' || i.id, i.display_name, 'registry-portfolio', 0
    FROM crm_registry.employee_identities i WHERE i.id = identity_id
    ON CONFLICT(staff_id) DO NOTHING
$$;
REVOKE ALL ON FUNCTION crm_registry.register_portfolio_owner(text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION crm_registry.register_portfolio_owner(text, uuid) TO crm_exchange;

CREATE OR REPLACE FUNCTION crm_registry.include_new_contracts(ids text[])
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    INSERT INTO public.crm_managed_contracts(team_id, contract_uuid, capability)
    SELECT DISTINCT d.team_id, c.contract_uuid, d.capability
    FROM public.crm_distribution_managers d
    JOIN public.crm_employees e ON e.employee_uuid=d.employee_uuid AND e.active=1
    JOIN public.crm_actor_links l ON l.employee_uuid=e.employee_uuid AND l.active=1
    JOIN public.crm_authority_scopes s ON s.actor_uid=l.actor_uid AND s.all_contracts=1
    JOIN public.crm_contracts c ON c.contract_uuid=ANY(ids)
    JOIN crm_registry.clients r ON c.legacy_id=r.contract_id AND c.creditor_id='registry-partner:' || trim(r.partner)
    ON CONFLICT DO NOTHING
$$;
REVOKE ALL ON FUNCTION crm_registry.include_new_contracts(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION crm_registry.include_new_contracts(text[]) TO crm_exchange;
