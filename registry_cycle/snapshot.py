"""Стабильный локальный снимок всех источников одного запуска."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .baseline import baseline_path
from .config import ConfigBundle
from .errors import SafetyViolation
from .files import stable_copy
from .models import DiscoveryReport, FileFingerprint, ManifestEntry


@dataclass(frozen=True, slots=True)
class CapturedPersonal:
    entry: ManifestEntry
    copy_path: Path
    fingerprint: FileFingerprint

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry.as_dict(),
            "copy_path": str(self.copy_path),
            "fingerprint": self.fingerprint.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class SnapshotSet:
    run_id: str
    run_dir: Path
    master_original: Path
    master_copy: Path
    master_fingerprint: FileFingerprint
    personals: tuple[CapturedPersonal, ...]
    baseline_copy: Path | None = None
    baseline_fingerprint: FileFingerprint | None = None
    config_identity: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "config_identity": self.config_identity,
            "master": {
                "original": str(self.master_original),
                "copy": str(self.master_copy),
                "fingerprint": self.master_fingerprint.as_dict(),
            },
            "personals": [personal.as_dict() for personal in self.personals],
            "baseline": (
                {
                    "copy": str(self.baseline_copy),
                    "fingerprint": self.baseline_fingerprint.as_dict(),
                }
                if self.baseline_copy is not None
                and self.baseline_fingerprint is not None
                else None
            ),
        }


def new_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _assert_run_dir_under_workspace(
    bundle: ConfigBundle,
    run_dir: Path,
    *,
    at: datetime | None = None,
) -> None:
    workspace = (
        bundle.workspace_path(at)
        if getattr(bundle, "app", None) is not None
        else Path(bundle.project_root) / ".registry-cycle"
    )
    runs_root = (workspace / "runs").resolve()
    resolved_run_dir = run_dir.resolve()
    try:
        resolved_run_dir.relative_to(runs_root)
    except ValueError as exc:
        raise SafetyViolation(
            "run_dir snapshot находится вне workspace/runs: "
            f"{run_dir}"
        ) from exc
    if resolved_run_dir == runs_root:
        raise SafetyViolation(
            "run_dir snapshot должен быть отдельной папкой внутри "
            f"workspace/runs: {run_dir}"
        )


def capture_sources(
    bundle: ConfigBundle,
    discovery: DiscoveryReport,
    *,
    run_id: str | None = None,
    now: datetime | None = None,
) -> SnapshotSet:
    """Скопировать мастер и все manifest-файлы в `_local`.

    Любая ошибка останавливает захват; частичная папка намеренно остаётся как
    доказательство, но не может быть использована для планирования.
    """
    if discovery.blocked:
        raise SafetyViolation("discovery заблокирован; снимок не создаётся")
    run_id = run_id or new_run_id(now)
    run_dir = bundle.workspace_path(now) / "runs" / run_id
    _assert_run_dir_under_workspace(bundle, run_dir, at=now)
    source_dir = run_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=False)

    master_original = bundle.master_path(now)
    if not master_original.is_file():
        raise SafetyViolation(f"общий реестр не найден: {master_original}")
    master_copy = source_dir / f"master{master_original.suffix.casefold()}"
    master_fingerprint = stable_copy(master_original, master_copy)
    current_baseline = baseline_path(bundle, master_original)
    baseline_copy: Path | None = None
    baseline_fingerprint: FileFingerprint | None = None
    if current_baseline.is_file():
        baseline_copy = source_dir / "baseline.json.gz"
        baseline_fingerprint = stable_copy(current_baseline, baseline_copy)

    personals: list[CapturedPersonal] = []
    seen_paths: set[str] = set()
    for entry in discovery.entries:
        folded = str(entry.path).casefold()
        if folded in seen_paths:
            raise SafetyViolation(f"один файл дважды попал в manifest: {entry.path}")
        seen_paths.add(folded)
        destination = source_dir / f"{entry.entry_id}{entry.path.suffix.casefold()}"
        source_fingerprint = stable_copy(entry.path, destination)
        personals.append(
            CapturedPersonal(
                entry=entry,
                copy_path=destination,
                fingerprint=source_fingerprint,
            )
        )

    snapshot = SnapshotSet(
        run_id=run_id,
        run_dir=run_dir,
        master_original=master_original,
        master_copy=master_copy,
        master_fingerprint=master_fingerprint,
        personals=tuple(personals),
        baseline_copy=baseline_copy,
        baseline_fingerprint=baseline_fingerprint,
        config_identity=bundle.config_identity,
    )
    _atomic_json(run_dir / "snapshot.json", snapshot.as_dict())
    return snapshot


def load_snapshot(path: str | Path) -> SnapshotSet:
    """Открыть ранее зафиксированный набор копий для повторного анализа."""
    snapshot_path = Path(path)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    master = payload["master"]
    personals = []
    for item in payload["personals"]:
        raw_entry = item["entry"]
        raw_fingerprint = item["fingerprint"]
        personals.append(
            CapturedPersonal(
                entry=ManifestEntry(
                    entry_id=str(raw_entry["entry_id"]),
                    path=Path(raw_entry["path"]),
                    country=str(raw_entry["country"]),
                    group=str(raw_entry["group"]),
                    profile=str(raw_entry["profile"]),
                    employee_folder=str(raw_entry["employee_folder"]),
                    responsible_hint=str(raw_entry["responsible_hint"]),
                ),
                copy_path=Path(item["copy_path"]),
                fingerprint=FileFingerprint(
                    size=int(raw_fingerprint["size"]),
                    mtime_ns=int(raw_fingerprint["mtime_ns"]),
                    sha256=str(raw_fingerprint["sha256"]),
                ),
            )
        )
    master_fingerprint = master["fingerprint"]
    raw_baseline = payload.get("baseline")
    baseline_copy = (
        Path(raw_baseline["copy"])
        if raw_baseline
        else None
    )
    baseline_fingerprint = (
        FileFingerprint(
            size=int(raw_baseline["fingerprint"]["size"]),
            mtime_ns=int(raw_baseline["fingerprint"]["mtime_ns"]),
            sha256=str(raw_baseline["fingerprint"]["sha256"]),
        )
        if raw_baseline
        else None
    )
    snapshot = SnapshotSet(
        run_id=str(payload["run_id"]),
        run_dir=Path(payload["run_dir"]),
        master_original=Path(master["original"]),
        master_copy=Path(master["copy"]),
        master_fingerprint=FileFingerprint(
            size=int(master_fingerprint["size"]),
            mtime_ns=int(master_fingerprint["mtime_ns"]),
            sha256=str(master_fingerprint["sha256"]),
        ),
        personals=tuple(personals),
        baseline_copy=baseline_copy,
        baseline_fingerprint=baseline_fingerprint,
        config_identity=str(payload.get("config_identity") or ""),
    )
    missing = [
        str(item)
        for item in [
            snapshot.master_copy,
            *(p.copy_path for p in snapshot.personals),
            *([snapshot.baseline_copy] if snapshot.baseline_copy is not None else []),
        ]
        if not item.is_file()
    ]
    if missing:
        raise SafetyViolation(
            "снимок неполон; отсутствуют копии: " + ", ".join(missing[:10])
        )
    return snapshot


def assert_snapshot_config_identity(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
) -> None:
    expected = str(getattr(bundle, "config_identity", "") or "")
    if snapshot.config_identity != expected:
        raise SafetyViolation(
            "snapshot создан с другой конфигурацией; повторный запуск "
            "после config drift запрещён"
        )


def assert_snapshot_run_dir(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
) -> None:
    _assert_run_dir_under_workspace(bundle, snapshot.run_dir)
