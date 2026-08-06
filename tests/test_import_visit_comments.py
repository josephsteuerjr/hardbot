"""Контракт текущего и историчного комментариев при импорте реестра."""

import json
from pathlib import Path

import db
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
    grid: list[list], *, replace_current: bool, import_historical: bool
) -> tuple[dict, dict]:
    preview = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        preview_only=True,
        replace_current_visit_comments=replace_current,
        import_historical_visit_comments=import_historical,
    )
    assert preview["blocked"] is False
    applied = db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        replace_current_visit_comments=replace_current,
        import_historical_visit_comments=import_historical,
        expected_comment_effect_digest=preview["comment_effect_digest"],
    )
    assert applied["blocked"] is False
    return preview, applied


def test_ordinary_import_preserves_both_existing_comments(tmp_path, monkeypatch):
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
    assert report["historical_comments_preserved"] == 1
    assert saved["visit_comment"] == "текущий БД"
    assert saved["historical_visit_comment"] == "история БД"


def test_new_contract_accepts_current_but_not_historical_without_opt_in(
    tmp_path, monkeypatch
):
    _init(tmp_path, monkeypatch)

    report = _seed(_row("COMMENTS-NEW", "свежий офисный", "не грузить"))

    saved = db.get_client("COMMENTS-NEW")
    assert report["inserted"] == 1
    assert saved["visit_comment"] == "свежий офисный"
    assert not (saved["historical_visit_comment"] or "")


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

    preview, applied = _preview_and_apply(
        grid, replace_current=True, import_historical=True
    )

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

    _preview_and_apply(
        _grid(_row("COMMENTS-PROOF", None, "история из реестра")),
        replace_current=True,
        import_historical=True,
    )

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
        import_historical_visit_comments=True,
    )

    assert report["blocked"] is True
    assert report["missing_comment_headers"] == ["historical_visit_comment"]


def test_historical_comment_is_read_only_and_rendered_on_third_tab(
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
        Path(__file__).parents[1] / "hardbot" / "miniapp" / "index.html"
    ).read_text(encoding="utf-8")

    assert card["historical_visit_comment"] == "раньше обещал оплатить"
    assert "historical_visit_comment" not in web.ALLOWED_SAVE
    assert '${tab("visit",t.visit)}${tab("debt",t.debt)}${tab("client",t.client)}' in miniapp
    assert "d.historical_visit_comment" in miniapp
    assert miniapp.index("function panelClient") < miniapp.index(
        "d.historical_visit_comment"
    ) < miniapp.index("function panelDebt")
    assert 'FORM_TOUCHED.add("historical_visit_comment")' not in miniapp
    assert 'if(d.visit_comment) FORM_TOUCHED.add("visit_comment")' not in miniapp
