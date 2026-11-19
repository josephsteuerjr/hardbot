"""Confirmed contract status, independent of Excel assignment and contact fields."""

from .contract import CRMError, now


DDL = """
CREATE TABLE IF NOT EXISTS crm_contact_status(
 contract_uuid TEXT NOT NULL REFERENCES crm_contracts,
 effective_at TEXT NOT NULL, withdrawn INTEGER NOT NULL CHECK(withdrawn IN (0,1)),
 source TEXT NOT NULL, source_sha256 TEXT NOT NULL, recorded_at TEXT NOT NULL,
 PRIMARY KEY(contract_uuid,effective_at));
CREATE INDEX IF NOT EXISTS crm_contact_status_recorded ON crm_contact_status(recorded_at);
CREATE VIEW IF NOT EXISTS crm_contact_withdrawals AS
 SELECT s.contract_uuid,s.effective_at,s.source FROM crm_contact_status s
 WHERE s.withdrawn=1 AND NOT EXISTS(
   SELECT 1 FROM crm_contact_status n WHERE n.contract_uuid=s.contract_uuid
   AND n.effective_at>s.effective_at);
"""

CONTACT_ACTIONS = frozenset({"call", "visit", "contact", "promise", "delivery"})


def blocked(conn, ids=None):
    query = "SELECT contract_uuid FROM crm_contact_withdrawals"
    if ids is None:
        return {row[0] for row in conn.execute(query)}
    result = set()
    ordered = sorted(set(ids))
    for start in range(0, len(ordered), 400):
        batch = ordered[start:start + 400]
        marks = ",".join("?" for _ in batch)
        result.update(row[0] for row in conn.execute(
            query + " WHERE contract_uuid IN (" + marks + ")", batch))
    return result


def require_contactable(conn, ids):
    if blocked(conn, ids):
        raise CRMError("Договор отозван кредитором. Работа по нему остановлена.",
                       "contract_withdrawn", 409)


def apply_confirmed(conn, evidence, *, apply=False):
    """Plan or append a batch of explicit, verified creditor-status observations."""
    from datetime import date
    from re import fullmatch

    from .contract import digest

    if not isinstance(evidence, list) or not evidence:
        raise ValueError("Expected explicit creditor status evidence")
    changes = []
    seen = set()
    for item in evidence:
        if set(item) != {'legacy_id', 'creditor_id', 'day', 'source', 'sha256', 'withdrawn'}:
            raise ValueError("Invalid creditor status evidence")
        if not fullmatch(r'[0-9a-f]{64}', item['sha256']) or type(item['withdrawn']) is not bool:
            raise ValueError("Invalid source digest or status")
        if not item['source'].strip() or date.fromisoformat(item['day']).isoformat() != item['day']:
            raise ValueError("Invalid source or effective day")
        match = conn.execute("SELECT contract_uuid FROM crm_contracts WHERE legacy_id=? AND creditor_id=?",
                             (item['legacy_id'], item['creditor_id'])).fetchone()
        if not match:
            raise ValueError("Contract and creditor do not match the working registry")
        key = (match[0], item['day'])
        if key in seen:
            raise ValueError("Duplicate contract status on the same day")
        seen.add(key)
        prior = conn.execute("SELECT withdrawn,source,source_sha256 FROM crm_contact_status "
                             "WHERE contract_uuid=? AND effective_at=?", key).fetchone()
        status = (int(item['withdrawn']), item['source'], item['sha256'])
        if prior:
            if tuple(prior[index] for index in range(3)) != status:
                raise ValueError("Conflicting evidence for the same contract and day")
            continue
        changes.append((*key, *status))
    if apply:
        recorded = now()
        conn.executemany("INSERT INTO crm_contact_status VALUES(?,?,?,?,?,?)",
                         [(*change, recorded) for change in changes])
    return {"evidence_sha256": digest(evidence), "reviewed": len(evidence),
            "new": len(changes), "applied": apply}
