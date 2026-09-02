"""
Геокодинг адресов реестра → координаты. Слоями (комбинация, к которой пришли с Егором):

0) НОРМАЛИЗАЦИЯ адреса — `normalize` (libpostal, если поднят; иначе офлайн): кривую
   таджикскую кириллицу/сокращения → чистый запрос и отдельный локалити-токен;
1) точный геокодер — Nominatim/OSM → Photon/OSM (бесплатно; опц. Yandex по ключу) — города/улицы;
2) центроид населённого пункта из GeoNames (feature class P, страны TJ/UZ) — аулы/кишлаки,
   где точного адреса не найти (в Таджикистане это норма);
3) ничего → координату ставит выездник на месте (geo_src=manual, не перетираем).

Запускается в ПУЛЬТЕ/на ПК владельца (есть интернет), не на телефоне и НЕ на сервере-витрине
(реальные ПД туда не уезжают). Точные геокодеры по умолчанию публичные; для 152-ФЗ их базовые
URL переключаются на свой Nominatim/Photon через env NOMINATIM_URL / PHOTON_URL.
Дампы GeoNames кэшируются локально (TJ ~0.2 МБ, UZ ~0.4 МБ).
Атрибуция: © OpenStreetMap, © GeoNames (CC-BY).
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import addr_cis                                           # слой 0: структурный разбор адреса СНГ
import db
import geoindex                                           # иерархический офлайн-справочник координат
import normalize                                          # слой 1: libpostal/офлайн-нормализация
import scoring                                            # источник истины по городам Согда (parse_city)

GEO_DIR = Path(os.getenv("HARDBOT_DATA", Path(__file__).parent)) / "geonames"
COUNTRIES = ["TJ", "UZ"]
UA = "hardbot-geocoder/0.1 (internal collection-agency tool)"
YANDEX_KEY = os.getenv("YANDEX_GEOCODER_KEY", "").strip()
# Базовые URL точных геокодеров — по умолчанию публичные OSM-сервисы; для 152-ФЗ/он-прем
# переключаются на свой Nominatim/Photon одной env-переменной (адреса должников не уходят наружу).
NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search").rstrip("/")
PHOTON_URL = os.getenv("PHOTON_URL", "https://photon.komoot.io/api").rstrip("/")
# Гео-привязка по странам: иначе «Похтачи» уедет в «Потаниху» под Казанью. TJ/UZ/RU.
COUNTRYCODES = os.getenv("GEOCODER_COUNTRIES", "tj,uz,ru")
_ALLOWED_CC = {c.strip().upper() for c in COUNTRYCODES.split(",") if c.strip()}
# Точный хит принимаем, только если он рядом с известным центром города. Иначе фаззи-матч
# уводит таджикский кишлак в РФ (Похтачи→Потаниха). 60 км покрывает город+пригород.
MAX_ANCHOR_KM = float(os.getenv("GEOCODER_MAX_KM", "60"))


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))

_INDEX: dict[str, tuple[float, float, int]] | None = None


def _norm(s: str | None) -> str:
    return (s or "").strip().lower().replace("ё", "е")


def _tokens(text: str) -> list[str]:
    """Слова + соседние биграммы (для «Нижний Новгород», «кишлак Похтачи»)."""
    words = [w for w in re.split(r"[^\wёЁ]+", _norm(text)) if len(w) >= 3]
    grams = list(words)
    grams += [f"{a} {b}" for a, b in zip(words, words[1:])]
    return grams


# --- GeoNames (офлайн-газеттир населённых пунктов) -------------------------
def _download_geonames(cc: str) -> Path:
    GEO_DIR.mkdir(parents=True, exist_ok=True)
    txt = GEO_DIR / f"{cc}.txt"
    if txt.exists() and txt.stat().st_size > 0:
        return txt
    url = f"https://download.geonames.org/export/dump/{cc}.zip"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    data = urllib.request.urlopen(req, timeout=90).read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        txt.write_bytes(z.read(f"{cc}.txt"))
    return txt


def load_gazetteer(countries: list[str] = COUNTRIES) -> dict:
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    idx: dict[str, tuple[float, float, int]] = {}
    for cc in countries:
        path = _download_geonames(cc)
        for line in path.read_text(encoding="utf-8").splitlines():
            p = line.split("\t")
            if len(p) < 15 or p[6] != "P":          # только populated places
                continue
            try:
                lat, lon = float(p[4]), float(p[5])
            except ValueError:
                continue
            pop = int(p[14]) if p[14].isdigit() else 0
            names = [p[1], p[2]] + (p[3].split(",") if p[3] else [])
            for nm in names:
                k = _norm(nm)
                if len(k) < 3:
                    continue
                if k not in idx or pop > idx[k][2]:   # держим вариант с наиб. населением
                    idx[k] = (lat, lon, pop)
    _INDEX = idx
    return idx


def geonames_lookup(text: str, countries: list[str] = COUNTRIES) -> dict | None:
    idx = load_gazetteer(countries)
    best = None
    for tok in _tokens(text):
        hit = idx.get(tok)
        if hit and (best is None or hit[2] > best[2]):
            best = (hit[0], hit[1], hit[2], tok)
    if best:
        return {"lat": best[0], "lon": best[1], "src": "geonames", "name": best[3]}
    return None


# --- Точный геокодер -------------------------------------------------------
def _http_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def nominatim_lookup(address: str) -> dict | None:
    q = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1,
                                "countrycodes": COUNTRYCODES, "accept-language": "ru"})
    r = _http_json(f"{NOMINATIM_URL}?{q}")
    time.sleep(1.1)                                   # уважаем лимит 1 req/s публичного OSM
    if r:
        return {"lat": float(r[0]["lat"]), "lon": float(r[0]["lon"]),
                "src": "nominatim", "name": (r[0].get("display_name") or "")[:60]}
    return None


def photon_lookup(address: str) -> dict | None:
    """Photon (Komoot, OSM). Хорош на города/улицы; свой инстанс — по env PHOTON_URL.
    Без countrycodes у Photon нет, поэтому фильтруем выдачу по стране постфактум."""
    q = urllib.parse.urlencode({"q": address, "limit": 5})   # берём несколько → фильтр по стране
    r = _http_json(f"{PHOTON_URL}?{q}")
    time.sleep(0.5)                                   # вежливо к публичному инстансу
    feats = r.get("features") if isinstance(r, dict) else None
    for f in feats or []:
        p = f.get("properties", {})
        cc = (p.get("countrycode") or "").upper()
        if _ALLOWED_CC and cc and cc not in _ALLOWED_CC:
            continue                                  # отсекаем чужую страну (TJ-аул ≠ село под Казанью)
        lon, lat = f["geometry"]["coordinates"][:2]
        return {"lat": float(lat), "lon": float(lon), "src": "photon",
                "name": (p.get("name") or p.get("city") or p.get("district") or "")[:60]}
    return None


def yandex_lookup(address: str) -> dict | None:
    if not YANDEX_KEY:
        return None
    q = urllib.parse.urlencode({"apikey": YANDEX_KEY, "geocode": address,
                                "format": "json", "results": 1, "lang": "ru_RU"})
    r = _http_json(f"https://geocode-maps.yandex.ru/1.x/?{q}")
    feats = r["response"]["GeoObjectCollection"]["featureMember"]
    if feats:
        lon, lat = feats[0]["GeoObject"]["Point"]["pos"].split()
        return {"lat": float(lat), "lon": float(lon), "src": "yandex",
                "name": feats[0]["GeoObject"].get("name", "")}
    return None


def _city_anchor(address: str | None, city: str | None) -> dict | None:
    """НАДЁЖНЫЙ центр города из курируемого справочника Согда (scoring) — только он годится
    как порог расстояния. GeoNames по имени даёт коллизии (в ТДж есть село «Москва»,
    кишлак «Ленинский»), поэтому в guard его не берём — точные геокодеры с привязкой по
    стране сами справляются с городами РФ/УЗ."""
    _disp, lat, lon = scoring.parse_city(address, city)
    if lat is not None:
        return {"lat": lat, "lon": lon, "src": "city", "name": _disp}
    return None


# Страны и города, которых в дампах нет. Список КУРИРУЕМЫЙ и заведомо неполный —
# он не «определяет страну», а закрывает то, что реально набирают руками и что
# схлопывалось в тёзку: русские областные центры и прямо названные страны.
# Появится дамп по региону — строку отсюда убрать.
_NO_COVERAGE = (
    "казахстан", "kazakhstan", "кыргыз", "киргиз", "kyrgyz", "украина", "ukraine",
    "беларус", "belarus", "турци", "turkey", "türkiye",
    "москв", "moscow", "петербург", "petersburg", "екатеринбург", "новосибирск",
    "казань", "нижний новгород", "челябинск", "омск", "ростов", "уфа",
    "красноярск", "воронеж", "пермь", "волгоград", "краснодар", "саратов",
    "тюмень", "сургут", "иркутск",
)

# Самарская область В СПРАВОЧНИКЕ ЕСТЬ (server/geonames/RU.txt) — её и только её
# из России пропускаем. Само слово «Россия» тут не запрещаем: «Россия, Самарская
# область, Тольятти» — законный запрос, а «Россия, Москва» отсечётся по «москв».
_RU_COVERED = ("самар", "тольятти", "togliatti", "samara", "syzran", "сызран",
               "новокуйбышевск", "чапаевск", "жигулевск", "жигулёвск", "кинель",
               "отрадн", "похвистнев", "нефтегорск", "безенчук", "кинель-черкас")


_WORD_START = re.compile(r"[^\W\d_]", re.UNICODE)


def _outside_coverage(text: str) -> bool:
    """Адрес заведомо вне покрытия справочника?

    Маркеры — ОСНОВЫ слов, и сверяются они с НАЧАЛОМ слова, а не с целым словом:
    «москв» обязана поймать «Москва», «Москве», «Москвой». Первая версия требовала,
    чтобы после совпадения не шло букв, — и не ловила вообще ничего склоняемого:
    «Москва, Тверская 1» преспокойно уезжала в таджикский кишлак Москва.

    Граница нужна только в начале: без неё «уфа» срабатывала бы внутри «Мустафа»,
    а «ростов» — внутри «Ростовка».
    """
    low = f" {text.lower()} "
    if any(w in low for w in _RU_COVERED):
        return False                       # Самарская область есть в справочнике
    for marker in _NO_COVERAGE:
        pos = low.find(marker)
        while pos >= 0:
            prev = low[pos - 1:pos]
            if not _WORD_START.match(prev or " "):
                return True                # совпало с началом слова
            pos = low.find(marker, pos + 1)
    return False


def geocode_address(address: str | None, city: str | None = None,
                    use_precise: bool = True) -> dict | None:
    full = ", ".join(x for x in [address, city] if x)
    if not full:
        return None
    # Справочник знает только Таджикистан и Узбекистан. Раньше на чужой стране он
    # не молчал, а уверенно врал: «Самара, улица Ново-Садовая» → Samariy (Узбекистан),
    # точность «город»; «Самарская область, Тольятти» → Rudaki District (Таджикистан).
    # Молчаливый неверный ответ хуже отказа: точка уезжает за тысячи километров, а
    # карточка утверждает, что всё в порядке. Пока страны нет в дампах — отказываем.
    if _outside_coverage(full):
        return None
    # Слой 0: структурный разбор официального адреса СНГ. Он же даёт чистый запрос
    # для точных геокодеров — раньше туда уходила трёхалфавитная каша
    # («ўзбекистон самарканд вилояти пастдаргом тумани илм мфй улица илм кишлоги»),
    # теперь «Самаркандская обл., Пастдарг‘ом р-н, МФЙ Илм, ул. Илм».
    parsed = addr_cis.parse(address, city)
    idx = None
    try:
        idx = geoindex.load()
    except Exception:                                  # noqa: BLE001 - справочник не обязателен
        idx = None
    q = (addr_cis.pretty(parsed, idx.display_names(parsed) if idx else None)
         or normalize.query(address, city) or full)
    loc = normalize.locality(full) or full             # чистая строка локалити (старый путь)
    anchor = _city_anchor(address, city)               # надёжный якорь Согда (guard) | None
    if use_precise:
        # Yandex (если ключ) → Nominatim → Photon. Сеть не должна валить батч.
        for fn in (yandex_lookup, nominatim_lookup, photon_lookup):
            try:
                res = fn(q)
            except Exception:                          # noqa: BLE001
                res = None
            if res:
                if anchor and _km((anchor["lat"], anchor["lon"]),
                                  (res["lat"], res["lon"])) > MAX_ANCHOR_KM:
                    continue                           # точный хит уехал далеко от города — не верим
                res.setdefault("precision", "exact")
                return res
    # Офлайн-фолбэк. Иерархический справочник ИДЁТ ПЕРВЫМ и обходит якорь Согда:
    # якорь знает только 9 городов и всегда возвращает центр города, а справочник
    # доводит до махалли/кишлака и честно сообщает достигнутую точность.
    if idx:
        hit = idx.resolve(parsed)
        if hit:
            return hit
    if anchor:
        anchor.setdefault("precision", "city")
        return anchor
    hit = geonames_lookup(loc)
    if hit:
        hit.setdefault("precision", "region")
    return hit


# --- Пакетный прогон по реестру -------------------------------------------
def geocode_all(overwrite: bool = False, use_precise: bool = True,
                limit: int = 100000, progress=None) -> dict:
    rows = db.all_clients(limit=limit)
    stat = {"total": len(rows), "precise": 0, "aul": 0, "skip": 0, "fail": 0}
    for i, r in enumerate(rows):
        if r["geo_src"] == "manual":                   # точку поставил выездник — свято
            stat["skip"] += 1
        elif r["geo_lat"] and not overwrite:
            stat["skip"] += 1
        else:
            res = geocode_address(r["address"], r["city"], use_precise=use_precise)
            if res:
                db.update_fields(r["contract_id"], {
                    "geo_lat": round(res["lat"], 6), "geo_lon": round(res["lon"], 6),
                    "geo_src": res["src"],
                    "geo_precision": res.get("precision") or ""})
                stat["precise" if res["src"] in ("nominatim", "yandex", "photon") else "aul"] += 1
            else:
                stat["fail"] += 1
        if progress:
            progress(i + 1, stat)
    return stat


if __name__ == "__main__":
    db.init_db()
    print(geocode_all(progress=lambda i, s: print(i, s) if i % 10 == 0 else None))
