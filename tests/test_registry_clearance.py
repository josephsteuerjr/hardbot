import gzip
import hashlib
import json
import shutil
from pathlib import Path

import db
import limited_test_reset as reset
from openpyxl import Workbook
import pytest

from pult import enrich as pult_enrich
from registry_cycle import clearance
from registry_cycle.files import fingerprint


OWNER = "Test Employee"
CONTRACT = "C-1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(payload) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _gzip_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False)
    return path


@pytest.fixture
def hardbot_snapshot(monkeypatch, tmp_path: Path) -> Path:
    data = tmp_path / "hardbot-data"
    data.mkdir()
    database = data / "hardbot.db"
    photos = data / "photos"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(reset, "PHOTO_DIR", photos)
    db.init_db()
    db.import_grid(
        [
            [
                db.BY_KEY["contract_id"].title,
                db.BY_KEY["client"].title,
                db.BY_KEY["responsible"].title,
            ],
            [CONTRACT, "Test Client", OWNER],
        ],
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    visit_result = db.BY_KEY["call_status"].options[0]
    db.update_person(
        CONTRACT,
        {
            "visit_date": "2026-07-29",
            "call_status": visit_result,
            "visit_comment": "Door was closed",
            "tips": "+998 90 123 45 67",
        },
    )
    db.log_visit(CONTRACT, 101, "visit", op_id="clearance-test-visit")
    db.record_bot_visit(
        CONTRACT,
        {
            "call_status": visit_result,
            "visit_comment": "Door was closed",
            "tips": "+998 90 123 45 67",
        },
        "2026-07-29",
    )
    return reset.create_snapshot(
        data / "backups" / "limited-test-clearance"
    ) and data / "backups" / "limited-test-clearance"


def _safe_snapshot_rows(snapshot: Path) -> tuple[list[dict], list[dict]]:
    with gzip.open(
        snapshot / clearance.SAFE_EXPORT_NAME,
        "rt",
        encoding="utf-8",
    ) as stream:
        rows = json.load(stream)["rows"]
    fields = json.loads(
        (snapshot / clearance.SCHEMA_NAME).read_text(encoding="utf-8")
    )
    return rows, fields


def _write_enriched_workbook(
    path: Path,
    *,
    rows: list[dict],
    fields: list[dict],
) -> None:
    row = rows[0]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = pult_enrich.SHEET_NAME
    sheet.append([item["title"] for item in fields])
    values = []
    for field in fields:
        key = field["key"]
        value = row.get(key)
        if key == "overall_result":
            value = row.get("call_status")
        elif key == "extra_address":
            value = row.get("tips")
        values.append(value)
    sheet.append(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()


def _enrichment_receipt(
    snapshot: Path,
    personal: Path,
    *,
    covered: bool = True,
) -> Path:
    snapshot_data = clearance._snapshot_evidence(snapshot)
    rows, fields = _safe_snapshot_rows(snapshot)
    plan = clearance._xlsx_plan(personal, fields, rows)
    assert plan["cells_written"] == 0
    matched = [CONTRACT] if covered else []
    missing = [] if covered else [CONTRACT]
    owner_contracts = [CONTRACT]
    summary = {
        "filled_cases": 1,
        "filled_contract_ids": owner_contracts,
        "matched_cases": len(matched),
        "matched_contract_ids": matched,
        "changed_cases": 0,
        "cells_written": 0,
        "errors": [],
        "visit_cols_absent": [],
        "missing_contract_ids": missing,
    }
    personal = personal.resolve()
    file_result = {
        "responsible": OWNER,
        "path": str(personal),
        "pre_sha256": _sha(personal),
        "post_sha256": _sha(personal),
        "backup_path": None,
        "backup_sha256": None,
        "owner_contract_ids": owner_contracts,
        **summary,
        "post_plan": dict(summary),
    }
    unsigned = {
        "schema": pult_enrich.EVIDENCE_SCHEMA,
        "data_exchange_contract": pult_enrich.DATA_EXCHANGE_CONTRACT,
        "created_at": "2026-07-29T00:00:00+00:00",
        "snapshot": {
            "manifest_sha256": snapshot_data["manifest_sha256"],
            "database_state_digest": snapshot_data[
                "database_state_digest"
            ],
            "safe_export_sha256": snapshot_data["safe_export_sha256"],
            "raw_export_sha256": snapshot_data["raw_export_sha256"],
            "data_exchange_contract_sha256": snapshot_data[
                "data_exchange_contract_sha256"
            ],
        },
        "completed_contract_ids": [CONTRACT],
        "owners": [
            {
                "responsible": OWNER,
                "contract_ids": owner_contracts,
                "files": [str(personal)],
            }
        ],
        "expected_files": [str(personal)],
        "files": [file_result],
    }
    return _json(
        snapshot / "pult-enrichment-receipt.json",
        {**unsigned, "receipt_digest": _canonical_digest(unsigned)},
    )


def _copy_with_metadata(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _snapshot_payload(
    run_dir: Path,
    *,
    run_id: str,
    master: Path,
    personal: Path,
    baseline: Path,
) -> Path:
    source_dir = run_dir / "sources"
    master_copy = source_dir / "master.xlsx"
    personal_copy = source_dir / "personal.xlsx"
    baseline_copy = source_dir / "baseline.json.gz"
    _copy_with_metadata(master, master_copy)
    _copy_with_metadata(personal, personal_copy)
    _copy_with_metadata(baseline, baseline_copy)
    entry = {
        "entry_id": "personal-1",
        "path": str(personal.resolve()),
        "country": "TJ",
        "group": "G1",
        "profile": "default",
        "employee_folder": OWNER,
        "responsible_hint": OWNER,
    }
    payload = {
        "run_id": run_id,
        "run_dir": str(run_dir.resolve()),
        "master": {
            "original": str(master.resolve()),
            "copy": str(master_copy.resolve()),
            "fingerprint": fingerprint(master_copy).as_dict(),
        },
        "personals": [
            {
                "entry": entry,
                "copy_path": str(personal_copy.resolve()),
                "fingerprint": fingerprint(personal_copy).as_dict(),
            }
        ],
        "baseline": {
            "copy": str(baseline_copy.resolve()),
            "fingerprint": fingerprint(baseline_copy).as_dict(),
        },
    }
    return _json(run_dir / "snapshot.json", payload)


def _plan_payload(snapshot_path: Path) -> Path:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    personal = snapshot["personals"][0]
    unsigned = {
        "master_path": snapshot["master"]["original"],
        "master_fingerprint": snapshot["master"]["fingerprint"],
        "changes": [],
        "projections": [
            {
                "entry": personal["entry"],
                "responsible": OWNER,
                "columns": 1,
                # Схема проекции подписывается целиком: заголовки, служебная
                # строка типов и добавленные справа столбцы входят в неё,
                # чтобы допуск не подтвердил план с другой геометрией книги.
                "headers": ["Номер КД / ID"],
                "type_tags": ["сх"],
                "added_columns": [],
                "rows": 1,
                "preserved_cells": 0,
                "employee_owned_preserved_cells": 0,
                "handoff_cells": 0,
                "rollover_cleared_cells": 0,
                "retained_rows": 0,
                "removed_rows": 0,
                "source_fingerprint": personal["fingerprint"],
            }
        ],
        "issues": [],
        "month_rollover": None,
        "blocked": False,
    }
    return _json(
        snapshot_path.parent / "plan.json",
        {**unsigned, "digest": _canonical_digest(unsigned)},
    )


def _stage_payload(snapshot_path: Path, plan_path: Path) -> tuple[Path, Path]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    staging = snapshot_path.parent / "staging"
    master_staged = staging / "master.xlsx"
    personal_staged = staging / "personal.xlsx"
    _copy_with_metadata(Path(snapshot["master"]["copy"]), master_staged)
    _copy_with_metadata(
        Path(snapshot["personals"][0]["copy_path"]),
        personal_staged,
    )
    personal_entry = snapshot["personals"][0]["entry"]
    stage = {
        "run_id": snapshot["run_id"],
        "run_dir": snapshot["run_dir"],
        "staging_dir": str(staging.resolve()),
        "plan_digest": plan["digest"],
        "master": {
            "kind": "master",
            "entry_id": None,
            "original_path": snapshot["master"]["original"],
            "source_copy": snapshot["master"]["copy"],
            "staged_path": str(master_staged.resolve()),
            "source_fingerprint": snapshot["master"]["fingerprint"],
            "staged_fingerprint": fingerprint(master_staged).as_dict(),
        },
        "personals": [
            {
                "kind": "personal",
                "entry_id": personal_entry["entry_id"],
                "original_path": personal_entry["path"],
                "source_copy": snapshot["personals"][0]["copy_path"],
                "staged_path": str(personal_staged.resolve()),
                "source_fingerprint": snapshot["personals"][0][
                    "fingerprint"
                ],
                "staged_fingerprint": fingerprint(
                    personal_staged
                ).as_dict(),
            }
        ],
    }
    stage_path = _json(staging / "stage.json", stage)
    validation = _json(
        staging / "validation.json",
        {
            "run_id": snapshot["run_id"],
            "plan_digest": plan["digest"],
            "checked_files": 2,
            "checked_master_cells": 0,
            "checked_projection_cells": 0,
            "issues": [],
            "blocked": False,
        },
    )
    return stage_path, validation


def _registry_artifacts(
    tmp_path: Path,
    snapshot: Path,
    *,
    journal: bool = False,
) -> dict[str, Path]:
    rows, fields = _safe_snapshot_rows(snapshot)
    live = tmp_path / "registry-live"
    master = live / "master.xlsx"
    personal = live / "personal.xlsx"
    _write_enriched_workbook(master, rows=rows, fields=fields)
    _write_enriched_workbook(personal, rows=rows, fields=fields)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline = _gzip_json(
        workspace / "baselines" / "active.json.gz",
        {
            "schema_version": 1,
            "created_at": "2026-07-29T00:00:00",
            "master_path": str(master.resolve()),
            "plan_digest": "0" * 64,
            "records": [],
        },
    )
    first_snapshot = _snapshot_payload(
        workspace / "runs" / "initial",
        run_id="initial",
        master=master,
        personal=personal,
        baseline=baseline,
    )
    first_plan = _plan_payload(first_snapshot)
    first_stage, first_validation = _stage_payload(
        first_snapshot,
        first_plan,
    )
    post_snapshot = _snapshot_payload(
        workspace / "runs" / "post",
        run_id="post",
        master=master,
        personal=personal,
        baseline=baseline,
    )
    post_plan = _plan_payload(post_snapshot)
    post_stage, post_validation = _stage_payload(
        post_snapshot,
        post_plan,
    )
    if journal:
        journal_path = workspace / "runs" / "unfinished" / (
            "publish.journal.jsonl"
        )
        journal_path.parent.mkdir(parents=True)
        journal_path.write_text(
            json.dumps(
                {
                    "event": "publish_started",
                    "run_id": "unfinished",
                    "plan_digest": "f" * 64,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    return {
        "workspace": workspace,
        "personal": personal,
        "registry_snapshot": first_snapshot,
        "plan": first_plan,
        "stage": first_stage,
        "validation": first_validation,
        "post_snapshot": post_snapshot,
        "post_plan": post_plan,
        "post_stage": post_stage,
        "post_validation": post_validation,
        "active_baseline": baseline,
    }


def _issue(
    snapshot: Path,
    enrichment: Path,
    registry: dict[str, Path],
    output: Path,
) -> dict:
    return clearance.issue_clearance(
        snapshot_dir=snapshot,
        enrichment_evidence=enrichment,
        workspace=registry["workspace"],
        registry_snapshot=registry["registry_snapshot"],
        plan=registry["plan"],
        stage=registry["stage"],
        validation=registry["validation"],
        publication=None,
        post_snapshot=registry["post_snapshot"],
        post_plan=registry["post_plan"],
        post_stage=registry["post_stage"],
        post_validation=registry["post_validation"],
        active_baseline=registry["active_baseline"],
        mode="already_zero",
        output=output,
    )


def test_clearance_binds_real_snapshot_workbook_and_registry_artifacts(
    hardbot_snapshot,
    tmp_path,
):
    registry = _registry_artifacts(tmp_path, hardbot_snapshot)
    evidence = _enrichment_receipt(
        hardbot_snapshot,
        registry["personal"],
    )
    output = hardbot_snapshot / clearance.RECEIPT_NAME

    issued = _issue(hardbot_snapshot, evidence, registry, output)
    verified = clearance.verify_clearance_receipt(
        output,
        snapshot_dir=hardbot_snapshot,
    )

    assert verified["clearance_digest"] == issued["clearance_digest"]
    assert verified["provenance"]["completed_cases"] == 1
    assert verified["coverage"]["uncovered_cases"] == 0
    assert verified["registry"]["post_run_changes"] == 0
    assert verified["enrichment"]["final_registry_verification"][
        "covered_cases"
    ] == 1


def test_clearance_blocks_self_asserted_uncovered_case(
    hardbot_snapshot,
    tmp_path,
):
    registry = _registry_artifacts(tmp_path, hardbot_snapshot)
    evidence = _enrichment_receipt(
        hardbot_snapshot,
        registry["personal"],
        covered=False,
    )

    with pytest.raises(clearance.ClearanceBlocked, match="Enrichment"):
        _issue(
            hardbot_snapshot,
            evidence,
            registry,
            hardbot_snapshot / clearance.RECEIPT_NAME,
        )


def test_clearance_revalidation_blocks_changed_live_personal(
    hardbot_snapshot,
    tmp_path,
):
    registry = _registry_artifacts(tmp_path, hardbot_snapshot)
    evidence = _enrichment_receipt(
        hardbot_snapshot,
        registry["personal"],
    )
    output = hardbot_snapshot / clearance.RECEIPT_NAME
    _issue(hardbot_snapshot, evidence, registry, output)

    registry["personal"].write_bytes(b"not an Excel workbook")
    with pytest.raises(clearance.ClearanceBlocked, match="fingerprint"):
        clearance.verify_clearance_receipt(
            output,
            snapshot_dir=hardbot_snapshot,
        )


def test_clearance_blocks_non_excel_file_even_with_consistent_receipt(
    hardbot_snapshot,
    tmp_path,
):
    registry = _registry_artifacts(tmp_path, hardbot_snapshot)
    fake = tmp_path / "fake.xlsb"
    fake.write_bytes(b"not an Excel workbook")
    evidence = _enrichment_receipt(
        hardbot_snapshot,
        registry["personal"],
    )
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["owners"][0]["files"] = [str(fake.resolve())]
    payload["expected_files"] = [str(fake.resolve())]
    file_result = payload["files"][0]
    file_result["path"] = str(fake.resolve())
    file_result["pre_sha256"] = _sha(fake)
    file_result["post_sha256"] = _sha(fake)
    payload["receipt_digest"] = _canonical_digest(
        {
            key: value
            for key, value in payload.items()
            if key != "receipt_digest"
        }
    )
    _json(evidence, payload)

    with pytest.raises(clearance.ClearanceBlocked, match="registry pre/post"):
        _issue(
            hardbot_snapshot,
            evidence,
            registry,
            hardbot_snapshot / clearance.RECEIPT_NAME,
        )


def test_clearance_blocks_tampered_plan_even_if_claimed_digest_unchanged(
    hardbot_snapshot,
    tmp_path,
):
    registry = _registry_artifacts(tmp_path, hardbot_snapshot)
    evidence = _enrichment_receipt(
        hardbot_snapshot,
        registry["personal"],
    )
    plan = json.loads(registry["plan"].read_text(encoding="utf-8"))
    plan["projections"][0]["rows"] = 999
    _json(registry["plan"], plan)

    with pytest.raises(clearance.ClearanceBlocked, match="canonical digest"):
        _issue(
            hardbot_snapshot,
            evidence,
            registry,
            hardbot_snapshot / clearance.RECEIPT_NAME,
        )


def test_clearance_blocks_unfinished_publish_journal(
    hardbot_snapshot,
    tmp_path,
):
    registry = _registry_artifacts(
        tmp_path,
        hardbot_snapshot,
        journal=True,
    )
    evidence = _enrichment_receipt(
        hardbot_snapshot,
        registry["personal"],
    )

    with pytest.raises(clearance.ClearanceBlocked, match="publish journals"):
        _issue(
            hardbot_snapshot,
            evidence,
            registry,
            hardbot_snapshot / clearance.RECEIPT_NAME,
        )


def test_clearance_lease_blocks_concurrent_registry_cycle(
    hardbot_snapshot,
    tmp_path,
):
    registry = _registry_artifacts(tmp_path, hardbot_snapshot)
    evidence = _enrichment_receipt(
        hardbot_snapshot,
        registry["personal"],
    )
    receipt = _issue(
        hardbot_snapshot,
        evidence,
        registry,
        hardbot_snapshot / clearance.RECEIPT_NAME,
    )

    with clearance.clearance_lease(receipt):
        with pytest.raises(clearance.ClearanceBlocked, match="lease"):
            with clearance.clearance_lease(receipt):
                pass
