"""Контракт делегированного закрытия только точных SMB-дескрипторов."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ConfigBundle
from .errors import SafetyViolation
from .file_access import BusyFile, busy_files
from .files import fingerprint


@dataclass(frozen=True, slots=True)
class ForceCloseResult:
    requested: tuple[Path, ...]
    manifest_path: Path
    command_returncode: int
    remaining: tuple[BusyFile, ...]

    @property
    def ok(self) -> bool:
        return self.command_returncode == 0 and not self.remaining


class DelegatedForceCloser:
    """Запустить заранее установленный least-privilege helper без shell."""

    def __init__(self, bundle: ConfigBundle):
        self.bundle = bundle
        raw = bundle.app.safety.get("force_close") or {}
        self.config: dict[str, Any] = dict(raw)

    @property
    def unavailable_reason(self) -> str | None:
        if not bool(self.config.get("enabled", False)):
            return "делегированный серверный механизм отключён"
        if not str(self.config.get("command", "")).strip():
            return "не настроена команда делегированного механизма"
        policy = str(
            self.config.get("unsaved_changes_policy", "block")
        )
        if policy == "block":
            return (
                "не выбрана явная политика несохранённых изменений "
                "(save_then_close или discard)"
            )
        if policy not in {"save_then_close", "discard"}:
            return "неизвестная политика несохранённых изменений"
        return None

    def close(
        self,
        busy: tuple[BusyFile, ...],
        *,
        audit_dir: Path,
    ) -> ForceCloseResult:
        reason = self.unavailable_reason
        if reason:
            raise SafetyViolation(f"force_close недоступен: {reason}")
        if not busy:
            raise SafetyViolation("force_close вызван без занятых файлов")

        audit_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        manifest_path = audit_dir / f"force-close-{stamp}.json"
        records = []
        for item in busy:
            try:
                before = fingerprint(item.path).as_dict()
            except Exception as exc:  # noqa: BLE001
                raise SafetyViolation(
                    f"не снят fingerprint перед force_close: {item.path}: {exc}"
                ) from exc
            records.append(
                {
                    **item.as_dict(),
                    "fingerprint_before": before,
                }
            )
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "unsaved_changes_policy": self.config[
                "unsaved_changes_policy"
            ],
            "allowed_paths": [str(item.path) for item in busy],
            "files": records,
        }
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(manifest_path)

        command = str(self.config["command"])
        arguments = [
            str(value).replace("{manifest}", str(manifest_path))
            for value in self.config.get(
                "arguments",
                ["--manifest", "{manifest}"],
            )
        ]
        timeout_seconds = max(
            5,
            min(int(self.config.get("timeout_seconds", 60)), 300),
        )
        result = subprocess.run(
            [command, *arguments],
            cwd=str(self.bundle.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log_path = manifest_path.with_suffix(".result.json")
        log_path.write_text(
            json.dumps(
                {
                    "returncode": result.returncode,
                    "stdout": result.stdout[-20000:],
                    "stderr": result.stderr[-20000:],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise SafetyViolation(
                "делегированный force_close завершился ошибкой; "
                f"см. {log_path}"
            )

        grace = max(
            0,
            min(int(self.config.get("grace_seconds", 15)), 60),
        )
        deadline = time.monotonic() + grace
        remaining = busy_files(tuple(item.path for item in busy))
        while remaining and time.monotonic() < deadline:
            time.sleep(0.5)
            remaining = busy_files(tuple(item.path for item in busy))
        outcome = ForceCloseResult(
            requested=tuple(item.path for item in busy),
            manifest_path=manifest_path,
            command_returncode=result.returncode,
            remaining=remaining,
        )
        if remaining:
            raise SafetyViolation(
                "force_close не освободил все точные цели: "
                + ", ".join(str(item.path) for item in remaining)
            )
        return outcome
