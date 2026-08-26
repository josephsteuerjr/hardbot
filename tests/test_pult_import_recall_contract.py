"""Пульт остаётся fail-closed по эффекту отзыва.

Признак «в файле есть колонки отзыва» приходит из отчёта предпросмотра
(recall_columns): старый сервер этого ключа не отдаёт — требование не
включается, и новый пульт со старым сервером работает как раньше.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PULT_DIR = ROOT / "pult"
sys.path.insert(0, str(PULT_DIR))

SPEC = importlib.util.spec_from_file_location(
    "hardbot_recall_pult", PULT_DIR / "pult.py")
assert SPEC is not None and SPEC.loader is not None
pult_ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pult_ui)

D64 = hashlib.sha256(b"[]").hexdigest()


def _report(**kw):
    base = {"recall_columns": ["Дата отзыва"], "recalled": 3,
            "recalled_digest": D64, "recall_restored": 1,
            "recall_restored_digest": D64,
            "auto_unassigned": 0, "auto_unassigned_digest": D64,
            "manual_restored": 0, "manual_restore_digest": D64,
            "comment_effect_digest": D64}
    base.update(kw)
    return base


def test_missing_recalled_digest_is_an_error():
    out = pult_ui.Pult._expected_from_preview(
        _report(recalled_digest=None), {})
    assert isinstance(out, str) and "отзыв" in out.lower()


def test_full_report_yields_expected_pairs():
    out = pult_ui.Pult._expected_from_preview(_report(), {})
    assert out["expected_recalled"] == 3
    assert out["expected_recalled_digest"] == D64
    assert out["expected_recall_restored"] == 1
    assert out["expected_recall_restored_digest"] == D64


def test_old_server_without_recall_columns_requires_nothing():
    rep = _report()
    del rep["recall_columns"]
    rep["recalled_digest"] = None
    rep["recall_restored_digest"] = None
    out = pult_ui.Pult._expected_from_preview(rep, {})
    assert isinstance(out, dict)          # ошибки нет — требование не включилось


def test_other_effects_still_fail_closed():
    out = pult_ui.Pult._expected_from_preview(
        _report(auto_unassigned_digest="short"), {"full_registry": True})
    assert isinstance(out, str) and "снятий" in out


def test_imp_pairs_show_recall_rows():
    pairs = dict(pult_ui.Pult._imp_pairs(_report(recalled_had_owner=2,
                                                 recall_held=5)))
    assert pairs["отозвано реестром (у сотрудников)"] == 2
    assert pairs["отзыв держится"] == 5
    assert pairs["возвращено из отзыва"] == 1


def test_imp_notes_explain_no_responsible_and_unrecognized():
    notes = "\n".join(pult_ui.Pult._imp_notes(_report(
        recalled_had_owner=2, recalled_by_employee={"Иванов И.": 2},
        recalled_previously_unassigned=0, recalled_without_owner=1,
        no_responsible=1400, recall_held=3,
        recalled_unrecognized=2,
        recalled_unrecognized_sample=[
            {"contract_id": "X", "column": "Причина отзыва", "value": "???"}])))
    assert "не авария" in notes
    assert "не признанных отзывом" in notes
    assert "Иванов И." in notes
