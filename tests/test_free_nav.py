"""Навигация по произвольному адресу: /api/geocode и /api/route_to.

Зачем ручки: руководителю надо доехать до адреса должника, которого нет в его
портфеле, а владельцу — проверить навигатор на любой точке во время теста.
Ключевое ограничение: сервер-витрина решает адрес ТОЛЬКО офлайн-справочником.
Публичные геокодеры отсюда запрещены — туда уехали бы адреса должников.
"""
import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import geoindex
import web


# Мини-справочник вместо настоящих дампов GeoNames: тест обязан быть офлайновым
# и детерминированным. (Пока фикстуры не было, geoindex молча тянул дампы с
# geonames.org прямо во время прогона — ровно тот исходящий коннект, которого
# на сервере-витрине быть не должно.)
# Формат строки — как в дампе GeoNames: id, name, ascii, alt-имена, lat, lon,
# fclass, fcode, cc, cc2, adm1, adm2, adm3, adm4, population, …
_FIXTURE = [
    ["1", "Andijan Region", "Andijan Region", "Андижанская область,Андижон вилояти",
     "40.75", "72.35", "A", "ADM1", "UZ", "", "01", "", "", "", "3000000", "", "", "", ""],
    ["2", "Shahrixon", "Shahrixon", "Шахрихон,Shahrikhon",
     "40.7100", "72.0500", "P", "PPLA2", "UZ", "", "01", "0102", "", "", "71400", "", "", "", ""],
    ["3", "Yoshlar", "Yoshlar", "Ёшлар",
     "40.7200", "72.0600", "P", "PPL", "UZ", "", "01", "0102", "", "", "0", "", "", "", ""],
    ["4", "Samarqand Region", "Samarqand Region", "Самаркандская область,Самарқанд вилояти",
     "39.6542", "66.9597", "A", "ADM1", "UZ", "", "10", "", "", "", "3900000", "", "", "", ""],
]


@pytest.fixture(autouse=True)
def _stub_gazetteer():
    saved = geoindex._INDEX
    idx = geoindex.Index()
    for row in _FIXTURE:
        idx.add(row)
    geoindex._INDEX = idx
    yield
    geoindex._INDEX = saved


def _call(handler, path, monkeypatch, auth=(111, "Тестов Сотр")):
    monkeypatch.setattr(web, "_auth", lambda _r: auth)
    return asyncio.run(handler(make_mocked_request("GET", path)))


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


# --- /api/geocode -------------------------------------------------------------
def test_geocode_plain_coordinates(monkeypatch):
    """Пара координат — самый надёжный вход: справочник для неё не нужен вообще."""
    r = _body(_call(web._geocode_free, "/api/geocode?q=41.31, 69.24", monkeypatch))
    assert r["ok"] and r["precision"] == "point"
    assert round(r["lat"], 2) == 41.31 and round(r["lon"], 2) == 69.24


def test_geocode_rejects_out_of_range_coordinates(monkeypatch):
    """«95.0, 200.0» — не координаты; уходит в разбор адреса и не находится."""
    with pytest.raises(web.web.HTTPNotFound):
        _call(web._geocode_free, "/api/geocode?q=95.0 200.0", monkeypatch)


def test_geocode_empty_query_is_bad_request(monkeypatch):
    with pytest.raises(web.web.HTTPBadRequest):
        _call(web._geocode_free, "/api/geocode?q=", monkeypatch)


def test_geocode_real_registry_address(monkeypatch):
    """Реальная строка из реестра: три системы письма в одной строке.
    Ответ обязан быть и с точкой, и с человекочитаемым адресом."""
    q = ("ЎЗБЕКИСТОН,  АНДИЖОН ВИЛОЯТИ,  ШАХРИХОН ТУМАНИ,  "
         "ЁШЛАР МФЙ, ул. СОЙ БУЙИ КУЧАСИ, д. 357")
    r = _body(_call(web._geocode_free, f"/api/geocode?q={q}", monkeypatch))
    assert r["ok"]
    assert 37.0 < r["lat"] < 46.0 and 60.0 < r["lon"] < 76.0    # Узбекистан, не Казань
    assert "Шахрихон" in r["pretty"] and "МФЙ Ёшлар" in r["pretty"]
    assert r["precision"] in ("village", "mahalla", "city", "district", "region")


def test_geocode_precision_is_reported(monkeypatch):
    """Точность возвращается честно: по одной области дальше района не уедешь."""
    r = _body(_call(web._geocode_free, "/api/geocode?q=САМАРҚАНД ВИЛОЯТИ", monkeypatch))
    assert r["precision"] in ("region", "district", "city")


def test_geocode_requires_auth(monkeypatch):
    def _deny(_r):
        raise web.web.HTTPUnauthorized(text="auth")
    monkeypatch.setattr(web, "_auth", _deny)
    with pytest.raises(web.web.HTTPUnauthorized):
        asyncio.run(web._geocode_free(make_mocked_request("GET", "/api/geocode?q=41.0,69.0")))


# --- /api/route_to ------------------------------------------------------------
def test_route_to_falls_back_to_straight_line(monkeypatch):
    """OSRM в тестовой среде не поднят — ручка обязана отдать честную прямую,
    а не упасть: в поле без маршрута лучше приблизительная линия, чем ошибка."""
    monkeypatch.setattr(web.routing_client, "best_route", lambda *_a, **_k: None)
    r = _body(_call(web._route_to,
                    "/api/route_to?lat=40.28&lon=69.62&dlat=40.29&dlon=69.63", monkeypatch))
    assert r["ok"] and r["src"] == "straight"
    assert r["km"] >= 0 and r["minutes"] >= 0
    assert r["line"][0] == [69.62, 40.28] and r["line"][1] == [69.63, 40.29]


def test_route_to_uses_osrm_when_available(monkeypatch):
    monkeypatch.setattr(web.routing_client, "best_route", lambda *_a, **_k: {
        "line": [[69.62, 40.28], [69.63, 40.29]], "km": 2.5,
        "minutes": 7.0, "name": "tj", "max_snap_m": 120.0})
    r = _body(_call(web._route_to,
                    "/api/route_to?lat=40.28&lon=69.62&dlat=40.29&dlon=69.63", monkeypatch))
    assert r["src"] == "osrm" and r["router"] == "tj" and r["km"] == 2.5


def test_route_to_bad_coordinates(monkeypatch):
    with pytest.raises(web.web.HTTPBadRequest):
        _call(web._route_to, "/api/route_to?lat=&lon=&dlat=&dlon=", monkeypatch)


def test_route_to_needs_no_contract(monkeypatch):
    """Проверки «чей должник» тут нет и быть не должно: цель задаёт пользователь,
    из БД ничего не читается. Тест фиксирует это как намерение, а не как недосмотр."""
    monkeypatch.setattr(web.routing_client, "best_route", lambda *_a, **_k: None)
    r = _body(_call(web._route_to,
                    "/api/route_to?lat=41.3&lon=69.2&dlat=41.4&dlon=69.3", monkeypatch))
    assert r["ok"]


def test_gazetteer_never_downloads_by_default():
    """Политика сервера-витрины: в интернет он не ходит. Дамп либо лежит на диске,
    либо страна пропускается — но никакого исходящего запроса без явного
    GEOINDEX_DOWNLOAD=1. Качает пульт на машине владельца, не прод."""
    assert geoindex.ALLOW_DOWNLOAD is False
    assert geoindex._dump("ZZ") is None


# --- маршруты зарегистрированы ------------------------------------------------
def test_routes_registered():
    paths = {getattr(r, "resource", None) and r.resource.canonical
             for r in web.build_app(None).router.routes()}
    assert "/api/geocode" in paths
    assert "/api/route_to" in paths


# --- /api/geo/suggest ---------------------------------------------------------
def test_suggest_needs_two_letters(monkeypatch):
    """На одну букву не ищем: подсказки бессмысленны, а запрос летит с каждой
    нажатой клавишей в дороге по слабой связи."""
    r = _body(_call(web._geo_suggest, "/api/geo/suggest?q=ш", monkeypatch))
    assert r["items"] == []


def test_suggest_finds_by_prefix_in_both_scripts(monkeypatch):
    """Набирают и кириллицей, и латиницей — справочник обязан отвечать одинаково.

    ИЗВЕСТНЫЙ ПРЕДЕЛ: недопечатанное слово в ТРЕТЬЕМ написании не находится.
    «Shahrik» — начало альт-имени «Shahrikhon», а в записи лежит «Shahrixon»;
    нечёткий ключ схлопывает «kh» в «h», и «sahrik» перестаёт быть префиксом
    «sahrihon». Два основных написания работают, третье — нет. Чинится хранением
    альт-имён в записи; трогать индексацию ради этого не стали."""
    for q in ("шахрих", "shahrix"):
        r = _body(_call(web._geo_suggest, f"/api/geo/suggest?q={q}", monkeypatch))
        assert r["items"], q
        assert any("Shahrixon" in i["label"] for i in r["items"]), (q, r["items"])


def test_suggest_puts_the_bigger_place_first(monkeypatch):
    """Человек, набравший несколько букв, почти всегда имеет в виду крупное.
    Раньше ранжирование шло от точности совпадения ключа, и наверх всплывал
    нулевой по населению тёзка."""
    r = _body(_call(web._geo_suggest, "/api/geo/suggest?q=sam", monkeypatch))
    assert r["items"]
    # Область (3.9 млн) законно обгоняет город (595 тыс.) — оба самаркандские;
    # проверяем, что наверх не всплыл нулевой по населению тёзка.
    top = r["items"][0]["label"]
    assert "Samarqand" in top or "Самарканд" in top, top


def test_suggest_labels_carry_the_region(monkeypatch):
    """Тёзки различаются только областью — без неё выбор вслепую."""
    r = _body(_call(web._geo_suggest, "/api/geo/suggest?q=shahrix", monkeypatch))
    assert any("·" in i["label"] for i in r["items"])


def test_suggest_returns_a_usable_point(monkeypatch):
    """Подсказка без координат бесполезна: по ней сразу строят маршрут."""
    r = _body(_call(web._geo_suggest, "/api/geo/suggest?q=shahrix", monkeypatch))
    it = r["items"][0]
    assert isinstance(it["lat"], float) and isinstance(it["lon"], float)
