"""Narrow retry policy for a lost local Excel COM session."""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def is_transient_excel_com_error(error: BaseException) -> bool:
    """True only for pywin32/xlwings COM transport errors, never business errors."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        cls = type(current)
        module = cls.__module__.casefold()
        name = cls.__name__.casefold()
        if name == "com_error" and (
            "pywintypes" in module
            or "win32com" in module
            or "xlwings" in module
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def retry_local_excel(
    action: Callable[[int], T],
    *,
    attempts: int,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Retry with a fresh caller-created Excel instance after COM loss only."""
    if attempts < 1:
        raise ValueError("attempts должен быть >= 1")
    for attempt in range(1, attempts + 1):
        try:
            return action(attempt)
        except Exception as exc:
            if attempt >= attempts or not is_transient_excel_com_error(exc):
                raise
            if on_retry is not None:
                on_retry(attempt + 1, exc)
    raise AssertionError("unreachable")
