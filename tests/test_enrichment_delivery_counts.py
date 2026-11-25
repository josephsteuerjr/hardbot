"""A batch acknowledgment must not be multiplied by its workbook count."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pult"))
import enrich


def entry(cid):
    return {"contract_id": cid, "key": "call_status", "value": "done",
            "visit_date": "2026-10-29", "kind": "written"}


def test_batch_ack_counts_each_delivered_value_once(tmp_path):
    receipt = tmp_path / "batch.json"
    receipt.write_text("{}", encoding="utf-8")
    results = [
        {"file": "a.xlsb", "employee": "A", "ledger_entries": [entry("1"), entry("2")]},
        {"file": "b.xlsb", "employee": "B", "ledger_entries": [entry("3")]},
        {"file": "c.xlsb", "employee": "C", "error": "busy",
         "ledger_entries": [entry("4")]},
    ]
    sent = []
    def acknowledge(payload):
        sent.append(payload)
        return {"ok": True, "recorded": 3}
    enrich._record_deliveries(results, acknowledge, receipt)
    assert len(sent[0]["entries"]) == 3
    assert [r["ledger_recorded"] for r in results] == [2, 1, 0]


def test_partial_ack_is_not_reported_as_complete_delivery(tmp_path):
    receipt = tmp_path / "batch.json"
    receipt.write_text("{}", encoding="utf-8")
    results = [{"file": "a.xlsb", "employee": "A",
                "ledger_entries": [entry("1"), entry("2")]}]
    enrich._record_deliveries(results, lambda _: {"ok": True, "recorded": 1}, receipt)
    assert results[0].get("ledger_error")
    assert not results[0].get("ledger_recorded")
