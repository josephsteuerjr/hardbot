-- Migration companion, to run as the schema owner in the chosen CRM namespace.
-- Client names are editable business data. Staff, roles and account tables stay
-- SELECT-only for crm_app; client IDs and their source identity stay read-only.
GRANT UPDATE (name) ON crm_people TO crm_app;
