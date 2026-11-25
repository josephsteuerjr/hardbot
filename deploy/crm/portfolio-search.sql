SET lock_timeout = '5s';
SET statement_timeout = '120s';

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS crm_people_portfolio_name_trgm
    ON crm_people USING gin (lower(name) gin_trgm_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS crm_contracts_portfolio_contact_trgm
    ON crm_contracts USING gin (
        lower(external_id || ' ' || coalesce(fields_json::jsonb->>'phone','')
              || ' ' || coalesce(fields_json::jsonb->>'address','')) gin_trgm_ops
    );
