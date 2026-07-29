"""Optimistic-lock, атомарная публикация и rollback."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .baseline import baseline_path, build_baseline, save_baseline
from .config import ConfigBundle
from .errors import PublishError, SafetyViolation
from .file_access import BusyFile, busy_files
from .files import fingerprint, stable_copy
from .force_close import DelegatedForceCloser
from .models import Issue, SyncPlan
from .reader import read_portfolio
from .staging import StageSet, StagedFile
from .validation import ValidationReport


@dataclass(slots=True)
class PublishResult:
    run_id: str
    plan_digest: str
    started_at: str
    finished_at: str | None = None
    published: list[str] = field(default_factory=list)
    sidecars: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    rolled_back: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    baseline: str | None = None

    @property
    def ok(self) -> bool:
        return self.finished_at is not None and not any(
            issue.severity == "error"
            for issue in self.issues
        )

    @property
    def complete(self) -> bool:
        return self.ok and not self.sidecars

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "published": self.published,
            "sidecars": self.sidecars,
            "backups": self.backups,
            "rolled_back": self.rolled_back,
            "issues": [issue.as_dict() for issue in self.issues],
            "baseline": self.baseline,
            "ok": self.ok,
            "complete": self.complete,
        }


class _Journal:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **payload: Any) -> None:
        record = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _publish_order(stage: StageSet) -> tuple[StagedFile, ...]:
    # Общий реестр заменяется последним: до этого все персональные temp уже
    # готовы, а при любом сбое персональные книги откатываются из snapshot.
    return (*stage.personals, stage.master)


def _assert_unique_targets(files: tuple[StagedFile, ...]) -> None:
    targets = [str(item.original_path.resolve()).casefold() for item in files]
    if len(targets) != len(set(targets)):
        raise SafetyViolation("публикация содержит повторный целевой путь")


def _assert_unchanged(item: StagedFile) -> None:
    if not item.original_path.is_file():
        raise SafetyViolation(f"целевой файл исчез: {item.original_path}")
    current = fingerprint(item.original_path)
    if current != item.source_fingerprint:
        raise SafetyViolation(
            f"целевой файл изменился после snapshot: {item.original_path}"
        )


def _temp_path(item: StagedFile, run_id: str, suffix: str) -> Path:
    token = item.entry_id or "master"
    random_part = secrets.token_hex(3)
    return item.original_path.parent / (
        f".registry-cycle-{run_id}-{token}-{random_part}-{suffix}"
        f"{item.original_path.suffix.casefold()}"
    )


def _cleanup_temps(paths: list[Path], journal: _Journal) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
            journal.append("temp_removed", path=str(path))
        except OSError as exc:
            journal.append("temp_remove_failed", path=str(path), error=str(exc))


def _unique_sibling(
    item: StagedFile,
    *,
    label: str,
    stamp: datetime,
) -> Path:
    base = item.original_path.with_name(
        f"Копия {item.original_path.stem} "
        f"({label} {stamp:%d.%m.%y %H-%M})"
        f"{item.original_path.suffix}"
    )
    if not base.exists():
        return base
    for number in range(2, 1000):
        candidate = base.with_name(
            f"{base.stem} {number}{base.suffix}"
        )
        if not candidate.exists():
            return candidate
    raise SafetyViolation(
        f"не найдено свободное имя соседней копии: {item.original_path}"
    )


def _busy_targets(
    files: tuple[StagedFile, ...],
    *,
    mode: str,
    journal: _Journal,
    force_close: Callable[[tuple[BusyFile, ...]], None] | None,
) -> set[str]:
    if mode not in {"block", "sidecar", "force_close"}:
        raise SafetyViolation(f"неизвестный режим занятых файлов: {mode}")
    by_path = {
        str(item.original_path).casefold(): item
        for item in files
    }
    busy = busy_files(tuple(item.original_path for item in files))
    if busy and mode == "force_close":
        if force_close is None:
            raise SafetyViolation(
                "force_close выбран, но делегированный механизм не передан"
            )
        journal.append(
            "force_close_requested",
            files=[item.as_dict() for item in busy],
        )
        force_close(busy)
        busy = busy_files(tuple(item.original_path for item in files))
        if busy:
            raise SafetyViolation(
                "после force_close остались занятые цели: "
                + ", ".join(str(item.path) for item in busy)
            )
        journal.append("force_close_passed")
        return set()
    if not busy:
        return set()

    sidecars: set[str] = set()
    for item in busy:
        staged = by_path.get(str(item.path).casefold())
        if mode == "sidecar" and staged is not None and staged.kind == "personal":
            sidecars.add(str(staged.original_path).casefold())
            continue
        raise SafetyViolation(
            "live-файл занят; публикация остановлена: "
            f"{item.path} ({item.reason})"
        )
    journal.append(
        "sidecars_required",
        files=[
            item.as_dict()
            for item in busy
            if str(item.path).casefold() in sidecars
        ],
    )
    return sidecars


def atomic_publish_files(
    stage: StageSet,
    *,
    journal_path: str | Path | None = None,
    busy_mode: str = "block",
    force_close: Callable[[tuple[BusyFile, ...]], None] | None = None,
) -> PublishResult:
    """Опубликовать уже проверенные файлы; пригодно для fault-injection тестов."""
    files = _publish_order(stage)
    _assert_unique_targets(files)
    journal = _Journal(
        Path(journal_path)
        if journal_path is not None
        else stage.run_dir / "publish.journal.jsonl"
    )
    result = PublishResult(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        started_at=datetime.now().isoformat(timespec="seconds"),
    )
    prepared: dict[str, Path] = {}
    replaced: list[StagedFile] = []
    created_sidecars: list[tuple[StagedFile, Path]] = []
    sidecar_targets: set[str] = set()
    sidecar_paths: dict[str, Path] = {}
    stamp = datetime.now()
    journal.append(
        "publish_started",
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        files=len(files),
    )
    try:
        sidecar_targets |= _busy_targets(
            files,
            mode=busy_mode,
            journal=journal,
            force_close=force_close,
        )
        for item in files:
            if fingerprint(item.staged_path) != item.staged_fingerprint:
                raise SafetyViolation(
                    f"staging изменился после validation: {item.staged_path}"
                )
            _assert_unchanged(item)
        journal.append("preflight_passed")

        for item in files:
            temporary = _temp_path(item, stage.run_id, "next")
            stable_copy(item.staged_path, temporary)
            if fingerprint(temporary) != item.staged_fingerprint:
                raise PublishError(
                    f"сетевая temp-копия не совпала со staging: {item.original_path}"
                )
            prepared[str(item.original_path).casefold()] = temporary
            journal.append(
                "temp_prepared",
                target=str(item.original_path),
                temp=str(temporary),
            )

        # Второй барьер закрывает окно между длинным копированием temp и replace.
        sidecar_targets |= _busy_targets(
            files,
            mode=busy_mode,
            journal=journal,
            force_close=force_close,
        )
        for item in files:
            _assert_unchanged(item)
        journal.append("second_preflight_passed")

        for item in files:
            sidecar_targets |= _busy_targets(
                (item,),
                mode=busy_mode,
                journal=journal,
                force_close=force_close,
            )
            _assert_unchanged(item)
            temporary = prepared[str(item.original_path).casefold()]
            target_key = str(item.original_path).casefold()
            if target_key in sidecar_targets:
                sidecar = sidecar_paths.setdefault(
                    target_key,
                    _unique_sibling(
                        item,
                        label="после ночного цикла",
                        stamp=stamp,
                    ),
                )
                os.replace(temporary, sidecar)
                created_sidecars.append((item, sidecar))
                if fingerprint(sidecar) != item.staged_fingerprint:
                    raise PublishError(
                        f"проверка sidecar не пройдена: {sidecar}"
                    )
                result.sidecars.append(str(sidecar))
                journal.append(
                    "sidecar_created",
                    target=str(item.original_path),
                    sidecar=str(sidecar),
                )
                continue
            backup = _unique_sibling(
                item,
                label="до ночного цикла",
                stamp=stamp,
            )
            stable_copy(item.original_path, backup)
            if fingerprint(backup) != item.source_fingerprint:
                raise PublishError(
                    f"соседний backup не совпал с live: {backup}"
                )
            result.backups.append(str(backup))
            journal.append(
                "sibling_backup_created",
                target=str(item.original_path),
                backup=str(backup),
            )
            os.replace(temporary, item.original_path)
            replaced.append(item)
            if fingerprint(item.original_path) != item.staged_fingerprint:
                raise PublishError(
                    f"проверка после replace не пройдена: {item.original_path}"
                )
            result.published.append(str(item.original_path))
            journal.append("target_replaced", target=str(item.original_path))
    except Exception as exc:  # noqa: BLE001 — обязан запустить rollback
        journal.append("publish_failed", error=str(exc))
        rollback_failures: list[str] = []
        for item in reversed(replaced):
            rollback_temp = _temp_path(item, stage.run_id, "rollback")
            try:
                stable_copy(item.source_copy, rollback_temp)
                os.replace(rollback_temp, item.original_path)
                if fingerprint(item.original_path) != item.source_fingerprint:
                    raise PublishError("SHA rollback-копии не совпал")
                result.rolled_back.append(str(item.original_path))
                journal.append("target_rolled_back", target=str(item.original_path))
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_failures.append(str(item.original_path))
                journal.append(
                    "rollback_failed",
                    target=str(item.original_path),
                    backup=str(item.source_copy),
                    error=str(rollback_exc),
                )
        for item, sidecar in reversed(created_sidecars):
            try:
                if fingerprint(sidecar) != item.staged_fingerprint:
                    raise PublishError(
                        "SHA sidecar перед rollback не совпал"
                    )
                sidecar.unlink()
                result.sidecars.remove(str(sidecar))
                result.rolled_back.append(str(sidecar))
                journal.append(
                    "sidecar_removed_on_rollback",
                    path=str(sidecar),
                )
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_failures.append(str(sidecar))
                journal.append(
                    "sidecar_rollback_failed",
                    path=str(sidecar),
                    error=str(rollback_exc),
                )
        _cleanup_temps(list(prepared.values()), journal)
        result.issues.append(
            Issue(
                "error",
                "publish_failed",
                "публикация не завершена; выполнен rollback",
                {
                    "error": str(exc),
                    "rollback_failures": rollback_failures,
                },
            )
        )
        _atomic_json(stage.run_dir / "publish.json", result.as_dict())
        if rollback_failures:
            raise PublishError(
                "rollback не завершён для: " + ", ".join(rollback_failures)
            ) from exc
        raise PublishError(f"публикация отменена и откачена: {exc}") from exc

    result.finished_at = datetime.now().isoformat(timespec="seconds")
    if result.sidecars:
        result.issues.append(
            Issue(
                "warning",
                "publish_sidecars_pending",
                "часть занятых личных файлов опубликована соседними копиями; "
                "пакет требует последующего сведения",
                {"sidecars": list(result.sidecars)},
            )
        )
    journal.append("publish_files_completed")
    return result


def publish_validated(
    bundle: ConfigBundle,
    stage: StageSet,
    plan: SyncPlan,
    validation: ValidationReport,
    *,
    confirmation_digest: str,
    busy_mode: str | None = None,
    allow_initial_baseline: bool = False,
) -> PublishResult:
    if plan.blocked:
        raise SafetyViolation("заблокированный план нельзя публиковать")
    if validation.blocked:
        raise SafetyViolation("validation содержит ошибки")
    if not (
        stage.plan_digest
        == plan.digest
        == validation.plan_digest
        == confirmation_digest
    ):
        raise SafetyViolation("digest подтверждения не совпал с планом")
    if validation.checked_files != len(stage.files):
        raise SafetyViolation("validation проверил не все staging-файлы")

    target_baseline = baseline_path(bundle, stage.master.original_path)
    if not target_baseline.is_file() and not allow_initial_baseline:
        raise SafetyViolation(
            "активный baseline отсутствует; обычная публикация запрещена. "
            "Первый baseline публикуется только через promote-first-baseline"
        )
    if target_baseline.exists() and allow_initial_baseline:
        raise SafetyViolation(
            "активный baseline уже существует; initial-baseline режим запрещён"
        )

    staged_master = read_portfolio(stage.master.staged_path, bundle.fields)
    staged_master = replace(
        staged_master,
        path=stage.master.original_path,
    )
    baseline = build_baseline(
        staged_master,
        bundle.fields,
        plan_digest=plan.digest,
    )
    pending_baseline = stage.run_dir / "baseline.next.json.gz"
    save_baseline(pending_baseline, baseline)
    target_baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline_temp = target_baseline.with_name(
        f".{target_baseline.name}.{stage.run_id}.{secrets.token_hex(3)}.next"
    )
    stable_copy(pending_baseline, baseline_temp)

    selected_busy_mode = (
        busy_mode
        if busy_mode is not None
        else str(bundle.app.safety.get("busy_file_policy", "block"))
    )
    closer = DelegatedForceCloser(bundle)

    def force_close_callback(items: tuple[BusyFile, ...]) -> None:
        closer.close(
            items,
            audit_dir=stage.run_dir / "force-close",
        )

    try:
        result = atomic_publish_files(
            stage,
            busy_mode=selected_busy_mode,
            force_close=(
                force_close_callback
                if selected_busy_mode == "force_close"
                else None
            ),
        )
    except Exception:
        baseline_temp.unlink(missing_ok=True)
        raise
    if result.sidecars:
        # Sidecar означает, что хотя бы один живой личный файл остался на
        # прежнем состоянии. Активировать baseline всего staging в этот момент
        # опасно: на следующем цикле старый открытый оригинал выглядел бы как
        # новая правка сотрудника относительно уже нового baseline.
        baseline_temp.unlink(missing_ok=True)
        pending_originals = [
            str(item.original_path)
            for item in stage.personals
            if str(item.original_path) not in result.published
        ]
        result.issues.append(
            Issue(
                "warning",
                "baseline_deferred_for_sidecars",
                "baseline не активирован до контролируемого сведения sidecar",
                {
                    "candidate_baseline": str(pending_baseline),
                    "sidecars": list(result.sidecars),
                    "originals": pending_originals,
                },
            )
        )
        _atomic_json(
            stage.run_dir / "pending-sidecars.json",
            {
                "run_id": stage.run_id,
                "plan_digest": plan.digest,
                "candidate_baseline": str(pending_baseline),
                "sidecars": result.sidecars,
                "originals": pending_originals,
                "pairs": [
                    {
                        "original": original,
                        "sidecar": sidecar,
                    }
                    for original, sidecar in zip(
                        pending_originals,
                        result.sidecars,
                        strict=True,
                    )
                ],
            },
        )
        _atomic_json(stage.run_dir / "publish.json", result.as_dict())
        return result
    try:
        os.replace(baseline_temp, target_baseline)
    except Exception as exc:
        journal = _Journal(stage.run_dir / "publish.journal.jsonl")
        journal.append("baseline_activation_failed", error=str(exc))
        rollback_failures: list[str] = []
        published = {
            path.casefold()
            for path in result.published
        }
        for item in reversed(_publish_order(stage)):
            if str(item.original_path).casefold() not in published:
                continue
            rollback_temp = _temp_path(item, stage.run_id, "baseline-rollback")
            try:
                if fingerprint(item.original_path) != item.staged_fingerprint:
                    raise PublishError(
                        "цель изменилась после публикации; автоматический rollback опасен"
                    )
                stable_copy(item.source_copy, rollback_temp)
                os.replace(rollback_temp, item.original_path)
                if fingerprint(item.original_path) != item.source_fingerprint:
                    raise PublishError("SHA rollback-копии не совпал")
                result.rolled_back.append(str(item.original_path))
                journal.append(
                    "target_rolled_back_after_baseline_failure",
                    target=str(item.original_path),
                )
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_failures.append(str(item.original_path))
                journal.append(
                    "rollback_failed_after_baseline_failure",
                    target=str(item.original_path),
                    backup=str(item.source_copy),
                    error=str(rollback_exc),
                )
        for sidecar_text in list(result.sidecars):
            sidecar = Path(sidecar_text)
            try:
                expected_fingerprints = {
                    item.staged_fingerprint
                    for item in stage.personals
                }
                if fingerprint(sidecar) not in expected_fingerprints:
                    raise PublishError(
                        "SHA sidecar перед rollback baseline не совпал"
                    )
                sidecar.unlink()
                result.sidecars.remove(sidecar_text)
                result.rolled_back.append(sidecar_text)
                journal.append(
                    "sidecar_removed_after_baseline_failure",
                    path=sidecar_text,
                )
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_failures.append(sidecar_text)
                journal.append(
                    "sidecar_rollback_failed_after_baseline_failure",
                    path=sidecar_text,
                    error=str(rollback_exc),
                )
        baseline_temp.unlink(missing_ok=True)
        result.issues.append(
            Issue(
                "error",
                "baseline_activation_failed",
                "baseline не активирован; публикация откачена",
                {
                    "error": str(exc),
                    "rollback_failures": rollback_failures,
                },
            )
        )
        _atomic_json(stage.run_dir / "publish.json", result.as_dict())
        if rollback_failures:
            raise PublishError(
                "baseline не активирован, rollback неполон: "
                + ", ".join(rollback_failures)
            ) from exc
        raise PublishError(
            f"baseline не активирован; файлы откачены: {exc}"
        ) from exc
    result.baseline = str(target_baseline)
    _atomic_json(stage.run_dir / "publish.json", result.as_dict())
    return result
