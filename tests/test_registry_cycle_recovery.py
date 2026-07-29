from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import registry_cycle.recovery as recovery
from registry_cycle.config import ConfigBundle, FieldConfig
from registry_cycle.errors import SafetyViolation
from registry_cycle.models import FileFingerprint, ManifestEntry, WorkbookSnapshot
from registry_cycle.recovery import (
    CapturedSource,
    RecoveryConfig,
    RecoveryField,
    audit_recovery_against_master,
    build_recovery_plan,
    publish_recovery,
)
from registry_cycle.snapshot import CapturedPersonal, SnapshotSet


HEADERS = (
    "Номер КД / ID",
    "ответственный Сотрудник",
    "Телефон глав",
)
TYPES = ("сх", "сх", "хард")


def _fingerprint(seed: str) -> FileFingerprint:
    return FileFingerprint(
        size=len(seed),
        mtime_ns=len(seed),
        sha256=(seed.encode("utf-8").hex() + "0" * 64)[:64],
    )


def _book(path: Path, rows, seed: str) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        path=path,
        sheet="Портфели",
        sheet_names=("Портфели",),
        fingerprint=_fingerprint(seed),
        header_row=0,
        type_row=1,
        data_start=2,
        headers=HEADERS,
        type_tags=TYPES,
        rows=tuple(tuple(row) for row in rows),
    )


def _bundle(tmp_path: Path) -> ConfigBundle:
    fields = FieldConfig(
        sheet_name="Портфели",
        statistics_sheet="Статистика",
        status_sheet="Статусы",
        key_title=HEADERS[0],
        responsible_title=HEADERS[1],
        header_search_rows=10,
        type_row_offset=1,
        profile_tags={"hard": ("сх", "хард")},
        collect=(),
        preserve_if_master_blank=(),
        aliases={},
    )
    return ConfigBundle(
        app=None,  # type: ignore[arg-type] - плану recovery AppConfig не нужен
        fields=fields,
        theme={},
        config_dir=tmp_path,
        project_root=tmp_path,
    )


def _config(
    tmp_path: Path,
    *,
    not_before: datetime | None = None,
    append_only: bool = True,
    max_writes: int = 300,
    max_share: float = 1.0,
) -> RecoveryConfig:
    return RecoveryConfig(
        archive_root=tmp_path,
        archive_points=(),
        vss={},
        extra_sources={},
        fields=(
            RecoveryField(
                title=HEADERS[2],
                profiles=("hard",),
                max_length=500,
                append_only=append_only,
                not_before=not_before,
            ),
        ),
        safety={
            "max_writes_per_file": max_writes,
            "max_write_share": max_share,
            "exclude_filename_regex": r"^Копия\s",
            "require_same_responsible": True,
            "block_history_conflicts": True,
        },
    )


def _run_plan(
    monkeypatch,
    tmp_path: Path,
    *,
    current_rows,
    histories,
    master_rows=None,
    config: RecoveryConfig | None = None,
):
    current_path = tmp_path / "current.xlsb"
    master_path = tmp_path / "master.xlsb"
    current = _book(current_path, current_rows, "current")
    master = _book(
        master_path,
        current_rows if master_rows is None else master_rows,
        "master",
    )
    entry = ManifestEntry(
        entry_id="employee-1",
        path=tmp_path / "Иванов Иван.xlsb",
        country="Тест",
        group="Город",
        profile="hard",
        employee_folder="Иванов Иван",
        responsible_hint="Иванов Иван",
    )
    snapshot = SnapshotSet(
        run_id="test-recovery",
        run_dir=tmp_path / ".registry-cycle" / "runs" / "run",
        master_original=master_path,
        master_copy=master_path,
        master_fingerprint=master.fingerprint,
        personals=(
            CapturedPersonal(
                entry=entry,
                copy_path=current_path,
                fingerprint=current.fingerprint,
            ),
        ),
    )
    books = {current_path: current, master_path: master}
    sources = []
    for index, (captured_at, rows) in enumerate(histories, start=1):
        path = tmp_path / f"history-{index}.xlsb"
        book = _book(path, rows, f"history-{index}")
        books[path] = book
        sources.append(
            CapturedSource(
                entry_id=entry.entry_id,
                label=f"H{index}",
                kind="test",
                original_path=path,
                copy_path=path,
                captured_at=captured_at,
                fingerprint=book.fingerprint,
            )
        )

    monkeypatch.setattr(recovery, "load_captured_sources", lambda _path: tuple(sources))
    monkeypatch.setattr(recovery, "read_portfolio", lambda path, _fields: books[Path(path)])
    plan = build_recovery_plan(
        _bundle(tmp_path),
        snapshot,
        config or _config(tmp_path),
        tmp_path / "source_manifest.json",
        snapshot_path=tmp_path / "snapshot.json",
    )
    return plan, snapshot


UTC = timezone.utc


def test_recovery_publish_is_disabled_for_limited_testing():
    with pytest.raises(SafetyViolation, match="ограниченного тестирования"):
        publish_recovery(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            confirmation_digest="x",
            notify_owner=False,
        )


def test_current_nonblank_value_always_wins(monkeypatch, tmp_path):
    plan, _snapshot = _run_plan(
        monkeypatch,
        tmp_path,
        current_rows=(("1", "Иванов Иван", "актуальный"),),
        histories=(
            (
                datetime(2026, 7, 20, tzinfo=UTC),
                (("1", "Иванов Иван", "старый"),),
            ),
        ),
    )

    assert not plan.writes
    assert not plan.blocked


def test_history_for_another_responsible_is_not_restored(monkeypatch, tmp_path):
    plan, _snapshot = _run_plan(
        monkeypatch,
        tmp_path,
        current_rows=(("1", "Иванов Иван", None),),
        histories=(
            (
                datetime(2026, 7, 20, tzinfo=UTC),
                (("1", "Петров Пётр", "чужое"),),
            ),
        ),
    )

    assert not plan.writes


def test_field_cutoff_excludes_previous_period(monkeypatch, tmp_path):
    plan, _snapshot = _run_plan(
        monkeypatch,
        tmp_path,
        current_rows=(("1", "Иванов Иван", None),),
        histories=(
            (
                datetime(2026, 7, 2, tzinfo=UTC),
                (("1", "Иванов Иван", "июньский хвост"),),
            ),
        ),
        config=_config(
            tmp_path,
            not_before=datetime(2026, 7, 6, tzinfo=UTC),
        ),
    )

    assert not plan.writes
    assert any(issue.code == "recovery_before_field_cutoff" for issue in plan.issues)


def test_conflicting_history_is_skipped(monkeypatch, tmp_path):
    plan, _snapshot = _run_plan(
        monkeypatch,
        tmp_path,
        current_rows=(("1", "Иванов Иван", None),),
        histories=(
            (
                datetime(2026, 7, 20, tzinfo=UTC),
                (("1", "Иванов Иван", "значение A"),),
            ),
            (
                datetime(2026, 7, 21, tzinfo=UTC),
                (("1", "Иванов Иван", "значение B"),),
            ),
        ),
        config=_config(tmp_path, append_only=False),
    )

    assert not plan.writes
    assert any(issue.code == "recovery_history_conflicts" for issue in plan.issues)


def test_mass_change_is_withheld_and_report_directory_is_created(
    monkeypatch,
    tmp_path,
):
    plan, snapshot = _run_plan(
        monkeypatch,
        tmp_path,
        current_rows=(
            ("1", "Иванов Иван", None),
            ("2", "Иванов Иван", None),
        ),
        histories=(
            (
                datetime(2026, 7, 20, tzinfo=UTC),
                (
                    ("1", "Иванов Иван", "111"),
                    ("2", "Иванов Иван", "222"),
                ),
            ),
        ),
        config=_config(tmp_path, max_share=0.25),
    )

    assert not plan.writes
    assert plan.blocked
    assert plan.blocked_entries == {"employee-1"}
    assert any(issue.code == "recovery_file_limit_exceeded" for issue in plan.issues)
    assert (snapshot.run_dir / "recovery" / "plan.json").is_file()


def test_second_plan_is_idempotent(monkeypatch, tmp_path):
    plan, _snapshot = _run_plan(
        monkeypatch,
        tmp_path,
        current_rows=(
            ("1", "Иванов Иван", "111"),
            ("2", "Иванов Иван", "222"),
        ),
        histories=(
            (
                datetime(2026, 7, 20, tzinfo=UTC),
                (
                    ("1", "Иванов Иван", "111"),
                    ("2", "Иванов Иван", "222"),
                ),
            ),
        ),
    )

    assert not plan.writes
    assert not plan.blocked


def test_master_audit_accepts_blank_and_equal_values(monkeypatch, tmp_path):
    plan, snapshot = _run_plan(
        monkeypatch,
        tmp_path,
        current_rows=(
            ("1", "Иванов Иван", None),
            ("2", "Иванов Иван", None),
        ),
        master_rows=(
            ("1", "Иванов Иван", None),
            ("2", "Иванов Иван", "222"),
        ),
        histories=(
            (
                datetime(2026, 7, 20, tzinfo=UTC),
                (
                    ("1", "Иванов Иван", "111"),
                    ("2", "Иванов Иван", "222"),
                ),
            ),
        ),
    )

    audit = audit_recovery_against_master(_bundle(tmp_path), snapshot, plan)

    assert not audit.blocked
    assert audit.counts == {"blank": 1, "equal": 1}
    assert audit.checked_cells == 2


def test_master_audit_blocks_different_nonblank_value(monkeypatch, tmp_path):
    plan, snapshot = _run_plan(
        monkeypatch,
        tmp_path,
        current_rows=(("1", "Иванов Иван", None),),
        master_rows=(("1", "Иванов Иван", "актуальное в мастере"),),
        histories=(
            (
                datetime(2026, 7, 20, tzinfo=UTC),
                (("1", "Иванов Иван", "архивное"),),
            ),
        ),
    )

    audit = audit_recovery_against_master(_bundle(tmp_path), snapshot, plan)

    assert audit.blocked
    assert audit.counts == {"different_nonblank": 1}
    assert audit.conflicts[0]["recovery_value"] == "архивное"
