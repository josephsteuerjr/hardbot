"""Межпроцессный lock одного цикла."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable

from .errors import SafetyViolation


@contextmanager
def _exclusive_guard(path: Path):
    """Короткий OS-lock сериализует acquire/reclaim/release.

    Один лишь ``exists → unlink stale → O_EXCL`` имеет ABA-гонку: второй
    reclaimer способен удалить уже новый lock первого. Guard освобождается ОС
    при падении процесса; сам файл намеренно постоянный и данных не защищает.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(
                    stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except (OSError, BlockingIOError) as exc:
            raise SafetyViolation(
                f"другой процесс меняет cycle lock: {path}"
            ) from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def _lock_token(payload: bytes) -> str | None:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    token = parsed.get("owner_token") if isinstance(parsed, dict) else None
    return str(token) if token else None


class CycleLock:
    def __init__(
        self,
        path: str | Path,
        *,
        stale_seconds: int,
        before_stale_remove: Callable[[], None] | None = None,
    ):
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self.before_stale_remove = before_stale_remove
        self.acquired = False
        self.owner_token = secrets.token_hex(16)
        self.reclaimed_tombstone: Path | None = None

    @property
    def guard_path(self) -> Path:
        return self.path.with_name(f".{self.path.name}.guard")

    def _create_owned_lock(self) -> None:
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as exc:
            raise SafetyViolation(
                f"другой цикл уже захватил lock: {self.path}"
            ) from exc
        payload = {
            "owner_token": self.owner_token,
            "pid": os.getpid(),
            "started": datetime.now().isoformat(timespec="seconds"),
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def __enter__(self) -> "CycleLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _exclusive_guard(self.guard_path):
            if self.path.exists():
                observed = self.path.read_bytes()
                observed_stat = self.path.stat()
                age = time.time() - observed_stat.st_mtime
                if age < self.stale_seconds:
                    raise SafetyViolation(
                        f"другой цикл уже выполняется: {self.path}"
                    )
                if self.before_stale_remove is not None:
                    self.before_stale_remove()
                # Callback мог занять время; перед атомарным claim доказываем,
                # что перед нами всё ещё тот же stale owner.
                if (
                    not self.path.is_file()
                    or self.path.read_bytes() != observed
                    or self.path.stat().st_mtime_ns
                    != observed_stat.st_mtime_ns
                ):
                    raise SafetyViolation(
                        f"cycle lock сменил владельца перед reclaim: {self.path}"
                    )
                old_token = (
                    _lock_token(observed)
                    or hashlib.sha256(observed).hexdigest()[:16]
                )
                tombstone = self.path.with_name(
                    f"{self.path.name}.stale.{old_token}."
                    f"{self.owner_token[:12]}"
                )
                os.replace(self.path, tombstone)
                if tombstone.read_bytes() != observed:
                    raise SafetyViolation(
                        f"stale tombstone не совпал с owner lock: {tombstone}"
                    )
                self.reclaimed_tombstone = tombstone
            self._create_owned_lock()
        self.acquired = True
        return self

    def assert_owned(self) -> None:
        if not self.acquired:
            raise SafetyViolation("cycle lock не захвачен этим entrypoint")
        with _exclusive_guard(self.guard_path):
            try:
                current = _lock_token(self.path.read_bytes())
            except OSError as exc:
                raise SafetyViolation(
                    f"cycle lock исчез: {self.path}"
                ) from exc
            if current != self.owner_token:
                raise SafetyViolation(
                    f"cycle lock сменил владельца: {self.path}"
                )

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self.acquired:
            return
        mismatch = False
        try:
            with _exclusive_guard(self.guard_path):
                try:
                    current = _lock_token(self.path.read_bytes())
                except OSError:
                    current = None
                if current == self.owner_token:
                    self.path.unlink()
                else:
                    # Старый владелец никогда не удаляет lock нового процесса.
                    mismatch = True
        finally:
            self.acquired = False
        if mismatch and exc_type is None:
            raise SafetyViolation(
                f"cycle lock уже принадлежит другому owner: {self.path}"
            )
