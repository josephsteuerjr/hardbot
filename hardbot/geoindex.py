"""
Иерархический офлайн-справочник координат (GeoNames) — «куда именно ехать».

ЧЕМ ОТЛИЧАЕТСЯ ОТ ПРЕЖНЕГО ПОИСКА. Раньше `geocode.geonames_lookup` разбивал адрес
на токены, искал каждый в плоском индексе и брал тот, **у кого больше населения**.
На реальном реестре это давало 95.8% «попаданий», из которых 362 из 597 — одна и та
же точка «Самарканд»: адрес называл кишлак Зангибой в Пастдаргомском районе, а точка
ставилась в центр области за 40 км. Формально геокодировано, по факту — выезд впустую.

Здесь наоборот: сначала разбираем адрес (`addr_cis`), потом идём **от самого точного
уровня к самому грубому** — кишлак → махалля → джамоат → город → район → область —
и на каждом шаге ищем только среди объектов НУЖНОЙ области/района (коды adm1/adm2
из GeoNames). Это же убирает старую беду с коллизиями имён: в Таджикистане есть село
«Москва» и кишлак «Ленинский», и без привязки к области они уводили точку в РФ.

Точность возвращается явно (`precision`), чтобы её было видно в пульте и в карточке:
    village → mahalla → jamoat → city → district → region
Ниже `district` ехать по такой точке бессмысленно — это «где-то в области».

ДВА ИСТОЧНИКА, и это не роскошь. GeoNames знает по Узбекистану 8010 населённых
пунктов и почти не знает махаллей (МФЙ) — а в узбекском адресе локалити это именно
МФЙ. Поэтому 37% узбекских адресов доезжали только до района. Второй слой — места
из того же OSM, из которого собран дорожный граф OSRM (~15 000 объектов по УЗ,
включая neighbourhood/quarter): на реальных 17 075 договорах доля адресов, годных
для выезда, выросла с 57.7% до 66.2% по Узбекистану.

Атрибуция: © GeoNames (CC-BY), © участники OpenStreetMap (ODbL).
Оба справочника лежат файлами рядом с модулем; в рантайме бота ничего не качается.
"""
from __future__ import annotations

import io
import math
import os
import urllib.request
import zipfile
from pathlib import Path

import addr_cis

# Справочник ищем в ДВУХ местах, и порядок важен.
#   1) том с данными (HARDBOT_DATA) — туда кладут обновлённые дампы без передеплоя;
#   2) папка рядом с кодом — туда справочник приезжает ВМЕСТЕ с релизом.
# Без второго пути на проде случилась ровно эта ошибка: файлы уехали в /app/geonames,
# а модуль смотрел только в /data/geonames и молча грузил половину справочника
# (20 016 ключей вместо 40 118, ноль улиц) — без единой ошибки в логах.
GEO_DIRS = tuple(dict.fromkeys([
    Path(os.getenv("HARDBOT_DATA", Path(__file__).parent)) / "geonames",
    Path(__file__).parent / "geonames",
]))
GEO_DIR = GEO_DIRS[0]          # куда качать дампы (совместимость)


def _find(name: str) -> Path | None:
    """Первый существующий файл справочника из GEO_DIRS."""
    for directory in GEO_DIRS:
        candidate = directory / name
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None
UA = "hardbot-geocoder/0.2 (internal collection-agency tool)"
# RU здесь — это НЕ вся Россия. В hardbot/geonames/RU.txt лежит только Самарская
# область (adm1=65, 2010 записей, 300 КБ): владелец тестирует навигатор со своего
# адреса, а полный дамп на 412 тысяч строк ради этого возить незачем. Понадобится
# другой регион — доложить строки тем же фильтром и расширить _NO_COVERAGE в geocode.
DEFAULT_COUNTRIES = tuple(
    c.strip().upper()
    for c in os.getenv("GEOINDEX_COUNTRIES", "TJ,UZ,RU").split(",") if c.strip()
)

# Порядок уровней от точного к грубому — он же порядок попыток поиска.
PRECISION_ORDER = ["street", "village", "mahalla", "jamoat", "city", "district", "region"]

# Улицу принимаем только рядом с уже найденным населённым пунктом. Названия улиц
# в СНГ повторяются в каждом городе («Навои», «Мустақиллик», «Ленина»), поэтому
# искать их по всей стране бессмысленно — нужна привязка к локалити.
STREET_MAX_KM = 12.0

# Какие типы объектов GeoNames годятся под какой уровень адреса.
# P — населённый пункт, ADM1 — область, ADM2 — район. Реки/горы (T/H/S) не годятся:
# в реестре встречается «НАРПАЙ ТУМАНИ», и без этого фильтра «Нарпай» матчится в реку.
_OK_CLASS = {
    "village": ("P",), "mahalla": ("P",), "jamoat": ("P", "A"), "city": ("P", "A"),
    "district": ("A", "P"), "region": ("A",),
}
_OK_CODE = {
    "district": ("ADM2", "ADM3", "PPLA2", "PPLA3"),
    "region": ("ADM1", "PPLA"),
    # ADM1 — это ЦЕНТРОИД ОБЛАСТИ. Раньше на уровне «город» фильтра по коду не было,
    # класс "A" проходил, и запись области отдавалась как precision="city": метка
    # врала, а точка вдобавок перебивала известный центр района, который был бы
    # заметно ближе. Ниже области — только настоящие населённые пункты.
    "city": ("PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLC", "PPLX", "ADM3", "ADM4"),
    "jamoat": ("PPL", "PPLA2", "PPLA3", "PPLA4", "PPLX", "ADM3", "ADM4"),
}


class _Rec:
    __slots__ = ("name", "fclass", "fcode", "cc", "adm1", "adm2",
                 "lat", "lon", "pop", "ru", "src")

    def __init__(self, p: list[str]):
        self.name = p[1]
        self.fclass, self.fcode, self.cc = p[6], p[7], p[8]
        self.adm1, self.adm2 = p[10], p[11]
        self.lat, self.lon = float(p[4]), float(p[5])
        self.pop = int(p[14]) if p[14].isdigit() else 0
        self.ru = _ru_variant(p[3])
        self.src = "geonames"

    def __repr__(self) -> str:                                  # pragma: no cover
        return f"<{self.name} {self.fcode} {self.cc}/{self.adm1}/{self.adm2}>"


# --- Второй источник: места из OSM ---------------------------------------------
# GeoNames по Узбекистану знает 8010 населённых пунктов и почти не знает махаллей
# (МФЙ) — а в узбекском адресе МФЙ и есть локалити. Из-за этого 37% узбекских
# адресов доезжали только до района. Тот же дорожный граф OSM, из которого собран
# OSRM, содержит ~15 000 названных мест по УЗ, включая neighbourhood/quarter.
#
# Плата за плотность: у объектов OSM НЕТ кодов adm1/adm2, поэтому защита «искать
# только в своей области» через коды к ним неприменима. Вместо кодов — геометрия:
# кандидат принимается, только если он рядом с уже найденным центром района или
# области (см. _MAX_KM). Иначе тёзка из другого конца страны снова победит.
_OSM_LEVEL = {
    "city": "city", "town": "city", "borough": "city",
    "village": "village", "hamlet": "village", "isolated_dwelling": "village",
    "locality": "village", "farm": "village", "allotments": "village",
    "neighbourhood": "mahalla", "quarter": "mahalla", "suburb": "mahalla",
    "city_block": "mahalla", "residential": "mahalla",
}
_ADMIN_LEVEL = {"4": "region", "6": "district", "7": "district",
                "8": "city", "9": "mahalla", "10": "mahalla"}
# Насколько далеко от опорной точки (центр района/области) разрешено принимать
# объект OSM. Район УЗ/ТДж редко больше 40 км в поперечнике, область — 120.
_MAX_KM = {"district": 40.0, "region": 120.0}


class _OsmRec:
    __slots__ = ("name", "fclass", "fcode", "cc", "adm1", "adm2",
                 "lat", "lon", "pop", "ru", "src", "level")

    def __init__(self, cc: str, kind: str, admin_level: str,
                 name: str, lat: float, lon: float):
        self.name, self.cc = name, cc
        self.lat, self.lon = lat, lon
        self.level = _OSM_LEVEL.get(kind) or _ADMIN_LEVEL.get(admin_level) or "village"
        self.fclass, self.fcode = "P", "OSM"
        self.adm1 = self.adm2 = ""          # кодов нет — работает гео-проверка
        self.pop, self.ru, self.src = 0, "", "osm"

    def __repr__(self) -> str:                                  # pragma: no cover
        return f"<osm {self.name} {self.level} {self.cc}>"


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _ru_variant(alt: str) -> str:
    """Русское написание из альтернативных имён GeoNames (для читаемого адреса).

    Ловушка: в списке альтернатив лежат имена на ДЕСЯТКАХ языков, и многие из них
    кириллические — «Самаркандан область» (чеченский), «Самарканд облусу» (киргизский),
    «Вилояти Самарқанд» (таджикский). Просто «первое кириллическое со словом область»
    даёт чушь. Русскую форму узнаём по прилагательному на -ская/-ский."""
    best = ""
    for name in (alt or "").split(","):
        s = name.strip()
        if not s or not any("а" <= ch.lower() <= "я" for ch in s):
            continue
        low = s.lower()
        if low.startswith(("вилояти", "viloyat")):
            continue                       # узбекско-таджикская калька, не русский
        if "ская область" in low or "ский район" in low or "ская обл" in low:
            return s
        if ("област" in low or "район" in low) and not best:
            best = s
    return best


# Слова, по которым запись ADM1 опознаётся как ОБЛАСТЬ, а не как одноимённый
# город-столица. В GeoNames у обоих fcode=ADM1, различаются они только именем:
# «Tashkent Region» (adm1=14) и «Tashkent» (adm1=13).
_REGION_WORDS = ("region", "province", "viloyat", "oblast", "область", "вилоят")


def _prefer_admin1(hits, *, want_region: bool):
    """Выбрать ADM1 из нескольких одноимённых: столица или одноимённая область.

    Раньше брали первую попавшуюся через `break`, а порядок задавал файл дампа.
    Для «Ташкент» первой ложилась Ташкентская ОБЛАСТЬ, и опорной точкой для всего
    города становился её центроид — адрес в Юнусабаде уезжал за 65 км, а точность
    при этом рапортовалась «махалля».

    Что именно имел в виду адрес, знает разбор: «ТОШКЕНТ ВИЛОЯТИ» кладётся в
    region, «ТОШКЕНТ ШАҲРИ» — в city. По этому и выбираем.
    """
    hits = list(hits)
    if not hits:
        return None

    def is_region(rec) -> bool:
        low = (rec.name or "").lower()
        return any(w in low for w in _REGION_WORDS)

    preferred = [r for r in hits if is_region(r) == want_region]
    return (preferred or hits)[0]


class Index:
    """Индекс GeoNames: по нечёткому ключу имени → записи, плюс коды adm1/adm2."""

    def __init__(self) -> None:
        self.by_key: dict[str, list[_Rec]] = {}
        self.adm1_by_key: dict[str, list[_Rec]] = {}
        self.adm2_by_key: dict[str, list[_Rec]] = {}
        self.adm1_by_code: dict[tuple[str, str], _Rec] = {}
        self.adm2_by_code: dict[tuple[str, str, str], _Rec] = {}
        self.streets: dict[str, list[tuple[str, float, float]]] = {}

    def add_street(self, name: str, alt: str, lat: float, lon: float) -> None:
        """Именованная дорога из OSM. Отдельный слой: улицу ищем не по всей стране,
        а только рядом с уже найденным населённым пунктом."""
        for nm in [name] + [a for a in (alt or "").split("|") if a]:
            if len(nm.strip()) < 3:
                continue
            for key in addr_cis.keys(addr_cis.strip_markers(nm)):
                if key:
                    self.streets.setdefault(key, []).append((name, lat, lon))

    def _street_near(self, name: str, lat: float, lon: float):
        """Ближайшая одноимённая улица в пределах STREET_MAX_KM от локалити."""
        best = None
        for key in addr_cis.keys(addr_cis.strip_markers(name)):
            for nm, slat, slon in self.streets.get(key, ()):
                d = _km((lat, lon), (slat, slon))
                if d <= STREET_MAX_KM and (best is None or d < best[0]):
                    best = (d, nm, slat, slon)
        return best

    # --- построение ---------------------------------------------------------
    def add_osm(self, cc: str, kind: str, admin_level: str,
                name: str, alt: str, lat: float, lon: float) -> None:
        """Место из OSM. Кладём только в общий индекс имён: для привязки к области
        оно не годится (кодов adm нет), зато закрывает махалли и кишлаки, которых
        в GeoNames нет."""
        rec = _OsmRec(cc, kind, admin_level, name, lat, lon)
        for nm in [name] + [a for a in (alt or "").split("|") if a]:
            if len(nm.strip()) < 3:
                continue
            for variant in {nm, addr_cis.strip_markers(nm)}:
                for key in addr_cis.keys(variant):
                    if key:
                        self.by_key.setdefault(key, []).append(rec)

    def add(self, p: list[str]) -> None:
        rec = _Rec(p)
        names = [p[1], p[2]] + (p[3].split(",") if p[3] else [])
        buckets = [self.by_key]
        if rec.fcode == "ADM1":
            buckets.append(self.adm1_by_key)
            self.adm1_by_code[(rec.cc, rec.adm1)] = rec
        elif rec.fcode in ("ADM2", "ADM3"):
            buckets.append(self.adm2_by_key)
            self.adm2_by_code.setdefault((rec.cc, rec.adm1, rec.adm2), rec)
        for name in names:
            if len(name.strip()) < 3:
                continue
            # Индексируем и «как есть», и без тип-слова: в реестре пишут
            # «САМАРКАНДСКАЯ ОБЛАСТЬ», в GeoNames — «Самаркандская область»,
            # а район там же «Pastdarg‘om Tumani». Без среза ключи не сходятся,
            # и привязка к области/району отваливается вместе с защитой от
            # коллизий имён.
            for variant in {name, addr_cis.strip_markers(name)}:
                for key in addr_cis.keys(variant):
                    if not key:
                        continue
                    for bucket in buckets:
                        bucket.setdefault(key, []).append(rec)

    # --- поиск --------------------------------------------------------------
    def _hits(self, name: str, bucket: dict[str, list[_Rec]]) -> list[_Rec]:
        """Записи по имени, БЕЗ повторов. Один объект лежит в индексе под
        несколькими именами (основное + альтернативные + вариант без тип-слова),
        и без дедупликации он выглядел бы как несколько разных кандидатов — из-за
        чего правило «принимаем OSM только при единственном кандидате» ошибочно
        отказывало на однозначных совпадениях."""
        out: list[_Rec] = []
        seen: set[int] = set()
        for key in addr_cis.keys(name):
            for rec in bucket.get(key, ()):
                if id(rec) not in seen:
                    seen.add(id(rec))
                    out.append(rec)
        return out

    def _admin_codes(self, parsed: dict) -> tuple[str | None, str | None, str | None]:
        """(cc, adm1, adm2) по названиям области и района из адреса."""
        cc = adm1 = adm2 = None
        # Область ищем не только в поле region: в таджикских адресах её пишут без
        # слова «вилояти» — «Суғд шаҳри Истаравшан…», и разбор кладёт «Суғд» в city.
        # Без этой подстраховки привязка к области теряется, а с ней и защита от
        # тёзок: «Ҷамоати Зарҳалол» уезжал из-под Истаравшана в Хатлон за 281 км.
        for field in ("region", "city", "district"):
            name = parsed.get(field)
            if not name:
                continue
            hits = self._hits(name, self.adm1_by_key)
            rec = _prefer_admin1(hits, want_region=(field == "region"))
            if rec is not None:
                cc, adm1 = rec.cc, rec.adm1
                break
        district = parsed.get("district")
        if district:
            for rec in self._hits(district, self.adm2_by_key):
                if adm1 and rec.adm1 != adm1:
                    continue                    # район из другой области — не наш
                cc, adm1, adm2 = rec.cc, rec.adm1, rec.adm2
                break
        return cc, adm1, adm2

    def _pick(self, recs: list[_Rec], level: str, cc: str | None,
              adm1: str | None, adm2: str | None, anchor=None):
        """Сначала GeoNames (там есть коды области/района — защита строгая),
        и только если он пуст — OSM с гео-проверкой по опорной точке."""
        best = self._pick_geonames([r for r in recs if r.src == "geonames"],
                                   level, cc, adm1, adm2, anchor)
        if best:
            return best
        return self._pick_osm([r for r in recs if r.src == "osm"], level, anchor)

    def _pick_osm(self, cands: list, level: str, anchor):
        """Объект OSM годится, только если он нужного уровня И лежит рядом с уже
        найденным центром района/области. Кодов adm у OSM нет, поэтому геометрия
        здесь — единственная защита от тёзки с другого конца страны.

        Без опорной точки принимаем ТОЛЬКО единственного кандидата: названий
        «Навобод» или «Гулистон» в Таджикистане десятки, и выбирать из них
        наугад — то же самое, что ставить точку случайно."""
        cands = [r for r in cands if r.level == level]
        if not cands:
            return None
        if anchor:
            lat0, lon0, kind = anchor
            limit = _MAX_KM.get(kind, 120.0)
            near = [r for r in cands if _km((lat0, lon0), (r.lat, r.lon)) <= limit]
            if not near:
                return None
            near.sort(key=lambda r: _km((lat0, lon0), (r.lat, r.lon)))
            return near[0]
        return cands[0] if len(cands) == 1 else None

    def _pick_geonames(self, recs: list[_Rec], level: str, cc: str | None,
                       adm1: str | None, adm2: str | None, anchor=None) -> _Rec | None:
        """Выбрать запись нужного типа, максимально согласованную с адресом."""
        ok_class = _OK_CLASS.get(level, ("P", "A"))
        ok_code = _OK_CODE.get(level)
        cands = [r for r in recs
                 if r.fclass in ok_class and (not ok_code or r.fcode in ok_code)]
        if not cands:
            return None
        # Чем больше совпало уровней административной привязки, тем вернее объект.
        def rank(r: _Rec) -> tuple:
            return (
                -int(bool(adm2) and r.adm2 == adm2),
                -int(bool(adm1) and r.adm1 == adm1),
                -int(bool(cc) and r.cc == cc),
                -r.pop if level in ("city", "district", "region") else r.pop,
            )
        cands.sort(key=rank)
        best = cands[0]
        # Если область в адресе названа, а лучший кандидат оказался из ДРУГОЙ —
        # это совпадение имён, а не наш объект. Именно так «Ҷамоати Зарҳалол»
        # под Истаравшаном уезжал за 281 км в Хатлон, а таджикское село «Москва» —
        # в Россию. Отбрасываем и идём на уровень грубее (район → область), где
        # ответ гарантированно свой.
        if adm1 and best.adm1 != adm1 and level != "region":
            return None
        # Гео-проверка — та же, что для OSM. Код района в дампах заполнен не всегда
        # (у всех 62 записей «Гулистон» и 25 «Навобод» класса P поле adm2 пустое),
        # поэтому ранг по adm2 там ничего не решает: все кандидаты внутри области
        # равны, и побеждает тот, кто первым лёг в индекс — тёзка за сотню
        # километров. Опорная точка это ловит.
        if anchor and level not in ("district", "region"):
            lat0, lon0, kind = anchor
            if _km((lat0, lon0), (best.lat, best.lon)) > _MAX_KM.get(kind, 120.0):
                near = [r for r in cands
                        if _km((lat0, lon0), (r.lat, r.lon)) <= _MAX_KM.get(kind, 120.0)]
                if not near:
                    return None
                near.sort(key=lambda r: _km((lat0, lon0), (r.lat, r.lon)))
                return near[0]
        return best

    def resolve(self, parsed: dict) -> dict | None:
        """Разобранный адрес → {lat, lon, name, precision, src} или None.
        Идём от самого точного уровня к самому грубому."""
        parsed = _apply_aliases(parsed)
        cc, adm1, adm2 = self._admin_codes(parsed)
        anchor = self._anchor(cc, adm1, adm2, parsed)
        for name, level in addr_cis.geo_candidates(parsed):
            rec = self._pick(self._hits(name, self.by_key), level, cc, adm1, adm2, anchor)
            if rec:
                hit = {"lat": rec.lat, "lon": rec.lon, "name": rec.name,
                       "precision": level, "src": rec.src, "cc": rec.cc}
                # Локалити найдено — пробуем уточнить до улицы. Одноимённые улицы
                # есть в каждом городе, поэтому ищем только вокруг локалити.
                street = parsed.get("street")
                if street and level in ("village", "mahalla", "jamoat", "city"):
                    near = self._street_near(street, rec.lat, rec.lon)
                    if near:
                        _d, nm, slat, slon = near
                        return {"lat": slat, "lon": slon, "name": f"{nm} ({rec.name})",
                                "precision": "street", "src": "osm", "cc": rec.cc}
                return hit
        # Ничего именного не нашли — отдаём центр района, затем области.
        if cc and adm1 and adm2:
            rec = self.adm2_by_code.get((cc, adm1, adm2))
            if rec:
                return {"lat": rec.lat, "lon": rec.lon, "name": rec.name,
                        "precision": "district", "src": "geonames", "cc": rec.cc}
        if cc and adm1:
            rec = self.adm1_by_code.get((cc, adm1))
            if rec:
                return {"lat": rec.lat, "lon": rec.lon, "name": rec.name,
                        "precision": "region", "src": "geonames", "cc": rec.cc}
        return None

    def _anchor(self, cc, adm1, adm2, parsed: dict | None = None):
        """Опорная точка для гео-проверки объектов OSM: город из адреса, затем центр
        района, затем области. (lat, lon, «насколько далеко разрешено») или None —
        тогда OSM принимается только при единственном кандидате.

        Город идёт ПЕРВЫМ, хотя раньше его не спрашивали вовсе. «ОХАНГАРОН ШАҲРИ,
        BO‘STON MFY»: района в адресе нет, поэтому опорной точкой становился центр
        Ташкентской области — а до него неправильный «Bo'ston» под Ангреном ближе
        (34 км), чем правильный под Охангароном (43 км). Побеждал тёзка, и точка
        уезжала за 42 км при заявленной точности «махалля». Город в адресе назван
        прямым текстом и на порядок надёжнее центра области."""
        if cc and adm1 and adm2:
            rec = self.adm2_by_code.get((cc, adm1, adm2))
            if rec:
                return rec.lat, rec.lon, "district"
        # Района нет, а город в адресе назван — он и есть лучшая опора. «ОХАНГАРОН
        # ШАҲРИ, BO‘STON MFY»: без этого опорой становился центр Ташкентской области,
        # до которого неправильный «Bo'ston» под Ангреном ближе (34 км), чем
        # правильный под Охангароном (43 км) — побеждал тёзка за 39 км от адреса.
        # Только при ОТСУТСТВИИ района: когда район известен, он надёжнее города
        # (в «г. Ташкент, Юнусабадский район» город-миллионник слишком крупная опора).
        if parsed and parsed.get("city"):
            rec = self._pick_geonames(
                [r for r in self._hits(parsed["city"], self.by_key)
                 if r.src == "geonames"], "city", cc, adm1, None, None)
            if rec is not None:
                return rec.lat, rec.lon, "district"
        if cc and adm1:
            rec = self.adm1_by_code.get((cc, adm1))
            if rec:
                return rec.lat, rec.lon, "region"
        return None

    def _admin_codes_pub(self, parsed: dict):        # для тестов и диагностики
        return self._admin_codes(_apply_aliases(parsed))

    def display_names(self, parsed: dict) -> dict:
        """Справочные написания для человекочитаемого адреса: область по-русски
        («Самарқанд» → «Самаркандская»), если GeoNames её знает."""
        out: dict[str, str] = {}
        cc, adm1, _adm2 = self._admin_codes(parsed)
        if cc and adm1:
            rec = self.adm1_by_code.get((cc, adm1))
            if rec and rec.ru:
                out["region"] = rec.ru.replace(" область", "").replace(" Область", "")
        return out


# Курируемые псевдонимы: то, что GeoNames не знает, а реестр пишет постоянно.
# Ключ — как в реестре, значение — имя, под которым объект лежит в справочнике.
# Держим здесь, а не в GeoNames-дампе, чтобы обновление дампа их не стирало.
_ALIASES: dict[str, str] = {
    # Ноҳияҳои тобеи марказ (районы республиканского подчинения) — в реестре
    # пишут «НТМ» и «шахру нохияхои тобеи Марказ». Это РЕГИОН, а не город;
    # без этой строки «НТМ» читался как населённый пункт «Нтм».
    "нтм": "Districts of Republican Subordination",
    "шахру нохияхои тобеи марказ": "Districts of Republican Subordination",
    "нохияхои тобеи марказ": "Districts of Republican Subordination",
}
# Уровень, на котором псевдоним разрешается искать (иначе «НТМ» подставится
# как город и снова уведёт точку не туда).
_ALIAS_LEVEL = {"нтм": "region", "шахру нохияхои тобеи марказ": "region",
                "нохияхои тобеи марказ": "region"}

_ALIAS_KEYS = {addr_cis.fuzzy(k): (v, _ALIAS_LEVEL.get(k, "city"))
               for k, v in _ALIASES.items()}


def _apply_aliases(parsed: dict) -> dict:
    """Заменить известные сокращения на справочные имена и, если надо, перенести
    их на правильный уровень. «НТМ» в поле city — это не город, а регион; пока
    он оставался городом, точка вставала в случайный населённый пункт."""
    out = None
    for field in ("city", "district", "region", "village", "mahalla", "jamoat"):
        val = parsed.get(field)
        if not val:
            continue
        hit = _ALIAS_KEYS.get(addr_cis.fuzzy(val))
        if not hit:
            continue
        name, level = hit
        if out is None:
            out = dict(parsed)
        out.pop(field, None)
        out.setdefault(level, name)
    return out if out is not None else parsed


_INDEX: Index | None = None


# Скачивание дампов — ЯВНОЕ и по умолчанию ВЫКЛЮЧЕНО.
# Справочник читают двое: пульт на машине владельца (там интернет есть и качать
# нормально) и бот на сервере-витрине (а вот ему в сеть ходить нельзя — это то же
# правило, по которому точный геокодинг живёт в пульте, а не на сервере).
# Молчаливая докачка «когда файла нет» превратила бы первый же запрос навигации
# в неожиданный исходящий коннект с прода.
ALLOW_DOWNLOAD = os.getenv("GEOINDEX_DOWNLOAD", "").strip().lower() in ("1", "true", "yes")


def _dump(cc: str) -> Path | None:
    """Путь к дампу страны или None, если его нет и качать не разрешено."""
    found = _find(f"{cc}.txt")
    if found is not None:
        return found
    txt = GEO_DIR / f"{cc}.txt"
    if not ALLOW_DOWNLOAD:
        return None
    GEO_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://download.geonames.org/export/dump/{cc}.zip"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    data = urllib.request.urlopen(req, timeout=120).read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        txt.write_bytes(z.read(f"{cc}.txt"))
    return txt


def load(countries: tuple[str, ...] = DEFAULT_COUNTRIES) -> Index:
    """Построить (или вернуть готовый) индекс. Отсутствующая страна пропускается:
    лучше геокодить по тем справочникам, что есть, чем падать целиком."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    idx = Index()
    for cc in countries:
        path = _dump(cc)
        if path is not None:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    p = line.rstrip("\n").split("\t")
                    if len(p) >= 15 and p[6] in ("P", "A"):
                        try:
                            idx.add(p)
                        except ValueError:
                            continue
        # Второй слой — места из того же OSM, из которого собран дорожный граф.
        # Файл готовится офлайн (osmium по .osm.pbf) и просто лежит рядом;
        # его отсутствие не ошибка — работаем на одном GeoNames.
        osm = _find(f"osm_places_{cc}.tsv")
        if osm is not None:
            with osm.open(encoding="utf-8") as fh:
                next(fh, None)                                  # заголовок
                for line in fh:
                    f = line.rstrip("\n").split("\t")
                    if len(f) < 6:
                        continue
                    try:
                        idx.add_osm(cc, f[0], f[1], f[2], f[3], float(f[4]), float(f[5]))
                    except ValueError:
                        continue
        # Третий слой — именованные улицы из того же OSM. Нужен, потому что улица
        # распознаётся у 76% узбекских адресов, а точка до сих пор ставилась
        # в центр махалли.
        streets = _find(f"osm_streets_{cc}.tsv")
        if streets is not None:
            with streets.open(encoding="utf-8") as fh:
                next(fh, None)
                for line in fh:
                    f = line.rstrip("\n").split("\t")
                    if len(f) < 4:
                        continue
                    try:
                        idx.add_street(f[0], f[1], float(f[2]), float(f[3]))
                    except ValueError:
                        continue
    _INDEX = idx
    return idx


def resolve(address: str | None, city: str | None = None,
            countries: tuple[str, ...] = DEFAULT_COUNTRIES) -> dict | None:
    """Адрес как есть → точка + точность. Всё офлайн."""
    return load(countries).resolve(addr_cis.parse(address, city))
