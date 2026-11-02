"""Проверка Excel lock-файлов и эксклюзивного доступа к live-книгам."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import SafetyViolation


@dataclass(frozen=True, slots=True)
class BusyFile:
    path: Path
    reason: str
    lock_path: Path | None = None
    kind: str = 'locked'

    def as_dict(self) -> dict[str, str | None]:
        return {
            "path": str(self.path),
            "reason": self.reason,
            "lock_path": str(self.lock_path) if self.lock_path else None,
            "kind":self.kind,
        }


def excel_lock_path(path: str | Path) -> Path:
    source = Path(path)
    return source.with_name("~$" + source.name)


def exclusive_file_check(path: str | Path) -> None:
    """Проверить, что Windows разрешит безопасную замену файла."""
    source = Path(path)
    if os.name != "nt":
        return
    import pywintypes
    import win32con
    import win32file

    try:
        handle = win32file.CreateFile(
            str(source),
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            0,
            None,
            win32con.OPEN_EXISTING,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None,
        )
    except pywintypes.error as exc:
        raise SafetyViolation(
            f"файл занят или недоступен: {source} ({exc})"
        ) from exc
    else:
        handle.Close()


def inspect_busy_file(path: str | Path) -> BusyFile | None:
    source = Path(path)
    lock = excel_lock_path(source)
    if lock.exists():
        return BusyFile(
            source,
            "обнаружен Excel lock-файл",
            lock,
        )
    try:
        exclusive_file_check(source)
    except Exception as exc:  # noqa: BLE001 — причина нужна в preflight
        cause=exc.__cause__ or exc
        code=getattr(cause,'winerror',None)
        if code is None and getattr(cause,'args',None) and isinstance(cause.args[0],int):code=cause.args[0]
        kind='locked' if code in (32,33) else 'missing' if code in (2,3) else 'access' if code==5 else 'unavailable'
        return BusyFile(source, str(exc),kind=kind)
    return None


def busy_files(paths: list[Path] | tuple[Path, ...]) -> tuple[BusyFile, ...]:
    found = []
    for path in paths:
        busy = inspect_busy_file(path)
        if busy is not None:
            found.append(busy)
    return tuple(found)
