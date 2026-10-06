"""Порядок показа накопленных заметок; исходный текст не изменяется."""
from __future__ import annotations

import re
from datetime import datetime

_HEADER = re.compile(r"^(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}) · [^\n:]+: ", re.M)


def newest_first(text: str | None) -> list[str]:
    """Разворачивать записи целиком, а не строки многострочного комментария.

    Понимаем только подписанные заметки приложения. Текст офиса до первой
    такой заметки остаётся одним прежним блоком в конце. Уже отсортированный
    текст не переворачивается обратно; одинаковое время — последняя запись.
    """
    source = str(text or "")
    starts = []
    for match in _HEADER.finditer(source):
        try:
            stamp = datetime.strptime(match[1], "%d.%m.%Y %H:%M")
        except ValueError:
            continue
        starts.append((match.start(), stamp))
    if not starts:
        return [source] if source.strip() else []
    entries = []
    for i, (offset, stamp) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(source)
        entries.append((stamp, i, source[offset:end].rstrip("\r\n")))
    entries.sort(key=lambda e: (e[0], e[1]), reverse=True)
    result = [e[2] for e in entries]
    before = source[:starts[0][0]].rstrip("\r\n")
    if before.strip():
        result.append(before)
    return result
