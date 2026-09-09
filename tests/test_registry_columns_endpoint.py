"""Ручка карты колонок и разбор шапки на стороне пульта.

Экран «Колонки реестра» обязан показывать ровно тот разбор, который потом
сделает сервер при импорте. Поэтому справочники (схема, алиасы контракта,
список известных лишних колонок) приходят с сервера одним ответом, а пульт
раскладывает по ним шапку теми же правилами.
"""
import asyncio
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import db
import web
from fields import FIELDS

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT / "pult"), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pult.core import header_map as hm            # noqa: E402

KEY = {"X-Admin-Key": "master"}


def _call(method, path, *, headers, json_body=None):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers,
                                        json=json_body)
            try:
                return resp.status, await resp.json()
            except Exception:                      # noqa: BLE001
                return resp.status, {}
        finally:
            await client.close()

    return asyncio.run(scenario())


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?",
                     (db.REGISTRY_COLUMN_MAP_SETTING,))
        conn.commit()
    hm.forget()
    yield
    hm.forget()
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?",
                     (db.REGISTRY_COLUMN_MAP_SETTING,))
        conn.commit()


def test_map_requires_admin():
    status, _ = _call("GET", "/admin/registry_columns.json", headers={})
    assert status == 401


def test_get_returns_everything_the_screen_needs():
    status, payload = _call("GET", "/admin/registry_columns.json", headers=KEY)
    assert status == 200
    assert payload["map"] == db.registry_column_map()
    assert {f["key"] for f in payload["schema"]} == {f.key for f in FIELDS}
    assert "ответственный Сотрудник ХАРД" in payload["contract_aliases"]["responsible"]
    assert "Страна" in payload["contract_ignored"]
    assert "responsible" in payload["required_keys"]


def test_save_round_trip_and_validation():
    status, payload = _call(
        "POST", "/admin/registry_columns.json", headers=KEY,
        json_body={"map": {"aliases": {"responsible": ["Сотрудник выезда"]},
                           "extras": [{"title": "условия акции",
                                       "label": "Акция"}]}})
    assert status == 200 and payload["ok"]
    assert payload["map"]["aliases"] == {"responsible": ["Сотрудник выезда"]}
    assert db.registry_column_map()["extras"][0]["label"] == "Акция"

    status, payload = _call(
        "POST", "/admin/registry_columns.json", headers=KEY,
        json_body={"map": {"aliases": {"city": ["Адрес"]}}})
    assert status == 409 and "штатный заголовок" in payload["error"]
    # Отказ ничего не переписал: предыдущая карта на месте.
    assert db.registry_column_map()["aliases"] == {"responsible": ["Сотрудник выезда"]}


# --- разбор шапки в пульте ----------------------------------------------------
def _grid(header, types=None):
    return [header, types or [""] * len(header), ["1", "Иванов И."]]


def _remember_from_server():
    _status, payload = _call("GET", "/admin/registry_columns.json", headers=KEY)
    hm.remember(payload)


def test_pult_reads_the_renamed_column_through_the_contract():
    _remember_from_server()
    header = [f.title for f in FIELDS]
    header[header.index("ответственный Сотрудник")] = "ответственный Сотрудник ХАРД"
    analysis = hm.analyze(_grid(header))
    assert analysis["has_contract_key"]
    assert analysis["missing_required"] == []


def test_pult_suggests_a_field_for_a_renamed_header():
    """Подсказка — это половина работы оператора: заголовок «поле + уточнение»
    сам показывает, каким полем он был."""
    _remember_from_server()
    header = [f.title for f in FIELDS]
    header[header.index("ответственный Сотрудник")] = "ответственный Сотрудник ОТДЕЛ"
    analysis = hm.analyze(_grid(header))
    unknown = {item["title"]: item["suggest"] for item in analysis["unknown"]}
    assert unknown["ответственный Сотрудник ОТДЕЛ"] == "responsible"
    assert [item["key"] for item in analysis["missing_required"]] == ["responsible"]


def test_decisions_turn_into_a_map_the_server_accepts():
    _remember_from_server()
    new_map = hm.apply_decisions(hm.current_map(), [
        {"title": "Сотрудник выезда", "decision": "field:responsible"},
        {"title": "условия акции", "decision": "extra", "label": "Акция"},
        {"title": "служебная", "decision": "hide"},
        {"title": "непонятная колонка", "decision": "skip"},
    ])
    status, payload = _call("POST", "/admin/registry_columns.json", headers=KEY,
                            json_body={"map": new_map})
    assert status == 200, payload
    saved = payload["map"]
    assert saved["aliases"]["responsible"] == ["Сотрудник выезда"]
    assert saved["extras"] == [{"title": "условия акции", "label": "Акция",
                                "show_in_card": True}]
    assert saved["ignored"] == ["служебная"]


def test_decisions_do_not_touch_columns_that_were_not_shown():
    """Оператор смотрит одну книгу, а карта общая: решения по колонкам других
    книг обязаны пережить сохранение."""
    base = hm.normalize_map({"aliases": {"city": ["Город офиса"]},
                             "ignored": ["чужая колонка"]})
    out = hm.apply_decisions(base, [{"title": "новая", "decision": "hide"}])
    assert out["aliases"] == {"city": ["Город офиса"]}
    assert set(out["ignored"]) == {"чужая колонка", "новая"}


def test_decision_options_offer_only_missing_fields():
    """Связать колонку с полем, которое в книге уже есть, нельзя: получилось бы
    два источника одного ключа."""
    options = dict(hm.decision_options([
        {"key": "responsible", "title": "ответственный Сотрудник", "required": True},
        {"key": "city", "title": "Город", "required": False},
    ]))
    assert options["field:responsible"] == "Поле: ответственный Сотрудник (обязательное)"
    assert options["field:city"] == "Поле: Город"
    assert "field:contract_id" not in options


def test_analysis_marks_extras_and_hidden_columns_separately():
    db.save_registry_column_map({"extras": [{"title": "условия акции",
                                             "label": "Акция"}],
                                 "ignored": ["служебная"]})
    _remember_from_server()
    # «прочее» здесь нельзя: так называется штатное поле долга — заголовок был
    # бы распознан, и проверка потеряла бы смысл.
    header = [f.title for f in FIELDS] + ["условия акции", "служебная",
                                          "непонятная колонка"]
    analysis = hm.analyze(_grid(header))
    assert [item["label"] for item in analysis["extras"]] == ["Акция"]
    assert [item["title"] for item in analysis["ignored"]] == ["служебная"]
    assert [item["title"] for item in analysis["unknown"]] == ["непонятная колонка"]
