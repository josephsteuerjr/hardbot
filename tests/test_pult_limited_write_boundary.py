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
import os
import sys
from pathlib import Path

import pytest

# Пульт — окно на PySide6. На сервере, где ставят только ядро, Qt нет и не нужен:
# без этой строки модуль падал на ImportError ещё на сборе тестов, и «одна команда
# pytest» краснела целиком из-за десктопной зависимости. Ставим offscreen до
# импорта — иначе Qt ищет экран, которого на сервере тоже нет.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip(
    "PySide6.QtWidgets", exc_type=ImportError,
    reason="нет работающего PySide6 (pip install -r requirements-dev.txt; "
           "на голом Linux Qt дополнительно требует системную libGL)")



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


class _BoolVar:
    def __init__(self, value: bool) -> None:
        self.value = value

    def get(self) -> bool:
        return self.value


class _EntryValue:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class _Widget:
    def __init__(self) -> None:
        self.options = {}

    def configure(self, **kwargs) -> None:
        self.options.update(kwargs)


class _KV:
    def __init__(self) -> None:
        self.pairs = []

    def set_pairs(self, pairs) -> None:
        self.pairs = list(pairs)


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


# --- выбор сотрудников галочками ---------------------------------------------
DIRTY = [["блоки"], HEADER, ["сх", "хард", "софт"],
         ["41", "Егор", "К"], ["42", "егор", "К"], ["43", "Егор ", "К"],
         ["44", "Илья", "К"], ["45", "Михаил", "К"]]


def _book(grid):
    head, first, c_id, c_who = pult_ui.Pult._grid_layout(grid)
    return {"grid": grid, "head": head, "first": first, "c_id": c_id, "c_who": c_who}


def test_one_employee_written_three_ways_is_one_checkbox():
    """Иначе половина строк молча не уехала бы: галочка стояла бы на «Егор», а
    строки с «егор» и «Егор » остались бы дома."""
    keys = [pult_ui.Pult._emp_key(v) for v in ("Егор", "егор", "Егор ", " ЕГОР")]
    assert len(set(keys)) == 1


def test_selected_employee_takes_all_his_spellings():
    grid = pult_ui.Pult._grid_for(_book(DIRTY), {pult_ui.Pult._emp_key("Егор")})
    assert grid[:3] == DIRTY[:3], "шапка книги должна уехать целиком"
    assert [row[0] for row in grid[3:]] == ["41", "42", "43"]


def test_unselected_employees_do_not_leak():
    grid = pult_ui.Pult._grid_for(_book(DIRTY), {pult_ui.Pult._emp_key("Илья")})
    assert [row[0] for row in grid[3:]] == ["44"]


def test_empty_selection_is_an_error_not_an_empty_import():
    """Пустой отбор — это «ничего не грузим», а не «загрузить пустую книгу»."""
    try:
        pult_ui.Pult._grid_for(_book(DIRTY), set())
    except ValueError:
        return
    raise AssertionError("пустой отбор прошёл как валидная книга")


def test_full_registry_is_an_explicit_flag_not_inferred_from_source_kind():
    class Probe:
        imp_book = {
            "file_sha256": "f" * 64,
            "source_kind": pult_ui.IMPORT_SOURCE_PUBLISHED_MASTER,
            "employees": [{"key": "егор"}, {"key": "илья"}],
        }
        imp_selected = {"егор", "илья"}
        imp_full_var = _BoolVar(False)

    probe = Probe()
    assert pult_ui.Pult._imp_signature(probe)[2] is False

    probe.imp_full_var = _BoolVar(True)
    assert pult_ui.Pult._imp_signature(probe)[2] is True


def test_import_employee_list_is_union_of_file_and_database():
    employees = pult_ui.Pult._merge_import_employees(
        [
            {"key": "егор", "name": "Егор", "count": 2, "variants": 1},
            {"key": "илья", "name": "Илья", "count": 1, "variants": 1},
        ],
        [
            {"employee": "ЕГОР", "contracts": 5},
            {"employee": "Мария", "contracts": 7},
        ],
    )

    by_key = {row["key"]: row for row in employees}
    assert by_key["егор"]["file_count"] == 2
    assert by_key["егор"]["db_count"] == 5
    assert by_key["илья"]["file_count"] == 1
    assert by_key["илья"]["db_count"] == 0
    assert by_key["мария"]["file_count"] == 0
    assert by_key["мария"]["db_count"] == 7


def test_database_only_employee_can_form_an_empty_full_slice():
    book = _book(DIRTY)
    grid = pult_ui.Pult._grid_for(
        book, {pult_ui.Pult._emp_key("Мария")}, allow_empty=True
    )

    assert grid == DIRTY[:3]
    assert pult_ui.Pult._scope_from_grid(grid, allow_empty=True) == []


def test_import_report_exposes_unassignment_count():
    pairs = dict(pult_ui.Pult._imp_pairs({"auto_unassigned": 12}))
    assert pairs["будет снято / снято"] == 12


def test_export_request_uses_no_employee_filter_for_familiar_full_export():
    class Probe:
        db_employees = [
            {"employee": "Егор", "contracts": 5},
            {"employee": "Илья", "contracts": 7},
        ]
        exp_selected = {"егор", "илья"}
        exp_since = _EntryValue("")
        exp_until = _EntryValue("")

        def _merge_import_employees(self, file_rows, db_rows):
            return pult_ui.Pult._merge_import_employees(file_rows, db_rows)

    request = pult_ui.Pult._export_request(Probe())
    query = pult_ui.urllib.parse.parse_qs(
        pult_ui.urllib.parse.urlsplit(request["path"]).query
    )

    assert request["all_employees"] is True
    assert "employee" not in query
    assert query["mode"] == ["full"]


def test_export_request_combines_employee_subset_and_visit_window():
    class Probe:
        db_employees = [
            {"employee": "Егор", "contracts": 5},
            {"employee": "Илья", "contracts": 7},
        ]
        exp_selected = {"егор"}
        exp_since = _EntryValue("01.08.2026")
        exp_until = _EntryValue("2026-08-03")

        def _merge_import_employees(self, file_rows, db_rows):
            return pult_ui.Pult._merge_import_employees(file_rows, db_rows)

    request = pult_ui.Pult._export_request(Probe())
    query = pult_ui.urllib.parse.parse_qs(
        pult_ui.urllib.parse.urlsplit(request["path"]).query
    )

    assert request["all_employees"] is False
    assert query["employee"] == ["Егор"]
    assert query["visit_from"] == ["2026-08-01"]
    assert query["visit_to"] == ["2026-08-03"]


def test_pult_previews_before_apply_and_reuses_the_exact_request():
    class Probe:
        def __init__(self):
            self.imp_book = {
                "path": "C:/registry.xlsx",
                "file_sha256": "a" * 64,
                "employees": [
                    {
                        "key": "егор", "name": "Егор", "file_count": 1,
                        "db_count": 4,
                    }
                ],
            }
            self.imp_selected = {"егор"}
            self.imp_full_var = _BoolVar(True)
            self.imp_replace_current_comments_var = _BoolVar(True)
            self.imp_import_historical_comments_var = _BoolVar(True)
            self.imp_kvs = _KV()
            self.imp_warn = _Widget()
            self.imp_apply_btn = _Widget()
            self._imp_preview_body = None
            self._imp_preview_signature = None
            self._imp_preview_meta = {}
            self.calls = []
            self.toasts = []
            self.refreshed = False

        # done() собирает expected_* единым статметодом Pult
        _expected_from_preview = staticmethod(
            pult_ui.Pult._expected_from_preview)

        def _prepare_import(self, _book, _picked, *, full_registry=False,
                            force_test_subset=False):
            assert full_registry is True
            assert force_test_subset is False
            return {
                "source_kind": pult_ui.IMPORT_SOURCE_PUBLISHED_MASTER,
                "source_proof": {"proof": "exact"},
                "sheets": [{"name": "Портфели", "grid": [["id"]]}],
                "proof_digest": "d" * 64,
                "authoritative_employees": ["Егор"],
            }

        def api(self, method, path, body=None, timeout=20):
            self.calls.append((method, path, body, timeout))
            if path == "/admin/import/preview.json":
                return {
                    "blocked": False,
                    "total_rows": 1,
                    "inserted": 0,
                    "updated": 1,
                    "auto_unassigned": 3,
                    "auto_unassigned_by_employee": {"Егор": 3},
                    "auto_unassigned_digest": "e" * 64,
                    "replace_current_visit_comments": True,
                    "import_historical_visit_comments": True,
                    "current_comments_replaced": 1,
                    "historical_comments_imported": 1,
                    "comment_effect_digest": "c" * 64,
                }
            if path == "/admin/import/authorize.json":
                return {"ok": True}
            if path == "/admin/import.json":
                return {
                    "blocked": False,
                    "inserted": 0,
                    "updated": 1,
                    "auto_unassigned": 3,
                }
            raise AssertionError(path)

        def bg_task(self, fn, done=None, fail=None):
            try:
                result = fn()
            except Exception as exc:  # pragma: no cover - диагностическая ветка
                if fail:
                    fail(exc)
                else:
                    raise
            else:
                if done:
                    done(result)

        def toast(self, message, *, err=False):
            self.toasts.append((message, err))

        def refresh(self):
            self.refreshed = True

        def _imp_signature(self):
            return pult_ui.Pult._imp_signature(self)

        def _invalidate_import_preview(self, *, clear_report):
            return pult_ui.Pult._invalidate_import_preview(
                self, clear_report=clear_report
            )

        def _imp_pairs(self, report):
            return pult_ui.Pult._imp_pairs(report)

        def _imp_notes(self, report):
            return pult_ui.Pult._imp_notes(report)

        def _show_import_error(self, report, *, registration_hint=False):
            return pult_ui.Pult._show_import_error(
                self, report, registration_hint=registration_hint
            )

    probe = Probe()
    pult_ui.Pult.do_import(probe)

    assert [call[1] for call in probe.calls] == ["/admin/import/preview.json"]
    preview_request = probe.calls[0][2]
    assert preview_request["full_registry"] is True
    assert preview_request["authoritative_employees"] == ["Егор"]
    assert preview_request["replace_current_visit_comments"] is True
    assert preview_request["import_historical_visit_comments"] is True
    assert "expected_auto_unassigned" not in preview_request
    assert probe._imp_preview_body["expected_auto_unassigned"] == 3
    assert probe._imp_preview_body["expected_auto_unassigned_digest"] == "e" * 64
    assert probe._imp_preview_body["expected_comment_effect_digest"] == "c" * 64
    assert probe.imp_apply_btn.options["state"] == "normal"
    assert "снять 3" in probe.imp_apply_btn.options["text"]

    pult_ui.Pult.apply_import(probe)

    assert [call[1] for call in probe.calls] == [
        "/admin/import/preview.json",
        "/admin/import/authorize.json",
        "/admin/import.json",
    ]
    apply_request = probe.calls[-1][2]
    assert apply_request["source_proof"] == preview_request["source_proof"]
    assert apply_request["sheets"] == preview_request["sheets"]
    assert apply_request["expected_auto_unassigned"] == 3
    assert apply_request["expected_auto_unassigned_digest"] == "e" * 64
    assert apply_request["expected_comment_effect_digest"] == "c" * 64
    assert probe.refreshed is True


def test_full_export_carries_the_proven_visit_not_the_import_copy(tmp_path, monkeypatch):
    """Полная выгрузка (mode=full) — источник файлов, которыми заменяют личные
    книги. Сырые колонки клиента хранят копию ПОСЛЕДНЕГО ИМПОРТА: пока офис по
    кейсу не высказался, там стоит его дефолт «Не обработан». Выгрузка обязана
    отдавать доказанный результат визита — иначе собранный из неё файл затирает
    живые статусы дефолтом (замечено владельцем 26.08.2026).
    """
    import db as dbm
    monkeypatch.setattr(dbm, "DB_PATH", tmp_path / "hardbot.db")
    dbm.init_db()
    with dbm.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO clients (contract_id, client, responsible,"
            " overall_result, call_status) VALUES ('EX-1','Клиент В.',"
            "'Выгрузкин С.','Не обработан','Не обработан')")
        conn.commit()
    dbm.record_bot_visit("EX-1", {"call_status": "Обещает оплатить",
                                  "contact_with": "Должник"},
                         dbm.date.today().isoformat())
    row = [r for r in dbm.registry_rows() if r["contract_id"] == "EX-1"][0]
    assert row["call_status"] == "Обещает оплатить"
    assert dbm.get_client("EX-1")["call_status"] == "Не обработан"  # сырое цело
