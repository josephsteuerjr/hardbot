"""CRM payment facts used by the registry readers on the same PostgreSQL connection."""
import json
from decimal import Decimal
from zoneinfo import ZoneInfo

from .contract import instant


def facts(conn, kind, contract_ids=None, *, include_revisions=False):
    if kind not in {"reported_payment", "payment_confirmed"}:
        raise ValueError("Unknown payment kind")
    if contract_ids == []:
        return []
    where = ""
    args = [kind]
    if contract_ids is not None:
        where = " AND c.legacy_id = ANY(?)"
        args.append(list(contract_ids))
    revision_join = ("(revision.event_id=first.event_id OR revision.correction_of=first.event_id)"
                     if include_revisions else "revision.event_id=latest.event_id")
    rows = conn.execute("""
        SELECT c.legacy_id, c.currency AS contract_currency, x.entity_id, revision.payload_json AS data_json,
               first.payload_json AS original_json, first.sequence,
               first.occurred_at, first.received_at, first.actor_uid, author.name,
               revision.occurred_at AS edited_at, revision.received_at AS recorded_at,
               (SELECT m.timezone FROM public.crm_memberships m
                 WHERE m.employee_uuid=first.employee_uuid AND m.team_id=first.team_id
                   AND m.starts_at<=first.received_at AND (m.ends_at IS NULL OR m.ends_at>first.received_at)
                 ORDER BY m.starts_at DESC, m.membership_id LIMIT 1) AS office_zone
          FROM public.crm_entities x
          JOIN public.crm_contracts c USING(contract_uuid)
          JOIN clients book ON book.contract_id=c.legacy_id
               AND c.creditor_id='registry-partner:' || trim(book.partner)
          JOIN public.crm_events latest ON latest.event_id=x.event_id
          JOIN public.crm_events first ON first.event_id=COALESCE(latest.correction_of,latest.event_id)
          JOIN public.crm_events revision ON """ + revision_join + """
          JOIN public.crm_employees author ON author.employee_uuid=first.employee_uuid
         WHERE x.kind=? AND latest.projection_status='applied' AND revision.projection_status='applied' AND first.origin!='legacy'
        """ + where + " ORDER BY latest.received_at, first.sequence", args)
    result = []
    for row in rows:
        payload = json.loads(row["data_json"])
        original = instant(row["occurred_at"])
        zone = ZoneInfo(row["office_zone"]) if row["office_zone"] else original.tzinfo
        result.append(dict(
            id=row["sequence"], op="crm:" + row["entity_id"], contract_id=row["legacy_id"],
            employee=row["name"], telegram_id=row["actor_uid"], source="crm",
            paid_at=payload.get("paid_at") or json.loads(row["original_json"]).get("paid_at")
                    or original.astimezone(zone).date().isoformat(),
            event_day=instant(row["edited_at"]).astimezone(zone).date().isoformat(),
            amount=float(Decimal(payload["amount"])),
            currency="₽" if payload["currency"] == "RUB" else payload["currency"],
            created_at=row["recorded_at"], result_status=None,
            same_currency=payload["currency"] == row["contract_currency"],
            source_name=payload.get("source"), source_id=payload.get("source_id"),
        ))
    return result



def latest_confirmations(rows):
    latest = {}
    for item in rows:
        if not item["same_currency"] or item["amount"] <= 0:
            continue
        previous = latest.get(item["contract_id"])
        if previous is None or (item["paid_at"], item["created_at"], item["id"]) > (
                previous["paid_at"], previous["created_at"], previous["id"]):
            latest[item["contract_id"]] = item
    return latest


def payment_history(registry, posted):
    """A registry row has one unlabelled observation per contract, day and currency."""
    from collections import defaultdict
    grouped = defaultdict(list)
    for item in posted:
        grouped[item["contract_id"], item["paid_at"], item["currency"]].append(item)
    result = []
    for key, entries in grouped.items():
        observation = registry.get(key)
        if observation:
            seen = Decimal(str(observation["amount"]))
            amounts = [Decimal(str(item["amount"])) for item in entries]
            if seen not in amounts and seen != sum(amounts):
                # Neither a specific transaction nor their daily total matches the
                # observation. Keep the established registry figure for this key.
                continue
            del registry[key]
        result.extend({k: item[k] for k in ("contract_id", "paid_at", "currency", "amount", "source_name", "source_id")}
                      for item in entries)
    return [*registry.values(), *result]
