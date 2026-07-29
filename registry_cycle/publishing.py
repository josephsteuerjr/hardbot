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
from .cycle_lock import CycleLock
from .errors import PublishError, SafetyViolation
from .file_access import BusyFile, busy_files
from .files import fingerprint, stable_copy
from .force_close import DelegatedForceCloser
from .models import Issue, SyncPlan
from .reader import read_portfolio
from .staging import StageSet, StagedFile, load_stage
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


_PUBLISH_TERMINALS = frozenset({
    "publish_committed",
    "publish_rolled_back",
    "reconciled_all_old",
    "reconciled_all_new",
})
_DISTRIBUTION_TERMINALS = frozenset({
    "distribution_publish_committed",
    "distribution_publish_rolled_back",
})
_PUBLICATION_PROTOCOLS = {
    "publish_started": _PUBLISH_TERMINALS,
    "distribution_publish_started": _DISTRIBUTION_TERMINALS,
}


def _terminal_matches_start(
    record: dict[str, Any],
    start_record: dict[str, Any],
) -> bool:
    """Terminal закрывает только последний publish с теми же identity."""
    terminals = _PUBLICATION_PROTOCOLS.get(start_record.get("event"))
    run_id = start_record.get("run_id")
    plan_digest = start_record.get("plan_digest")
    return bool(
        terminals
        and record.get("event") in terminals
        and run_id
        and plan_digest
        and record.get("run_id") == run_id
        and record.get("plan_digest") == plan_digest
    )


def _journal_records(path: Path) -> tuple[list[dict[str, Any]], bool]:
    records: list[dict[str, Any]] = []
    malformed = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records, True
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed = True
            continue
        if not isinstance(record, dict) or not record.get("event"):
            malformed = True
            continue
        records.append(record)
    return records, malformed


def _legacy_publish_is_committed(
    journal_path: Path,
    tail: list[dict[str, Any]],
) -> bool:
    """Совместимость с зелёными публикациями до terminal-event.

    Старый код писал ``publish_files_completed``, затем атомарно активировал
    baseline и только после этого сохранял ``publish.json`` с
    ``complete=true``. Такой уже завершённый live-run нельзя ошибочно объявить
    аварийным после обновления.
    """
    publish_path = journal_path.with_name("publish.json")
    if not any(
        record.get("event") == "publish_files_completed"
        for record in tail
    ):
        return False
    start_record = tail[0]
    # Текущий формат всегда имеет identity и обязан завершаться exact terminal.
    # publish.json — compatibility proof только для действительно старых журналов.
    if start_record.get("run_id") or start_record.get("plan_digest"):
        return False
    try:
        payload = json.loads(publish_path.read_text(encoding="utf-8"))
        baseline = Path(payload["baseline"])
        publish_is_newer = (
            publish_path.stat().st_mtime_ns
            >= journal_path.stat().st_mtime_ns
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return False
    return bool(
        payload.get("complete")
        and payload.get("finished_at")
        and not payload.get("sidecars")
        and baseline.is_file()
        and publish_is_newer
        and (
            not start_record.get("run_id")
            or payload.get("run_id") == start_record.get("run_id")
        )
        and (
            not start_record.get("plan_digest")
            or payload.get("plan_digest") == start_record.get("plan_digest")
        )
    )


def incomplete_publish_journals(workspace: str | Path) -> tuple[Path, ...]:
    """Незавершённые ordinary/distribution publish journal во всём workspace.

    Сам факт ``publish_files_completed`` недостаточен: kill мог произойти до
    baseline. Нужен terminal-event нового кода либо доказанный legacy
    ``publish.json complete + active baseline``. Distribution имеет отдельные
    events и намеренно не получает автоматический reconcile: её аварийный
    journal остаётся STOP до ручной сверки intent/result.
    """
    root = Path(workspace)
    if not root.exists():
        return ()
    incomplete: list[Path] = []
    for path in sorted(root.glob("runs/**/publish.journal.jsonl")):
        records, malformed = _journal_records(path)
        # Любая непустая неразбираемая запись — STOP независимо от уже
        # увиденного terminal. Иначе обрезанный новый publish_started после
        # корректно закрытого старого запуска исчезает из records, и terminal
        # старого запуска ошибочно делает весь повреждённый хвост complete.
        if malformed:
            incomplete.append(path)
            continue
        starts = [
            index
            for index, record in enumerate(records)
            if record.get("event") in _PUBLICATION_PROTOCOLS
        ]
        if not starts:
            continue
        tail = records[starts[-1]:]
        if any(
            _terminal_matches_start(record, tail[0])
            for record in tail
        ):
            continue
        if (
            tail[0].get("event") == "publish_started"
            and _legacy_publish_is_committed(
                path,
                tail,
            )
        ):
            continue
        incomplete.append(path)
    return tuple(incomplete)


def assert_no_incomplete_publications(workspace: str | Path) -> None:
    pending = incomplete_publish_journals(workspace)
    if pending:
        raise SafetyViolation(
            "обнаружена незавершённая публикация; новый publish запрещён. "
            "Обычный цикл: reconcile-publish; distribution: ручная сверка "
            "publication_intent.json/publish.json и явное закрытие journal: "
            + ", ".join(str(path.parent) for path in pending[:5])
        )


def publication_cycle_lock(
    bundle: ConfigBundle,
    *,
    allow_incomplete: bool = False,
) -> CycleLock:
    """Единый lock для каждого publish/rehearsal/reconcile entrypoint."""
    app = getattr(bundle, "app", None)
    workspace = (
        bundle.workspace_path()
        if app is not None
        else Path(bundle.project_root) / ".registry-cycle"
    )
    safety = getattr(app, "safety", {}) if app is not None else {}
    stale = int(safety.get("lock_stale_seconds", 21600))
    return CycleLock(
        workspace / "cycle.lock",
        stale_seconds=stale,
        before_stale_remove=(
            None
            if allow_incomplete
            else lambda: assert_no_incomplete_publications(workspace)
        ),
    )


def _assert_stage_run_under_workspace(
    bundle: ConfigBundle,
    stage: StageSet,
) -> None:
    runs_root = (bundle.workspace_path() / "runs").resolve()
    run_dir = stage.run_dir.resolve()
    try:
        run_dir.relative_to(runs_root)
    except ValueError as exc:
        raise SafetyViolation(
            "run_dir staging находится вне workspace/runs; "
            f"публикация запрещена: {stage.run_dir}"
        ) from exc
    if run_dir == runs_root:
        raise SafetyViolation(
            "run_dir staging должен быть отдельной папкой внутри "
            f"workspace/runs: {stage.run_dir}"
        )


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
        files=[item.as_dict() for item in files],
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
                journal.append(
                    "sidecar_replace_started",
                    target=str(item.original_path),
                    sidecar=str(sidecar),
                )
                if sidecar.exists():
                    raise SafetyViolation(
                        f"имя sidecar занято перед replace: {sidecar}"
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
            # Backup сетевого файла может быть долгим. За это время сотрудник
            # способен сохранить новую версию и закрыть Excel; busy-check уже
            # ничего не увидит. Непосредственно перед destructive replace снова
            # требуем exact source fingerprint.
            _assert_unchanged(item)
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
                if fingerprint(item.original_path) != item.staged_fingerprint:
                    raise SafetyViolation(
                        "цель изменилась после публикации; автоматический "
                        f"rollback опасен: {item.original_path}"
                    )
                stable_copy(item.source_copy, rollback_temp)
                # Копирование rollback-temp тоже открывает окно для ручной
                # правки. Третье состояние не затираем.
                if fingerprint(item.original_path) != item.staged_fingerprint:
                    rollback_temp.unlink(missing_ok=True)
                    raise SafetyViolation(
                        "цель изменилась непосредственно перед rollback: "
                        f"{item.original_path}"
                    )
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
                # Повторный exact barrier непосредственно перед unlink:
                # изменённый пользователем sidecar сохраняем для ручного разбора.
                if fingerprint(sidecar) != item.staged_fingerprint:
                    raise SafetyViolation(
                        f"sidecar изменился перед rollback unlink: {sidecar}"
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
        journal.append(
            "publish_rolled_back",
            run_id=stage.run_id,
            plan_digest=stage.plan_digest,
            reason="caught_exception",
        )
        raise PublishError(f"публикация отменена и откачена: {exc}") from exc

    # The import trust ledger derives its monotonic source sequence from this
    # durable timestamp. Microseconds make two legitimate consecutive
    # publications distinguishable; an actual collision still fails closed.
    result.finished_at = datetime.now().isoformat(timespec="microseconds")
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


def _fingerprint_payload(path: Path) -> dict[str, Any]:
    return fingerprint(path).as_dict()


def _matches_fingerprint_payload(path: Path, payload: dict[str, Any]) -> bool:
    if not path.is_file():
        return False
    return _fingerprint_payload(path) == {
        "size": int(payload["size"]),
        "mtime_ns": int(payload["mtime_ns"]),
        "sha256": str(payload["sha256"]),
    }


def _prepare_publish_recovery(
    stage: StageSet,
    *,
    target_baseline: Path,
    pending_baseline: Path,
    allow_initial_baseline: bool,
) -> dict[str, Any]:
    """Зафиксировать всё нужное для all-old/all-new до первого live replace."""
    previous_baseline = stage.run_dir / "baseline.previous.json.gz"
    previous_payload = None
    if target_baseline.is_file():
        stable_copy(target_baseline, previous_baseline)
        previous_payload = {
            "path": str(previous_baseline),
            "fingerprint": _fingerprint_payload(previous_baseline),
        }
    else:
        previous_baseline.unlink(missing_ok=True)

    payload = {
        "kind": "registry-cycle-publish-recovery-v1",
        "run_id": stage.run_id,
        "plan_digest": stage.plan_digest,
        "stage": str(stage.staging_dir / "stage.json"),
        "target_baseline": str(target_baseline),
        "pending_baseline": {
            "path": str(pending_baseline),
            "fingerprint": _fingerprint_payload(pending_baseline),
        },
        "previous_baseline": previous_payload,
        "allow_initial_baseline": allow_initial_baseline,
    }
    manifest_path = stage.run_dir / "publish.recovery.json"
    _atomic_json(manifest_path, payload)
    _Journal(stage.run_dir / "publish.journal.jsonl").append(
        "recovery_manifest_written",
        path=str(manifest_path),
        target_baseline=str(target_baseline),
    )
    return payload


def _baseline_state(manifest: dict[str, Any]) -> str:
    target = Path(manifest["target_baseline"])
    pending = manifest["pending_baseline"]
    previous = manifest.get("previous_baseline")
    if _matches_fingerprint_payload(target, pending["fingerprint"]):
        return "new"
    if previous and _matches_fingerprint_payload(
        target,
        previous["fingerprint"],
    ):
        return "old"
    if previous is None and not target.exists():
        return "old"
    return "unknown"


def _restore_old_baseline(
    manifest: dict[str, Any],
    journal: _Journal,
) -> None:
    target = Path(manifest["target_baseline"])
    state = _baseline_state(manifest)
    if state == "old":
        return
    if state != "new":
        raise PublishError(
            f"baseline имеет неизвестное состояние; all-old опасен: {target}"
        )
    previous = manifest.get("previous_baseline")
    if previous is None:
        if _baseline_state(manifest) != "new":
            raise SafetyViolation(
                f"baseline изменился перед all-old unlink: {target}"
            )
        target.unlink()
        journal.append("baseline_removed_on_reconcile", target=str(target))
        return
    previous_path = Path(previous["path"])
    if not _matches_fingerprint_payload(
        previous_path,
        previous["fingerprint"],
    ):
        raise PublishError(
            f"предыдущий baseline не прошёл SHA-проверку: {previous_path}"
        )
    temporary = target.with_name(
        f".{target.name}.{manifest['run_id']}.reconcile-old"
    )
    stable_copy(previous_path, temporary)
    if not _matches_fingerprint_payload(
        temporary,
        previous["fingerprint"],
    ):
        temporary.unlink(missing_ok=True)
        raise PublishError(
            f"baseline rollback temp не прошёл SHA-проверку: {temporary}"
        )
    # Target проверялся до копирования temp, но за это время мог получить
    # третье ручное состояние. Не перезаписываем его.
    if _baseline_state(manifest) != "new":
        temporary.unlink(missing_ok=True)
        raise SafetyViolation(
            f"baseline изменился непосредственно перед all-old replace: {target}"
        )
    os.replace(temporary, target)
    if not _matches_fingerprint_payload(target, previous["fingerprint"]):
        raise PublishError(f"baseline all-old не прошёл SHA-проверку: {target}")
    journal.append("baseline_restored_on_reconcile", target=str(target))


def _activate_new_baseline(
    manifest: dict[str, Any],
    journal: _Journal,
) -> None:
    target = Path(manifest["target_baseline"])
    pending = manifest["pending_baseline"]
    pending_path = Path(pending["path"])
    if not _matches_fingerprint_payload(
        pending_path,
        pending["fingerprint"],
    ):
        raise PublishError(
            f"candidate baseline не прошёл SHA-проверку: {pending_path}"
        )
    state = _baseline_state(manifest)
    if state == "new":
        return
    if state != "old":
        raise PublishError(
            f"baseline имеет неизвестное состояние; all-new опасен: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{manifest['run_id']}.reconcile-new"
    )
    stable_copy(pending_path, temporary)
    if not _matches_fingerprint_payload(
        temporary,
        pending["fingerprint"],
    ):
        temporary.unlink(missing_ok=True)
        raise PublishError(
            f"baseline candidate temp не прошёл SHA-проверку: {temporary}"
        )
    # Повторный exact-old barrier после копирования temp и непосредственно
    # перед destructive replace.
    if _baseline_state(manifest) != "old":
        temporary.unlink(missing_ok=True)
        raise SafetyViolation(
            f"baseline изменился непосредственно перед all-new replace: {target}"
        )
    os.replace(temporary, target)
    if not _matches_fingerprint_payload(target, pending["fingerprint"]):
        raise PublishError(f"baseline all-new не прошёл SHA-проверку: {target}")
    journal.append("baseline_activated_on_reconcile", target=str(target))


def publish_validated(
    bundle: ConfigBundle,
    stage: StageSet,
    plan: SyncPlan,
    validation: ValidationReport,
    *,
    confirmation_digest: str,
    busy_mode: str | None = None,
    allow_initial_baseline: bool = False,
    cycle_lock: CycleLock | None = None,
) -> PublishResult:
    if cycle_lock is None:
        with publication_cycle_lock(bundle) as acquired:
            return publish_validated(
                bundle,
                stage,
                plan,
                validation,
                confirmation_digest=confirmation_digest,
                busy_mode=busy_mode,
                allow_initial_baseline=allow_initial_baseline,
                cycle_lock=acquired,
            )
    cycle_lock.assert_owned()
    _assert_stage_run_under_workspace(bundle, stage)
    assert_no_incomplete_publications(bundle.workspace_path())
    config_identity = str(getattr(bundle, "config_identity", "") or "")
    if not (
        str(getattr(stage, "config_identity", "") or "")
        == str(getattr(plan, "config_identity", "") or "")
        == config_identity
    ):
        raise SafetyViolation(
            "plan/staging относятся к другой конфигурации; publish запрещён"
        )
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
    recovery_manifest = _prepare_publish_recovery(
        stage,
        target_baseline=target_baseline,
        pending_baseline=pending_baseline,
        allow_initial_baseline=allow_initial_baseline,
    )

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
        if not _matches_fingerprint_payload(
            baseline_temp,
            recovery_manifest["pending_baseline"]["fingerprint"],
        ):
            raise PublishError(
                f"baseline temp не совпал с candidate: {baseline_temp}"
            )
        # Live publish мог занять минуты. За это время active baseline не
        # должен был получить третье состояние; проверяем его непосредственно
        # перед активацией candidate.
        if _baseline_state(recovery_manifest) != "old":
            raise SafetyViolation(
                "active baseline изменился непосредственно перед publish replace"
            )
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
                if fingerprint(item.original_path) != item.staged_fingerprint:
                    rollback_temp.unlink(missing_ok=True)
                    raise SafetyViolation(
                        "цель изменилась непосредственно перед baseline rollback: "
                        f"{item.original_path}"
                    )
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
                if fingerprint(sidecar) not in expected_fingerprints:
                    raise SafetyViolation(
                        f"sidecar изменился перед baseline rollback unlink: {sidecar}"
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
        try:
            _restore_old_baseline(recovery_manifest, journal)
        except Exception as rollback_exc:  # noqa: BLE001
            rollback_failures.append(str(target_baseline))
            journal.append(
                "baseline_rollback_failed_after_activation_failure",
                target=str(target_baseline),
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
        journal.append(
            "publish_rolled_back",
            run_id=stage.run_id,
            plan_digest=stage.plan_digest,
            reason="baseline_activation_failed",
        )
        raise PublishError(
            f"baseline не активирован; файлы откачены: {exc}"
        ) from exc
    result.baseline = str(target_baseline)
    _atomic_json(stage.run_dir / "publish.json", result.as_dict())
    _Journal(stage.run_dir / "publish.journal.jsonl").append(
        "publish_committed",
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        baseline=str(target_baseline),
    )
    return result


def _recovery_sidecars(
    journal_path: Path,
    stage: StageSet,
) -> tuple[tuple[Path, StagedFile], ...]:
    by_target = {
        str(item.original_path).casefold(): item
        for item in stage.personals
    }
    records, _malformed = _journal_records(journal_path)
    starts = [
        index
        for index, record in enumerate(records)
        if record.get("event") == "publish_started"
    ]
    if starts:
        records = records[starts[-1]:]
    candidates: dict[str, tuple[Path, StagedFile]] = {}
    removed: set[str] = set()
    for record in records:
        event = record.get("event")
        if event in {"sidecar_replace_started", "sidecar_created"}:
            item = by_target.get(str(record.get("target") or "").casefold())
            sidecar_text = str(record.get("sidecar") or "")
            if item is not None and sidecar_text:
                candidates[sidecar_text.casefold()] = (Path(sidecar_text), item)
        elif event in {
            "sidecar_removed_on_rollback",
            "sidecar_removed_after_baseline_failure",
        }:
            removed.add(
                str(record.get("path") or record.get("sidecar") or "").casefold()
            )
    return tuple(
        pair
        for key, pair in sorted(candidates.items())
        if key not in removed
    )


def _reconcile_live_states(stage: StageSet) -> dict[str, str]:
    """Снять exact source/staged состояние всех live-целей."""
    states: dict[str, str] = {}
    for item in _publish_order(stage):
        if fingerprint(item.staged_path) != item.staged_fingerprint:
            raise SafetyViolation(
                f"staging изменился; reconcile запрещён: {item.staged_path}"
            )
        if not item.original_path.is_file():
            raise SafetyViolation(
                f"live-цель исчезла; reconcile запрещён: {item.original_path}"
            )
        current = fingerprint(item.original_path)
        if current == item.staged_fingerprint:
            states[str(item.original_path)] = "new"
        elif current == item.source_fingerprint:
            states[str(item.original_path)] = "old"
        else:
            raise SafetyViolation(
                "live-цель не равна ни source, ни staging; ручная правка "
                f"не будет затёрта: {item.original_path}"
            )
    return states


def _assert_reconcile_live_state(
    stage: StageSet,
    expected: str,
) -> None:
    states = _reconcile_live_states(stage)
    wrong = [
        path
        for path, state in states.items()
        if state != expected
    ]
    if wrong:
        raise SafetyViolation(
            f"reconcile barrier ожидал {expected}, но состояние иное: "
            + ", ".join(wrong)
        )


def _assert_reconcile_final(
    stage: StageSet,
    manifest: dict[str, Any],
    sidecars: tuple[tuple[Path, StagedFile], ...],
    expected: str,
) -> None:
    """Последний live+baseline barrier непосредственно перед terminal."""
    _assert_reconcile_live_state(stage, expected)
    baseline_expected = "new" if expected == "new" else "old"
    actual_baseline = _baseline_state(manifest)
    if actual_baseline != baseline_expected:
        raise SafetyViolation(
            "baseline изменился перед terminal: "
            f"ожидался {baseline_expected}, получен {actual_baseline}"
        )
    leftovers = [str(path) for path, _item in sidecars if path.exists()]
    if leftovers:
        raise SafetyViolation(
            "sidecar появился/остался перед terminal: "
            + ", ".join(leftovers)
        )


def reconcile_incomplete_publication(
    bundle: ConfigBundle,
    run_dir: str | Path,
    *,
    mode: str = "auto",
    cycle_lock: CycleLock | None = None,
) -> dict[str, Any]:
    """Детерминированно свести аварийный publish к all-old либо all-new.

    ``auto`` выбирает all-new только когда КАЖДАЯ live-цель уже в точности равна
    staging; при смеси source/staged выбирает all-old. Любой третий fingerprint
    останавливает reconciliation до записи.
    """
    if cycle_lock is None:
        with publication_cycle_lock(
            bundle,
            allow_incomplete=True,
        ) as acquired:
            return reconcile_incomplete_publication(
                bundle,
                run_dir,
                mode=mode,
                cycle_lock=acquired,
            )
    cycle_lock.assert_owned()
    if mode not in {"auto", "all-old", "all-new"}:
        raise SafetyViolation(f"неизвестный режим reconcile: {mode}")
    run = Path(run_dir)
    journal_path = run / "publish.journal.jsonl"
    pending = {
        path.resolve()
        for path in incomplete_publish_journals(bundle.workspace_path())
    }
    if journal_path.resolve() not in pending:
        raise SafetyViolation(
            f"run не отмечен как незавершённая публикация: {run}"
        )
    journal_records, malformed_journal = _journal_records(journal_path)
    if malformed_journal:
        raise SafetyViolation(
            f"publish journal повреждён; автоматический reconcile запрещён: "
            f"{journal_path}"
        )
    starts = [
        index
        for index, record in enumerate(journal_records)
        if record.get("event") == "publish_started"
    ]
    if not starts:
        raise SafetyViolation("в publish journal нет publish_started")
    latest_start = journal_records[starts[-1]]
    manifest_path = run / "publish.recovery.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyViolation(
            f"recovery manifest не прочитан: {manifest_path}: {exc}"
        ) from exc
    if manifest.get("kind") != "registry-cycle-publish-recovery-v1":
        raise SafetyViolation("неизвестная версия publish recovery manifest")
    if (
        latest_start.get("run_id") != manifest.get("run_id")
        or latest_start.get("plan_digest") != manifest.get("plan_digest")
    ):
        raise SafetyViolation(
            "latest publish_started не совпадает с recovery manifest"
        )

    stage = load_stage(manifest["stage"])
    if (
        stage.run_id != manifest.get("run_id")
        or stage.plan_digest != manifest.get("plan_digest")
        or stage.run_dir.resolve() != run.resolve()
    ):
        raise SafetyViolation(
            "stage/recovery manifest относятся к разным publish run"
        )

    states = _reconcile_live_states(stage)

    baseline_state = _baseline_state(manifest)
    if baseline_state == "unknown":
        raise SafetyViolation(
            "active baseline не равен ни previous, ни candidate; reconcile запрещён"
        )
    sidecars = _recovery_sidecars(journal_path, stage)
    for sidecar, item in sidecars:
        if sidecar.exists() and fingerprint(sidecar) != item.staged_fingerprint:
            raise SafetyViolation(
                f"sidecar изменился; reconcile запрещён: {sidecar}"
            )

    selected = mode
    if selected == "auto":
        selected = (
            "all-new"
            if all(state == "new" for state in states.values())
            else "all-old"
        )
    if selected == "all-new" and any(
        state != "new"
        for state in states.values()
    ):
        raise SafetyViolation(
            "all-new разрешён только когда каждая live-цель точно равна staging"
        )

    journal = _Journal(journal_path)
    changed: list[str] = []
    if selected == "all-old":
        # Полный preflight source-копий до первого rollback replace.
        for item in _publish_order(stage):
            if fingerprint(item.source_copy) != item.source_fingerprint:
                raise SafetyViolation(
                    f"source backup не прошёл SHA-проверку: {item.source_copy}"
                )
        previous = manifest.get("previous_baseline")
        if previous is not None and not _matches_fingerprint_payload(
            Path(previous["path"]),
            previous["fingerprint"],
        ):
            raise SafetyViolation(
                "previous baseline backup не прошёл SHA-проверку"
            )
        for item in reversed(_publish_order(stage)):
            current_state = _reconcile_live_states(stage)[
                str(item.original_path)
            ]
            if current_state == "old":
                continue
            if current_state != "new":
                raise SafetyViolation(
                    f"цель сменилась перед all-old: {item.original_path}"
                )
            temporary = _temp_path(item, stage.run_id, "reconcile-old")
            stable_copy(item.source_copy, temporary)
            if fingerprint(temporary) != item.source_fingerprint:
                raise PublishError(
                    f"rollback temp не совпал с source: {item.original_path}"
                )
            # Exact recheck непосредственно перед destructive replace закрывает
            # окно между общей классификацией/копированием temp и записью.
            if _reconcile_live_states(stage)[
                str(item.original_path)
            ] != "new":
                temporary.unlink(missing_ok=True)
                raise SafetyViolation(
                    f"цель сменилась непосредственно перед all-old replace: "
                    f"{item.original_path}"
                )
            os.replace(temporary, item.original_path)
            if fingerprint(item.original_path) != item.source_fingerprint:
                raise PublishError(
                    f"all-old не прошёл SHA-проверку: {item.original_path}"
                )
            changed.append(str(item.original_path))
            journal.append(
                "target_reconciled_old",
                target=str(item.original_path),
            )
        # Все live уже exact-old до изменения baseline.
        _assert_reconcile_live_state(stage, "old")
        _restore_old_baseline(manifest, journal)
    else:
        # all-new не пишет live-файлы; поэтому повторная ПОЛНАЯ проверка всех
        # целей обязательна непосредственно перед активацией baseline.
        _assert_reconcile_live_state(stage, "new")
        _activate_new_baseline(manifest, journal)

    removed_sidecars: list[str] = []
    for sidecar, item in sidecars:
        if not sidecar.exists():
            continue
        if fingerprint(sidecar) != item.staged_fingerprint:
            raise PublishError(
                f"sidecar изменился во время reconcile: {sidecar}"
            )
        if fingerprint(sidecar) != item.staged_fingerprint:
            raise SafetyViolation(
                f"sidecar изменился непосредственно перед reconcile unlink: "
                f"{sidecar}"
            )
        sidecar.unlink()
        removed_sidecars.append(str(sidecar))
        journal.append("sidecar_removed_on_reconcile", path=str(sidecar))

    result = {
        "ok": True,
        "run_id": stage.run_id,
        "plan_digest": stage.plan_digest,
        "mode": selected,
        "changed": changed,
        "removed_sidecars": removed_sidecars,
        "baseline": str(manifest["target_baseline"]),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    pending_result = run / "reconcile.pending.json"
    _atomic_json(
        pending_result,
        {
            **result,
            "ok": False,
            "phase": "awaiting_final_barrier",
        },
    )
    _assert_reconcile_final(
        stage,
        manifest,
        sidecars,
        "old" if selected == "all-old" else "new",
    )
    journal.append(
        "reconciled_all_old" if selected == "all-old" else "reconciled_all_new",
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
    )
    _atomic_json(run / "reconcile.json", result)
    pending_result.unlink(missing_ok=True)
    return result
