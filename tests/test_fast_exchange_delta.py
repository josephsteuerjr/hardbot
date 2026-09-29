"""Импорт разницей (опыт exp/fast-exchange): транспорт другой, грид — тот же.

Проверяем: разница собирается в тот же грид на любых правках (случайные
вставки/удаления/замены); подделку и чужую базу сервер не собирает; ручки
предпросмотра и импорта отдают ядру ровно тот грид, что прислан бы целиком;
пульт без базы, с устаревшей базой или с выключенным опытом шлёт целиком.
"""
from __future__ import annotations

import asyncio
import copy
import random
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import db  # noqa: E402
import grid_delta as gd  # noqa: E402
import web  # noqa: E402
from pult.core import exchange_state as xs  # noqa: E402

SHEET = db.WORKBOOK_SHEET_NAME


def _grid(n=300, seed=1):
    r = random.Random(seed)
    head = [["Блок", None, None, None], ["Номер КД / ID", "Клиент", "Сумма", "Сотрудник"]]
    return head + [[str(1000 + i), f"Клиент {i}'", r.randint(1, 10**6), f"Сотр {i % 7}"]
                   for i in range(n)]


def _mutate(g, rnd, k=20):
    g = copy.deepcopy(g)
    for _ in range(k):
        op = rnd.choice(["ins", "del", "set", "tail"])
        i = rnd.randrange(2, len(g))
        if op == "ins":
            g.insert(i, [str(rnd.randrange(10**8)), "новый", 1.5, None])
        elif op == "del" and len(g) > 3:
            del g[i]
        elif op == "set":
            g[i][rnd.randrange(4)] = rnd.choice([None, "=x", "0012", 3.25, True])
        else:
            g.append(["хвост", None, None, "Сотр 1"])
    return g


@pytest.mark.parametrize("seed", range(25))
def test_delta_rebuilds_exactly_the_new_grid(seed):
    rnd = random.Random(seed)
    base = _grid(seed=seed)
    new = _mutate(base, rnd, k=rnd.randint(0, 60))
    d = gd.make_delta(base, new)
    assert gd.canonical(gd.apply_delta(base, d)) == gd.canonical(new)


@pytest.mark.parametrize("tamper", ["base", "digest", "count", "overrun", "row", "rows"])
def test_tampered_or_foreign_delta_is_refused(tamper):
    base, new = _grid(), _grid(seed=2)
    d = gd.make_delta(base, new)
    if tamper == "base":
        d["base"] = "0" * 64
    elif tamper == "digest":
        d["digest"] = "f" * 64
    elif tamper == "count":
        d["ops"].append(["=", 1])
    elif tamper == "overrun":
        d["ops"] = [["=", len(base) + 5]]
    elif tamper == "row":
        d["ops"].append(["+", [[{"obj": 1}]]])
    else:
        d["rows"] += 1
    with pytest.raises(gd.DeltaError):
        gd.apply_delta(base, d)


# --- ручки сервера -------------------------------------------------------------------
@pytest.fixture()
def server(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    monkeypatch.setattr(web, "_admin", lambda request: None)
    seen = []

    def fake_import(grid, **kw):
        seen.append((copy.deepcopy(grid), kw.get("preview_only", False)))
        return {"ok": True, "updated": len(grid)}
    monkeypatch.setattr(db, "import_grid", fake_import)
    monkeypatch.setattr(web, "validate_contract_binding", lambda _b: None, raising=False)
    return seen


def _post(path, body):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.post(path, json=body)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return asyncio.run(scenario())


BODY = {"source_kind": db.IMPORT_SOURCE_TEST_SUBSET, "source_proof": {"x": 1},
        "full_registry": False}


def test_preview_then_delta_reaches_the_core_as_the_same_grid(server):
    g1, g2 = _grid(), _mutate(_grid(), random.Random(3))
    st, a = _post("/admin/import/preview.json", {**BODY, "sheets": [{"name": SHEET, "grid": g1}]})
    assert st == 200 and a["delta_base"] == gd.grid_digest(g1)
    delta = gd.make_delta(g1, g2)
    st, b = _post("/admin/import/preview.json", {**BODY, "sheets_delta": delta})
    assert st == 200, b
    assert gd.canonical(server[-1][0]) == gd.canonical(g2) and server[-1][1] is True
    st, c = _post("/admin/import.json", {**BODY, "sheets_delta": gd.make_delta(g2, g2)})
    assert gd.canonical(server[-1][0]) == gd.canonical(g2) and server[-1][1] is False


def test_unknown_base_and_mismatch_are_409_and_touch_nothing(server):
    g1, g2 = _grid(), _grid(seed=9)
    st, a = _post("/admin/import/preview.json", {**BODY, "sheets_delta": gd.make_delta(g1, g2)})
    assert st == 409 and a["error"] == "delta_base_unknown" and not server
    _post("/admin/import/preview.json", {**BODY, "sheets": [{"name": SHEET, "grid": g1}]})
    bad = gd.make_delta(g1, g2)
    bad["digest"] = "a" * 64
    st, a = _post("/admin/import.json", {**BODY, "sheets_delta": bad})
    assert st == 409 and a["error"] == "delta_mismatch"
    assert all(p for _, p in server), "импорт с неверной разницей не дошёл до ядра"


def test_server_keeps_only_a_few_bases(server):
    for s in range(5):
        _post("/admin/import/preview.json", {**BODY, "sheets": [{"name": SHEET, "grid": _grid(seed=s)}]})
    assert len(list(web._import_bases_dir().glob("*.json.gz"))) == web.IMPORT_BASES_KEEP


# --- пульт: когда разницей, когда целиком ---------------------------------------------
class _Admin:
    """Сервер в одном процессе: та же развёртка разницы и та же память баз."""

    def __init__(self):
        self.sent, self.bases = [], {}

    def __call__(self, method, path, body, timeout=0):
        body = copy.deepcopy(body)
        self.sent.append("delta" if "sheets_delta" in body else "full")
        if "sheets_delta" in body:
            base = self.bases.get(body["sheets_delta"]["base"])
            if base is None:
                return {"blocked": True, "error": "delta_base_unknown"}
            body["sheets"] = [{"name": SHEET, "grid": gd.apply_delta(base, body.pop("sheets_delta"))}]
        grid = body["sheets"][0]["grid"]
        self.bases[gd.grid_digest(grid)] = grid
        self.last = grid
        return {"ok": True, "delta_base": gd.grid_digest(grid)}


def test_pult_sends_full_first_then_deltas_and_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("HARD_EXCHANGE_DB", str(tmp_path / "x.sqlite"))
    monkeypatch.setenv("HARD_EXCHANGE_DELTA", "1")
    adm = _Admin()
    g1 = _grid()
    g2 = _mutate(g1, random.Random(4), k=5)
    xs.post_import(adm, "/p", {**BODY, "sheets": [{"name": SHEET, "grid": g1}]}, sheet=SHEET)
    xs.post_import(adm, "/p", {**BODY, "sheets": [{"name": SHEET, "grid": g2}]}, sheet=SHEET)
    assert adm.sent == ["full", "delta"] and gd.canonical(adm.last) == gd.canonical(g2)
    adm.bases.clear()                                   # сервер перезапустили и почистили
    g3 = _mutate(g2, random.Random(5), k=5)
    xs.post_import(adm, "/p", {**BODY, "sheets": [{"name": SHEET, "grid": g3}]}, sheet=SHEET)
    assert adm.sent[-2:] == ["delta", "full"] and gd.canonical(adm.last) == gd.canonical(g3)


def test_pult_sends_full_when_delta_does_not_pay_off_or_is_off(monkeypatch, tmp_path):
    monkeypatch.setenv("HARD_EXCHANGE_DB", str(tmp_path / "x.sqlite"))
    adm = _Admin()
    g1 = _grid()
    xs.post_import(adm, "/p", {**BODY, "sheets": [{"name": SHEET, "grid": g1}]}, sheet=SHEET)
    other = _grid(seed=77)                               # поменялось всё
    xs.post_import(adm, "/p", {**BODY, "sheets": [{"name": SHEET, "grid": other}]}, sheet=SHEET)
    monkeypatch.setenv("HARD_EXCHANGE_DELTA", "0")
    xs.post_import(adm, "/p", {**BODY, "sheets": [{"name": SHEET, "grid": other}]}, sheet=SHEET)
    assert adm.sent == ["full", "full", "full"]
