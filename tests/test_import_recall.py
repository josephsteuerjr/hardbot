"""Колонки отзыва общего реестра — данные, а не команда.

Решение владельца 22.09.2026: НИКАКАЯ колонка отзыва не открепляет договор.
Отзыв делают люди: в конце месяца аналитик удаляет строку из реестра
(путь `absent_from_registry`), а сразу после отзыва руководитель снимает
ответственного в пульте (ручное снятие, оно держится). Пока колонки решали,
они молча забирали договоры: 16.09 «Вывод из работы» снял 84 КД у 19 человек,
а 22.09 «Дата отзыва»/«Причина отзыва» — ещё 227 у 39.

Легаси-метка `recalled` (наследие старого правила) снимается первой же
строкой файла с ответственным. Экспортная схема и контракт обмена не
меняются ни на байт (test_export_schema_and_contract_are_untouched).
"""
import hashlib
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


def _mark_recalled(cid=CID, frm=EMP, **set_extra):
    """Поставить легаси-метку так, как её ставило старое правило."""
    with db.connect() as conn:
        conn.execute(
            "UPDATE clients SET responsible='', unassigned_from=?, "
            f"unassigned_reason='{db.UNASSIGNED_REASON_RECALLED}' "
            f"WHERE contract_id=?", (frm, cid))
        for col, val in set_extra.items():
            conn.execute(f"UPDATE clients SET {col}=? WHERE contract_id=?",
                         (val, cid))
        conn.commit()


# --- ни одна колонка не открепляет -------------------------------------------
@pytest.mark.parametrize("col,val", [
    ("Дата отзыва", "01.08.2026"),
    ("Дата отзыва", 46204),                 # серийная дата Excel → 2026-07-01
    ("Причина отзыва", "Погашение"),
    ("Причина отзыва", "Вывод из работы"),  # ровно то, что писал офис 22.09
    ("Передача на отзыв", "да"),
    ("Передача на отзыв", True),
    ("Вывод из работы", "4"),
    ("Вывод из работы", "5"),
])
def test_no_recall_column_detaches_a_contract(tmp_path, monkeypatch, col, val):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    rep = _imp([_row(**{col: val})])
    assert not rep["blocked"], rep.get("error")
    row = db.get_client(CID)
    assert row["responsible"] == EMP, "договор остаётся у выездника"
    assert not (row["unassigned_reason"] or "")
    assert rep["recalled"] == 0


def test_recall_values_are_stored_as_data(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _imp([_row(**{"Дата отзыва": 46204, "Причина отзыва": "полный отзыв портфеля",
                  "Вывод из работы": "4"})])
    row = db.get_client(CID)
    assert row["recall_date"] == "2026-07-01"
    assert row["recall_reason"] == "полный отзыв портфеля"
    assert row["recall_withdrawn"] == "4"
    assert row["responsible"] == EMP


@pytest.mark.parametrize("junk", ["", "-", "—", "0", "0,00", "нет", "н/д",
                                  "#Н/Д", "НЕТ ", "Не отозван", "не отозвано",
                                  "00:00:00", "30.12.1899"])
def test_junk_values_are_stored_as_nothing(tmp_path, monkeypatch, junk):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    rep = _imp([_row(**{"Дата отзыва": junk})])
    assert db.get_client(CID)["responsible"] == EMP
    assert rep["recalled"] == 0


def test_unrecognized_nonempty_value_is_counted(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    rep = _imp([_row(**{"Дата отзыва": "-"})])   # непусто, но отзывом не признано
    assert rep["recalled_unrecognized"] == 1
    assert rep["recalled_unrecognized_sample"][0]["contract_id"] == CID
    assert db.get_client(CID)["responsible"] == EMP


def test_new_row_with_recall_keeps_responsible(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    rep = _imp([_row(cid="RC-NEW", **{"Дата отзыва": "01.08.2026"})])
    row = db.get_client("RC-NEW")
    assert row["responsible"] == EMP
    assert row["recall_date"] == "2026-08-01"
    assert rep["recalled"] == 0
    assert rep["recalled_by_employee"] == {}   # ни у кого не забирали


def test_recall_mark_is_sticky_across_duplicate_contract_rows(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    grid = _grid([_row(), _row(**{"Причина отзыва": "Погашение"})])
    rep = db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    row = db.get_client(CID)
    assert row["responsible"] == EMP
    assert row["recall_reason"] == "Погашение"   # пометка из второй строки не потеряна


# --- легаси-метка «recalled» возвращается строкой файла ----------------------
def test_legacy_recalled_returns_with_any_responsible(tmp_path, monkeypatch):
    """Ровно так чинится база после правила «колонки открепляют»: первая же
    строка файла с ответственным возвращает договор в работу."""
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _mark_recalled(recall_date="2026-09-15", recall_reason="полный отзыв портфеля")
    rep = _imp([_row(**{"Дата отзыва": "15.09.2026",
                        "Причина отзыва": "полный отзыв портфеля"})])
    row = db.get_client(CID)
    assert row["responsible"] == EMP
    assert not (row["unassigned_from"] or "")
    assert not (row["unassigned_reason"] or "")
    assert rep["recall_restored"] == 1 and rep["recall_restored_to_owner"] == 1
    assert rep["recall_restored_by_employee"] == {EMP: 1}
    assert len(rep["recall_restored_digest"]) == 64


def test_legacy_recalled_returns_without_recall_columns(tmp_path, monkeypatch):
    """Колонок отзыва в книге нет — договор всё равно возвращается:
    имя ответственного в строке важнее любых колонок."""
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _mark_recalled()
    rep = _imp([_row()], with_recall=False)
    assert db.get_client(CID)["responsible"] == EMP
    assert rep["recall_restored"] == 1


def test_legacy_recalled_without_responsible_stays_unassigned(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _mark_recalled()
    rep = _imp([_row(resp="")])
    row = db.get_client(CID)
    assert row["responsible"] == ""
    assert row["unassigned_reason"] == db.UNASSIGNED_REASON_RECALLED
    assert rep["recall_restored"] == 0


def test_legacy_recalled_goes_to_the_files_responsible(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _mark_recalled()
    _imp([_row(resp="Новиков Н.")])
    assert db.get_client(CID)["responsible"] == "Новиков Н."


def test_legacy_recalled_not_reannounced_by_the_same_book(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _mark_recalled()
    _imp([_row()])
    rep = _imp([_row()])
    assert rep["recall_restored"] == 0          # эффект только на переходе
    assert rep["recall_restored_digest"] == EMPTY_DIGEST


def test_manual_unassign_is_not_touched_by_recall_columns(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    db.unassign_clients(EMP, [CID])
    _imp([_row(**{"Дата отзыва": "01.08.2026", "Причина отзыва": "Погашение"})])
    row = db.get_client(CID)
    assert row["responsible"] == ""
    assert row["unassigned_reason"] == db.UNASSIGNED_REASON_MANUAL


def test_restored_contract_is_back_in_the_portfolio(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    import scoring
    _imp([_row(), _row(cid="RC-2")])
    _mark_recalled(CID)
    _imp([_row(), _row(cid="RC-2")])
    persons = db.persons_for_employee(EMP)
    cids = {c["contract_id"] for p in persons for c in p["contracts"]}
    assert CID in cids and "RC-2" in cids
    items = scoring._items_from_db()
    reps = {it["rep"] for it in items}
    assert CID in reps


# --- гейт предпросмотра для возврата ------------------------------------------
def test_preview_rolls_back_and_apply_matches_digest(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _mark_recalled()
    prev = db.import_grid(_grid([_row()]),
                          source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
                          preview_only=True)
    assert prev["recall_restored"] == 1
    assert db.get_client(CID)["responsible"] == ""      # БД не тронута
    rep = _imp([_row()],
               expected_recalled=prev["recalled"],
               expected_recalled_digest=prev["recalled_digest"],
               expected_recall_restored=prev["recall_restored"],
               expected_recall_restored_digest=prev["recall_restored_digest"])
    assert not rep["blocked"], rep.get("error")
    assert db.get_client(CID)["responsible"] == EMP


def test_stale_restore_set_blocks_apply(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _mark_recalled()
    rep = _imp([_row()],
               expected_recalled=0, expected_recalled_digest=EMPTY_DIGEST,
               expected_recall_restored=5,         # оператор видел другое множество
               expected_recall_restored_digest=EMPTY_DIGEST)
    assert rep["blocked"] and rep.get("preview_stale")
    assert db.get_client(CID)["responsible"] == ""      # транзакция откачена


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


def test_unassigned_overview_lists_legacy_recalled(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _imp([_row()])
    _mark_recalled(recall_date="2026-08-01", recall_reason="Погашение")
    data = db.unassigned_overview()
    assert data["totals"]["recalled"] == 1
    assert data["by_employee"] == {EMP: 1}
    r = data["rows"][0]
    assert r["recall_date"] == "2026-08-01" and r["recall_reason"] == "Погашение"
