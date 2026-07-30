"""Иерархический справочник координат: точность и защита от тёзок.

Главный дефект прежнего поиска: из всех совпавших токенов брался населённый пункт
с НАИБОЛЬШИМ населением, то есть заведомо самый грубый. На реальном реестре это
давало 95.8% «попаданий», из которых 362 из 597 — одна точка «Самарканд», хотя
адрес называл конкретный кишлак. Здесь закрепляем обратное поведение.
"""
import addr_cis as A
import geoindex


def _gn(gid, name, alt, lat, lon, fclass, fcode, cc, adm1, adm2="", pop=0):
    """Строка дампа GeoNames (19 колонок)."""
    row = [""] * 19
    row[0], row[1], row[2], row[3] = gid, name, name, alt
    row[4], row[5] = str(lat), str(lon)
    row[6], row[7], row[8] = fclass, fcode, cc
    row[10], row[11] = adm1, adm2
    row[14] = str(pop)
    return row


def _index():
    idx = geoindex.Index()
    # Согдийская область (ТДж) и её район
    idx.add(_gn("1", "Viloyati Sughd", "Согдийская область,Суғд", 40.2, 69.6, "A", "ADM1", "TJ", "03", "", 2600000))
    idx.add(_gn("2", "Nohiyai Spitamen", "Спитамен", 40.18, 69.05, "A", "ADM2", "TJ", "03", "0305", 140000))
    # Хатлонская область — там тоже есть «Зарҳалол», и раньше точка уезжала туда
    idx.add(_gn("3", "Viloyati Khatlon", "Хатлонская область,Хатлон", 37.9, 69.1, "A", "ADM1", "TJ", "02", "", 3300000))
    idx.add(_gn("4", "Zarhalol", "Зарҳалол", 37.80, 69.20, "P", "PPL", "TJ", "02", "", 5000))
    # Самаркандская область (УЗ) и крупный город — «магнит» старого алгоритма
    idx.add(_gn("5", "Samarqand Region", "Самаркандская область,Самарқанд вилояти", 39.65, 66.96, "A", "ADM1", "UZ", "10", "", 3900000))
    idx.add(_gn("6", "Samarkand", "Самарканд,Самарқанд", 39.6542, 66.9597, "P", "PPLA", "UZ", "10", "", 513572))
    idx.add(_gn("7", "Pastdarg‘om", "Пастдарғом", 39.55, 66.75, "A", "ADM2", "UZ", "10", "1005", 360000))
    return idx


# --- точность: самый мелкий уровень, а не самый населённый ----------------------
def test_village_wins_over_big_city():
    idx = _index()
    idx.add_osm("UZ", "village", "", "Zangiboy", "Зангибой", 39.52, 66.70)
    p = A.parse("ЎЗБЕКИСТОН, САМАРҚАНД ВИЛОЯТИ, ПАСТДАРҒОМ ТУМАНИ, "
                "ЗАНГИБОЙИ МФЙ, ул. ЗАНГИБОЙИ ҚИШЛОҒИ")
    hit = idx.resolve(p)
    assert hit["precision"] in ("village", "mahalla")
    assert hit["name"] == "Zangiboy"          # не «Samarkand», как было раньше


def test_falls_back_to_district_then_region():
    idx = _index()
    p = A.parse("САМАРҚАНД ВИЛОЯТИ, ПАСТДАРҒОМ ТУМАНИ, НЕИЗВЕСТНОЕ МФЙ")
    assert idx.resolve(p)["precision"] == "district"
    p2 = A.parse("САМАРҚАНД ВИЛОЯТИ")
    assert idx.resolve(p2)["precision"] == "region"


# --- защита от тёзок -----------------------------------------------------------
def test_namesake_in_other_region_is_rejected():
    """«Ҷамоати Зарҳалол» под Истаравшаном (Согд) не должен уезжать в Хатлон
    только потому, что там есть посёлок с тем же именем — это было 281 км мимо."""
    idx = _index()
    p = A.parse("вилояти Суғд, ноҳияи Спитамен, Ҷамоати Зарҳалол")
    hit = idx.resolve(p)
    assert hit is not None
    assert hit["name"] != "Zarhalol"
    assert hit["precision"] in ("district", "region")


def test_osm_place_far_from_anchor_is_rejected():
    """Объект OSM принимается только рядом с найденным центром района/области:
    у OSM нет кодов adm, и геометрия — единственная защита."""
    idx = _index()
    idx.add_osm("UZ", "village", "", "Dalyon", "Далён", 55.75, 37.61)   # это Москва
    p = A.parse("САМАРҚАНД ВИЛОЯТИ, ПАСТДАРҒОМ ТУМАНИ, ДАЛЁН ҚИШЛОҒИ")
    hit = idx.resolve(p)
    assert hit["name"] != "Dalyon"
    assert hit["precision"] in ("district", "region")


def test_osm_ambiguous_name_without_anchor_is_refused():
    """Без опоры на область выбирать из нескольких тёзок наугад нельзя."""
    idx = geoindex.Index()
    idx.add_osm("TJ", "village", "", "Navobod", "Навобод", 40.1, 69.5)
    idx.add_osm("TJ", "village", "", "Navobod", "Навобод", 37.9, 69.1)
    assert idx.resolve({"village": "Навобод"}) is None


def test_osm_single_candidate_without_anchor_is_accepted():
    idx = geoindex.Index()
    idx.add_osm("TJ", "village", "", "Bahoriston", "Баҳористон", 38.6, 68.9)
    hit = idx.resolve({"village": "Баҳористон"})
    assert hit and hit["name"] == "Bahoriston" and hit["precision"] == "village"


# --- псевдонимы ----------------------------------------------------------------
def test_ntm_is_a_region_not_a_town():
    """«НТМ» — Ноҳияҳои тобеи марказ, республиканское подчинение. Пока оно
    читалось как город, точка вставала в случайный населённый пункт."""
    idx = geoindex.Index()
    idx.add(_gn("9", "Districts of Republican Subordination",
                "Районы республиканского подчинения", 38.9, 68.9, "A", "ADM1", "TJ", "RR", "", 2100000))
    hit = idx.resolve(A.parse("НТМ шахри Вахдат кучаи Фирдавси"))
    assert hit and hit["precision"] == "region"


# --- читаемое имя области ------------------------------------------------------
def test_region_display_name_is_russian():
    """Среди альтернативных имён GeoNames кириллицы много и на других языках
    («Самаркандан область» — чеченский). Нужна именно русская форма."""
    idx = _index()
    names = idx.display_names(A.parse("САМАРҚАНД ВИЛОЯТИ, ПАСТДАРҒОМ ТУМАНИ"))
    assert names.get("region") == "Самаркандская"


def test_empty_address_resolves_to_nothing():
    assert _index().resolve(A.parse("")) is None


# --- где лежит справочник ------------------------------------------------------
def test_gazetteer_is_found_next_to_code_when_data_dir_is_empty(monkeypatch, tmp_path):
    """На проде HARDBOT_DATA=/data, а справочник приезжает в /app/geonames вместе
    с релизом. Пока модуль смотрел только в /data, он молча грузил ПОЛОВИНУ
    справочника — 20 016 ключей вместо 40 118 и ноль улиц, без единой ошибки
    в логах. Файл рядом с кодом обязан находиться."""
    monkeypatch.setattr(geoindex, "GEO_DIRS",
                        (tmp_path / "geonames", geoindex.Path(geoindex.__file__).parent / "geonames"))
    monkeypatch.setattr(geoindex, "_INDEX", None)
    idx = geoindex.load()
    assert len(idx.by_key) > 30_000, "справочник рядом с кодом не подхватился"
    assert len(idx.streets) > 10_000, "слой улиц не подхватился"
    monkeypatch.setattr(geoindex, "_INDEX", None)


def test_data_dir_wins_over_code_dir(tmp_path, monkeypatch):
    """Порядок важен: обновлённый дамп кладут в том с данными, и он должен
    перекрывать версию из релиза — иначе обновить справочник без передеплоя нельзя."""
    (tmp_path / "geonames").mkdir()
    (tmp_path / "geonames" / "TJ.txt").write_text("x" * 10, encoding="utf-8")
    monkeypatch.setattr(geoindex, "GEO_DIRS",
                        (tmp_path / "geonames", geoindex.Path(geoindex.__file__).parent / "geonames"))
    assert geoindex._find("TJ.txt") == tmp_path / "geonames" / "TJ.txt"
