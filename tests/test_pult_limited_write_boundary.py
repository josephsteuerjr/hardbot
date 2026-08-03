"""Что Tk-пульту можно, а что нет.

03.08.2026 владелец вернул пульту две операции: импорт реестра и запись
обогащения в личные книги. Обе — штатная работа пульта, и обе он делал до
первого live-baseline; выключали их, чтобы в личные книги ничего не писало мимо
registry_cycle, пока baseline не опубликован (опубликован 29.07).

Что осталось закрытым — и должно оставаться:

* сброс базы из пульта: он обходит snapshot/export/clearance, а именно ими
  доказывается очистка перед тестом на серверах компании;
* загрузка ЛИЧНЫХ книг в бота: personal ↔ master обслуживает только
  registry_cycle, обратного направления не существует.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PULT_DIR = ROOT / "pult"
sys.path.insert(0, str(PULT_DIR))

SPEC = importlib.util.spec_from_file_location(
    "hardbot_limited_test_pult",
    PULT_DIR / "pult.py",
)
assert SPEC is not None and SPEC.loader is not None
pult_ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pult_ui)


class _Label:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, **kwargs) -> None:
        self.text = kwargs.get("text", self.text)


class _ResetGuardProbe:
    def __init__(self) -> None:
        self.reset_out = _Label()
        self.toasts: list[tuple[str, bool]] = []

    def toast(self, message: str, *, err: bool = False) -> None:
        self.toasts.append((message, err))

    def __getattr__(self, name: str):
        raise AssertionError(f"reset continued past limited-test guard: {name}")


class _BatchProbe:
    def __init__(self) -> None:
        self.imp_warn = _Label()
        self.toasts: list[tuple[str, bool]] = []

    def toast(self, message: str, *, err: bool = False) -> None:
        self.toasts.append((message, err))

    def __getattr__(self, name: str):
        raise AssertionError(f"personal-book import continued: {name}")


def _must_not_run(*_args, **_kwargs):
    raise AssertionError("запрещённый путь записи всё-таки вызвался")


def test_reset_stops_before_confirmation_and_api(monkeypatch):
    assert pult_ui.LIMITED_TEST_RESET_UI_ENABLED is False
    monkeypatch.setattr(pult_ui.messagebox, "askyesno", _must_not_run)
    for scope in ("visits", "all"):
        probe = _ResetGuardProbe()
        pult_ui.Pult.do_reset(probe, scope)
        expected = pult_ui.LIMITED_TEST_RESET_DISABLED_MESSAGE
        assert probe.reset_out.text == expected
        assert probe.toasts == [(expected, True)]
        assert "snapshot" in expected
        assert "clearance" in expected


def test_personal_books_are_never_imported_into_the_bot():
    probe = _BatchProbe()
    pult_ui.Pult.do_import_batch(probe)
    assert "registry_cycle" in probe.imp_warn.text
    assert probe.toasts and probe.toasts[0][1] is True


def test_enrichment_and_import_are_back():
    assert pult_ui.LIMITED_TEST_ENRICH_WRITES_ENABLED is True
    assert pult_ui.LIMITED_TEST_MASTER_IMPORT_UI_ENABLED is True


# --- scope, который пульт строит сам -----------------------------------------
HEADER = ["Номер КД / ID", "ответственный Сотрудник", "Клиент"]


def test_scope_is_built_from_the_workbook():
    grid = [["блоки"], HEADER, ["сх"], [41.0, "Егор", "К"], ["42", "Илья", "К"]]
    scope = pult_ui.Pult._scope_from_grid(grid)
    assert scope == [{"contract_id": "41", "responsible": "Егор"},
                     {"contract_id": "42", "responsible": "Илья"}]


def test_dirty_responsible_does_not_block_the_whole_file():
    """Ручной scope валился целиком из-за одного хвостового пробела.

    `scope_invalid` не называл ни строку, ни значение — оператор вставал намертво
    на файле, который сервер потом принимал без единого возражения.
    """
    grid = [["блоки"], HEADER, ["сх"], ["41", "Егор ", "К"], ["42", "егор", "К"]]
    scope = pult_ui.Pult._scope_from_grid(grid)
    assert [item["responsible"] for item in scope] == ["Егор", "егор"]


def test_workbook_without_the_key_column_is_refused():
    for grid in ([["Клиент"], ["К"]], [["Номер КД / ID", "Клиент"], ["41", "К"]]):
        try:
            pult_ui.Pult._scope_from_grid(grid)
        except (ValueError, KeyError):
            continue
        raise AssertionError("книга без обязательной колонки принята")


def test_profile_row_is_not_a_contract():
    """«сх/хард/софт» под заголовком — форма книги, а не тринадцатый договор.

    Пока пульт считал её данными, scope уезжал на одну строку длиннее, сервер её
    штатно пропускал, и импорт валился на `scope_mismatch` — с текстом, который
    винит реестр, хотя виноват был построитель scope.
    """
    grid = [["блоки"], HEADER, ["сх", "хард", "софт"],
            ["41", "Егор", "К"], ["42", "Егор", "К"]]
    scope = pult_ui.Pult._scope_from_grid(grid)
    assert [item["contract_id"] for item in scope] == ["41", "42"]


def test_rows_without_contract_id_are_skipped_not_guessed():
    grid = [["блоки"], HEADER, ["сх"], ["", "Егор", "К"], ["41", "Егор", "К"],
            ["41", "Илья", "дубль того же КД"]]
    scope = pult_ui.Pult._scope_from_grid(grid)
    assert scope == [{"contract_id": "41", "responsible": "Егор"}]
