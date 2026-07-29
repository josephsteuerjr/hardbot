import gzip
import hashlib
import io
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PULT_DIR = ROOT / "pult"
if str(PULT_DIR) not in sys.path:
    sys.path.insert(0, str(PULT_DIR))

import limited_test_import as operator


def _write_master(path: Path) -> None:
    from openpyxl import Workbook

    values = {
        "contract_id": "MASTER-1",
        "responsible": "Агент Master",
        "client": "Проверочный Клиент",
    }
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = operator.contract.WORKBOOK_SHEET_NAME
    sheet.append([field.title for field in operator._db.FIELDS])
    sheet.append([field.source for field in operator._db.FIELDS])
    sheet.append([values.get(field.key) for field in operator._db.FIELDS])
    workbook.save(path)


def _write_completed_publication(master: Path, workspace: Path) -> None:
    master_sha = hashlib.sha256(master.read_bytes()).hexdigest()
    run_dir = workspace / "runs" / "published-master-run"
    staging = run_dir / "staging"
    staging.mkdir(parents=True)
    plan_digest = hashlib.sha256(b"published-master-plan").hexdigest()
    baseline = workspace / "baselines" / "active.json.gz"
    baseline.parent.mkdir(parents=True)
    with gzip.open(baseline, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "plan_digest": plan_digest,
                "master_path": str(master),
            },
            stream,
        )
    (staging / "stage.json").write_text(
        json.dumps(
            {
                "run_id": "published-master-run",
                "plan_digest": plan_digest,
                "master": {
                    "original_path": str(master),
                    "staged_fingerprint": {"sha256": master_sha},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "publish.json").write_text(
        json.dumps(
            {
                "run_id": "published-master-run",
                "plan_digest": plan_digest,
                "complete": True,
                "ok": True,
                "sidecars": [],
                "finished_at": "2026-07-29T15:30:00+04:00",
                "baseline": str(baseline),
                "published": [str(master)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "publish.journal.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "event": event,
                    "run_id": "published-master-run",
                    "plan_digest": plan_digest,
                }
            )
            for event in ("publish_started", "publish_committed")
        )
        + "\n",
        encoding="utf-8",
    )


def _json_output(capsys) -> dict:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False

    def getcode(self):
        return self.status


def test_published_master_prepare_register_apply_is_executable(
    monkeypatch,
    tmp_path,
    capsys,
):
    master = tmp_path / "ОБЩИЙ РЕЕСТР.xlsx"
    workspace = tmp_path / "registry-cycle-workspace"
    output_dir = tmp_path / "published-import"
    _write_master(master)
    _write_completed_publication(master, workspace)

    assert operator.main(
        [
            "prepare-published-master",
            "--workbook",
            str(master),
            "--workspace",
            str(workspace),
            "--output-dir",
            str(output_dir),
        ]
    ) == 0
    prepared = _json_output(capsys)
    assert prepared["command"] == "prepare-published-master"
    assert prepared["source_kind"] == (
        operator.contract.IMPORT_SOURCE_PUBLISHED_MASTER
    )
    assert prepared["row_count"] == 1
    proof = json.loads(
        (output_dir / "proof.json").read_text(encoding="utf-8")
    )
    request_payload = json.loads(
        (output_dir / "request.json").read_text(encoding="utf-8")
    )
    assert request_payload["source_proof"] == proof
    assert len(request_payload["sheets"]) == 1

    server_db = tmp_path / "server" / "hardbot.db"
    server_db.parent.mkdir()
    monkeypatch.setattr(operator._db, "DB_PATH", server_db)
    operator._db.init_db()
    registered = operator._db.register_import_authorization(
        proof,
        source_kind=operator.contract.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    assert registered["status"] == "pending"
    assert registered["proof_digest"] == prepared["proof_digest"]

    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "url": "https://hardbot.example",
                "key": "private-admin-key",
            }
        ),
        encoding="utf-8",
    )
    os.chmod(settings, 0o600)

    def registered_server(request, timeout):
        assert timeout == 120
        body = json.loads(request.data.decode("utf-8"))
        grid = operator._db.pick_grid(body["sheets"])
        report = operator._db.import_grid(
            grid,
            source_kind=body["source_kind"],
            source_proof=body["source_proof"],
            require_source_proof=True,
        )
        assert report["blocked"] is False
        return _Response(
            json.dumps(report, ensure_ascii=False).encode("utf-8")
        )

    monkeypatch.setattr(
        operator,
        "_urlopen_no_redirect",
        registered_server,
    )
    assert operator.main(
        [
            "apply",
            "--request",
            str(output_dir / "request.json"),
            "--settings",
            str(settings),
            "--confirm-proof-digest",
            prepared["proof_digest"],
        ]
    ) == 0
    applied = _json_output(capsys)
    assert applied["proof_digest"] == prepared["proof_digest"]
    assert applied["inserted"] == 1
    assert applied["updated"] == 0
    assert operator._db.get_client("MASTER-1")["responsible"] == "Агент Master"
    assert operator._db.import_authorization_status(
        prepared["proof_digest"]
    )["status"] == "consumed"
