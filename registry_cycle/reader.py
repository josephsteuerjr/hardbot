"""Чтение `.xlsb` только через python-calamine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import FieldConfig
from .errors import WorkbookReadError
from .files import fingerprint
from .models import WorkbookSnapshot
from .normalize import norm_title


def _trim_width(rows: list[list[Any]], header_row: int) -> int:
    if header_row >= len(rows):
        return 0
    header = rows[header_row]
    nonempty = [index for index, value in enumerate(header) if norm_title(value)]
    return nonempty[-1] + 1 if nonempty else 0


def _find_header(rows: list[list[Any]], fields: FieldConfig) -> int:
    # Колонки границы ищем по всем известным написаниям: переименование в общем
    # реестре («ответственный Сотрудник» → «…ХАРД») не должно останавливать
    # перенос личных реестров.
    key = fields.title_variants(fields.key_title)
    responsible = fields.title_variants(fields.responsible_title) | fields.title_variants(fields.soft_responsible_title)
    candidates: list[tuple[bool, int, int]] = []
    for index, row in enumerate(rows[: fields.header_search_rows]):
        titles = {norm_title(value) for value in row}
        candidates.append((bool(key & titles), int(bool(responsible & titles)),
                           index))
    with_key = [candidate for candidate in candidates if candidate[0]]
    if not with_key:
        raise WorkbookReadError(
            f"в первых {fields.header_search_rows} строках нет «{fields.key_title}»"
        )
    return max(with_key, key=lambda item: (item[1], -item[2]))[2]


def _validate_headers(headers: tuple[Any, ...], fields: FieldConfig) -> None:
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for index, value in enumerate(headers):
        title = norm_title(value)
        if not title:
            continue
        if title in seen:
            duplicates.append(str(value))
        else:
            seen[title] = index
    if duplicates:
        raise WorkbookReadError(
            "повторяющиеся заголовки: " + ", ".join(sorted(set(duplicates)))
        )
    if not ((fields.title_variants(fields.responsible_title) | fields.title_variants(fields.soft_responsible_title)) & set(seen)):
        raise WorkbookReadError(f"нет колонки «{fields.responsible_title}»")


def read_portfolio(path: str | Path, fields: FieldConfig) -> WorkbookSnapshot:
    """Прочитать локальную стабильную копию книги.

    Живые сетевые файлы до этого шага должен копировать SnapshotManager. Здесь
    есть дополнительная проверка stat до/после чтения, но намеренного Excel-lock
    функция не создаёт.
    """
    from python_calamine import CalamineWorkbook

    source = Path(path)
    if not source.is_file():
        raise WorkbookReadError(f"файл не найден: {source}")
    stat_before = source.stat()
    try:
        workbook = CalamineWorkbook.from_path(str(source))
        sheet_names = tuple(workbook.sheet_names)
        if fields.sheet_name not in sheet_names:
            raise WorkbookReadError(
                f"в {source.name} нет листа «{fields.sheet_name}»"
            )
        raw_rows = workbook.get_sheet_by_name(fields.sheet_name).to_python()
    except WorkbookReadError:
        raise
    except Exception as exc:  # noqa: BLE001 — библиотека поднимает разные типы
        raise WorkbookReadError(f"не прочитан {source}: {exc}") from exc
    stat_after = source.stat()
    if (stat_before.st_size, stat_before.st_mtime_ns) != (
        stat_after.st_size,
        stat_after.st_mtime_ns,
    ):
        raise WorkbookReadError(f"файл изменился во время чтения: {source}")
    if not raw_rows:
        raise WorkbookReadError(f"пустой лист «{fields.sheet_name}»: {source}")

    rows = [list(row) for row in raw_rows]
    header_row = _find_header(rows, fields)
    width = _trim_width(rows, header_row)
    if width == 0:
        raise WorkbookReadError(f"пустая строка заголовков в {source}")
    headers = tuple((rows[header_row] + [None] * width)[:width])
    _validate_headers(headers, fields)

    type_row = header_row + fields.type_row_offset
    if type_row >= len(rows):
        type_row_value: int | None = None
        type_tags = tuple(None for _ in range(width))
        data_start = header_row + 1
    else:
        type_row_value = type_row
        type_tags = tuple((rows[type_row] + [None] * width)[:width])
        data_start = type_row + 1

    data_rows: list[tuple[Any, ...]] = []
    for row in rows[data_start:]:
        trimmed = tuple((row + [None] * width)[:width])
        # Пустую строку внутри диапазона нельзя выбрасывать: её индекс нужен
        # для точных координат поклеточного diff и последующей записи Excel.
        data_rows.append(trimmed)

    return WorkbookSnapshot(
        path=source,
        sheet=fields.sheet_name,
        sheet_names=sheet_names,
        fingerprint=fingerprint(source),
        header_row=header_row,
        type_row=type_row_value,
        data_start=data_start,
        headers=headers,
        type_tags=type_tags,
        rows=tuple(data_rows),
    )
