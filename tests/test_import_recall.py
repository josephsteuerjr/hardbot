"""Открепление по колонкам отзыва общего реестра («Дата отзыва» и соседние).

Отзыв — положительное утверждение самой строки: работает на частичной выборке
без full_registry, держится, пока колонка непуста, и не отменяется ни другим
ФИО, ни галочкой возврата ручных снятий. Экспортная схема и контракт обмена
не меняются ни на байт (test_export_schema_and_contract_are_untouched).
"""
import hashlib
from datetime import date, timedelta
from pathlib import Path

import pytest

import db
from exchange_contract import WORKBOOK_ALLOWED_IGNORED_HEADERS
from fields import FIELDS

RECALL_HEADERS = ["Дата отзыва", "Причина отзыва", "Передача на отзыв",
                  "Вывод из работы"]
EMP = "Иванов И."
CID = "RC-1"
EMPTY_DIGEST = hashlib.sha256(b"[]").hexdigest()


def _grid(rows: list[dict], with_recall: bool = True):
    """Мини-книга: у «Дата отзыва» тег «сх», у трёх остальных пусто — ровно как
    в боевом «Общий реестр новый формат.xlsb»."""
    extra = RECALL_HEADERS if with_recall else []
    header = [f.title for f in FIELDS] + extra
    types = [f.source for f in FIELDS] + (["сх", "", "", ""] if with_recall else [])
    out = [header, types]
    for r in rows:
        out.append([r.get(f.key, "") for f in FIELDS]
                   + [r.get(h, "") for h in extra])
    return out


def _row(cid=CID, resp=EMP, **extra):
    base = {"contract_id": cid, "client": f"Клиент {cid}",
            "birth_date": "1990-01-01", "responsible": resp,
            "address": "Худжанд, ул. Тестовая, 1", "debt_total": "10000"}
    base.update(extra)
    return base


def _isolated_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()


def _imp(rows, with_recall=True, **kw):
    rep = db.import_grid(_grid(rows, with_recall),
                         source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER, **kw)
    return rep


def test_recall_detaches_on_partial_import_without_full_registry(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    rep = _imp([_row(**{"Дата отзыва": "01.08.2026"})])
    assert not rep["blocked"], rep.get("error")
    row = db.get_client(CID)
    assert row["responsible"] == ""
    assert row["unassigned_reason"] == db.UNASSIGNED_REASON_RECALLED
    assert row["unassigned_from"] == EMP
    assert row["recall_date"] == "2026-08-01"
    assert rep["recalled"] == 1 and rep["recalled_had_owner"] == 1
    assert rep["recalled_by_employee"] == {EMP: 1}
    assert len(rep["recalled_digest"]) == 64
    assert rep["recall_columns"] == RECALL_HEADERS


def test_recall_survives_reimport_with_the_same_responsible(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _imp([_row(**{"Дата отзыва": "01.08.2026"})])
    rep = _imp([_row(**{"Дата отзыва": "01.08.2026"})])
    assert db.get_client(CID)["responsible"] == ""
    assert rep["recalled"] == 0          # эффект только на переходе
    assert rep["recall_held"] == 1
    assert rep["recalled_digest"] == EMPTY_DIGEST


def test_file_without_recall_columns_does_not_return_a_recalled_contract(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _imp([_row(**{"Дата отзыва": "01.08.2026"})])
    rep = _imp([_row()], with_recall=False)   # непустое ФИО, колонок отзыва нет
    row = db.get_client(CID)
    assert row["responsible"] == "" and rep["recall_held"] == 1
    assert row["unassigned_reason"] == db.UNASSIGNED_REASON_RECALLED


def test_cleared_recall_columns_return_the_contract(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _imp([_row(**{"Дата отзыва": "01.08.2026"})])
    rep = _imp([_row()])                      # колонки есть, ячейки пусты
    row = db.get_client(CID)
    assert row["responsible"] == EMP
    assert (row["unassigned_from"] or "") == ""
    assert (row["unassigned_reason"] or "") == ""
    assert row["recall_date"] is None         # снятый отзыв вычищен
    assert rep["recall_restored"] == 1 and rep["recall_restored_to_owner"] == 1
    assert len(rep["recall_restored_digest"]) == 64


def test_restore_requires_every_stored_recall_column_in_the_file(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _imp([_row(**{"Причина отзыва": "Погашение"})])

    # книга видит только «Дата отзыва» (пустую) — про признак в reason не знает
    header = [f.title for f in FIELDS] + ["Дата отзыва"]
    types = [f.source for f in FIELDS] + ["сх"]
    grid = [header, types,
            [(_row().get(f.key, "")) for f in FIELDS] + [""]]
    rep = db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    row = db.get_client(CID)
    assert row["responsible"] == ""           # возврата нет
    assert rep["recall_held"] == 1 and rep["recall_restored"] == 0
    # и хранившаяся причина отзыва НЕ затёрта доливкой None
    assert row["recall_reason"] == "Погашение"


@pytest.mark.parametrize("junk", ["", "-", "—", "0", "0,00", "нет", "н/д",
                                  "#Н/Д", "НЕТ ", "Не отозван", "не отозвано",
                                  "00:00:00", "30.12.1899"])
def test_junk_values_are_not_a_recall(tmp_path, monkeypatch, junk):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    rep = _imp([_row(**{"Дата отзыва": junk})])
    assert db.get_client(CID)["responsible"] == EMP
    assert rep["recalled"] == 0


def test_boolean_true_is_a_recall_and_false_is_not(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    rep = _imp([_row(**{"Передача на отзыв": True})])
    assert rep["recalled"] == 1
    assert db.get_client(CID)["recall_transfer"] == "да"
    # False не отзывает и, главное, НЕ возвращает ранее отозванное
    rep2 = _imp([_row(**{"Передача на отзыв": False})])
    assert rep2["recall_restored"] == 1       # галочку сняли — офис очистил
    _imp([_row(**{"Передача на отзыв": "да"})])
    rep3 = _imp([_row(**{"Передача на отзыв": False})])
    assert db.get_client(CID)["responsible"] == EMP


@pytest.mark.parametrize("col,val", [("Причина отзыва", "Погашение"),
                                     ("Передача на отзыв", "да")])
def test_any_meaningful_value_in_a_deciding_column_is_a_recall(tmp_path, monkeypatch, col, val):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    rep = _imp([_row(**{col: val})])
    assert rep["recalled"] == 1
    assert db.get_client(CID)["responsible"] == ""


# --- «Вывод из работы» — пометка, а не отзыв ---------------------------------
def test_withdrawal_column_does_not_detach(tmp_path, monkeypatch):
    """Коды «4» и «5» значат «отзовём в конце месяца», а не «отозван».

    Договор всё это время в реестре, с ФИО ответственного, и выездник по нему
    работает. Пока колонка считалась отзывом, она молча забирала договоры —
    16.09.2026 так снято 84 КД у 19 человек.
    """
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    for code in ("4", "5"):
        rep = _imp([_row(**{"Вывод из работы": code})])
        assert rep["recalled"] == 0, f"код «{code}» не отзыв"
        row = db.get_client(CID)
        assert row["responsible"] == EMP, "договор остаётся у выездника"
        assert row["recall_withdrawn"] == code, "пометку офиса всё равно храним"


def test_withdrawal_does_not_shield_a_real_recall(tmp_path, monkeypatch):
    """Рядом с настоящим отзывом пометка ничего не отменяет."""
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    rep = _imp([_row(**{"Вывод из работы": "4", "Причина отзыва": "Погашение"})])
    assert rep["recalled"] == 1
    assert db.get_client(CID)["responsible"] == ""


def test_contract_detached_by_the_old_rule_comes_back(tmp_path, monkeypatch):
    """Снятые прежним правилом возвращаются сами — обычным импортом.

    Иначе пришлось бы чинить руками 84 договора; возврат идёт штатной веткой
    «офис очистил колонки отзыва».
    """
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    with db.connect() as conn:            # как если бы сняло старое правило
        conn.execute("UPDATE clients SET responsible='', unassigned_from=?, "
                     "unassigned_reason=?, recall_withdrawn='4' WHERE contract_id=?",
                     (EMP, db.UNASSIGNED_REASON_RECALLED, CID))
        conn.commit()
    rep = _imp([_row(**{"Вывод из работы": "4"})])
    assert rep["recall_restored"] == 1
    row = db.get_client(CID)
    assert row["responsible"] == EMP and not (row["unassigned_reason"] or "")


def test_excel_serial_recall_date_is_stored_as_iso(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    rep = _imp([_row(**{"Дата отзыва": 46204})])
    # 1899-12-30 + 46204 дней = 2026-07-01 (см. tests/test_adversarial_fixes)
    assert db.get_client(CID)["recall_date"] == "2026-07-01"
    assert rep["recalled"] == 1


def test_future_and_unparsed_recall_dates(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row(cid="RC-F"), _row(cid="RC-U")])
    future = (date.today() + timedelta(days=30)).strftime("%d.%m.%Y")
    rep = _imp([_row(cid="RC-F", **{"Дата отзыва": future}),
                _row(cid="RC-U", **{"Дата отзыва": "по решению"})])
    assert db.get_client("RC-F")["responsible"] == ""    # будущая дата — уже отзыв
    assert db.get_client("RC-U")["responsible"] == ""
    assert rep["recalled_future_dated"] == 1
    assert rep["recalled_unparsed_date"] == 1


def test_new_row_with_recall_is_not_counted_as_taken_from_employee(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    rep = _imp([_row(cid="RC-NEW", **{"Дата отзыва": "01.08.2026"})])
    assert rep["recalled"] == 1
    assert rep["recalled_had_owner"] == 0
    assert rep["recalled_without_owner"] == 1
    assert rep["recalled_by_employee"] == {}   # ни у кого не забирали


def test_manual_unassign_survives_a_recall_cycle(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    db.unassign_clients(EMP, [CID])
    _imp([_row(**{"Дата отзыва": "01.08.2026"})])      # отзыв поверх ручного
    assert db.get_client(CID)["unassigned_reason"] == db.UNASSIGNED_REASON_RECALLED
    rep = _imp([_row()])                                # офис очистил колонки
    row = db.get_client(CID)
    assert row["responsible"] == ""                     # НЕ вернулся в работу
    assert row["unassigned_reason"] == db.UNASSIGNED_REASON_MANUAL
    assert rep["recall_restored"] == 0
    assert rep["recall_restored_to_manual"] == 1


def test_recall_relabels_absent_from_registry(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row(), _row(cid="RC-KEEP", resp="Пётров П.")])
    rep_full = db.import_grid(
        _grid([_row(cid="RC-KEEP", resp="Пётров П.")]),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        full_registry=True, authoritative_employees=[EMP, "Пётров П."])
    assert rep_full["auto_unassigned"] == 1
    rep = _imp([_row(**{"Дата отзыва": "01.08.2026"})])
    row = db.get_client(CID)
    assert row["responsible"] == ""
    assert row["unassigned_reason"] == db.UNASSIGNED_REASON_RECALLED
    assert rep["recalled"] == 1
    assert rep["recalled_previously_unassigned"] == 1


def test_recall_survives_a_new_responsible_in_the_file(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _imp([_row(**{"Дата отзыва": "01.08.2026"})])
    _imp([_row(resp="Новиков Н.", **{"Дата отзыва": "01.08.2026"})])
    assert db.get_client(CID)["responsible"] == ""      # снят, пока непусто
    rep = _imp([_row(resp="Новиков Н.")])               # очистили → новому
    assert db.get_client(CID)["responsible"] == "Новиков Н."
    assert rep["recall_restored"] == 1


def test_recall_is_sticky_across_duplicate_contract_rows(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    grid = _grid([_row(), _row(**{"Дата отзыва": "01.08.2026"})])
    rep = db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert db.get_client(CID)["responsible"] == ""      # вторая строка победила
    assert rep["recalled"] == 1


def test_preview_rolls_back_and_apply_matches_digest(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    prev = db.import_grid(_grid([_row(**{"Дата отзыва": "01.08.2026"})]),
                          source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
                          preview_only=True)
    assert prev["recalled"] == 1
    assert prev["recalled_sample"]                       # sample только в preview
    assert db.get_client(CID)["responsible"] == EMP      # БД не тронута
    rep = _imp([_row(**{"Дата отзыва": "01.08.2026"})],
               expected_recalled=prev["recalled"],
               expected_recalled_digest=prev["recalled_digest"],
               expected_recall_restored=prev["recall_restored"],
               expected_recall_restored_digest=prev["recall_restored_digest"])
    assert not rep["blocked"]
    assert rep["recalled_sample"] == []                  # в apply ПД не консервируем
    assert db.get_client(CID)["responsible"] == ""


def test_stale_recall_set_blocks_apply(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    rep = _imp([_row(**{"Дата отзыва": "01.08.2026"})],
               expected_recalled=2,             # оператор видел другое множество
               expected_recalled_digest=EMPTY_DIGEST,
               expected_recall_restored=0,
               expected_recall_restored_digest=EMPTY_DIGEST)
    assert rep["blocked"] and rep.get("preview_stale")
    assert db.get_client(CID)["responsible"] == EMP      # транзакция откачена


def test_export_schema_and_contract_are_untouched(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    assert not (set(db.RECALL_KEYS) & {f.key for f in FIELDS})
    for title in RECALL_HEADERS:
        assert title in WORKBOOK_ALLOWED_IGNORED_HEADERS
    with db.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(clients)")}
    assert set(db.RECALL_KEYS) <= cols and "recall_prev_reason" in cols
    rep = _imp([_row()])
    assert rep["unknown_columns"] == []


def test_recalled_contract_leaves_the_route_and_the_portfolio(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    import scoring
    _imp([_row(), _row(cid="RC-2", **{"birth_date": "1990-01-01"})])
    _imp([_row(**{"Дата отзыва": "01.08.2026"}),
          _row(cid="RC-2")])
    persons = db.persons_for_employee(EMP)
    cids = {c["contract_id"] for p in persons for c in p["contracts"]}
    assert CID not in cids and "RC-2" in cids
    items = scoring._items_from_db()
    reps = {it["rep"] for it in items}
    assert CID not in reps and "RC-2" in reps


def test_unassigned_overview_lists_recalled(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _imp([_row(**{"Дата отзыва": "01.08.2026", "Причина отзыва": "Погашение"})])
    data = db.unassigned_overview()
    assert data["totals"]["recalled"] == 1
    assert data["by_employee"] == {EMP: 1}
    r = data["rows"][0]
    assert r["recall_date"] == "2026-08-01" and r["recall_reason"] == "Погашение"
