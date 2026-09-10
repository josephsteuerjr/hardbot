"""Занятая книга: отказ до копии и до первой записи.

10.09.2026. Обогащение Бозорова упало с «backup/pre/post contract нарушен».
Разбор: книга была открыта у сотрудника с 10:56, Excel отдал её пульту только
для чтения, 66 ячеек «записались» в память, а `wb.save()` при подавленных
диалогах молча не сделал ничего — файл на диске остался прежним. Контракт
pre/post это поймал (данные целы, реестр доставленного не засеян), но поймал
уже ПОСЛЕ копии и записи, а сообщение не называло ни причину, ни виновника.

Здесь закреплено три вещи: занятую книгу писатель не трогает вообще, режим
«только для чтения» — тоже отказ, а если молчаливое несохранение всё же
случится, сообщение назовёт именно этот инвариант.
"""

import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import enrich  # noqa: E402


FIELDS = [
    {"key": "contract_id", "title": "Номер КД / ID"},
    {"key": "visit_date", "title": "Дата выезда", "date": True},
    {"key": "call_status", "title": "Статус звонка"},
    {"key": "overall_result", "title": "Общий результат"},
    {"key": "visit_comment", "title": "Комментарий текущего месяца выезда"},
    {"key": "extra_address", "title": "Доп. Адрес/Доп.Номер"},
    {"key": "contact_with", "title": "С кем был контакт?"},
    {"key": "agreed_payment", "title": "Договорились на оплату?"},
    {"key": "who_at_address", "title": "Кто был на адресе?"},
    {"key": "client_location", "title": "Местоположение клиента"},
    {"key": "purpose_rf", "title": "Цель приезда в РФ"},
]
TITLES = [f["title"] for f in FIELDS]
ROWS = [{"contract_id": "A", "visit_date": "2026-09-10",
         "call_status": "Оплачен", enrich.FILLED_MARKER: True}]


# --- поддельный Excel --------------------------------------------------------
# Ровно та поверхность COM, которой пользуется писатель: лист по имени,
# used_range, чтение блоком и запись ячейкой. Плюс два переключателя, которые и
# воспроизводят аварию: книга «только для чтения» и сохранение «в никуда».

class _Sheets(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            for sheet in self:
                if sheet.name == key:
                    return sheet
            raise KeyError(key)
        return list.__getitem__(self, key)


class _Range:
    def __init__(self, sheet, top_left, bottom_right=None):
        self.sheet, self.top_left, self.bottom_right = sheet, top_left, bottom_right

    @property
    def value(self):
        r1, c1 = self.top_left
        if self.bottom_right is None:
            return self.sheet.grid[r1 - 1][c1 - 1]
        r2, c2 = self.bottom_right
        return [row[c1 - 1:c2] for row in self.sheet.grid[r1 - 1:r2]]

    @value.setter
    def value(self, new):
        r1, c1 = self.top_left
        self.sheet.grid[r1 - 1][c1 - 1] = new


class _Sheet:
    def __init__(self, name, grid):
        self.name, self.grid = name, grid

    @property
    def used_range(self):
        last = types.SimpleNamespace(row=len(self.grid),
                                     column=max(len(r) for r in self.grid))
        return types.SimpleNamespace(last_cell=last)

    def range(self, top_left, bottom_right=None):
        return _Range(self, top_left, bottom_right)


class _Book:
    def __init__(self, app, path):
        self.app, self.path = app, Path(path)
        self.sheets = _Sheets([_Sheet(enrich.SHEET_NAME, app.grid)])
        self.api = types.SimpleNamespace(ReadOnly=app.read_only)
        self.closed = False

    def save(self):
        self.app.saves += 1
        if self.app.saving:
            # Настоящий Excel переписывает файл целиком; нам важно лишь то,
            # что байты на диске изменились.
            self.path.write_bytes(repr(self.app.grid).encode("utf-8"))

    def close(self):
        self.closed = True


class _App:
    def __init__(self, grid, *, read_only=False, saving=True):
        self.grid, self.read_only, self.saving = grid, read_only, saving
        self.saves = 0
        self.opened: list[Path] = []
        self.books = types.SimpleNamespace(open=self._open)

    def _open(self, path, **_kw):
        self.opened.append(Path(path))
        return _Book(self, path)


def _grid():
    return [list(TITLES), ["A", None, None, None, None, None, None, None, None,
                           None, None]]


def _book(tmp_path: Path) -> Path:
    """Живой файл в маске папок сотрудника: <город>/<ФИО>/<ФИО>.xlsb."""
    folder = tmp_path / "Худжанд" / "Бозоров Мухриддин"
    folder.mkdir(parents=True)
    path = folder / "Бозоров Мухриддин.xlsb"
    path.write_bytes(b"\x00livebook")
    return path


def _owner_blob(name: str) -> bytes:
    """Служебный файл Excel: длина + ANSI-имя до 54 байт, длина + UTF-16-имя."""
    ansi = name.encode("cp1251", "replace")
    return (bytes([len(ansi)]) + ansi.ljust(enrich._LOCK_ANSI_FIELD, b" ")
            + len(name).to_bytes(2, "little") + name.encode("utf-16-le"))


# --- 1. Книга открыта у другого человека -------------------------------------

def test_read_only_book_is_refused_before_the_backup_and_the_first_write(tmp_path):
    """Случай Бозорова: до копии и до записи дело не доходит вообще."""
    path, root = _book(tmp_path), tmp_path / "копии"
    before = path.read_bytes()
    app = _App(_grid(), read_only=True)

    with pytest.raises(RuntimeError, match="книга занята"):
        enrich._enrich_with_evidence(app, path, FIELDS, ROWS, backup_root=root)

    assert not root.exists(), "копия занятой книги — мусор: писать было некуда"
    assert path.read_bytes() == before
    assert app.saves == 0
    assert app.grid[1][2] is None, "в занятую книгу писали в память"


def test_the_refusal_names_who_holds_the_book(tmp_path):
    path = _book(tmp_path)
    enrich.excel_owner_file(path).write_bytes(_owner_blob("Аличон Махмадиев"))
    app = _App(_grid(), read_only=True)

    with pytest.raises(RuntimeError, match="Аличон Махмадиев"):
        enrich._enrich_with_evidence(app, path, FIELDS, ROWS)


# --- 2. Молчаливое несохранение ----------------------------------------------

def test_silent_non_save_names_the_broken_invariant(tmp_path):
    """Последний рубеж: если Excel всё же не сохранил — сообщение говорит это.

    Раньше здесь было глухое «backup/pre/post contract нарушен», по которому
    неотличимы «копии нет», «копия не от этого прогона» и «файл не изменился».
    """
    path, root = _book(tmp_path), tmp_path / "копии"
    before = path.read_bytes()
    app = _App(_grid(), saving=False)

    with pytest.raises(RuntimeError, match="файл на диске не изменился"):
        enrich._enrich_with_evidence(app, path, FIELDS, ROWS, backup_root=root)

    assert path.read_bytes() == before
    copies = list(root.rglob("*.xlsb"))
    assert len(copies) == 1 and copies[0].read_bytes() == before


def test_a_free_book_is_still_written_and_verified(tmp_path):
    """Контроль: новая дверь не мешает обычному прогону."""
    path, root = _book(tmp_path), tmp_path / "копии"
    before = path.read_bytes()
    app = _App(_grid())

    plan = enrich._enrich_with_evidence(app, path, FIELDS, ROWS, backup_root=root)

    assert plan["cells_written"] > 0
    assert plan["pre_sha256"] != plan["post_sha256"]
    assert plan["post_plan"]["cells_written"] == 0
    assert Path(plan["backup_path"]).read_bytes() == before


# --- 3. Кто держит книгу -----------------------------------------------------

def test_owner_name_survives_cyrillic():
    assert enrich.parse_lock_owner(_owner_blob("Аличон Махмадиев")) == \
        "Аличон Махмадиев"
    assert enrich.parse_lock_owner(_owner_blob("Alichon Makhmadiev")) == \
        "Alichon Makhmadiev"
    assert enrich.parse_lock_owner(b"") == ""
    assert enrich.parse_lock_owner(b"\x05abc") == ""      # обрезанный файл


def test_a_free_book_has_no_owner(tmp_path):
    path = _book(tmp_path)
    assert enrich.workbook_busy_owner(path) is None
    enrich.assert_writable(path)                          # не бросает


def test_a_read_only_file_is_not_reported_as_someone_elses_book(tmp_path):
    """«Только для чтения» лечится правами, а не ожиданием — так и скажем."""
    path = _book(tmp_path)
    path.chmod(0o444)
    try:
        with pytest.raises(RuntimeError, match="только для чтения"):
            enrich.assert_writable(path)
    finally:
        path.chmod(0o666)


@pytest.mark.skipif(os.name != "nt", reason="блокировка файла — Windows")
def test_a_really_locked_book_is_busy_with_a_name(tmp_path):
    """Настоящий замок Windows, а не подделка: так его держит чужой Excel."""
    import ctypes
    from ctypes import wintypes

    path = _book(tmp_path)
    enrich.excel_owner_file(path).write_bytes(_owner_blob("Аличон Махмадиев"))
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
    assert handle not in (None, 0, ctypes.c_void_p(-1).value)
    try:
        assert enrich.workbook_busy_owner(path) == "Аличон Махмадиев"
        with pytest.raises(RuntimeError, match="Аличон Махмадиев"):
            enrich.assert_writable(path)
    finally:
        kernel32.CloseHandle(handle)

    assert enrich.workbook_busy_owner(path) is None, "замок сняли — книга свободна"
