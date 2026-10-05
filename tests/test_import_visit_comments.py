"""Контракт текущего и историчного комментариев при импорте реестра."""

import json
from pathlib import Path

import db
import exchange_contract as contract
import web


def _grid(*rows: dict) -> list[list]:
    return [
        [field.title for field in db.FIELDS],
        [field.source for field in db.FIELDS],
        *[[row.get(field.key) for field in db.FIELDS] for row in rows],
    ]


def _row(cid: str, current: str | None, historical: str | None) -> dict:
    return {
        "contract_id": cid,
        "client": f"Клиент {cid}",
        "birth_date": "1990-01-01",
        "responsible": "Тестов Сотрудник",
        "visit_comment": current,
        "historical_visit_comment": historical,
    }


def _init(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()


def _seed(*rows: dict) -> dict:
    report = db.import_grid(
        _grid(*rows), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER
    )
    assert report["blocked"] is False
    return report


def _preview_and_apply(
    grid: list[list], *, replace_current: bool
) -> tuple[dict, dict]:
    preview = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        preview_only=True,
        replace_current_visit_comments=replace_current,
    )
    assert preview["blocked"] is False
    applied = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        replace_current_visit_comments=replace_current,
        expected_comment_effect_digest=preview["comment_effect_digest"],
    )
    assert applied["blocked"] is False
    return preview, applied


def test_ordinary_import_keeps_current_and_takes_history_from_file(
    tmp_path, monkeypatch
):
    """Обычный импорт: текущий комментарий — совместный, его не трогаем;
    историчный односторонний, он приходит из файла и повторяет файл."""
    _init(tmp_path, monkeypatch)
    _seed(_row("COMMENTS-KEEP", "текущий БД", None))
    db.update_fields(
        "COMMENTS-KEEP",
        {"historical_visit_comment": "история БД"},
    )

    report = db.import_grid(
        _grid(_row("COMMENTS-KEEP", "текст файла", "история файла")),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )

    saved = db.get_client("COMMENTS-KEEP")
    assert report["blocked"] is False
    assert report["current_comments_preserved"] == 1
    assert report["historical_comments_column"] is True
    assert report["historical_comments_imported"] == 1
    assert saved["visit_comment"] == "текущий БД"
    assert saved["historical_visit_comment"] == "история файла"


def test_history_without_its_column_in_the_file_is_left_alone(
    tmp_path, monkeypatch
):
    """Молчание файла — не приказ стереть. Колонки нет — история в базе цела,
    и импорт из-за неё не блокируется."""
    _init(tmp_path, monkeypatch)
    _seed(_row("COMMENTS-NOCOL", "текущий", None))
    db.update_fields("COMMENTS-NOCOL", {"historical_visit_comment": "история БД"})

    report = db.import_grid(
        [
            ["Номер КД / ID", "ответственный Сотрудник"],
            ["сх", "сх"],
            ["COMMENTS-NOCOL", "Тестов Сотрудник"],
        ],
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )

    assert report["blocked"] is False
    assert report["historical_comments_column"] is False
    assert report["historical_comments_preserved"] == 1
    assert db.get_client("COMMENTS-NOCOL")[
        "historical_visit_comment"
    ] == "история БД"


def test_new_contract_gets_its_history_without_any_toggle(tmp_path, monkeypatch):
    """Ради чего всё и сделано: раньше историчный текст выбрасывался и у новой
    строки тоже. Выездник получал сорок договоров без единой строки истории —
    и узнать об этом было неоткуда: галочку просто забывали."""
    _init(tmp_path, monkeypatch)

    report = _seed(_row("COMMENTS-NEW", "свежий офисный", "обещал 10-го, не оплатил"))

    saved = db.get_client("COMMENTS-NEW")
    assert report["inserted"] == 1
    assert saved["visit_comment"] == "свежий офисный"
    assert saved["historical_visit_comment"] == "обещал 10-го, не оплатил"


def test_explicit_modes_replace_and_clear_exact_file_values(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _seed(
        _row("COMMENTS-REPLACE", "текущий старый", None),
        _row("COMMENTS-CLEAR", "очистить текущий", None),
    )
    db.update_fields(
        "COMMENTS-REPLACE",
        {"historical_visit_comment": "история старая"},
    )
    db.update_fields(
        "COMMENTS-CLEAR",
        {"historical_visit_comment": "очистить историю"},
    )
    grid = _grid(
        _row("COMMENTS-REPLACE", "текущий новый", "история новая"),
        _row("COMMENTS-CLEAR", None, None),
    )

    preview, applied = _preview_and_apply(grid, replace_current=True)

    assert preview["current_comments_replaced"] == 1
    assert preview["current_comments_cleared"] == 1
    assert preview["historical_comments_imported"] == 1
    assert preview["historical_comments_cleared"] == 1
    assert db.get_client("COMMENTS-REPLACE")["visit_comment"] == "текущий новый"
    assert db.get_client("COMMENTS-REPLACE")[
        "historical_visit_comment"
    ] == "история новая"
    assert db.get_client("COMMENTS-CLEAR")["visit_comment"] == ""
    assert db.get_client("COMMENTS-CLEAR")["historical_visit_comment"] == ""
    assert applied["comment_effect_digest"] == preview["comment_effect_digest"]


def test_month_reset_does_not_rewrite_proven_visit_snapshot_or_history(
    tmp_path, monkeypatch
):
    _init(tmp_path, monkeypatch)
    _seed(_row("COMMENTS-PROOF", "до визита", None))
    assert db.log_visit(
        "COMMENTS-PROOF", 123, '{"result":"visited"}', op_id="comments-proof"
    )
    assert db.record_bot_visit(
        "COMMENTS-PROOF",
        {"call_status": "Оплачен", "visit_comment": "факт визита"},
        "2026-08-05",
    ) == 1
    before = db.get_client("COMMENTS-PROOF")
    immutable_before = before["bot_visit_data"]

    # «Дата выезда» в файле — это и есть месячный сброс: реестр видел ЭТОТ
    # выезд, значит пустая ячейка комментария — решение аналитика. Строку без
    # даты выезда (копия старше визита) с 15.09.2026 разбирает отдельный тест:
    # стирать последний экземпляр текста выездника импорт не вправе.
    month_reset_row = _row("COMMENTS-PROOF", None, "история из реестра")
    month_reset_row["visit_date"] = "2026-08-05"

    _preview_and_apply(_grid(month_reset_row), replace_current=True)

    after = db.get_client("COMMENTS-PROOF")
    assert after["visit_comment"] == ""
    assert after["historical_visit_comment"] == "история из реестра"
    assert after["bot_visit_data"] == immutable_before
    assert json.loads(after["bot_visit_data"])["visit_comment"] == "факт визита"
    assert db.bot_visit_snapshot(after)["visit_comment"] == ""
    with db.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM visits WHERE contract_id='COMMENTS-PROOF'"
        ).fetchone()[0] == 1


def test_apply_blocks_when_comment_state_changed_after_preview(
    tmp_path, monkeypatch
):
    _init(tmp_path, monkeypatch)
    _seed(_row("COMMENTS-STALE", "до preview", None))
    grid = _grid(_row("COMMENTS-STALE", "из файла", None))
    preview = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        preview_only=True,
        replace_current_visit_comments=True,
    )
    assert preview["current_comments_replaced"] == 1
    db.update_fields("COMMENTS-STALE", {"visit_comment": "параллельная правка"})

    report = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        replace_current_visit_comments=True,
        expected_comment_effect_digest=preview["comment_effect_digest"],
    )

    assert report["blocked"] is True
    assert report["preview_stale"] is True
    assert db.get_client("COMMENTS-STALE")[
        "visit_comment"
    ] == "параллельная правка"


def test_requested_comment_action_requires_its_real_header(tmp_path, monkeypatch):
    """Замена текущих комментариев — решение оператора, и без своей колонки оно
    бессмысленно: «заменить значениями файла», которых в файле нет, очистило бы
    всё подряд. Историчный комментарий сюда не входит: у него решения нет,
    есть колонка — грузится, нет колонки — тишина."""
    _init(tmp_path, monkeypatch)
    grid = [
        ["Номер КД / ID", "ответственный Сотрудник"],
        ["сх", "сх"],
        ["COMMENTS-NO-COLUMN", "Тестов Сотрудник"],
    ]

    report = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        preview_only=True,
        replace_current_visit_comments=True,
    )

    assert report["blocked"] is True
    assert report["missing_comment_headers"] == ["visit_comment"]


def test_historical_comment_is_read_only_and_rendered_on_client_tab(
    tmp_path, monkeypatch
):
    _init(tmp_path, monkeypatch)
    _seed(_row("COMMENTS-READONLY", "текущий", None))
    db.update_fields(
        "COMMENTS-READONLY",
        {"historical_visit_comment": "раньше обещал оплатить"},
    )

    card = web._person_full("COMMENTS-READONLY", "ru")
    miniapp = (
        Path(__file__).parents[1] / "server" / "miniapp" / "index.html"
    ).read_text(encoding="utf-8")

    assert card["historical_visit_comment"] == "раньше обещал оплатить"
    assert "historical_visit_comment" not in web.ALLOWED_SAVE
    assert '${tab("debt",t.debt)}${tab("client",t.client)}' in miniapp
    assert "d.historical_visit_comment" in miniapp
    assert miniapp.index("function panelClient") < miniapp.index(
        "d.historical_visit_comment"
    ) < miniapp.index("function panelDebt")
    assert 'FORM_TOUCHED.add("historical_visit_comment")' not in miniapp
    assert 'if(d.visit_comment) FORM_TOUCHED.add("visit_comment")' not in miniapp


# --- замена комментариев не откатывает доказанный визит -------------------------
# 10.09.2026: загрузка «ОБЩИЙ РЕЕСТР СЕНТЯБРЬ» с заменой комментариев очистила
# живой комментарий у выездов того же дня — файл их ещё не видел. Обогащение
# после этого молчало: пустое значение в план не попадает.
def _visit_row(cid: str, current: str | None, visit_date: str | None) -> dict:
    row = _row(cid, current, None)
    row["visit_date"] = visit_date
    return row


def test_replace_does_not_clear_comment_of_visit_file_has_not_seen(
    tmp_path, monkeypatch
):
    _init(tmp_path, monkeypatch)
    _seed(_visit_row("COMMENTS-FRESH", None, None))
    db.record_bot_visit(
        "COMMENTS-FRESH",
        {"call_status": "В работе", "visit_comment": "приехал, вышла мать"},
        "2026-09-10",
    )

    # В файле у строки нет ни выезда, ни комментария: копия старше визита.
    preview, applied = _preview_and_apply(
        _grid(_visit_row("COMMENTS-FRESH", None, None)),
        replace_current=True,
    )

    saved = db.get_client("COMMENTS-FRESH")
    assert preview["current_comments_protected"] == 1
    assert applied["current_comments_protected"] == 1
    assert applied["current_comments_cleared"] == 0
    assert saved["visit_comment"] == "приехал, вышла мать"
    assert db.bot_visit_snapshot(saved)["visit_comment"] == "приехал, вышла мать"


def test_replace_still_clears_comment_when_file_saw_the_visit(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _seed(_visit_row("COMMENTS-MONTH", None, None))
    db.record_bot_visit(
        "COMMENTS-MONTH",
        {"call_status": "В работе", "visit_comment": "августовский текст"},
        "2026-08-20",
    )

    # Начало месяца: файл видел тот самый выезд, пустая ячейка — решение аналитика.
    _preview, applied = _preview_and_apply(
        _grid(_visit_row("COMMENTS-MONTH", None, "2026-08-20")),
        replace_current=True,
    )

    saved = db.get_client("COMMENTS-MONTH")
    assert applied["current_comments_cleared"] == 1
    assert applied["current_comments_protected"] == 0
    assert str(saved["visit_comment"] or "") == ""


def test_history_change_without_a_fresh_preview_is_blocked(tmp_path, monkeypatch):
    """Галочки у истории больше нет — значит слепая массовая очистка стала
    ближе: хватит файла с пустой колонкой. Гейт тот же, что у колонок отзыва:
    есть эффект и нет свежего предпросмотра — импорт не применяется, история
    остаётся на месте."""
    _init(tmp_path, monkeypatch)
    _seed(_row("COMMENTS-GATE", "текущий", None))
    db.update_fields("COMMENTS-GATE", {"historical_visit_comment": "история БД"})

    grid = _grid(_row("COMMENTS-GATE", "текущий", None))
    proof = contract.build_test_subset_source_proof(
        grid,
        approval_id="limited-test-comments-20260916",
        contracts=[{"contract_id": "COMMENTS-GATE",
                    "responsible": "Тестов Сотрудник"}],
    )
    db.register_import_authorization(
        proof, source_kind=db.IMPORT_SOURCE_TEST_SUBSET)

    blocked = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        source_proof=proof,
        require_source_proof=True,
    )

    assert blocked["blocked"] is True
    assert blocked["comment_preview_required"] is True
    assert blocked["historical_comments_cleared"] == 1
    assert db.get_client("COMMENTS-GATE")[
        "historical_visit_comment"
    ] == "история БД"


def test_enrichment_row_names_the_cleared_comment(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _seed(_visit_row("COMMENTS-MARKER", None, None))
    db.record_bot_visit(
        "COMMENTS-MARKER",
        {"call_status": "В работе", "visit_comment": "текст выездника"},
        "2026-09-10",
    )
    db.update_fields("COMMENTS-MARKER", {"visit_comment": ""})

    row = next(r for r in db.enrichment_rows()
               if r["contract_id"] == "COMMENTS-MARKER")

    # Комментарий возить нечем, но кейс назван вслух: чинить на сервере.
    assert row[db.BOT_VISIT_MARKER] is True
    assert row[db.VISIT_COMMENT_CLEARED_MARKER] is True
    assert str(row["visit_comment"] or "") == ""
