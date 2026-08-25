"""Владелец книги берётся только из самой книги.

Прежняя версия сверяла ответственного с именем папки и файла и разрешала
подставить владельца из подсказки манифеста. Это убрано намеренно: имя
папки и файла больше не участвует в определении владельца, поэтому
переименованная или переложенная книга не может увести чужие строки.
"""

from pathlib import Path

from registry_cycle.models import FileFingerprint, WorkbookSnapshot
from registry_cycle.ownership import (
    canonical_responsible_name,
    resolve_book_responsible,
    workbook_data_is_empty,
)

RESPONSIBLE = "ответственный Сотрудник"


def _book(*responsibles: str) -> WorkbookSnapshot:
    return WorkbookSnapshot(
        path=Path("СОФТ Турсунов Улугбек-") / "Иванов Иван.xlsb",
        sheet="Портфели",
        sheet_names=("Портфели",),
        fingerprint=FileFingerprint(size=1, mtime_ns=1, sha256="0" * 64),
        header_row=2,
        type_row=3,
        data_start=4,
        headers=("Номер КД / ID", RESPONSIBLE),
        type_tags=("сх", "сх"),
        rows=tuple(("100000001", name) for name in responsibles),
    )


def test_owner_is_read_from_the_book_not_from_the_path():
    # Папка называется одним человеком, книга — другим: побеждает книга.
    assert resolve_book_responsible(_book("Уткиров Фирдавс"), RESPONSIBLE) == "Уткиров Фирдавс"


def test_two_owners_in_one_book_is_not_resolved():
    book = _book("Уткиров Фирдавс", "Исмоилов Хожиакбар")
    assert resolve_book_responsible(book, RESPONSIBLE) is None


def test_book_without_owner_is_not_guessed():
    assert resolve_book_responsible(_book(), RESPONSIBLE) is None
    assert resolve_book_responsible(_book(""), RESPONSIBLE) is None


def test_missing_responsible_column_is_not_resolved():
    book = WorkbookSnapshot(
        path=Path("Иванов Иван.xlsb"),
        sheet="Портфели",
        sheet_names=("Портфели",),
        fingerprint=FileFingerprint(size=1, mtime_ns=1, sha256="0" * 64),
        header_row=2,
        type_row=3,
        data_start=4,
        headers=("Номер КД / ID",),
        type_tags=("сх",),
        rows=(("100000001",),),
    )
    assert resolve_book_responsible(book, RESPONSIBLE) is None


def test_empty_book_is_recognised_as_empty():
    assert workbook_data_is_empty(_book())
    assert not workbook_data_is_empty(_book("Уткиров Фирдавс"))


def test_alias_is_applied_without_looping():
    aliases = {"Уткиров Фирдавс": "Уткиров Ф.", "Уткиров Ф.": "Уткиров Фирдавс"}
    # Цикл в алиасах не должен подвешивать нормализацию.
    assert canonical_responsible_name("Уткиров Фирдавс", aliases)
    assert canonical_responsible_name("Исмоилов Хожиакбар", {}) == canonical_responsible_name(
        " исмоилов   хожиакбар ", {}
    )
