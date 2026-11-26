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

-- Called only by the primary identity bridge after an approved account/device login.
CREATE TABLE IF NOT EXISTS crm_registry.registered_manager_areas (
    actor_uid bigint PRIMARY KEY REFERENCES public.crm_actor_links(actor_uid),
    country text NOT NULL CHECK(country IN ('tj','uz')),
    city text NOT NULL);

CREATE OR REPLACE VIEW crm_registry.registered_manager_scope AS
SELECT a.actor_uid, e.employee_uuid
FROM crm_registry.registered_manager_areas a
JOIN crm_registry.staff_roster r ON r.active=1 AND (r.manager_uid IS NULL OR r.manager_uid=a.actor_uid)
JOIN public.crm_employees e ON e.active=1 AND e.staff_id='authority-staff:' || r.employee_id
WHERE r.direction=a.country
  AND (a.city='' OR r.office=a.city OR jsonb_exists(r.offices::jsonb,a.city));
GRANT SELECT ON crm_registry.registered_manager_scope TO crm_app;

CREATE OR REPLACE FUNCTION crm_registry.project_registration(
    actor bigint, profile bigint, candidate text, member text, account_role text, label text,
    identity_id text, region text, selected_office text, capabilities text, zone text, activated text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    person text;
    linked text;
    existing_role text;
    staff_key text;
    permitted text;
BEGIN
    IF account_role NOT IN ('employee','manager','analyst') OR actor <= 0 OR profile <= 0 THEN
        RAISE EXCEPTION 'Invalid registration identity';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('crm-registration'), hashtext(actor::text));
    IF account_role = 'employee' THEN
        staff_key := 'authority-staff:' || identity_id;
        SELECT e.employee_uuid INTO person FROM public.crm_employees e
        JOIN crm_registry.employee_identities i ON i.id=identity_id AND e.staff_id=staff_key;
        IF person IS NULL THEN
            RAISE EXCEPTION 'No enrolled portfolio identity';
        END IF;
    ELSE
        staff_key := 'crm-registration:' || profile::text;
        INSERT INTO public.crm_employees(employee_uuid,staff_id,name,source,active)
        VALUES(candidate,staff_key,label,'crm-registration',1) ON CONFLICT(staff_id) DO NOTHING;
        SELECT e.employee_uuid INTO person FROM public.crm_employees e WHERE e.staff_id=staff_key AND e.name=label;
    END IF;
    IF person IS NULL THEN RAISE EXCEPTION 'Registration identity mismatch'; END IF;
    SELECT l.employee_uuid INTO linked FROM public.crm_actor_links l WHERE actor_uid=actor AND l.active=1;
    IF linked IS NOT NULL AND linked<>person THEN RAISE EXCEPTION 'Existing actor identity mismatch'; END IF;
    IF EXISTS(SELECT 1 FROM public.crm_actor_links l WHERE l.employee_uuid=person AND l.actor_uid<>actor AND l.active=1) THEN
        RAISE EXCEPTION 'Portfolio identity already linked';
    END IF;
    SELECT m.role INTO existing_role FROM public.crm_memberships m
    WHERE m.employee_uuid=person AND m.ends_at IS NULL ORDER BY m.starts_at DESC LIMIT 1;
    IF existing_role IS DISTINCT FROM account_role AND existing_role IS NOT NULL THEN
        RAISE EXCEPTION 'Existing membership role mismatch';
    END IF;
    UPDATE public.crm_employees SET active=1 WHERE employee_uuid=person;
    INSERT INTO public.crm_actor_links(actor_uid,employee_uuid,evidence,active)
    VALUES(actor,person,'crm-registration',1) ON CONFLICT(actor_uid) DO NOTHING;
    IF NOT EXISTS(SELECT 1 FROM public.crm_actor_links l WHERE actor_uid=actor AND employee_uuid=person AND l.active=1) THEN
        RAISE EXCEPTION 'Registration actor is unavailable';
    END IF;
    IF existing_role IS NULL THEN
        INSERT INTO public.crm_memberships(membership_id,employee_uuid,team_id,role,capabilities_json,timezone,starts_at)
        VALUES(member,person,'crm-cis',account_role,capabilities,zone,activated);
    END IF;
    IF account_role = 'manager' THEN
        INSERT INTO crm_registry.registered_manager_areas(actor_uid,country,city)
        VALUES(actor,region,selected_office) ON CONFLICT(actor_uid) DO NOTHING;
    ELSIF account_role = 'analyst' THEN
        SELECT COALESCE(json_agg(e.employee_uuid ORDER BY e.employee_uuid)::text,'[]') INTO permitted
        FROM public.crm_employees e WHERE e.active=1;
        INSERT INTO public.crm_authority_scopes(actor_uid,employees_json,all_contracts,source_sha256)
        VALUES(actor,permitted,1,repeat('0',64)) ON CONFLICT(actor_uid) DO NOTHING;
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION crm_registry.project_registration(bigint,bigint,text,text,text,text,text,text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION crm_registry.project_registration(bigint,bigint,text,text,text,text,text,text,text,text,text,text) TO crm_exchange;
