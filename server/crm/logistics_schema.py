DDL = """
CREATE TABLE IF NOT EXISTS crm_logistics_staff(
 employee_uuid TEXT NOT NULL REFERENCES crm_employees,
 country TEXT NOT NULL CHECK(country IN ('UZ','TJ')),
 work TEXT NOT NULL CHECK(work IN ('logist','call','delivery')),
 PRIMARY KEY(employee_uuid,country,work));
CREATE INDEX IF NOT EXISTS crm_logistics_staff_country ON crm_logistics_staff(country,work,employee_uuid);
CREATE TABLE IF NOT EXISTS crm_logistics_cases(
 contract_uuid TEXT PRIMARY KEY REFERENCES crm_contracts,
 country TEXT NOT NULL CHECK(country IN ('UZ','TJ')),
 workbook_sha256 TEXT NOT NULL,
 courier_label TEXT NOT NULL,
 courier_uuid TEXT REFERENCES crm_employees,
 transferred_on TEXT NOT NULL,
 phone TEXT NOT NULL,
 caller_uuid TEXT REFERENCES crm_employees,
 call_status TEXT NOT NULL DEFAULT '',
 call_comment TEXT NOT NULL DEFAULT '',
 call_at TEXT NOT NULL DEFAULT '',
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 revision BIGINT NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS crm_logistics_case_country ON crm_logistics_cases(country,active,contract_uuid);
CREATE INDEX IF NOT EXISTS crm_logistics_caller ON crm_logistics_cases(caller_uuid,active);
CREATE INDEX IF NOT EXISTS crm_logistics_courier ON crm_logistics_cases(courier_uuid,active);
CREATE TABLE IF NOT EXISTS crm_logistics_calls(
 call_id TEXT PRIMARY KEY,
 contract_uuid TEXT NOT NULL REFERENCES crm_logistics_cases,
 employee_uuid TEXT NOT NULL REFERENCES crm_employees,
 actor_uid BIGINT NOT NULL,
 status TEXT NOT NULL,
 comment TEXT NOT NULL,
 phone TEXT NOT NULL,
 at TEXT NOT NULL);
"""
