from pathlib import Path
from types import SimpleNamespace

import pytest

import registry_cycle.rehearsal as rehearsal
from registry_cycle.errors import SafetyViolation
from registry_cycle.files import fingerprint
from registry_cycle.models import FileFingerprint, ManifestEntry
from registry_cycle.snapshot import CapturedPersonal, SnapshotSet
from registry_cycle.staging import StageSet, StagedFile


def _promotion_fakes(tmp_path: Path, monkeypatch):
    master = tmp_path / "source-master.xlsb"
    personal = tmp_path / "source-personal.xlsb"
    candidate = tmp_path / "candidate-baseline.json.gz"
    for path in (master, personal, candidate):
        path.write_bytes(path.name.encode())
    master_fingerprint = fingerprint(master)
    entry = ManifestEntry(
        entry_id="p1",
        path=tmp_path / "live-personal.xlsb",
        country="Тест",
        group="Город",
        profile="hard",
        employee_folder="Иванов Иван",
        responsible_hint="Иванов Иван",
    )
    source = SnapshotSet(
        run_id="source",
        run_dir=tmp_path / "source-run",
        master_original=tmp_path / "live-master.xlsb",
        master_copy=master,
        master_fingerprint=master_fingerprint,
        personals=(
            CapturedPersonal(
                entry=entry,
                copy_path=personal,
                fingerprint=fingerprint(personal),
            ),
        ),
    )
    staged_master = tmp_path / "staged-master.xlsb"
    staged_personal = tmp_path / "staged-personal.xlsb"
    staged_master.write_bytes(b"staged-master")
    staged_personal.write_bytes(b"staged-personal")
    digest = "d" * 64
    stage = StageSet(
        run_id="candidate",
        run_dir=tmp_path / "candidate-run",
        staging_dir=tmp_path / "candidate-run" / "staging",
        plan_digest=digest,
        master=StagedFile(
            kind="master",
            original_path=source.master_original,
            source_copy=master,
            staged_path=staged_master,
            source_fingerprint=master_fingerprint,
            staged_fingerprint=fingerprint(staged_master),
        ),
        personals=(
            StagedFile(
                kind="personal",
                entry_id=entry.entry_id,
                original_path=entry.path,
                source_copy=personal,
                staged_path=staged_personal,
                source_fingerprint=fingerprint(personal),
                staged_fingerprint=fingerprint(staged_personal),
            ),
        ),
    )
    monkeypatch.setattr(rehearsal, "load_snapshot", lambda _path: source)
    monkeypatch.setattr(rehearsal, "load_stage", lambda _path: stage)
    monkeypatch.setattr(
        rehearsal,
        "baseline_path",
        lambda *_args: tmp_path / "not-active.json.gz",
    )
    monkeypatch.setattr(
        rehearsal,
        "load_baseline",
        lambda *_args, **_kwargs: SimpleNamespace(
            plan_digest=(
                "candidate-first-baseline:"
                + master_fingerprint.sha256
            )
        ),
    )
    prepared = SimpleNamespace(
        plan=SimpleNamespace(blocked=False, digest=digest)
    )

    class FakeService:
        def __init__(self, _bundle):
            pass

        def prepare_snapshot(self, *_args, **_kwargs):
            return prepared

    monkeypatch.setattr(rehearsal, "CycleService", FakeService)
    monkeypatch.setattr(
        rehearsal,
        "validate_staging",
        lambda *_args, **_kwargs: SimpleNamespace(blocked=False),
    )
    result = SimpleNamespace(complete=True)
    calls = []

    def fake_publish(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(rehearsal, "publish_validated", fake_publish)
    return candidate, digest, result, calls


def test_first_baseline_promotion_revalidates_before_publish(
    tmp_path,
    monkeypatch,
):
    candidate, digest, expected, calls = _promotion_fakes(
        tmp_path,
        monkeypatch,
    )

    bundle = SimpleNamespace(
        workspace_path=lambda: tmp_path / "workspace",
        app=SimpleNamespace(safety={}),
    )
    result = rehearsal.promote_first_baseline_rehearsal(
        bundle,
        tmp_path / "snapshot.json",
        tmp_path / "stage.json",
        candidate,
        confirmation_digest=digest,
        busy_mode="block",
    )

    assert result is expected
    assert len(calls) == 1
    assert calls[0][1]["confirmation_digest"] == digest
    assert calls[0][1]["busy_mode"] == "block"


def test_first_baseline_promotion_rejects_wrong_digest(
    tmp_path,
    monkeypatch,
):
    candidate, _digest, _expected, calls = _promotion_fakes(
        tmp_path,
        monkeypatch,
    )

    bundle = SimpleNamespace(
        workspace_path=lambda: tmp_path / "workspace",
        app=SimpleNamespace(safety={}),
    )
    with pytest.raises(SafetyViolation, match="digest"):
        rehearsal.promote_first_baseline_rehearsal(
            bundle,
            tmp_path / "snapshot.json",
            tmp_path / "stage.json",
            candidate,
            confirmation_digest="x" * 64,
        )

    assert calls == []
