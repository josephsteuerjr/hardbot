"""Durable cutover scope, pending projections and service health."""

DDL = """
CREATE TABLE IF NOT EXISTS crm_primary_fences(
 contract_uuid TEXT PRIMARY KEY REFERENCES crm_contracts,
 fence_id TEXT NOT NULL, activated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_primary_pending(
 contract_uuid TEXT PRIMARY KEY REFERENCES crm_contracts, changed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_runtime_status(
 worker TEXT PRIMARY KEY, heartbeat_at TEXT NOT NULL, ok INTEGER NOT NULL,
 code TEXT NOT NULL, detail_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_background_credentials(
 actor_uid BIGINT PRIMARY KEY, sealed TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
