"""Файловые отпечатки и стабильные копии без открытия Excel."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .errors import SafetyViolation
from .models import FileFingerprint


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: str | Path) -> FileFingerprint:
    source = Path(path)
    stat = source.stat()
    return FileFingerprint(
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=sha256_file(source),
    )


def stable_copy(source: str | Path, destination: str | Path) -> FileFingerprint:
    """Скопировать файл и доказать, что источник не менялся в процессе.

    Возвращается отпечаток исходника, который позже служит optimistic-lock перед
    публикацией. Назначение всегда локальное/staging; источник не изменяется.
    """
    source_path = Path(source)
    destination_path = Path(destination)
    before = source_path.stat()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)
    after = source_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        destination_path.unlink(missing_ok=True)
        raise SafetyViolation(f"файл изменился во время копирования: {source_path}")

    source_hash = sha256_file(source_path)
    destination_hash = sha256_file(destination_path)
    if source_hash != destination_hash:
        destination_path.unlink(missing_ok=True)
        raise SafetyViolation(f"SHA-256 копии не совпал с источником: {source_path}")
    return FileFingerprint(
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
        sha256=source_hash,
    )
