import json
import shutil
from pathlib import Path

from registry_cycle.files import fingerprint
from registry_cycle.publishing import PublishResult
from registry_cycle.receipts import write_publication_receipt
from registry_cycle.staging import StageSet, StagedFile


def test_publication_receipt_matches_live_and_central_backup(tmp_path: Path):
    run_dir = tmp_path / "run"
    staging_dir = run_dir / "staging"
    staging_dir.mkdir(parents=True)
    live = tmp_path / "live" / "employee.xlsb"
    source = run_dir / "source.xlsb"
    staged = staging_dir / "employee.xlsb"
    backup = tmp_path / "archive" / "employee-before.xlsb"
    receipt_dir = tmp_path / "archive" / "_квитанции"
    live.parent.mkdir()
    backup.parent.mkdir()
    staged.write_bytes(b"new")
    shutil.copy2(staged, live)
    source.write_bytes(b"old")
    shutil.copy2(source, backup)
    item = StagedFile(
        kind="personal",
        entry_id="entry-1",
        original_path=live,
        source_copy=source,
        staged_path=staged,
        source_fingerprint=fingerprint(source),
        staged_fingerprint=fingerprint(staged),
    )
    stage = StageSet(
        run_id="receipt-test",
        run_dir=run_dir,
        staging_dir=staging_dir,
        plan_digest="d" * 64,
        master=item,
        personals=(),
    )
    result = PublishResult(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        started_at="2026-08-13T12:00:00",
        finished_at="2026-08-13T12:00:01.123456",
        published=[str(live)],
        backups=[str(backup)],
    )

    receipt_path = write_publication_receipt(
        stage=stage,
        result=result,
        files=(item,),
        receipt_directory=receipt_dir,
        operation="раздача-вниз",
        context_by_entry={"entry-1": {"employee": "Иванов", "cells_written": 3}},
        summary={"published_files": 1},
    )

    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    local = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
    assert payload == local
    assert payload["summary"]["published_files"] == 1
    assert payload["files"][0]["employee"] == "Иванов"
    assert payload["files"][0]["pre_sha256"] == fingerprint(source).sha256
    assert payload["files"][0]["backup_sha256"] == fingerprint(backup).sha256
    assert payload["files"][0]["post_sha256"] == fingerprint(live).sha256
