"""Central publication receipts for the two manual registry operations."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import SafetyViolation
from .files import fingerprint
from .publishing import PublishResult
from .staging import StageSet, StagedFile


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(left.absolute()))) == os.path.normcase(
        os.path.normpath(str(right.absolute()))
    )


def ensure_archive_directory(
    path: str | Path,
    *,
    live_targets: Iterable[str | Path] = (),
) -> Path:
    """Create/check a central archive and reject a live-file sibling folder."""
    directory = Path(path)
    for target in live_targets:
        if _same_path(directory, Path(target).parent):
            raise SafetyViolation(
                "резервные копии нельзя хранить рядом с живым реестром: "
                f"{directory}"
            )
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SafetyViolation(
            f"архивная папка недоступна: {directory}: {exc}"
        ) from exc
    if not directory.is_dir():
        raise SafetyViolation(f"архивная папка недоступна: {directory}")
    return directory


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_publication_receipt(
    *,
    stage: StageSet,
    result: PublishResult,
    files: tuple[StagedFile, ...],
    receipt_directory: str | Path,
    operation: str,
    context_by_entry: Mapping[str, Mapping[str, Any]] | None = None,
    summary: Mapping[str, Any] | None = None,
) -> Path:
    """Write the same complete receipt locally and into the central archive."""
    if not result.complete:
        raise SafetyViolation("квитанцию можно создать только для полного publish")
    if result.sidecars:
        raise SafetyViolation("квитанция не допускает соседние sidecar-копии")
    if len(result.published) != len(files) or len(result.backups) != len(files):
        raise SafetyViolation(
            "число опубликованных файлов, backup-копий и целей не совпадает"
        )
    contexts = context_by_entry or {}
    records: list[dict[str, Any]] = []
    for item, published_text, backup_text in zip(
        files,
        result.published,
        result.backups,
        strict=True,
    ):
        published = Path(published_text)
        backup = Path(backup_text)
        if not _same_path(published, item.original_path):
            raise SafetyViolation(
                "порядок целей в результате публикации не совпал со staging"
            )
        live_fingerprint = fingerprint(published)
        backup_fingerprint = fingerprint(backup)
        if live_fingerprint != item.staged_fingerprint:
            raise SafetyViolation(
                f"квитанция: live не совпал со staging: {published}"
            )
        if backup_fingerprint != item.source_fingerprint:
            raise SafetyViolation(
                f"квитанция: backup не совпал с исходником: {backup}"
            )
        context = dict(contexts.get(item.entry_id or "master", {}))
        records.append(
            {
                **context,
                "kind": item.kind,
                "entry_id": item.entry_id,
                "file": item.original_path.name,
                "path": str(item.original_path),
                "backup_path": str(backup),
                "backup_sha256": backup_fingerprint.sha256,
                "pre_sha256": item.source_fingerprint.sha256,
                "post_sha256": live_fingerprint.sha256,
                "error": None,
                "written_unverified": False,
                "not_attempted": False,
            }
        )

    finished_at = result.finished_at or datetime.now().isoformat(timespec="microseconds")
    payload = {
        "at": finished_at,
        "operation": operation,
        "run_id": result.run_id,
        "plan_digest": result.plan_digest,
        "session_error": None,
        "summary": dict(summary or {}),
        "files": records,
    }
    archive = ensure_archive_directory(
        receipt_directory,
        live_targets=(item.original_path for item in files),
    )
    stamp = datetime.fromisoformat(finished_at).strftime("%Y%m%dT%H%M%S")
    central_path = archive / f"{operation}-{stamp}-{result.run_id}.json"
    local_path = stage.run_dir / "receipt.json"
    try:
        _atomic_json(local_path, payload)
        _atomic_json(central_path, payload)
    except Exception:
        local_path.unlink(missing_ok=True)
        central_path.unlink(missing_ok=True)
        raise
    return central_path
