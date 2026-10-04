"""Импорт реестра без Qt: тексты кнопок и отчётов, состав выгрузки, поток
предпросмотр → применение поверх протокола вида."""

from __future__ import annotations

import urllib.parse

import pytest

from pult.core import registry_import as ri
from pult.core.constants import (IMPORT_SOURCE_PUBLISHED_MASTER,
                                 LIMITED_TEST_RESET_DISABLED_MESSAGE)
from pult.core.export import export_confirm_text, export_request
from pult.core.import_flow import ImportFlow


# --- тексты ------------------------------------------------------------------
def test_preview_button_counts_people_and_rows():
    book = {"employees": [{"key": "егор", "file_count": 3}, {"key": "илья", "file_count": 2}]}
    assert ri.preview_button_text(book, {"егор"}, False) == "Предпросмотр выбранных (1 чел · 3 дог.)"
    assert ri.preview_button_text(book, {"егор", "илья"}, True) == \
        "Предпросмотр полного среза (2 чел · 5 дог.)"


def test_employee_note_states():
    assert "есть в файле и БД" in ri.employee_note({"file_count": 2, "db_count": 5})
    assert "новый для БД" in ri.employee_note({"file_count": 2, "db_count": 0})
    note = ri.employee_note({"file_count": 0, "db_count": 5, "variants": 3})
    assert "только в БД" in note and "написаний в файле: 3" in note


def test_apply_button_label_lists_every_effect():
    label = ri.apply_button_label({
        "auto_unassigned": 3, "current_comments_replaced": 1,
        "historical_comments_cleared": 2, "manual_restored": 4,
        "recalled_had_owner": 5, "recall_restored": 6})
    assert label == ("Применить импорт · снять 3 · комментариев 3 · вернуть 4 "
                     "· отозвать 5 · вернуть из отзыва 6")
    assert ri.apply_button_label({}) == "Применить импорт"


def test_apply_toast_replay_is_an_error():
    text, err = ri.apply_toast({"idempotent_replay": True})
    assert err and "receipt" in text
    text, err = ri.apply_toast({"inserted": 1, "updated": 2, "auto_unassigned": 3,
                                "manual_restored": 4})
    assert not err
    assert text == "Импорт готов: +1 / обновлено 2 / снято 3 / возвращено 4"


def test_preview_headline_full_vs_partial():
    assert ri.preview_headline({}, False).startswith("ЧАСТИЧНЫЙ ИМПОРТ")
    head = ri.preview_headline({"auto_unassigned": 2,
                                "auto_unassigned_by_employee": {"Егор": 2}}, True)
    assert head.startswith("ПОЛНЫЙ СРЕЗ ВЫБРАННЫХ") and "Егор: 2" in head


def test_import_error_text_with_registration_hint():
    d = {"error": "proof not registered", "header_sample": ["КД", "Клиент"],
         "_proof_digest": "d" * 64, "_source_kind": "test_subset", "_registered": False}
    text = ri.import_error_text(d, "C:/tmp/proof.json", registration_hint_wanted=True)
    assert text.startswith("proof not registered. В файле: КД, Клиент")
    assert "import_authorization.py register --source-kind test_subset" in text
    assert "C:/tmp/proof.json" in text and "d" * 64 in text
    plain = ri.import_error_text(d, None, registration_hint_wanted=False)
    assert "import_authorization.py" not in plain


def test_file_caption_names_source_kind(tmp_path):
    book = {"path": str(tmp_path / "ОБЩИЙ РЕЕСТР.xlsb"), "scope": [1, 2], "employees": [1],
            "published": None}
    caption = ri.file_caption(book)
    assert "ОБЩИЙ РЕЕСТР.xlsb · строк 2 · сотрудников 1" in caption
    assert "расписки registry_cycle для этого файла нет" in caption
    book["published"] = {"sheets": []}
    assert "опубликованный master" in ri.file_caption(book)


def test_recalled_pairs_and_row():
    pairs = dict(ri.recalled_pairs({"totals": {"recalled": 1469, "manual": 2,
                                               "absent_from_registry": 3}, "shown": 500}))
    assert pairs["показано"] == "500 из 1 469"
    row = ri.recalled_row({"contract_id": "1", "client": "К", "employee": "Е",
                           "recall_date": "2026-08-01", "recall_transfer": "передан",
                           "debt": 12345})
    assert row == ("1", "К", "Е", "2026-08-01", "передан", "12 345")
    assert ri.recalled_query(500, "Егор К") == "/admin/recalled.json?limit=500&employee=%D0%95%D0%B3%D0%BE%D1%80%20%D0%9A"


# --- выгрузка ------------------------------------------------------------------
DB = [{"employee": "Егор", "contracts": 5}, {"employee": "Илья", "contracts": 7}]


def test_export_request_subset_and_window():
    employees = ri.merge_import_employees([], DB)
    req = export_request(employees, {"егор"}, "01.08.2026", "2026-08-03")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(req["path"]).query)
    assert q["employee"] == ["Егор"] and q["mode"] == ["full"]
    assert (req["visit_from"], req["visit_to"]) == ("2026-08-01", "2026-08-03")
    text = export_confirm_text(req, 12)
    assert "Строк в выгрузке: 12" in text and "Сотрудники: Егор" in text
    assert "с 2026-08-01 по 2026-08-03" in text


def test_export_request_rejects_empty_selection_and_inverted_window():
    employees = ri.merge_import_employees([], DB)
    with pytest.raises(ValueError, match="хотя бы одного"):
        export_request(employees, set(), "", "")
    with pytest.raises(ValueError, match="позже окончания"):
        export_request(employees, {"егор", "илья"}, "05.08.2026", "01.08.2026")
    req = export_request(employees, {"егор", "илья"}, "", "")
    assert req["all_employees"] and "employee" not in req["path"]
    assert "весь реестр" in export_confirm_text(req, 0)


# --- поток предпросмотр → применение ----------------------------------------------
class _Out:
    def __init__(self):
        self.text = ""
        self.state = None
        self.pairs = None

    def configure(self, text=None, state=None, **_):
        if text is not None:
            self.text = text
        if state is not None:
            self.state = state

    def set_pairs(self, pairs):
        self.pairs = list(pairs)


class _Var:
    def __init__(self, value=False):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeView(ImportFlow):
    """Вид, каким его видит ImportFlow: атрибуты состояния, виджеты с
    configure/get/set_pairs, окно с toast/bg_task/api/refresh."""

    def __init__(self, report, apply_result=None, authorize=None):
        self.imp_book = {"path": "C:/registry.xlsx", "file_sha256": "a" * 64,
                         "employees": [{"key": "егор", "name": "Егор", "file_count": 1,
                                        "db_count": 4}]}
        self.imp_selected = {"егор"}
        self.imp_full_var = _Var(True)
        self.imp_restore_manual_var = _Var(False)
        self.imp_replace_current_comments_var = _Var(False)
        self.imp_kvs = _Out()
        self.imp_warn = _Out()
        self.imp_apply_btn = _Out()
        self.reset_out = _Out()
        self.db_employees = []
        self.exp_selected = set()
        self._imp_preview_body = None
        self._imp_preview_signature = None
        self._imp_preview_meta = {}
        self.calls, self.toasts = [], []
        self.refreshed = False
        self.report = report
        self.apply_result = apply_result or {}
        self.authorize = authorize

    def _prepare_import(self, book, picked, *, full_registry=False, force_test_subset=False):
        self._proof_path = "C:/tmp/proof.json"
        return {"source_kind": IMPORT_SOURCE_PUBLISHED_MASTER,
                "source_proof": {"proof": "exact"},
                "sheets": [{"name": "Портфели", "grid": [["id"]]}],
                "proof_digest": "d" * 64, "authoritative_employees": ["Егор"]}

    def api(self, method, path, body=None, timeout=20):
        self.calls.append((method, path, body, timeout))
        if path == "/admin/import/preview.json":
            return self.report
        if path == "/admin/import/authorize.json":
            if isinstance(self.authorize, Exception):
                raise self.authorize
            return self.authorize or {"ok": True}
        if path == "/admin/import.json":
            return self.apply_result
        raise AssertionError(path)

    def bg_task(self, fn, done=None, fail=None):
        try:
            result = fn()
        except Exception as exc:                        # noqa: BLE001
            (fail or self._fail)(exc)
        else:
            if done:
                done(result)

    def _fail(self, exc):
        self.toasts.append((str(exc), True))

    def toast(self, message, *, err=False):
        self.toasts.append((message, err))

    def refresh(self):
        self.refreshed = True

    def ask_yes_no(self, title, text):
        raise AssertionError("подтверждение не должно спрашиваться")


PREVIEW = {"total_rows": 1, "inserted": 0, "updated": 1, "auto_unassigned": 3,
           "auto_unassigned_by_employee": {"Егор": 3}, "auto_unassigned_digest": "e" * 64}


def test_preview_then_apply_reuses_exact_request():
    view = FakeView(PREVIEW, apply_result={"inserted": 0, "updated": 1, "auto_unassigned": 3})
    view.do_import()
    assert [c[1] for c in view.calls] == ["/admin/import/preview.json"]
    assert view.calls[0][3] == 180
    assert view.imp_apply_btn.state == "normal" and "снять 3" in view.imp_apply_btn.text
    assert view.imp_warn.text.startswith("ПОЛНЫЙ СРЕЗ ВЫБРАННЫХ")
    assert view.toasts[-1] == ("Предпросмотр готов: будет снято 3", False)
    assert view._imp_preview_body["expected_auto_unassigned_digest"] == "e" * 64

    view.apply_import()
    paths = [c[1] for c in view.calls]
    assert paths[1:] == ["/admin/import/authorize.json", "/admin/import.json"]
    assert view.calls[1][2] == {"source_kind": IMPORT_SOURCE_PUBLISHED_MASTER,
                                "source_proof": {"proof": "exact"}}
    assert view.calls[2][2]["expected_auto_unassigned"] == 3
    assert view.refreshed
    assert view.toasts[-1] == ("Импорт готов: +0 / обновлено 1 / снято 3", False)
    assert view._imp_preview_body is None and view.imp_apply_btn.state == "disabled"


def test_selection_change_during_preview_cancels_it():
    view = FakeView(PREVIEW)
    real_api = view.api

    def api(method, path, body=None, timeout=20):
        view.imp_selected = set()          # оператор снял галочку, пока шёл запрос
        return real_api(method, path, body, timeout)
    view.api = api
    view.do_import()
    assert view.imp_apply_btn.state == "disabled"
    assert any("Выбор изменился" in t for t, _ in view.toasts)


def test_apply_without_fresh_preview_is_refused():
    view = FakeView(PREVIEW)
    view.apply_import()
    assert view.calls == []
    assert view.toasts[-1][0].startswith("Сначала сделай свежий предпросмотр")


def test_server_without_exact_digest_stops_the_flow():
    view = FakeView({**PREVIEW, "auto_unassigned_digest": None})
    view.do_import()
    assert view.imp_apply_btn.state == "disabled"
    assert "нужен новый сервер" in view.toasts[-1][0]


def test_apply_error_shows_registration_hint_when_registrar_is_closed():
    view = FakeView(PREVIEW, apply_result={"error": "proof not registered"},
                    authorize=RuntimeError("На сервере нет /admin/import/authorize.json"))
    view.do_import()
    view.apply_import()
    assert "Разрешение не зарегистрировано" in view.imp_warn.text
    assert "C:/tmp/proof.json" in view.imp_warn.text
    assert view.imp_apply_btn.state == "normal"          # можно нажать ещё раз
    assert view.toasts[-1] == ("Импорт не прошёл", True)


def test_stale_preview_after_apply_invalidates_without_clearing_report():
    view = FakeView(PREVIEW, apply_result={"error": "preview stale", "preview_stale": True})
    view.do_import()
    view.imp_kvs.pairs = [("x", 1)]
    view.apply_import()
    assert view._imp_preview_body is None
    assert view.imp_apply_btn.state == "disabled"


def test_guards_stay_fail_closed():
    view = FakeView(PREVIEW)
    view.do_reset("all")
    assert view.reset_out.text == LIMITED_TEST_RESET_DISABLED_MESSAGE
    assert view.toasts == [(LIMITED_TEST_RESET_DISABLED_MESSAGE, True)]
    view.do_import_batch()
    assert "registry_cycle" in view.imp_warn.text
    view.imp_book = {}
    view.do_import()
    assert view.toasts[-1] == ("Сначала выбери реестр", True)


def test_signature_tolerates_missing_toggles():
    view = FakeView(PREVIEW)
    del view.imp_restore_manual_var
    assert view._imp_signature() == ("a" * 64, "hard", ("егор",), True, False, False)
