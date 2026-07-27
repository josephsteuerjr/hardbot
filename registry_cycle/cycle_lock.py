"""Межпроцессный lock одного цикла."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from .errors import SafetyViolation


class CycleLock:
    def __init__(self, path: str | Path, *, stale_seconds: int):
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self.acquired = False

    def __enter__(self) -> "CycleLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = time.time() - self.path.stat().st_mtime
            if age < self.stale_seconds:
                raise SafetyViolation(
                    f"другой цикл уже выполняется: {self.path}"
                )
            self.path.unlink()
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as exc:
            raise SafetyViolation(
                f"другой цикл уже захватил lock: {self.path}"
            ) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(
                f"pid={os.getpid()}\n"
                f"started={datetime.now().isoformat(timespec='seconds')}\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False
