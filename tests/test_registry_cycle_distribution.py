from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

import registry_cycle.distribution as distribution
from registry_cycle.config import ConfigBundle, FieldConfig
from registry_cycle.distribution import (
    DistributionConfig,
    DistributionPlan,
    DistributionPreflight,
    DistributionStage,
    DistributionStagedFile,
    DistributionValidation,
    build_distribution_plan,
    preflight_distribution,
    publish_distribution,
)
from registry_cycle.errors import SafetyViolation
from registry_cycle.models import (
    FileFingerprint,
    ManifestEntry,
    WorkbookSnapshot,
)
from registry_cycle.snapshot import CapturedPersonal, SnapshotSet
from registry_cycle.publishing import (
    incomplete_publish_journals,
    publication_cycle_lock,
    reconcile_incomplete_publication,
)


HEADERS = (
    "Номер КД / ID",
    "ответственный Сотрудник",
    "Телефон глав",
)
TYPES = ("сх", "сх", "хард")
EMPLOYEE = "Иванов Иван"


def test_new_soft_assignment_uses_soft_baseline_not_hard_owner(monkeypatch, tmp_path):
    headers = ("Номер КД / ID", "ответственный Сотрудник", "ответственный Сотрудник СОФТ")
    tags = ("сх", "сх", "софт")
    master_path, baseline_path = tmp_path/"master.xlsb", tmp_path/"baseline.xlsb"
    master = replace(_book(master_path,[("1","H1","S1")],"master"),headers=headers,type_tags=tags)
    baseline = replace(_book(baseline_path,[("1","H1",None)],"baseline"),headers=headers,type_tags=tags)
    hard = replace(_book(tmp_path/"hard.xlsb",[("1","H1")],"hard"),headers=headers[:2],type_tags=tags[:2])
    soft = replace(_book(tmp_path/"soft.xlsb",[],"soft"),headers=headers,type_tags=tags)
    books={master_path:master,baseline_path:baseline,hard.path:hard,soft.path:soft}
    monkeypatch.setattr(distribution,"read_portfolio",lambda path,fields:books[Path(path)])
    entries=[ManifestEntry(entry_id=profile,path=book.path,country="Тест",group=profile,
             profile=profile,employee_folder=owner,responsible_hint=owner)
             for profile,book,owner in (("hard",hard,"H1"),("soft",soft,"S1"))]
    captured=tuple(CapturedPersonal(entry=entry,copy_path=book.path,fingerprint=book.fingerprint)
                   for entry,book in zip(entries,(hard,soft)))
    snapshot=SnapshotSet(run_id="profiles",run_dir=tmp_path/".registry-cycle"/"runs"/"profiles",
                         master_original=master_path,master_copy=master_path,
                         master_fingerprint=master.fingerprint,personals=captured)
    bundle=_bundle(tmp_path)
    bundle=replace(bundle,fields=replace(bundle.fields,profile_tags={"hard":("сх","хард"),"soft":("сх","софт")}))
    plan=build_distribution_plan(bundle,snapshot,_config(),baseline_path)
    assert not plan.blocked, [(i.code,i.context) for i in plan.issues]
    assert len(plan.rows)==1
    assert plan.rows[0].responsible == "S1"
    assert plan.rows[0].values[2] == "S1"
    books[baseline_path]=replace(baseline,headers=headers[:2],type_tags=tags[:2],rows=(("1",None),))
    blocked=build_distribution_plan(bundle,snapshot,_config(),baseline_path)
    assert blocked.blocked
    assert any(i.code=="distribution_profile_owner_missing" for i in blocked.issues)


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


def test_distribution_publish_stops_on_incomplete_registry_journal(tmp_path):
    workspace = tmp_path / ".registry-cycle"
    run = workspace / "runs" / "interrupted"
    run.mkdir(parents=True)
    journal = run / "publish.journal.jsonl"
    journal.write_text(
        json.dumps({
            "event": "publish_started",
            "run_id": "interrupted",
            "plan_digest": "d" * 64,
        }) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SafetyViolation, match="reconcile-publish"):
        publish_distribution(
            _bundle(tmp_path),
            None,  # type: ignore[arg-type] - guard обязан сработать раньше
            None,  # type: ignore[arg-type]
            confirmation_digest="x",
            notify_owner=False,
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
    entry_employee: str | None = None,
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
        path=tmp_path / f"{entry_employee or employee}.xlsb",
        country="Тест",
        group="Город",
        profile="hard",
        employee_folder=entry_employee or employee,
        responsible_hint=entry_employee or employee,
    )
    snapshot = SnapshotSet(
        run_id="test-distribution",
        run_dir=tmp_path / ".registry-cycle" / "runs" / "run",
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


def test_first_assignment_can_target_new_empty_portfolio(
    monkeypatch,
    tmp_path,
):
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(("new", EMPLOYEE, "данные"),),
        baseline_rows=(("new", None, "данные"),),
        personal_rows=(),
    )

    assert not plan.blocked
    assert len(plan.rows) == 1
    assert plan.rows[0].contract_id == "new"
    assert plan.rows[0].responsible == EMPLOYEE


def test_new_assignment_without_personal_target_blocks_plan(
    monkeypatch,
    tmp_path,
):
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(("new", "Петров Пётр", "данные"),),
        baseline_rows=(("new", None, "данные"),),
        personal_rows=(),
    )

    assert plan.blocked
    assert not plan.rows
    issue = next(
        issue
        for issue in plan.issues
        if issue.code == "distribution_new_assignment_without_target"
    )
    assert issue.context["count"] == 1
    assert issue.context["samples"][0]["contract_id"] == "new"


def test_unmatched_owner_without_unambiguous_baseline_blocks_plan(
    monkeypatch,
    tmp_path,
):
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(("brand-new", "Петров Пётр", "данные"),),
        baseline_rows=(),
        personal_rows=(),
    )

    assert plan.blocked
    issue = next(
        issue
        for issue in plan.issues
        if issue.code == "distribution_new_assignment_without_target"
    )
    assert issue.context["samples"] == [
        {
            "contract_id": "brand-new",
            "master_row": 3,
            "responsible": "Петров Пётр",
            "reason": "baseline_missing_or_ambiguous",
        }
    ]


def test_same_responsible_in_two_active_profiles_blocks_distribution(
    monkeypatch,
    tmp_path,
):
    master_path = tmp_path / "master.xlsb"
    baseline_path = tmp_path / "baseline.xlsb"
    hard_path = tmp_path / "hard.xlsb"
    soft_path = tmp_path / "soft.xlsb"
    master = _book(master_path, (("new", EMPLOYEE, "данные"),), "master")
    baseline = _book(
        baseline_path,
        (("new", None, "данные"),),
        "baseline",
    )
    hard = _book(hard_path, (), "hard")
    soft = _book(soft_path, (), "soft")
    entries = (
        ManifestEntry(
            entry_id="hard-1",
            path=tmp_path / "hard" / f"{EMPLOYEE}.xlsb",
            country="Тест",
            group="Hard",
            profile="hard",
            employee_folder=EMPLOYEE,
            responsible_hint=EMPLOYEE,
        ),
        ManifestEntry(
            entry_id="soft-1",
            path=tmp_path / "soft" / f"{EMPLOYEE}.xlsb",
            country="Тест",
            group="Soft",
            profile="soft",
            employee_folder=EMPLOYEE,
            responsible_hint=EMPLOYEE,
        ),
    )
    bundle = _bundle(tmp_path)
    bundle = ConfigBundle(
        app=bundle.app,
        fields=FieldConfig(
            sheet_name=bundle.fields.sheet_name,
            statistics_sheet=bundle.fields.statistics_sheet,
            status_sheet=bundle.fields.status_sheet,
            key_title=bundle.fields.key_title,
            responsible_title=bundle.fields.responsible_title,
            header_search_rows=bundle.fields.header_search_rows,
            type_row_offset=bundle.fields.type_row_offset,
            profile_tags={
                "hard": ("сх", "хард"),
                "soft": ("сх", "хард"),
            },
            collect=bundle.fields.collect,
            preserve_if_master_blank=bundle.fields.preserve_if_master_blank,
            aliases=bundle.fields.aliases,
        ),
        theme=bundle.theme,
        config_dir=bundle.config_dir,
        project_root=bundle.project_root,
    )
    snapshot = SnapshotSet(
        run_id="cross-profile",
        run_dir=tmp_path / ".registry-cycle" / "runs" / "run",
        master_original=master_path,
        master_copy=master_path,
        master_fingerprint=master.fingerprint,
        personals=(
            CapturedPersonal(entries[0], hard_path, hard.fingerprint),
            CapturedPersonal(entries[1], soft_path, soft.fingerprint),
        ),
    )
    books = {
        master_path: master,
        baseline_path: baseline,
        hard_path: hard,
        soft_path: soft,
    }
    monkeypatch.setattr(
        distribution,
        "read_portfolio",
        lambda path, _fields: books[Path(path)],
    )

    plan = build_distribution_plan(
        bundle,
        snapshot,
        _config(),
        baseline_path,
    )

    assert plan.blocked
    assert any(
        issue.code == "responsible_in_multiple_active_profiles"
        for issue in plan.issues
    )


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
    intermediate = "Тургунов Музаффар (канон)"
    typo = "Тургунов Музафар"
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(("new", typo, "данные"),),
        baseline_rows=(("new", None, "данные"),),
        personal_rows=(("anchor", canonical, "существующее"),),
        employee=canonical,
        config=_config(
            aliases={
                typo: intermediate,
                intermediate: canonical,
            }
        ),
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


def test_folder_name_does_not_decide_the_owner_of_the_book(
    monkeypatch,
    tmp_path,
):
    """Книга в чужой папке больше не блокирует раздачу — и не уводит строки.

    Прежде совпадение имени папки с ответственным было условием раздачи,
    и переложенная книга останавливала работу. Теперь имя папки не участвует
    вовсе: владелец берётся из колонки «ответственный Сотрудник» внутри книги,
    поэтому строки уезжают тому, кто в ней записан, а не тому, чьим именем
    названа папка.
    """
    plan = _run_plan(
        monkeypatch,
        tmp_path,
        master_rows=(("1", EMPLOYEE, "A"),),
        baseline_rows=(("1", None, "A"),),
        personal_rows=(("anchor", EMPLOYEE, "существующее"),),
        entry_employee="Петров Пётр",
    )

    assert not plan.blocked
    assert not any(
        issue.code == "distribution_manifest_owner_mismatch"
        for issue in plan.issues
    )


def test_preflight_blocks_when_live_master_changed(monkeypatch, tmp_path):
    master_original = tmp_path / "live-master.xlsb"
    master_copy = tmp_path / "master-copy.xlsb"
    baseline_path = tmp_path / "assignment-baseline.xlsb"
    expected_master = _fingerprint("expected-master")
    expected_baseline = _fingerprint("baseline")
    changed_master = _fingerprint("changed-master")
    snapshot = SnapshotSet(
        run_id="preflight-run",
        run_dir=tmp_path / "run",
        master_original=master_original,
        master_copy=master_copy,
        master_fingerprint=expected_master,
        personals=(),
    )
    plan = DistributionPlan(
        run_id=snapshot.run_id,
        snapshot_path=tmp_path / "snapshot.json",
        assignment_baseline_path=baseline_path,
        assignment_baseline_fingerprint=expected_baseline,
        master_fingerprint=expected_master,
        rows=[],
        issues=[],
    )
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    stage = DistributionStage(
        run_id=snapshot.run_id,
        run_dir=snapshot.run_dir,
        staging_dir=staging_dir,
        plan_digest=plan.digest,
        files=(),
    )
    validation = DistributionValidation(
        run_id=snapshot.run_id,
        plan_digest=plan.digest,
        issues=[],
    )
    monkeypatch.setattr(
        distribution,
        "validate_distribution_staging",
        lambda _bundle, _stage, _plan: validation,
    )
    monkeypatch.setattr(
        distribution,
        "load_snapshot",
        lambda _path: snapshot,
    )
    monkeypatch.setattr(
        distribution,
        "_exclusive_file_check",
        lambda _path: None,
    )

    def fake_fingerprint(path):
        target = Path(path)
        if target == baseline_path:
            return expected_baseline
        if target == master_original:
            return changed_master
        raise AssertionError(f"unexpected fingerprint path: {target}")

    monkeypatch.setattr(distribution, "fingerprint", fake_fingerprint)

    report = preflight_distribution(
        _bundle(tmp_path),
        stage,
        plan,
    )

    assert report.blocked
    assert not report.checked_master
    assert any(
        issue.code == "distribution_live_master_changed"
        for issue in report.issues or []
    )


def test_publish_uses_sidecar_for_locked_target_and_replaces_free_target(
    monkeypatch,
    tmp_path,
):
    master = tmp_path / "master.xlsb"
    baseline = tmp_path / "baseline.xlsb"
    master.write_bytes(b"master")
    baseline.write_bytes(b"baseline")
    locked = tmp_path / "locked.xlsb"
    free = tmp_path / "free.xlsb"
    locked_stage = tmp_path / "locked-stage.xlsb"
    free_stage = tmp_path / "free-stage.xlsb"
    locked.write_bytes(b"locked-old")
    free.write_bytes(b"free-old")
    locked_stage.write_bytes(b"locked-new")
    free_stage.write_bytes(b"free-new")
    lock_file = locked.with_name("~$" + locked.name)
    lock_file.write_bytes(b"excel-lock")

    locked_entry = ManifestEntry(
        entry_id="locked",
        path=locked,
        country="Тест",
        group="Город",
        profile="hard",
        employee_folder="Занят",
        responsible_hint="Занят",
    )
    free_entry = ManifestEntry(
        entry_id="free",
        path=free,
        country="Тест",
        group="Город",
        profile="hard",
        employee_folder="Свободен",
        responsible_hint="Свободен",
    )
    run_dir = (
        tmp_path
        / ".registry-cycle"
        / "runs"
        / "mixed-publish"
    )
    snapshot = SnapshotSet(
        run_id="mixed-publish",
        run_dir=run_dir,
        master_original=master,
        master_copy=tmp_path / "master-copy.xlsb",
        master_fingerprint=distribution.fingerprint(master),
        personals=(
            CapturedPersonal(
                entry=locked_entry,
                copy_path=tmp_path / "locked-copy.xlsb",
                fingerprint=distribution.fingerprint(locked),
            ),
            CapturedPersonal(
                entry=free_entry,
                copy_path=tmp_path / "free-copy.xlsb",
                fingerprint=distribution.fingerprint(free),
            ),
        ),
    )
    plan = DistributionPlan(
        run_id=snapshot.run_id,
        snapshot_path=tmp_path / "snapshot.json",
        assignment_baseline_path=baseline,
        assignment_baseline_fingerprint=distribution.fingerprint(baseline),
        master_fingerprint=snapshot.master_fingerprint,
        rows=[],
        issues=[],
    )
    staging_dir = run_dir / "distribution" / "staging"
    staging_dir.mkdir(parents=True)
    stage = DistributionStage(
        run_id=snapshot.run_id,
        run_dir=run_dir,
        staging_dir=staging_dir,
        plan_digest=plan.digest,
        files=(
            DistributionStagedFile(
                entry_id=locked_entry.entry_id,
                original_path=locked,
                source_copy=tmp_path / "locked-copy.xlsb",
                staged_path=locked_stage,
                source_fingerprint=distribution.fingerprint(locked),
                staged_fingerprint=distribution.fingerprint(locked_stage),
                appended_rows=2,
            ),
            DistributionStagedFile(
                entry_id=free_entry.entry_id,
                original_path=free,
                source_copy=tmp_path / "free-copy.xlsb",
                staged_path=free_stage,
                source_fingerprint=distribution.fingerprint(free),
                staged_fingerprint=distribution.fingerprint(free_stage),
                appended_rows=3,
            ),
        ),
    )
    validation = DistributionValidation(
        run_id=snapshot.run_id,
        plan_digest=plan.digest,
        checked_files=2,
        issues=[],
    )
    monkeypatch.setattr(
        distribution,
        "validate_distribution_staging",
        lambda _bundle, _stage, _plan: validation,
    )
    monkeypatch.setattr(distribution, "load_snapshot", lambda _path: snapshot)

    result = publish_distribution(
        _bundle(tmp_path),
        stage,
        plan,
        confirmation_digest=plan.digest,
        notify_owner=False,
        sidecar_locked=True,
    )

    assert result.ok
    assert result.published == [str(free)]
    assert len(result.sidecars or []) == 1
    sidecar = Path((result.sidecars or [])[0])
    assert sidecar.name.startswith("Копия locked (после распределения ")
    assert locked.read_bytes() == b"locked-old"
    assert sidecar.read_bytes() == b"locked-new"
    assert free.read_bytes() == b"free-new"
    assert len(result.backups) == 1
    assert Path(result.backups[0]).read_bytes() == b"free-old"
    assert incomplete_publish_journals(
        tmp_path / ".registry-cycle"
    ) == ()


def _distribution_publish_case(
    monkeypatch,
    tmp_path: Path,
    *,
    files: int = 2,
    sidecar_indexes: tuple[int, ...] = (),
):
    workspace = tmp_path / ".registry-cycle"
    run_dir = workspace / "runs" / "distribution-case"
    staging_dir = run_dir / "distribution" / "staging-case"
    staging_dir.mkdir(parents=True)
    master = tmp_path / "master.xlsb"
    baseline = tmp_path / "baseline.xlsb"
    master.write_bytes(b"master")
    baseline.write_bytes(b"baseline")
    originals = []
    staged_paths = []
    staged_files = []
    for index in range(files):
        original = tmp_path / f"personal-{index}.xlsb"
        staged_path = staging_dir / f"personal-{index}.xlsb"
        source_copy = run_dir / f"source-{index}.xlsb"
        original.write_bytes(f"old-{index}".encode())
        source_copy.write_bytes(f"old-{index}".encode())
        staged_path.write_bytes(f"new-{index}".encode())
        originals.append(original)
        staged_paths.append(staged_path)
        staged_files.append(
            DistributionStagedFile(
                entry_id=f"entry-{index}",
                original_path=original,
                source_copy=source_copy,
                staged_path=staged_path,
                source_fingerprint=distribution.fingerprint(original),
                staged_fingerprint=distribution.fingerprint(staged_path),
                appended_rows=1,
            )
        )
    snapshot = SnapshotSet(
        run_id="distribution-case",
        run_dir=run_dir,
        master_original=master,
        master_copy=run_dir / "master-copy.xlsb",
        master_fingerprint=distribution.fingerprint(master),
        personals=(),
    )
    plan = DistributionPlan(
        run_id=snapshot.run_id,
        snapshot_path=run_dir / "snapshot.json",
        assignment_baseline_path=baseline,
        assignment_baseline_fingerprint=distribution.fingerprint(baseline),
        master_fingerprint=snapshot.master_fingerprint,
        rows=[],
        issues=[],
    )
    stage = DistributionStage(
        run_id=snapshot.run_id,
        run_dir=run_dir,
        staging_dir=staging_dir,
        plan_digest=plan.digest,
        files=tuple(staged_files),
    )
    validation = DistributionValidation(
        run_id=stage.run_id,
        plan_digest=plan.digest,
        checked_files=files,
        issues=[],
    )
    preflight = DistributionPreflight(
        run_id=stage.run_id,
        plan_digest=plan.digest,
        validation=validation,
        checked_master=True,
        checked_files=files,
        sidecar_files=[
            str(originals[index])
            for index in sidecar_indexes
        ],
        issues=[],
    )
    monkeypatch.setattr(
        distribution,
        "preflight_distribution",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        distribution,
        "load_snapshot",
        lambda _path: snapshot,
    )
    monkeypatch.setattr(
        distribution,
        "_exclusive_file_check",
        lambda _path: None,
    )
    return {
        "bundle": _bundle(tmp_path),
        "workspace": workspace,
        "stage": stage,
        "plan": plan,
        "originals": originals,
        "staged_paths": staged_paths,
        "journal": staging_dir / "publish.journal.jsonl",
    }


def _publish_case(case):
    return publish_distribution(
        case["bundle"],
        case["stage"],
        case["plan"],
        confirmation_digest=case["plan"].digest,
        notify_owner=False,
    )


def test_distribution_terminal_journal_closes_pending_guard(
    monkeypatch,
    tmp_path,
):
    case = _distribution_publish_case(monkeypatch, tmp_path, files=1)

    result = _publish_case(case)

    assert result.ok
    records = [
        json.loads(line)
        for line in case["journal"].read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["event"] == "distribution_publish_started"
    assert records[-1]["event"] == "distribution_publish_committed"
    assert records[-1]["run_id"] == records[0]["run_id"]
    assert records[-1]["plan_digest"] == records[0]["plan_digest"]
    assert incomplete_publish_journals(case["workspace"]) == ()


def test_distribution_hard_kill_leaves_mixed_set_and_blocks_stale_reclaim(
    monkeypatch,
    tmp_path,
):
    case = _distribution_publish_case(monkeypatch, tmp_path)
    first, second = case["originals"]
    real_replace = distribution._destructive_replace

    class SimulatedHardKill(BaseException):
        pass

    def kill_after_first_replace(source, target):
        real_replace(source, target)
        if Path(target) == first:
            raise SimulatedHardKill("power loss after first live replace")

    monkeypatch.setattr(
        distribution,
        "_destructive_replace",
        kill_after_first_replace,
    )

    with pytest.raises(SimulatedHardKill):
        _publish_case(case)

    assert first.read_bytes() == b"new-0"
    assert second.read_bytes() == b"old-1"
    pending = incomplete_publish_journals(case["workspace"])
    assert pending == (case["journal"],)
    records = [
        json.loads(line)
        for line in case["journal"].read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["event"] == "distribution_publish_started"
    assert not any(
        record["event"].startswith("distribution_publish_")
        and record["event"].endswith(("committed", "rolled_back"))
        for record in records
    )

    with pytest.raises(SafetyViolation, match="ручная сверка"):
        _publish_case(case)

    monkeypatch.setattr(
        ConfigBundle,
        "workspace_path",
        lambda _bundle, _at=None: case["workspace"],
    )
    with pytest.raises(SafetyViolation, match="publish_started"):
        reconcile_incomplete_publication(
            case["bundle"],
            case["stage"].staging_dir,
        )

    stale_lock = case["workspace"] / "cycle.lock"
    stale_payload = '{"owner_token":"crashed-distribution"}\n'
    stale_lock.write_text(stale_payload, encoding="utf-8")
    old = time.time() - 86400
    os.utime(stale_lock, (old, old))
    with pytest.raises(SafetyViolation, match="ручная сверка"):
        with publication_cycle_lock(case["bundle"]):
            pass
    assert stale_lock.read_text(encoding="utf-8") == stale_payload


def test_distribution_rechecks_target_after_last_exclusive_check(
    monkeypatch,
    tmp_path,
):
    case = _distribution_publish_case(monkeypatch, tmp_path, files=1)
    target = case["originals"][0]
    target_checks = 0
    live_replace_calls = 0
    real_replace = distribution._destructive_replace

    def mutate_at_last_exclusive(path):
        nonlocal target_checks
        if Path(path) == target:
            target_checks += 1
            if target_checks == 3:
                target.write_bytes(b"manual-third-state")

    def count_replace(source, destination):
        nonlocal live_replace_calls
        live_replace_calls += 1
        real_replace(source, destination)

    monkeypatch.setattr(
        distribution,
        "_exclusive_file_check",
        mutate_at_last_exclusive,
    )
    monkeypatch.setattr(
        distribution,
        "_destructive_replace",
        count_replace,
    )

    with pytest.raises(SafetyViolation, match="rollback неполон"):
        _publish_case(case)

    assert target.read_bytes() == b"manual-third-state"
    assert live_replace_calls == 0
    assert incomplete_publish_journals(case["workspace"]) == (
        case["journal"],
    )


def test_distribution_rollback_does_not_overwrite_third_live_state(
    monkeypatch,
    tmp_path,
):
    case = _distribution_publish_case(monkeypatch, tmp_path)
    first, second = case["originals"]
    real_replace = distribution._destructive_replace

    def fail_second_after_manual_edit(source, target):
        if Path(target) == second:
            first.write_bytes(b"manual-third-state")
            raise OSError("fault before second replace")
        real_replace(source, target)

    monkeypatch.setattr(
        distribution,
        "_destructive_replace",
        fail_second_after_manual_edit,
    )

    with pytest.raises(SafetyViolation, match="rollback неполон"):
        _publish_case(case)

    assert first.read_bytes() == b"manual-third-state"
    assert second.read_bytes() == b"old-1"
    assert incomplete_publish_journals(case["workspace"]) == (
        case["journal"],
    )


def test_distribution_rollback_does_not_unlink_changed_sidecar(
    monkeypatch,
    tmp_path,
):
    case = _distribution_publish_case(
        monkeypatch,
        tmp_path,
        sidecar_indexes=(0,),
    )
    second = case["originals"][1]
    real_replace = distribution._destructive_replace
    created_sidecar = None

    def fail_after_sidecar_is_manually_changed(source, target):
        nonlocal created_sidecar
        destination = Path(target)
        if destination == second:
            assert created_sidecar is not None
            created_sidecar.write_bytes(b"manual-sidecar-state")
            raise OSError("fault before free target replace")
        real_replace(source, destination)
        created_sidecar = destination

    monkeypatch.setattr(
        distribution,
        "_destructive_replace",
        fail_after_sidecar_is_manually_changed,
    )

    with pytest.raises(SafetyViolation, match="rollback неполон"):
        _publish_case(case)

    assert created_sidecar is not None
    assert created_sidecar.read_bytes() == b"manual-sidecar-state"
    assert incomplete_publish_journals(case["workspace"]) == (
        case["journal"],
    )
