from dataclasses import replace
from pathlib import Path

import pytest

from registry_cycle.config import load_config_bundle
from registry_cycle.errors import SafetyViolation
from registry_cycle.files import fingerprint, stable_copy
from registry_cycle.models import DiscoveryReport, ManifestEntry
from registry_cycle.snapshot import (
    assert_snapshot_config_identity,
    capture_sources,
    load_snapshot,
)


def test_stable_copy_preserves_content_and_returns_source_fingerprint(tmp_path):
    source = tmp_path / "source.bin"
    target = tmp_path / "nested" / "target.bin"
    source.write_bytes(b"registry-cycle")

    captured = stable_copy(source, target)

    assert target.read_bytes() == source.read_bytes()
    assert captured == fingerprint(source)


def test_capture_sources_uses_opaque_local_names(tmp_path):
    master = tmp_path / "master.xlsb"
    personal = tmp_path / "Иванов Иван.xlsb"
    master.write_bytes(b"master")
    personal.write_bytes(b"personal")

    bundle = load_config_bundle()
    app = replace(
        bundle.app,
        master_pattern=str(master),
        workspace=str(tmp_path / "work"),
        portfolio_roots=(),
    )
    bundle = replace(bundle, app=app)
    entry = ManifestEntry(
        entry_id="abc123",
        path=personal,
        country="Тест",
        group="Город",
        profile="hard",
        employee_folder="Иванов Иван",
        responsible_hint="Иванов Иван",
    )

    snapshot = capture_sources(
        bundle,
        DiscoveryReport(entries=[entry]),
        run_id="fixed-run",
    )

    assert snapshot.master_copy.name == "master.xlsb"
    assert snapshot.personals[0].copy_path.name == "abc123.xlsb"
    assert snapshot.personals[0].copy_path.read_bytes() == b"personal"
    assert (snapshot.run_dir / "snapshot.json").is_file()
    assert snapshot.config_identity == bundle.config_identity
    assert (
        load_snapshot(snapshot.run_dir / "snapshot.json").config_identity
        == bundle.config_identity
    )

    drifted_bundle = replace(bundle, config_identity="f" * 64)
    with pytest.raises(SafetyViolation, match="config drift"):
        assert_snapshot_config_identity(drifted_bundle, snapshot)

    with pytest.raises(SafetyViolation, match="вне workspace/runs"):
        capture_sources(
            bundle,
            DiscoveryReport(entries=[entry]),
            run_id="../escape",
        )
