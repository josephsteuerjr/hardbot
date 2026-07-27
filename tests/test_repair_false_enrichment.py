"""Доказательная починка ложного обогащения — чистая логика и атомарная установка."""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import repair_false_enrichment as repair  # noqa: E402


FIELDS = [
    {"key": "contract_id", "title": "Номер КД / ID", "date": False},
    {"key": "client", "title": "Клиент", "date": False},
    {"key": "visit_date", "title": "Дата выезда", "date": True},
    {"key": "contact_with", "title": "С кем был контакт?", "date": False},
    {"key": "client_location", "title": "Местоположение клиента", "date": False},
    {"key": "call_status", "title": "Статус звонка", "date": False},
]
HEADERS = [field["title"] for field in FIELDS]


def grid(*rows):
    return [HEADERS, ["сх", "сх", "хард", "хард", "хард", "хард"], *rows]


def snap(name, *rows):
    return repair.snapshot_from_grid(name, grid(*rows), FIELDS, file_hash=name)


def raw(cid="C-1", **values):
    row = {"contract_id": cid, "responsible": "Тестов Тест"}
    row.update(values)
    return row


def writes_by_key(plan):
    return {write["key"]: write for write in plan["writes"]}


def test_excel_serial_and_iso_poison_are_recognized():
    assert repair.excel_serial_date("531").isoformat() == "1901-06-14"
    assert repair.poison_equal("visit_date", datetime(1901, 6, 14), "531")
    # Excel умеет превратить ISO-строку в дату даже в ошибочной dropdown-колонке.
    assert repair.poison_equal("client_location", datetime(2026, 7, 9), "2026-07-09")


def test_numeric_1901_artifact_and_adjacent_poison_are_repaired():
    before = snap("before.xlsb", ["C-1", "Клиент", None, None, None, None])
    current = snap("current.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                    "CONSUMER", datetime(2026, 7, 9), None])
    plan = repair.build_chain_plan(
        [before, current], FIELDS,
        [raw(visit_date="531", contact_with="CONSUMER",
             client_location="2026-07-09")],
    )
    got = writes_by_key(plan)
    assert set(got) == {"visit_date", "contact_with", "client_location"}
    assert all(write["restore"]["kind"] == "empty" for write in got.values())
    assert got["visit_date"]["target_cell"] == "C3"


def test_trusted_bot_visit_is_never_rolled_back():
    before = snap("before.xlsb", ["C-1", "Клиент", None, None, None, None])
    current = snap("current.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                    "CONSUMER", None, None])
    plan = repair.build_chain_plan(
        [before, current], FIELDS,
        [raw(visit_date="531", contact_with="CONSUMER")], {"C-1"},
    )
    assert plan["writes"] == []
    assert plan["skipped_counts"]["trusted_bot_visit"] == 1


def test_current_manual_divergence_is_kept():
    before = snap("before.xlsb", ["C-1", "Клиент", None, None, None, None])
    poisoned = snap("poisoned.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                      "CONSUMER", None, None])
    current = snap("current.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                    "исправлено сотрудником", None, None])
    plan = repair.build_chain_plan(
        [before, poisoned, current], FIELDS,
        [raw(visit_date="531", contact_with="CONSUMER")],
    )
    got = writes_by_key(plan)
    assert set(got) == {"visit_date"}
    assert plan["skipped_counts"]["current_diverged_kept"] == 1


def test_latest_pre_poison_value_wins_over_older_backup():
    first = snap("b1.xlsb", ["C-1", "Клиент", None, "старое", None, None])
    poisoned = snap("b2.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                "CONSUMER", None, None])
    manual = snap("b3.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                              "ручная правка позже", None, None])
    current = snap("current.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                    "CONSUMER", None, None])
    plan = repair.build_chain_plan(
        [first, poisoned, manual, current], FIELDS,
        [raw(visit_date="531", contact_with="CONSUMER")],
    )
    contact = writes_by_key(plan)["contact_with"]
    assert repair.decode_value(contact["restore"]) == "ручная правка позже"
    assert contact["source_snapshot"] == "b3.xlsb"


def test_no_transition_means_no_guess_and_second_run_is_idempotent():
    already_bad = snap("before.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                       "CONSUMER", None, None])
    current_bad = snap("current.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                        "CONSUMER", None, None])
    risk = [raw(visit_date="531", contact_with="CONSUMER")]
    plan = repair.build_chain_plan([already_bad, current_bad], FIELDS, risk)
    assert plan["writes"] == []
    assert plan["skipped_counts"]["no_proven_transition"] == 2

    before = snap("clean-before.xlsb", ["C-1", "Клиент", None, None, None, None])
    poisoned = snap("poisoned.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                      "CONSUMER", None, None])
    repaired = snap("repaired.xlsb", ["C-1", "Клиент", None, None, None, None])
    second = repair.build_chain_plan([before, poisoned, repaired], FIELDS, risk)
    assert second["writes"] == []
    assert second["skipped_counts"]["current_diverged_kept"] == 2


def test_scatter_or_row_structure_change_is_a_hard_evidence_barrier():
    before = snap("b1.xlsb", ["C-1", "Клиент", None, "старое", None, None])
    poisoned = snap("b2.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                "CONSUMER", None, None])
    # После доказанного старого перехода файл был заново разбросан: появился
    # другой КД. Нельзя переносить прежнее значение через границу новой версии.
    scattered = snap(
        "current.xlsb",
        ["C-1", "Клиент", datetime(1901, 6, 14), "CONSUMER", None, None],
        ["C-2", "Другой", None, None, None, None],
    )
    plan = repair.build_chain_plan(
        [before, poisoned, scattered], FIELDS,
        [raw(visit_date="531", contact_with="CONSUMER")],
    )
    assert plan["writes"] == []
    assert plan["skipped_counts"]["no_proven_transition"] == 2


def test_only_visit_columns_and_same_edge_date_evidence_are_allowed():
    before = snap("before.xlsb", ["C-1", "старое имя", "2025-01-01", None, None, None])
    current = snap("current.xlsb", ["C-1", "серверное имя", "2025-01-01",
                                    "CONSUMER", None, None])
    plan = repair.build_chain_plan(
        [before, current], FIELDS,
        [raw(visit_date="531", contact_with="CONSUMER", client="серверное имя")],
    )
    # Имя клиента вне allowlist; contact не доказан, потому что на том же ребре
    # отсутствует raw visit_date, которым старый enrich разрешал контракт.
    assert plan["writes"] == []
    assert plan["skipped_counts"]["no_proven_transition"] == 1


def test_ambiguous_duplicate_backup_rows_are_not_guessed():
    before = snap(
        "before.xlsb",
        ["C-1", "Клиент", None, "первое", None, None],
        ["C-1", "Клиент", None, "второе", None, None],
    )
    current = snap(
        "current.xlsb",
        ["C-1", "Клиент", datetime(1901, 6, 14), "CONSUMER", None, None],
        ["C-1", "Клиент", datetime(1901, 6, 14), "CONSUMER", None, None],
    )
    plan = repair.build_chain_plan(
        [before, current], FIELDS,
        [raw(visit_date="531", contact_with="CONSUMER")],
    )
    assert "contact_with" not in {write["key"] for write in plan["writes"]}


def test_real_xlsx_chain_is_read_and_date_serial_is_planned(tmp_path):
    from openpyxl import Workbook, load_workbook

    current = tmp_path / "Тестов Тест.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Портфели"
    sheet.append(HEADERS)
    sheet.append(["сх", "сх", "хард", "хард", "хард", "хард"])
    sheet.append(["C-1", "Клиент", None, None, None, None])
    sheet["C3"].number_format = "DD.MM.YYYY"
    workbook.save(current)
    backup = tmp_path / "Тестов Тест (до обогащения 22.07 10-00).xlsx"
    shutil.copy2(current, backup)

    workbook = load_workbook(current)
    sheet = workbook["Портфели"]
    sheet["C3"] = 531
    sheet["C3"].number_format = "DD.MM.YYYY"
    sheet["D3"] = "CONSUMER"
    workbook.save(current)

    job = {"employee": "Тестов Тест", "path": current,
           "rows": [raw(visit_date="531", contact_with="CONSUMER")],
           "explicit_file": True}
    plan = repair.scan_file(job, FIELDS, set())
    assert plan["errors"] == []
    assert len(plan["chain"]) == 2
    assert {write["key"] for write in plan["writes"]} == {"visit_date", "contact_with"}


def test_report_hash_detects_tampering():
    report = {"format_version": repair.FORMAT_VERSION, "files": [], "kind": "test"}
    repair.seal_report(report)
    repair.verify_report(report)
    report["files"].append({"path": "changed"})
    with pytest.raises(ValueError, match="хеш"):
        repair.verify_report(report)


def test_last_provenance_gate_rejects_visit_finished_after_dry_run(monkeypatch):
    plan = {"writes": [{"contract_id": "C-1"}]}
    monkeypatch.setattr(repair, "_request_json", lambda *_args, **_kwargs: {
        "rows": [{"contract_id": "C-1", "_bot_completed": True}],
    })
    with pytest.raises(RuntimeError, match="завершён визит"):
        repair.assert_contracts_still_untrusted("https://example.invalid", "key", plan)

    monkeypatch.setattr(repair, "_request_json", lambda *_args, **_kwargs: {
        "rows": [{"contract_id": "C-1", "_bot_completed": False}],
    })
    repair.assert_contracts_still_untrusted("https://example.invalid", "key", plan)


def test_atomic_install_keeps_exact_backup_and_never_accepts_stale_hash(tmp_path):
    original = tmp_path / "registry.xlsb"
    prepared = tmp_path / "registry.__repair__.xlsb"
    backup = tmp_path / "registry (до починки).xlsb"
    original.write_bytes(b"original bytes")
    prepared.write_bytes(b"repaired bytes")
    old_hash = repair.sha256_file(original)

    installed_backup = repair.install_verified_copy(
        original, prepared, old_hash, backup=backup)
    assert installed_backup == backup
    assert original.read_bytes() == b"repaired bytes"
    assert backup.read_bytes() == b"original bytes"
    assert not prepared.exists()

    stale_prepared = tmp_path / "stale.__repair__.xlsb"
    stale_prepared.write_bytes(b"must not install")
    with pytest.raises(RuntimeError, match="изменился"):
        repair.install_verified_copy(original, stale_prepared, old_hash,
                                     backup=tmp_path / "must-not-exist.xlsb")
    assert original.read_bytes() == b"repaired bytes"
    assert stale_prepared.exists()


def test_backup_names_sort_chronologically_and_collision_suffix_is_ordered(tmp_path):
    current = tmp_path / "Иванов Иван.xlsb"
    current.write_bytes(b"current")
    names = [
        "Иванов Иван (до обогащения 22.07 10-02).xlsb",
        "Иванов Иван (до обогащения 22.07 10-01, 2).xlsb",
        "Иванов Иван (до обогащения 22.07 10-01).xlsb",
    ]
    for name in names:
        (tmp_path / name).write_bytes(name.encode("utf-8"))
    got = [item["path"].name for item in repair.discover_backups(current)]
    assert got == [names[2], names[1], names[0]]


def test_verify_prepared_copy_rejects_any_unplanned_cell_change():
    before = snap("before.xlsb", ["C-1", "Клиент", datetime(1901, 6, 14),
                                  "CONSUMER", None, None])
    after = snap("after.xlsb", ["C-1", "ИМЯ ТОЖЕ ИЗМЕНИЛОСЬ", None,
                                "CONSUMER", None, None])
    write = {
        "target_row": 2, "target_col": 2, "target_cell": "C3",
        "restore": repair.encode_value(None),
    }
    with pytest.raises(RuntimeError, match="посторонние"):
        repair.verify_prepared_copy(before, after, [write])


@pytest.mark.skipif(os.name != "nt" or os.getenv("RUN_EXCEL_COM_TEST") != "1",
                    reason="явная проверка установленного Excel/COM")
def test_real_excel_com_prepares_and_atomically_installs_copy(tmp_path):
    """Ручной e2e: Excel видит только temp, оригинал меняется одним os.replace."""
    from openpyxl import Workbook, load_workbook
    import xlwings as xw

    current = tmp_path / "Тестов Тест.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Портфели"
    sheet.append(HEADERS)
    sheet.append(["сх", "сх", "хард", "хард", "хард", "хард"])
    sheet.append(["C-1", "Клиент", None, None, None, None])
    sheet["C3"].number_format = "DD.MM.YYYY"
    workbook.save(current)
    backup_before_enrich = tmp_path / "Тестов Тест (до обогащения 22.07 10-00).xlsx"
    shutil.copy2(current, backup_before_enrich)

    workbook = load_workbook(current)
    sheet = workbook["Портфели"]
    sheet["C3"] = 531
    sheet["C3"].number_format = "DD.MM.YYYY"
    sheet["D3"] = "CONSUMER"
    workbook.save(current)
    poisoned_hash = repair.sha256_file(current)
    plan = repair.scan_file(
        {"employee": "Тестов Тест", "path": current,
         "rows": [raw(visit_date="531", contact_with="CONSUMER")],
         "explicit_file": True},
        FIELDS, set(),
    )
    assert plan["cells_to_repair"] == 2

    repair.enrich._co_init()
    with repair.enrich.open_excel(xw) as app:
        app.display_alerts = False
        app.screen_updating = False
        prepared = repair.prepare_excel_copy(app, plan, FIELDS)
    assert repair.sha256_file(current) == poisoned_hash
    permanent_backup = repair.install_verified_copy(
        current, prepared, poisoned_hash,
        backup=tmp_path / "Тестов Тест (до починки ложного обогащения).xlsx")
    assert repair.sha256_file(permanent_backup) == poisoned_hash
    repaired = repair.read_workbook_snapshot(current, FIELDS)
    assert repaired["rows"]["C-1"][0]["values"]["visit_date"] in (None, "")
    assert repaired["rows"]["C-1"][0]["values"]["contact_with"] in (None, "")
