"""Обогащение без Qt: подбор файлов, окно дат, аргументы enrich_many,
реестр доставленного, отчёт прогона с конфликтами и правками офиса."""

from __future__ import annotations

import inspect
import json
from datetime import date, timedelta
from pathlib import Path

import enrich

from pult.core import enrichment as en


# --- файлы -------------------------------------------------------------------
def test_personal_files_prefers_named_book_and_skips_copies(tmp_path):
    folder = tmp_path / "РТ" / "Душанбе" / "Иванов Иван"
    folder.mkdir(parents=True)
    (folder / "Иванов Иван.xlsb").write_bytes(b"")
    (folder / "Иванов Иван (до обогащения 01.08 10-00).xlsb").write_bytes(b"")
    (folder / "~$Иванов Иван.xlsb").write_bytes(b"")
    files = en.personal_files([{"path": folder}], "Иванов Иван")
    assert files == [folder / "Иванов Иван.xlsb"]

    other = tmp_path / "УЗ" / "Ташкент" / "Петров"
    other.mkdir(parents=True)
    (other / "портфель.xlsb").write_bytes(b"")
    (other / "портфель (до разброса 02.08 09-00).xlsb").write_bytes(b"")
    assert en.personal_files([{"path": other}], "Петров Пётр") == [other / "портфель.xlsb"]


class _Split(dict):
    orphan_filled = 0
    orphan_rows = 0
    orphan_cids: list = []


def test_match_employees_and_preview_texts(tmp_path):
    folder = tmp_path / "РТ" / "Душанбе" / "Иванов"
    folder.mkdir(parents=True)
    (folder / "Иванов.xlsb").write_bytes(b"")
    empty = tmp_path / "РТ" / "Душанбе" / "Сидоров"
    empty.mkdir(parents=True)
    by_emp = _Split({
        "иванов": {"employee": "Иванов", "filled": 2, "rows": [1, 2]},
        "сидоров": {"employee": "Сидоров", "filled": 1, "rows": [3]},
        "нетпапки": {"employee": "Нетпапки", "filled": 4, "rows": []},
        "пустой": {"employee": "Пустой", "filled": 0, "rows": []},
    })
    fmap = {"иванов": [{"path": folder}], "сидоров": [{"path": empty}]}
    matched, nofolder, nofile = en.match_employees(by_emp, fmap)
    assert [m["employee"] for m in matched] == ["Иванов"]
    assert nofolder == ["Нетпапки (4)"] and nofile == ["Сидоров"]
    pairs = dict(en.preview_pairs(by_emp, matched))
    assert pairs == {"с визитами бота": 3, "готовы к обогащению": 1,
                     "завершённых визитов": 2, "файлов": 1}
    warn = en.preview_warning(by_emp, matched, nofolder, nofile)
    assert "нет папки: Нетпапки (4)" in warn and "нет .xlsb: Сидоров" in warn
    assert en.pick_note(matched[0]) == "визитов: 2 · файлов: 1 · Душанбе"

    by_emp.orphan_filled, by_emp.orphan_rows, by_emp.orphan_cids = 2, 5, ["К1", "К2"]
    warn = en.preview_warning(by_emp, matched, [], [])
    assert warn.startswith("⚠ Завершённых визитов без ответственного: 2")
    assert "КД: К1, К2" in warn
    assert dict(en.preview_pairs(by_emp, matched))["визитов без ответственного"] == 2
    assert en.preview_warning(_Split(), [], [], []) == "Бот пока ничего не заполнил — обогащать нечего."


# --- окно и подтверждение --------------------------------------------------------
def test_window_text_is_loud_when_empty():
    assert en.window_text("01.08.2026", "") == "Окно выездов: с 01.08.2026 по —."
    assert en.window_text("", "").startswith("ОКНО НЕ ЗАДАНО")
    text = en.confirm_text(2, 3, "", "05.08.2026")
    assert text.startswith("Обогатить личные реестры: сотрудников 2, файлов 3?")
    assert "с — по 05.08.2026" in text and "Резервная копия" in text
    assert "Точно обогащать за всю историю?" in en.WHOLE_HISTORY_TEXT


def test_backup_and_receipt_folders():
    assert en.backup_root(None) is None and en.receipt_dir("") is None
    root = en.backup_root(r"U:\rbr\Развитие СНГ")
    assert root.endswith("!! Обогащение из БД")
    assert en.receipt_dir(r"U:\rbr\Развитие СНГ") == str(Path(root) / "_квитанции")


def test_suggest_window_from_last_receipt(tmp_path):
    assert en.suggest_window(None) == ("", "")
    assert en.suggest_window(str(tmp_path)) == ("", "")
    (tmp_path / "обогащение-2026-07-01T10-00-00.json").write_text(
        json.dumps({"at": "2026-07-01"}), encoding="utf-8")
    (tmp_path / "обогащение-2026-08-10T10-00-00.json").write_text(
        json.dumps({"at": "2026-08-10"}), encoding="utf-8")
    since, until = en.suggest_window(str(tmp_path))
    assert since == (date(2026, 8, 10) - timedelta(days=7)).strftime("%d.%m.%Y")
    assert until == date.today().strftime("%d.%m.%Y")
    assert en.last_receipt(str(tmp_path)).name.startswith("обогащение-2026-08-10")


# --- контракт с enrich ----------------------------------------------------------------
def test_enrich_many_kwargs_match_the_real_signature():
    kwargs = en.enrich_many_kwargs(since="01.08.2026", until="", backup_root="b",
                                   receipt_dir="r", ledger={}, on_delivered=print,
                                   server_authoritative=1,
                                   decisions={("A", "call_status"): "file"})
    accepted = inspect.signature(enrich.enrich_many).parameters
    assert set(kwargs) <= set(accepted)
    assert kwargs["server_authoritative"] is True and kwargs["ledger"] == {}
    assert kwargs["decisions"] == {("A", "call_status"): "file"}
    pk = en.preview_kwargs(since="", until="", ledger={})
    assert set(pk) <= set(inspect.signature(enrich.preview_many).parameters)


def test_decisions_helpers_and_preview_lines():
    assert en.conflict_key({"contract_id": "A", "key": "call_status"}) == ("A", "call_status")
    assert en.decisions_summary({("A", "k"): "file", ("B", "k"): "server",
                                 ("C", "k"): "file"}) == (2, 1)
    note = en.confirm_decisions_note({("A", "k"): "file"}, 0)
    assert "оставить как в книге 1" in note and "записать сервер 0" in note
    assert "Проверить конфликты" in en.NO_CHECK_NOTE
    results = [
        {"employee": "И", "file": "a.xlsb", "cells_written": 4, "conflicts": [
            {"contract_id": "A", "key": "call_status", "kind": "no_history"}],
         "office_edits": [], "resolved_file": 1, "resolved_server": 0},
        {"employee": "П", "file": "b.xlsb", "error": "занят"},
    ]
    text = "\n".join(en.preview_lines(results))
    assert "файлов 1 из 2" in text and "будет дописано ячеек: 4" in text
    assert "Конфликтов: 1" in text and "оставить как в книге 1" in text
    assert "b.xlsb: занят" in text
    assert en.DECISION_LABELS[en.DECISION_DEFAULT] == "оставить как в книге"


def test_ledger_query_joins_names_with_semicolon():
    q = en.ledger_query(["Иванов", "Петров", "", "Иванов"])
    assert q.startswith("/admin/enrichment/ledger.json?employees=")
    assert "%3B" in q                      # «;» между ФИО
    assert q.count("%D0%98%D0%B2%D0%B0%D0%BD%D0%BE%D0%B2") == 1


def test_replay_receipt_posts_entries_through_callback(tmp_path):
    receipt = tmp_path / "обогащение-2026-08-10T10-00-00.json"
    receipt.write_text(json.dumps({"ledger_entries": [
        {"contract_id": "1", "key": "visit_date", "value": "2026-08-01", "kind": "written"}]}),
        encoding="utf-8")
    seen = {}

    def on_delivered(payload):
        seen.update(payload)
        return {"recorded": 1}
    out = en.replay_receipt(receipt, on_delivered)
    assert out == {"recorded": 1, "entries": 1}
    assert seen["entries"][0]["contract_id"] == "1" and len(seen["receipt_digest"]) == 64


# --- отчёт -------------------------------------------------------------------------------
def _results():
    return [
        {"employee": "Иванов", "file": "C:/x/Иванов.xlsb", "cells_written": 5,
         "changed_cases": 2, "cells_over_nonempty": 1, "missing": ["К9"],
         "out_of_window": 3, "technical_overwrites": 1, "cells_updated_delivered": 2,
         "backup": "C:/b/Иванов (до обогащения).xlsb",
         "displaced": [{"contract_id": "К1", "column": "Общий результат", "was": "а",
                        "now": "б", "visit_date": "2026-08-01"}],
         "conflicts": [{"kind": "both_changed", "contract_id": "К2", "key": "overall",
                        "column": "Общий результат", "row": 7, "file": "офис",
                        "server": "бот", "delivered": "старое", "visit_date": "2026-08-02"}],
         "office_edits": [{"kind": "office_edit", "contract_id": "К3", "key": "comment",
                           "column": "Комментарий", "row": 8, "file": "правка", "server": "б",
                           "delivered": "б", "visit_date": ""}],
         "ledger_recorded": 3, "receipt_error": "диск полон"},
        {"employee": "Петров", "file": "C:/x/Петров.xlsb", "written_unverified": True,
         "error": "не удалось перечитать"},
        {"employee": "Сидоров", "file": "C:/x/Сидоров.xlsb", "error": "занят",
         "ledger_error": "сервер недоступен"},
    ]


def test_report_lines_cover_every_outcome():
    text = "\n".join(en.report_lines(_results()))
    assert text.startswith("Готово: файлов 1 из 3 · дописано ячеек: 5 (поверх старых: 1)")
    assert "кейсов не нашлось в файлах: 1" in text and "вне окна дат пропущено: 3" in text
    assert "поверх «Не обработан»: 1" in text and "обновлено доставленных ранее: 2" in text
    assert "Иванов — Иванов.xlsb: ячеек 5, кейсов 2" in text
    assert "ЗАМЕНЕНО ПОВЕРХ ЧУЖИХ ЗНАЧЕНИЙ (1)" in text and "«а» → «б»" in text
    assert "КОНФЛИКТЫ (1)" in text and "ПРАВКИ ОФИСА (1)" in text
    assert "⚠ Петров — Петров.xlsb: записано, но проверить не удалось" in text
    assert "⚠ Сидоров — Сидоров.xlsb: занят" in text
    assert "Резервные копии: 1." in text
    assert "⚠ КВИТАНЦИЯ НЕ ЗАПИСАНА: диск полон" in text
    assert "Реестр доставленного на сервере: записано 3." in text


def test_conflict_tables_and_ledger_error():
    conflicts, office = en.collect_conflicts(_results())
    assert [c["employee"] for c in conflicts] == ["Иванов"]
    row = en.conflict_row(conflicts[0])
    assert row == ("Иванов", "К2", "Общий результат", "офис", "бот", "старое",
                   "2026-08-02", "правили обе стороны")
    assert en.conflict_row(office[0])[-1] == "правка офиса"
    err = en.ledger_error(_results())
    assert err.startswith("⚠ РЕЕСТР ДОСТАВЛЕННОГО НЕ ОТПРАВЛЕН НА СЕРВЕР: сервер недоступен")
    assert en.ledger_error([{"employee": "x"}]) == ""
    assert en.toast_text(_results()) == ("Обогащено файлов: 1, ошибок: 1", True)


def test_single_file_report():
    text = en.single_file_report({
        "filled_cases": 3, "matched_cases": 2, "cells_written": 4, "cells_over_nonempty": 1,
        "cells_already": 5, "backup": "C:/b.xlsb", "missing": ["К1"],
        "visit_cols_absent": ["Дата выезда"]})
    assert "дописано ячеек: 4 (поверх старых значений: 1)" in text
    assert "Резервная копия: C:/b.xlsb" in text
    assert "не найдены в файле (1): К1" in text and "нет колонок: Дата выезда" in text
