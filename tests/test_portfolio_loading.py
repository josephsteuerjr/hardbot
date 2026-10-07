"""Большой портфель быстро открывается и оставляет измеримый след без ПД."""

import asyncio
import inspect
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

import web


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "server" / "miniapp" / "index.html").read_text("utf-8")
SW = (ROOT / "server" / "miniapp" / "sw.js").read_text("utf-8")


def _body(response):
    return json.loads(response.body.decode("utf-8"))


def test_bulk_cards_reuses_the_grouped_contract_rows(monkeypatch):
    """Один запрос группировки, без отдельного SQLite-вызова на человека."""
    contracts_a = [{"contract_id": "A1"}, {"contract_id": "A2"}]
    contracts_b = [{"contract_id": "B1"}]
    grouped = [
        {"rep": "A", "contracts": contracts_a},
        {"rep": "B", "contracts": contracts_b},
    ]
    seen = []
    audit = []
    extras_calls = []

    monkeypatch.setattr(web, "_auth", lambda _request: (77, "Сотрудник"))
    monkeypatch.setattr(web, "lang_of", lambda _uid: "ru")
    monkeypatch.setattr(web.db, "persons_for_employee", lambda _emp: grouped)
    monkeypatch.setattr(
        web.db, "person_contracts",
        lambda _rep: pytest.fail("N+1: повторно запросили договоры карточки"),
    )
    # Дополнительные поля реестра тоже читаются оптом: один запрос на весь
    # портфель, а не по одному на карточку.
    monkeypatch.setattr(
        web.db, "registry_extra_values",
        lambda ids: extras_calls.append(list(ids)) or {},
    monkeypatch.setattr(web.db, "receipts_for", lambda ids: {}),
    )
    monkeypatch.setattr(
        web, "_person_full_from_rows",
        lambda rows, rep, lang, extras=None, receipts=None, history=None, reported_payments=None: seen.append((rows, rep, lang))
        or {"rep": rep},
    )
    monkeypatch.setattr(web.db, "reported_payments_for", lambda ids: {})
    monkeypatch.setattr(web.db, "portfolio_card_history", lambda grouped, **kw: {
        p["rep"]: {"visit": None, "calls": []} for p in grouped})
    monkeypatch.setattr(
        web.db, "audit_access", lambda *args, **kwargs: audit.append((args, kwargs)),
    )

    monkeypatch.setattr(web.db, "soft_portfolio_exists", lambda _emp: False)
    response = asyncio.run(web._cards(SimpleNamespace(query={})))
    payload = _body(response)

    assert seen == [(contracts_a, "A", "ru"), (contracts_b, "B", "ru")]
    assert set(payload["cards"]) == {"A", "B"}
    assert payload["diagnostics"]["cards"] == 2
    assert payload["diagnostics"]["contracts"] == 3
    assert payload["diagnostics"]["cards_bytes"] > 0
    assert response.compression is True
    assert audit[0][1]["target"] == "Сотрудник"
    assert json.loads(audit[0][1]["detail"])["contracts"] == 3
    assert extras_calls == [["A1", "A2", "B1"]]


def test_loading_metrics_accept_only_bounded_numbers(monkeypatch):
    written = []

    class Request:
        content_length = 200

        async def read(self):
            return json.dumps({
                "event": "warm_ok",
                "metrics": {
                    "cards": 270,
                    "server_ms": 123.45,
                    "client_name": "секрет",
                    "message": "текст ошибки с ПД",
                },
            }, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr(web, "_auth", lambda _request: (88, "Сотрудник"))
    monkeypatch.setattr(
        web.db, "audit_access", lambda *args, **kwargs: written.append((args, kwargs)),
    )

    response = asyncio.run(web._client_event(Request()))
    assert _body(response) == {"ok": True}
    args, kwargs = written[0]
    assert args[:2] == (88, "client_warm_ok")
    assert kwargs["target"] == "Сотрудник"
    assert json.loads(kwargs["detail"]) == {
        "event": "warm_ok", "metrics": {"server_ms": 123.5, "cards": 270.0}
    }
    assert "секрет" not in kwargs["detail"] and "ошибки" not in kwargs["detail"]


def test_unknown_client_metric_event_is_rejected(monkeypatch):
    class Request:
        content_length = 20

        async def read(self):
            return b'{"event":"arbitrary_personal_log","metrics":{}}'

    monkeypatch.setattr(web, "_auth", lambda _request: (88, "Сотрудник"))
    with pytest.raises(web.web.HTTPBadRequest):
        asyncio.run(web._client_event(Request()))


def test_phone_replaces_card_cache_in_one_atomic_transaction():
    warm = INDEX[INDEX.index("async function warmCards("):INDEX.index(
        "async function syncWarmCount(")]
    replace = INDEX[INDEX.index("replace:(s,entries)"):INDEX.index("};\n})();")]

    assert 'await IDB.replace("cards", entries.concat(other.map' in warm
    assert 'await IDB.put("cards"' not in warm
    assert "o.clear()" in replace and "o.put(v,k)" in replace


def test_large_list_yields_between_bounded_batches():
    draw = INDEX[INDEX.index("function drawList("):INDEX.index("function filterList(")]
    assert "batch=60" in draw
    assert "requestAnimationFrame(append)" in draw
    assert "seq!==LIST_RENDER_SEQ" in draw


def test_unvisited_card_keeps_the_current_month_comment():
    open_card = INDEX[INDEX.index("async function openP("):INDEX.index(
        "function renderDetail(")]
    assert 'd.visit_comment=""' not in open_card
    assert 'd.call_comment=""' in open_card


def test_version_probe_bypasses_auth_and_every_cache():
    response = asyncio.run(web._version(object()))
    payload = _body(response)
    index_response = asyncio.run(web._index(object()))

    assert payload["shellVersion"] == web._shell_version()
    assert payload["dataExchangeContract"] == web.DATA_EXCHANGE_CONTRACT
    assert response.headers["Cache-Control"].startswith("no-store")
    assert index_response.headers["Cache-Control"].startswith("no-store")
    assert index_response.headers["X-Hardbot-Shell-Version"] == str(web._shell_version())
    assert 'fetch("/version.json", {cache:"no-store"})' in INDEX
    assert 'url.pathname === "/version.json"' in SW
    assert 'fetch(req, {cache: "no-store"})' in SW


def test_bootstrap_and_cards_report_server_timing_and_compress():
    cards_source = inspect.getsource(web._cards)
    bootstrap_source = inspect.getsource(web._bootstrap)
    for source in (cards_source, bootstrap_source):
        assert 'response.headers["Server-Timing"]' in source
        assert "response.enable_compression()" in source
