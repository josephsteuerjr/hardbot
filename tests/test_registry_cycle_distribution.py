from __future__ import annotations

from pathlib import Path

import registry_cycle.distribution as distribution
from registry_cycle.config import ConfigBundle, FieldConfig
from registry_cycle.distribution import (
    DistributionConfig,
    build_distribution_plan,
)
from registry_cycle.models import (
    FileFingerprint,
    ManifestEntry,
    WorkbookSnapshot,
)
from registry_cycle.snapshot import CapturedPersonal, SnapshotSet


HEADERS = (
    "Номер КД / ID",
    "ответственный Сотрудник",
    "Телефон глав",
)
TYPES = ("сх", "сх", "хард")
EMPLOYEE = "Иванов Иван"


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
        app=None,  # type: ignore[arg-type] - плану distribution не нужен AppConfig
        fields=fields,
        theme={},
        config_dir=tmp_path,
        project_root=tmp_path,
    )


def _config(
    *,
    aliases: dict[str, str] | None = None,
    max_total: int = 100,
    max_per_file: int = 100,
) -> DistributionConfig:
    return DistributionConfig(
        responsible_aliases=aliases or {},
        safety={
            "exclude_filename_regex": r"^Копия\s",
            "require_baseline_owner_blank": True,
            "max_total_rows": max_total,
            "max_rows_per_file": max_per_file,
        },
    )


def _run_plan(
    monkeypatch,
    tmp_path: Path,
    *,
    master_rows,
    baseline_rows,
    personal_rows,
    employee: str = EMPLOYEE,
    config: DistributionConfig | None = None,
):
    master_path = tmp_path / "master.xlsb"
    baseline_path = tmp_path / "baseline.xlsb"
    personal_path = tmp_path / "personal.xlsb"
    master = _book(master_path, master_rows, "master")
    baseline = _book(baseline_path, baseline_rows, "baseline")
    personal = _book(personal_path, personal_rows, "personal")
    entry = ManifestEntry(
        entry_id="employee-1",
        path=tmp_path / f"{employee}.xlsb",
        country="Тест",
        group="Город",
        profile="hard",
        employee_folder=employee,
        responsible_hint=employee,
    )
    snapshot = SnapshotSet(
        run_id="test-distribution",
        run_dir=tmp_path / "run",
        master_original=master_path,
        master_copy=master_path,
        master_fingerprint=master.fingerprint,
        personals=(
            CapturedPersonal(
                entry=entry,
                copy_path=personal_path,
                fingerprint=personal.fingerprint,
            ),
        ),
    )
    books = {
        master_path: master,
        baseline_path: baseline,
        personal_path: personal,
    }
    monkeypatch.setattr(
        distribution,
        "read_portfolio",
        lambda path, _fields: books[Path(path)],
    )
    return build_distribution_plan(
        _bundle(tmp_path),
        snapshot,
        config or _config(),
        baseline_path,
        snapshot_path=tmp_path / "snapshot.json",
    )


def test_existing_nonblank_row_is_never_rewritten(monkeypatch, tmp_path):
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(
            ("1", EMPLOYEE, "новое значение из общего"),
            ("2", EMPLOYEE, "новая строка"),
        ),
        baseline_rows=(
            ("1", None, "старое значение"),
            ("2", None, "новая строка"),
        ),
        personal_rows=(("1", EMPLOYEE, "живое непустое значение"),),
    )

    assert not plan.blocked
    assert [row.contract_id for row in plan.rows] == ["2"]
    assert plan.rows[0].values[2] == "новая строка"


def test_personal_row_missing_from_master_is_retained(monkeypatch, tmp_path):
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(),
        baseline_rows=(),
        personal_rows=(("orphan", EMPLOYEE, "не удалять"),),
    )

    assert not plan.blocked
    assert not plan.rows


def test_nonblank_baseline_owner_blocks_unproven_assignment(
    monkeypatch,
    tmp_path,
):
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(("new", EMPLOYEE, "данные"),),
        baseline_rows=(("new", "Петров Пётр", "данные"),),
        personal_rows=(("anchor", EMPLOYEE, "существующее"),),
    )

    assert plan.blocked
    assert not plan.rows
    assert any(
        issue.code == "distribution_assignment_not_new"
        for issue in plan.issues
    )


def test_responsible_alias_routes_to_canonical_employee(
    monkeypatch,
    tmp_path,
):
    canonical = "Тургунов Музаффар"
    typo = "Тургунов Музафар"
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(("new", typo, "данные"),),
        baseline_rows=(("new", None, "данные"),),
        personal_rows=(("anchor", canonical, "существующее"),),
        employee=canonical,
        config=_config(aliases={typo: canonical}),
    )

    assert not plan.blocked
    assert len(plan.rows) == 1
    assert plan.rows[0].responsible == canonical
    assert plan.rows[0].master_responsible == typo
    assert plan.rows[0].values[1] == canonical


def test_duplicate_master_contract_rows_are_both_preserved(
    monkeypatch,
    tmp_path,
):
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(
            ("dup", EMPLOYEE, "клиент A"),
            ("dup", EMPLOYEE, "клиент B"),
            ("anchor", EMPLOYEE, "существующее"),
        ),
        baseline_rows=(
            ("dup", None, "клиент A"),
            ("dup", None, "клиент B"),
            ("anchor", None, "существующее"),
        ),
        personal_rows=(("anchor", EMPLOYEE, "существующее"),),
    )

    assert not plan.blocked
    assert [row.contract_id for row in plan.rows] == ["dup", "dup"]
    assert [row.values[2] for row in plan.rows] == ["клиент A", "клиент B"]


def test_distribution_volume_limit_blocks_plan(monkeypatch, tmp_path):
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(
            ("1", EMPLOYEE, "A"),
            ("2", EMPLOYEE, "B"),
        ),
        baseline_rows=(
            ("1", None, "A"),
            ("2", None, "B"),
        ),
        personal_rows=(("anchor", EMPLOYEE, "существующее"),),
        config=_config(max_total=1),
    )

    assert plan.blocked
    assert len(plan.rows) == 2
    assert any(
        issue.code == "distribution_total_limit_exceeded"
        for issue in plan.issues
    )
