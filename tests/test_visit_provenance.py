"""Provenance завершённого визита: импортные HARD-поля не равны работе бота."""

import asyncio
import json
from datetime import date

import pytest
from aiohttp import web as aiohttp_web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

import db
import web as webmod


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()


def _import(*rows):
    keys = ["contract_id", "client", "birth_date", "responsible", "city",
            "visit_date", "call_status", "contact_with", "agreed_payment",
            "who_at_address", "client_location", "purpose_rf", "visit_comment"]
    titles = [db.BY_KEY[k].title for k in keys]
    grid = [titles]
    for values in rows:
        grid.append([values.get(k) for k in keys])
    return db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )


def _safe(cid: str) -> dict:
    return next(r for r in db.enrichment_rows() if r["contract_id"] == cid)


def test_enrichment_hides_imported_pseudo_visit():
    _import({
        "contract_id": "PV-RAW", "client": "Импортный Мусор",
        "responsible": "Сотрудник", "visit_date": "531",
        "contact_with": "CONSUMER", "agreed_payment": "Не определен",
        "who_at_address": "Не определен", "client_location": "2026-07-09",
        "visit_comment": "2024-12-21",
    })

    raw = next(r for r in db.registry_rows() if r["contract_id"] == "PV-RAW")
    assert raw["visit_date"] == "531"                 # БД не переписываем догадкой
    row = _safe("PV-RAW")
    assert row[db.BOT_VISIT_MARKER] is False
    assert all(row[k] is None for k in db.BOT_VISIT_KEYS)


def test_completed_snapshot_uses_only_submitted_values_and_propagates():
    _import(
        {"contract_id": "PV-1", "client": "Один Человек", "birth_date": "1990-01-01",
         "responsible": "Выездник", "visit_date": "531", "contact_with": "CONSUMER"},
        {"contract_id": "PV-2", "client": "Один Человек", "birth_date": "1990-01-01",
         "responsible": "Выездник", "visit_date": "531", "contact_with": "CONSUMER"},
    )
    today = date.today().isoformat()
    db.update_person("PV-1", {"visit_date": today, "call_status": "Оплачен"})
    db.log_visit("PV-1", 101, "визит", op_id="pv-real")
    assert db.record_bot_visit(
        "PV-1", {"call_status": "Оплачен", "visit_comment": "говорил лично",
                 "tips": "998901234567"}, today
    ) == 2

    for cid in ("PV-1", "PV-2"):
        row = _safe(cid)
        assert row[db.BOT_VISIT_MARKER] is True
        assert row["visit_date"] == today
        assert row["call_status"] == "Оплачен"
        assert row["visit_comment"] == "говорил лично"
        assert row["tips"] == "998901234567"
        assert row["contact_with"] is None             # CONSUMER не был в POST
        assert json.loads(db.get_client(cid)["bot_visit_data"])["tips"] == "998901234567"
    assert len(db.persons_processed("Выездник", today)) == 1
    assert db.stats_all()["visited"] == 2


def test_later_import_cannot_overwrite_confirmed_visit_fields():
    _import({"contract_id": "PV-PROT", "client": "Защищённый", "responsible": "Агент",
             "city": "Ташкент"})
    today = date.today().isoformat()
    db.update_person("PV-PROT", {"visit_date": today, "call_status": "Оплачен"})
    db.log_visit("PV-PROT", 102, "визит", op_id="pv-protected")
    db.record_bot_visit("PV-PROT", {"call_status": "Оплачен"}, today)

    _import({"contract_id": "PV-PROT", "client": "Защищённый", "responsible": "Агент",
             "city": "Самарканд", "visit_date": "531", "call_status": "Нет контакта",
             "contact_with": "CONSUMER"})
    raw = db.get_client("PV-PROT")
    assert raw["city"] == "Самарканд"                 # обычный источник обновился
    assert raw["visit_date"] == today                 # результат выездника защищён
    assert raw["call_status"] == "Оплачен"
    assert _safe("PV-PROT")["call_status"] == "Оплачен"


def test_tips_hidden_without_visit_and_legacy_snapshot_gets_bot_owned_fallback():
    """Черновик не выгружается; старые доказанные снимки не теряют уже сохранённые tips."""
    _import({"contract_id": "PV-TIPS", "client": "Наводки",
             "responsible": "Агент"})
    db.update_person("PV-TIPS", {"tips": "932840003"})
    draft = _safe("PV-TIPS")
    assert draft[db.BOT_VISIT_MARKER] is False
    assert draft.get("tips") is None

    today = date.today().isoformat()
    db.update_person("PV-TIPS", {"visit_date": today, "call_status": "В работе"})
    # Имитируем snapshot до релиза 24.07: tips ещё не входил в JSON.
    legacy = json.dumps(
        {"visit_date": today, "call_status": "В работе"},
        ensure_ascii=False, separators=(",", ":"),
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE clients SET bot_visit_at=?, bot_visit_data=? WHERE contract_id=?",
            (today, legacy, "PV-TIPS"),
        )
        conn.commit()
    safe = _safe("PV-TIPS")
    assert safe[db.BOT_VISIT_MARKER] is True
    assert safe["tips"] == "932840003"


def test_one_time_backfill_accepts_only_history_with_matching_date():
    today = date.today().isoformat()
    _import(
        {"contract_id": "PV-OLD-OK", "client": "Старый Настоящий",
         "responsible": "Агент", "visit_date": today, "call_status": "Оплачен"},
        {"contract_id": "PV-OLD-BAD", "client": "Старый Ложный",
         "responsible": "Агент", "visit_date": "531", "contact_with": "CONSUMER"},
    )
    db.log_visit("PV-OLD-OK", 103, "визит", op_id="pv-old-ok")
    db.log_visit("PV-OLD-BAD", 103, "визит", op_id="pv-old-bad")
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key=?",
                     ("migration_bot_visit_provenance_v1",))
        conn.commit()

    db.init_db()
    assert _safe("PV-OLD-OK")[db.BOT_VISIT_MARKER] is True
    assert _safe("PV-OLD-BAD")[db.BOT_VISIT_MARKER] is False


def test_reset_does_not_resurrect_history_on_next_start():
    today = date.today().isoformat()
    _import({"contract_id": "PV-RESET", "client": "Новая Сессия",
             "responsible": "Агент"})
    db.update_person("PV-RESET", {"visit_date": today, "call_status": "Оплачен"})
    db.log_visit("PV-RESET", 104, "визит", op_id="pv-reset")
    db.record_bot_visit("PV-RESET", {"call_status": "Оплачен"}, today)
    assert _safe("PV-RESET")[db.BOT_VISIT_MARKER] is True

    db.reset_visits()
    db.init_db()                                           # history.visits сохранена
    assert _safe("PV-RESET")[db.BOT_VISIT_MARKER] is False


def test_export_json_defaults_safe_and_full_mode_is_explicit(monkeypatch):
    _import({"contract_id": "PV-API", "client": "API", "responsible": "Агент",
             "visit_date": "531", "contact_with": "CONSUMER"})
    monkeypatch.setattr(webmod, "_admin", lambda _request: None)

    safe_resp = asyncio.run(webmod._admin_export_json(
        make_mocked_request("GET", "/admin/export.json")))
    safe = json.loads(safe_resp.text)
    safe_row = next(r for r in safe["rows"] if r["contract_id"] == "PV-API")
    assert safe_row["visit_date"] is None
    assert safe_row[db.BOT_VISIT_MARKER] is False

    full_resp = asyncio.run(webmod._admin_export_json(
        make_mocked_request("GET", "/admin/export.json?mode=full")))
    full = json.loads(full_resp.text)
    full_row = next(r for r in full["rows"] if r["contract_id"] == "PV-API")
    assert full_row["visit_date"] == "531"


def test_invalid_dropdown_cannot_become_trusted_snapshot():
    _import({"contract_id": "PV-INVALID", "client": "Подмена", "responsible": "Агент"})
    assert db.record_bot_visit(
        "PV-INVALID", {"contact_with": "CONSUMER"}, date.today().isoformat()
    ) == 0
    assert _safe("PV-INVALID")[db.BOT_VISIT_MARKER] is False


def _api_save(body, monkeypatch, employee="API Агент"):
    body = dict(body)
    if body.get("finish"):
        values = dict(body.get("values") or {})
        body.setdefault("form_version", webmod.FORM_PROTOCOL_VERSION)
        body.setdefault("input_keys", list(values))
    monkeypatch.setattr(webmod, "_auth", lambda _request: (501, employee))
    request = make_mocked_request("POST", "/api/save")

    async def _json():
        return body

    request.json = _json
    return asyncio.run(webmod._save(request))


def test_finish_api_creates_provenance_snapshot(monkeypatch):
    _import({"contract_id": "PV-FINISH", "client": "Финиш", "responsible": "API Агент",
             "visit_date": "531", "contact_with": "CONSUMER"})
    _api_save({
        "rep": "PV-FINISH", "finish": True, "op_id": "pv-finish-op",
        "values": {"call_status": "Оплачен", "contact_with": "Должник",
                    "visit_comment": "подтверждено", "tips": "новый номер"},
    }, monkeypatch)
    row = _safe("PV-FINISH")
    assert row[db.BOT_VISIT_MARKER] is True
    assert row["call_status"] == "Оплачен"
    assert row["contact_with"] == "Должник"
    assert row["visit_comment"] == "подтверждено"
    assert row["tips"] == "новый номер"


def test_stale_master_form_open_and_untouched_save_cannot_certify(monkeypatch):
    """До proven snapshot API формы не подмешивает ни одно HARD-поле master.

    Моделируем ровно клиентский open → readForm → save без касаний: payload пуст,
    сервер отклоняет finish до visit_date/log/snapshot.
    """
    _import({
        "contract_id": "PV-STALE-FORM",
        "client": "Старый префилл",
        "responsible": "API Агент",
        "visit_date": "2025-01-01",
        "call_status": "Оплачен",
        "contact_with": "Должник",
        "agreed_payment": "Обещает оплатить",
        "who_at_address": "Родственник",
        "client_location": "На работе",
        "purpose_rf": "Работа",
        "visit_comment": "старый master-комментарий",
    })
    db.update_person(
        "PV-STALE-FORM",
        {"call_comment": "старый звонок", "tips": "старый номер"},
    )

    opened = webmod._person_full("PV-STALE-FORM", "ru")
    assert opened["visited"] is False
    assert all(field["current"] == "" for field in opened["form"])
    # Текущий комментарий — живой рабочий текст, но он не входит в dropdown
    # prefill и без реального касания формы не становится доказательством визита.
    assert opened["visit_comment"] == "старый master-комментарий"
    assert opened["call_comment"] == ""
    assert opened["tips"] == ""

    untouched = {
        field["key"]: field["current"]
        for field in opened["form"]
        if field["current"]
    }
    with pytest.raises(aiohttp_web.HTTPBadRequest) as blocked:
        _api_save({
            "rep": "PV-STALE-FORM",
            "finish": True,
            "op_id": "pv-stale-untouched",
            "values": untouched,
        }, monkeypatch)
    assert "visit result" in blocked.value.text

    assert db.has_bot_visit("PV-STALE-FORM") is False
    with db.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM visits WHERE contract_id=?",
            ("PV-STALE-FORM",),
        ).fetchone()[0] == 0


def test_http_stale_master_open_then_untouched_finish_is_fail_closed(monkeypatch):
    """Реальный HTTP-маршрут не превращает master-prefill в proven visit."""
    _import({
        "contract_id": "PV-HTTP-STALE",
        "client": "HTTP старый префилл",
        "responsible": "API Агент",
        "visit_date": "2025-01-01",
        "call_status": "Оплачен",
        "contact_with": "Должник",
        "agreed_payment": "Обещает оплатить",
        "who_at_address": "Родственник",
        "client_location": "На работе",
        "purpose_rf": "Работа",
        "visit_comment": "старый master-комментарий",
    })
    db.update_person(
        "PV-HTTP-STALE",
        {"call_comment": "старый звонок", "tips": "старый номер"},
    )
    monkeypatch.setattr(webmod, "_auth", lambda _request: (501, "API Агент"))

    async def scenario():
        client = TestClient(TestServer(webmod.build_app(None)))
        await client.start_server()
        try:
            opened_response = await client.get(
                "/api/person",
                params={"rep": "PV-HTTP-STALE"},
            )
            assert opened_response.status == 200
            opened = await opened_response.json()
            assert opened["visited"] is False
            assert all(field["current"] == "" for field in opened["form"])
            assert opened["visit_comment"] == "старый master-комментарий"
            assert opened["call_comment"] == ""
            assert opened["tips"] == ""

            untouched = {
                field["key"]: field["current"]
                for field in opened["form"]
                if field["current"]
            }
            saved_response = await client.post(
                "/api/save",
                json={
                    "rep": "PV-HTTP-STALE",
                    "finish": True,
                    "op_id": "pv-http-stale-untouched",
                    "form_version": webmod.FORM_PROTOCOL_VERSION,
                    "input_keys": list(untouched),
                    "values": untouched,
                },
            )
            assert saved_response.status == 400
            assert "visit result" in await saved_response.text()
        finally:
            await client.close()

    asyncio.run(scenario())
    assert db.has_bot_visit("PV-HTTP-STALE") is False
    with db.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM visits WHERE contract_id=?",
            ("PV-HTTP-STALE",),
        ).fetchone()[0] == 0


def test_deliberate_result_does_not_pull_omitted_master_fields_into_proof(
    monkeypatch,
):
    _import({
        "contract_id": "PV-EXPLICIT-ONLY",
        "client": "Только явное",
        "responsible": "API Агент",
        "call_status": "Нет контакта",
        "contact_with": "Представитель",
        "agreed_payment": "Отказ",
        "visit_comment": "старый master-комментарий",
    })

    _api_save({
        "rep": "PV-EXPLICIT-ONLY",
        "finish": True,
        "op_id": "pv-explicit-only",
        "values": {"call_status": "Оплачен"},
    }, monkeypatch)

    snapshot = db.bot_visit_snapshot(db.get_client("PV-EXPLICIT-ONLY"))
    assert snapshot["call_status"] == "Оплачен"
    assert "contact_with" not in snapshot
    assert "agreed_payment" not in snapshot
    # Публичная карточка накладывает живой текущий комментарий поверх снимка,
    # однако immutable payload ниже по-прежнему содержит только отправленные поля.
    assert snapshot["visit_comment"] == "старый master-комментарий"
    with db.connect() as conn:
        payload = conn.execute(
            "SELECT payload FROM visits WHERE contract_id=?",
            ("PV-EXPLICIT-ONLY",),
        ).fetchone()[0]
    assert "старый master-комментарий" not in payload
    assert "Отказ" not in payload
    assert "Оплачен" in payload


def test_api_rejects_noncanonical_dropdown(monkeypatch):
    _import({"contract_id": "PV-BAD-API", "client": "Подмена API",
             "responsible": "API Агент"})
    with pytest.raises(aiohttp_web.HTTPBadRequest):
        _api_save({"rep": "PV-BAD-API", "finish": True,
                   "values": {"contact_with": "CONSUMER"}}, monkeypatch)
    assert _safe("PV-BAD-API")[db.BOT_VISIT_MARKER] is False


def test_finish_from_old_shell_without_explicit_field_protocol_is_blocked(
    monkeypatch,
):
    _import({
        "contract_id": "PV-OLD-SHELL",
        "client": "Старая оболочка",
        "responsible": "API Агент",
    })
    with pytest.raises(aiohttp_web.HTTPConflict):
        _api_save({
            "rep": "PV-OLD-SHELL",
            "finish": True,
            "form_version": 1,
            "values": {"call_status": "Оплачен"},
        }, monkeypatch)
    assert db.has_bot_visit("PV-OLD-SHELL") is False
