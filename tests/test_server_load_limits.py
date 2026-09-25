"""Один выездник не должен ни остановить сервер, ни раздуть базу (ревью 25.09.2026, #13).

Сервер — один процесс aiohttp с ОДНИМ циклом событий на все телефоны, пульт и
руководителей. Всё тяжёлое в корутине (разбор, справочник, openpyxl, импорт)
останавливает их всех разом. Поэтому тяжёлое уходит в поток, а входы режутся
по длине. Но ни одна новая граница не имеет права стоить выезда: на 4xx
оболочка откладывает запись очереди «на ручной разбор», и визит не доезжает.
Отсюда правило — длинный текст обрезаем, а не отвергаем; лишние точки трека
отбрасываем, а не роняем пачку.
"""
import asyncio
import json
import logging
import os
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pytest
from aiohttp import web as aiohttp_web
from aiohttp.test_utils import make_mocked_request

import db
import fields
import geoindex
import scoring
import web

EMP = "Лимитов Сотрудник"
UID = 7401


def _status_value() -> str:
    return fields.DROPDOWNS[web.OFFICE_RESULT_SNAPSHOT_KEY][0]


def _seed_person(*cids):
    """Один человек (ФИО + дата рождения) с несколькими договорами."""
    db.init_db()
    with db.connect() as conn:
        for cid in cids:
            conn.execute("DELETE FROM clients WHERE contract_id=?", (cid,))
            conn.execute("DELETE FROM visits WHERE contract_id=?", (cid,))
            conn.execute(
                "INSERT INTO clients (contract_id, client, birth_date, responsible) "
                "VALUES (?,?,?,?)", (cid, "Длиннов Д.", "1985-05-05", EMP))
        conn.commit()


def _save(body, monkeypatch):
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP))
    request = make_mocked_request("POST", "/api/save", app=aiohttp_web.Application())

    async def _json():
        return body
    request.json = _json

    async def run():
        resp = await web._save(request)
        await web.wait_visit_notify()
        return resp
    return json.loads(asyncio.run(run()).body.decode())


# --- текст визита: обрезка, а не отказ ------------------------------------------
def test_long_comment_is_cut_on_the_server_and_the_visit_is_kept(monkeypatch):
    """Старая оболочка без maxlength или чужой клиент прислали 10 тысяч знаков.
    Выезд обязан сохраниться; во все договоры человека, в снимок визита и в
    журнал ложится уже обрезанный текст — хвост нигде не размножается."""
    _seed_person("LIM-1", "LIM-2")
    body = {"rep": "LIM-1", "finish": True, "op_id": "lim-op-1",
            "form_version": web.FORM_PROTOCOL_VERSION,
            "input_keys": ["call_status", "visit_comment", "tips"],
            "values": {"call_status": _status_value(),
                       "visit_comment": "а" * 10_000, "tips": "б" * 5_000},
            "photos_pending": 0}
    out = _save(body, monkeypatch)
    assert out["ok"] is True
    assert sorted(out["truncated"]) == ["tips", "visit_comment"]
    assert "rejected" not in out, "обрезанное поле принято, а не отвергнуто"
    for cid in ("LIM-1", "LIM-2"):
        row = db.get_client(cid)
        assert row["visit_comment"] == "а" * web.VISIT_TEXT_MAX
        assert row["tips"] == "б" * web.VISIT_TEXT_MAX
        snap = db.bot_visit_snapshot(row)
        assert len(snap["visit_comment"]) == web.VISIT_TEXT_MAX
    with db.connect() as conn:
        payload = conn.execute(
            "SELECT payload FROM visits WHERE op_id='lim-op-1'").fetchone()[0]
    assert "а" * web.VISIT_TEXT_MAX in payload
    assert "а" * (web.VISIT_TEXT_MAX + 1) not in payload


def test_comment_at_the_limit_is_kept_whole(monkeypatch):
    _seed_person("LIM-3")
    text = "в" * web.VISIT_TEXT_MAX
    out = _save({"rep": "LIM-3", "values": {"visit_comment": text}}, monkeypatch)
    assert "truncated" not in out
    assert db.get_client("LIM-3")["visit_comment"] == text


def test_draft_autosave_is_cut_too(monkeypatch):
    """Черновик раз в 20 с пишет в те же поля — лимит обязан быть и там."""
    _seed_person("LIM-4")
    out = _save({"rep": "LIM-4", "values": {"visit_comment": "г" * 9_000}}, monkeypatch)
    assert out["truncated"] == ["visit_comment"]
    assert len(db.get_client("LIM-4")["visit_comment"]) == web.VISIT_TEXT_MAX


# --- трек: лишнее отбрасываем, пачку не роняем ----------------------------------
def _track(body, monkeypatch):
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP))
    request = make_mocked_request("POST", "/api/track")

    async def _json():
        return body
    request.json = _json
    return json.loads(asyncio.run(web._track(request)).body.decode())


def _clean_track(session):
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM track_points WHERE session_id=?", (session,))
        conn.commit()


def test_oversized_track_batch_is_cut_and_logged(monkeypatch, caplog):
    """Оболочка шлёт не больше 10 точек за раз; 600 — это чужой клиент. Первые
    TRACK_MAX_POINTS принимаем, остальное — в журнал, без 400: на отказ
    trackDrainQueue встал бы на этой пачке навсегда."""
    _clean_track("lim-track-big")
    points = [{"ts": f"2026-09-25T10:{i // 60:02d}:{i % 60:02d}Z",
               "lat": 40.28, "lon": 69.62} for i in range(600)]
    with caplog.at_level(logging.WARNING):
        out = _track({"session_id": "lim-track-big", "points": points}, monkeypatch)
    assert out["added"] == web.TRACK_MAX_POINTS
    assert any("600" in r.getMessage() and "track" in r.getMessage()
               for r in caplog.records), "обрезка пачки не оставила следа в журнале"


def test_broken_numbers_drop_the_point_not_the_batch(monkeypatch):
    """int(inf) у точности ронял всю пачку 500-й. Теперь выпадает только
    плохая точка, а у хорошей неконечные точность/скорость/курс — NULL."""
    _clean_track("lim-track-bad")
    inf, nan = float("inf"), float("nan")
    points = [
        {"ts": "2026-09-25T11:00:00Z", "lat": 40.28, "lon": 69.62,
         "acc": inf, "speed": nan, "heading": -inf},
        {"ts": "2026-09-25T11:00:01Z", "lat": inf, "lon": 69.62},
        {"ts": "2026-09-25T11:00:02Z", "lat": nan, "lon": 69.62},
        {"ts": "2026-09-25T11:00:03Z", "lat": 10 ** 400, "lon": 69.62},
        "не точка", 42, None, ["40.1", "69.1"],
        {"ts": "2026-09-25T11:00:04Z", "lat": 40.29, "lon": 69.63, "acc": 1e300},
        {"ts": "x" * 500, "lat": 40.30, "lon": 69.64, "acc": 7},
    ]
    out = _track({"session_id": "lim-track-bad", "points": points}, monkeypatch)
    assert out["added"] == 3
    rows = [dict(r) for r in db.track_points("lim-track-bad")]
    first = next(r for r in rows if r["ts"] == "2026-09-25T11:00:00Z")
    assert first["acc_m"] is None and first["speed"] is None and first["heading"] is None
    assert all(len(r["ts"]) <= 64 for r in rows)
    # В базе не осталось ничего, что превратилось бы в ответе в Infinity/NaN.
    json.dumps(rows, allow_nan=False)


# --- метрики клиента: одно маленькое событие на запрос -----------------------------
def test_client_event_is_one_small_event_per_request(monkeypatch):
    """clientMetric шлёт ОДНО событие на запрос и не повторяет его. Пачек событий
    сервер не принимает вовсе, тело — не больше 4 КБ."""
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP))

    class Declared:
        content_length = 10_000

        async def read(self):
            return b"{}"

    class Chunked:
        content_length = None

        async def read(self):
            return json.dumps({"event": "boot_ok", "metrics": {},
                               "pad": "x" * 5_000}).encode()

    class Batch:
        content_length = 200

        async def read(self):
            return json.dumps({"events": [{"event": "boot_ok"}] * 5}).encode()

    for req in (Declared(), Chunked()):
        with pytest.raises(web.web.HTTPRequestEntityTooLarge):
            asyncio.run(web._client_event(req))
    with pytest.raises(web.web.HTTPBadRequest):
        asyncio.run(web._client_event(Batch()))


# --- геокодер: обрезка до разбора и линейная регулярка ------------------------------
@pytest.fixture
def tiny_gazetteer():
    """Мини-справочник вместо настоящих дампов (их загрузка — 10+ секунд)."""
    saved = geoindex._INDEX
    idx = geoindex.Index()
    idx.add(["2", "Shahrixon", "Shahrixon", "Шахрихон,Shahrikhon", "40.7100",
             "72.0500", "P", "PPLA2", "UZ", "", "01", "0102", "", "", "71400",
             "", "", "", ""])
    geoindex._INDEX = idx
    yield idx
    geoindex._INDEX = saved


def test_latlon_regex_is_linear():
    """«Две цифры, 8000 пробелов, буква»: прежний «\\s*[, ]\\s*» перебирал все
    разбиения пробелов — 0,6–0,7 с цикла событий на один запрос."""
    for probe in ("12" + " " * 8000 + "x",
                  "1" + " " * 8000 + "2" + " " * 8000 + "x",
                  "12" + " " * 4000 + "," + " " * 4000 + "x"):
        started = time.perf_counter()
        assert web._LATLON_RX.match(probe) is None
        assert time.perf_counter() - started < 0.05, "квадратичная регулярка вернулась"


@pytest.mark.parametrize("typed", ["41.31, 69.24", "41.31 69.24", "41.31,69.24",
                                   " 41.31 , 69.24 ", "41.31  ,  69.24"])
def test_hand_typed_coordinates_still_parse(typed):
    m = web._LATLON_RX.match(typed)
    assert m and m.groups() == ("41.31", "69.24")


def test_decimal_comma_is_still_not_a_point():
    assert web._LATLON_RX.match("41,31 69,24") is None


def test_geocode_query_is_cut_before_the_regex_and_the_parser(monkeypatch, tiny_gazetteer):
    seen = {"rx": [], "parse": []}

    class SpyRx:
        def match(self, s):
            seen["rx"].append(len(s))
            return None

    real_parse = web.addr_cis.parse

    def spy_parse(q, *a, **kw):
        seen["parse"].append(len(q))
        return real_parse(q, *a, **kw)

    monkeypatch.setattr(web, "_LATLON_RX", SpyRx())
    monkeypatch.setattr(web.addr_cis, "parse", spy_parse)
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP))
    q = "12" + " " * 8000 + "x"
    with pytest.raises(web.web.HTTPNotFound):
        asyncio.run(web._geocode_free(
            make_mocked_request("GET", "/api/geocode?q=" + quote(q))))
    assert seen["rx"] == [web.GEO_QUERY_MAX]
    assert seen["parse"] == [web.GEO_QUERY_MAX]


def test_suggest_search_runs_off_the_event_loop(monkeypatch, tiny_gazetteer):
    """Проход по справочнику без совпадений — 40–130 мс на каждую паузу в
    наборе. Он обязан идти в потоке и видеть уже обрезанный запрос."""
    seen = {}

    def spy_items(idx, q):
        seen["worker"] = threading.get_ident()
        seen["q"] = q
        return []

    monkeypatch.setattr(web, "_suggest_items", spy_items)
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP))

    async def go():
        seen["loop"] = threading.get_ident()
        return await web._geo_suggest(make_mocked_request(
            "GET", "/api/geo/suggest?q=" + quote("ы" * 8000)))

    resp = asyncio.run(go())
    assert json.loads(resp.body.decode()) == {"items": []}
    assert seen["worker"] != seen["loop"], "поиск по справочнику шёл в цикле событий"
    assert len(seen["q"]) == web.GEO_QUERY_MAX


def test_suggest_still_finds_places(monkeypatch, tiny_gazetteer):
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP))
    resp = asyncio.run(web._geo_suggest(
        make_mocked_request("GET", "/api/geo/suggest?q=" + quote("шахрих"))))
    items = json.loads(resp.body.decode())["items"]
    assert items and "Shahrixon" in items[0]["label"]


# --- импорт реестра: в потоке и по одному ------------------------------------------
def _import_request(body, path="/admin/import.json"):
    request = make_mocked_request("POST", path)

    async def _json():
        return body
    request.json = _json
    return request


def _import_body():
    return {"data_exchange_contract": web.DATA_EXCHANGE_CONTRACT,
            "source_kind": db.IMPORT_SOURCE_PUBLISHED_MASTER,
            "source_proof": {"test": "proof"},
            "sheets": [{"name": "Портфели", "grid": [["id"]]}]}


def test_registry_import_runs_off_the_event_loop(monkeypatch):
    """import_grid и догадка офисов раньше шли прямо в корутине — поле стояло
    на всё время импорта. Геокодинг остаётся в цикле НАМЕРЕННО: его проход —
    одна транзакция записи (на свежей машине ~19 с, больше busy_timeout), и в
    потоке он превратил бы ожидание телефонов в 500. Вынести его можно, когда
    scoring.geocode_city_centroids начнёт коммитить порциями."""
    seen = {}
    monkeypatch.setattr(web, "_admin", lambda _r: None)
    monkeypatch.setattr(db, "pick_grid", lambda sheets: sheets[0]["grid"])

    def fake_import(grid, **options):
        seen["import"] = threading.get_ident()
        return {"blocked": False, "total_rows": 1}

    def fake_geocode():
        seen["geocode"] = threading.get_ident()
        return 3

    def fake_guess(apply=True):
        seen["guess"] = threading.get_ident()
        return {"filled": {}, "undecided": []}

    monkeypatch.setattr(db, "import_grid", fake_import)
    monkeypatch.setattr(scoring, "geocode_city_centroids", fake_geocode)
    monkeypatch.setattr(db, "guess_offices", fake_guess)

    async def go():
        seen["loop"] = threading.get_ident()
        resp = await web._admin_import_json(_import_request(_import_body()))
        return resp.status, json.loads(resp.body.decode())

    status, report = asyncio.run(go())
    assert status == 200
    assert report["geocoded"] == 3 and report["offices_guessed"] == {}
    for step in ("import", "guess"):
        assert seen[step] != seen["loop"], f"{step} шёл в цикле событий"
    assert seen["geocode"] == seen["loop"], \
        "геокодинг в потоке держал бы запись телефонов дольше busy_timeout"


def test_import_preview_runs_off_the_event_loop(monkeypatch):
    seen = {}
    monkeypatch.setattr(web, "_admin", lambda _r: None)
    monkeypatch.setattr(db, "pick_grid", lambda sheets: sheets[0]["grid"])

    def fake_import(grid, **options):
        seen["worker"] = threading.get_ident()
        seen["preview_only"] = options.get("preview_only")
        return {"blocked": False, "preview_only": True}

    monkeypatch.setattr(db, "import_grid", fake_import)

    async def go():
        seen["loop"] = threading.get_ident()
        resp = await web._admin_import_preview_json(
            _import_request(_import_body(), "/admin/import/preview.json"))
        return resp.status

    assert asyncio.run(go()) == 200
    assert seen["preview_only"] is True
    assert seen["worker"] != seen["loop"]


def test_imports_and_previews_never_overlap(monkeypatch):
    """Пока импорт шёл в корутине, второй сам ждал своей очереди. В потоке он
    упёрся бы в BEGIN IMMEDIATE первого и через busy_timeout получил бы
    «database is locked» — поэтому импорты и предпросмотры идут по одному."""
    state = {"active": 0, "peak": 0}
    guard = threading.Lock()

    def slow_import(grid, **options):
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.15)
        with guard:
            state["active"] -= 1
        return {"blocked": True}

    monkeypatch.setattr(db, "import_grid", slow_import)
    workers = [threading.Thread(target=web._import_grid_serial, args=([],)),
               threading.Thread(target=web._import_grid_serial, args=([],)),
               threading.Thread(target=web._import_grid_serial, args=([],),
                                kwargs={"preview_only": True})]
    for t in workers:
        t.start()
    for t in workers:
        t.join(timeout=10)
    assert state["peak"] == 1


# --- выгрузки: в потоке, уникальный временный файл, no-store --------------------------
def test_xlsx_export_is_built_off_the_loop_in_a_private_temp_file(monkeypatch):
    """Книга писалась в /tmp под постоянным именем и оставалась там: весь реестр
    с ПД лежал в слое контейнера, а два запроса писали в один файл."""
    monkeypatch.setattr(web, "_admin", lambda _r: None)
    seen = {"paths": []}

    def fake_export(path):
        seen["thread"] = threading.get_ident()
        seen["paths"].append(path)
        Path(path).write_bytes(b"PK-synthetic-book")
        return path

    monkeypatch.setattr(db, "export_xlsx", fake_export)

    async def go():
        seen["loop"] = threading.get_ident()
        first = await web._admin_export(make_mocked_request("GET", "/admin/export"))
        second = await web._admin_export(make_mocked_request("GET", "/admin/export"))
        return first, second

    first, second = asyncio.run(go())
    assert first.body == b"PK-synthetic-book"
    assert first.headers["Cache-Control"] == "no-store"
    assert "attachment" in first.headers["Content-Disposition"]
    assert first.content_type.startswith("application/vnd.openxmlformats")
    assert seen["thread"] != seen["loop"], "openpyxl шёл в цикле событий"
    one, two = seen["paths"]
    assert one != two, "имя временного файла постоянное"
    assert os.path.basename(one).startswith("hard-export-")
    assert not os.path.exists(one) and not os.path.exists(two), "реестр остался на диске"


def test_temp_book_is_removed_when_the_build_fails(monkeypatch):
    monkeypatch.setattr(web, "_admin", lambda _r: None)
    seen = {}

    def broken(path):
        seen["path"] = path
        Path(path).write_bytes(b"half a book")
        raise RuntimeError("openpyxl упал посреди книги")

    monkeypatch.setattr(db, "export_markers", broken)
    with pytest.raises(RuntimeError):
        asyncio.run(web._admin_markers_xlsx(
            make_mocked_request("GET", "/admin/markers.xlsx")))
    assert not os.path.exists(seen["path"])


def test_admin_json_exports_forbid_caching(monkeypatch):
    monkeypatch.setattr(web, "_admin", lambda _r: None)
    monkeypatch.setattr(db, "enrichment_rows", lambda: [])
    monkeypatch.setattr(db, "markers_rows", lambda: [])
    db.init_db()
    for handler, path in ((web._admin_export_json, "/admin/export.json"),
                          (web._admin_markers_json, "/admin/markers.json")):
        resp = asyncio.run(handler(make_mocked_request("GET", path)))
        assert resp.status == 200
        assert resp.headers["Cache-Control"] == "no-store", path


# --- догон фото: листание папок не в цикле ---------------------------------------------
def test_late_photo_poll_lists_folders_off_the_loop(monkeypatch):
    _seed_person("LIM-PH")
    db.set_setting("photo_grace_s", "1")
    seen = []

    def spy_day_photos(contracts, day):
        seen.append(threading.get_ident())
        return []

    monkeypatch.setattr(web, "day_photos", spy_day_photos)

    async def go():
        loop_id = threading.get_ident()
        await web._await_visit_photos("LIM-PH", date.today().isoformat(), 1)
        return loop_id

    try:
        loop_id = asyncio.run(go())
    finally:
        db.set_setting("photo_grace_s", "")
    assert seen, "догон фото ни разу не заглянул в папку"
    assert loop_id not in seen, "iterdir/stat снимков шли в цикле событий"
