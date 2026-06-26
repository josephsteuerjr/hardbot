"""Тесты единого газеттира (был тройной дубль _CITIES / GAZ / центр города)."""
import gazetteer


def test_parse_city_sughd_aliases():
    for alias in ("худжанд", "хучанд", "г. Худжанд ул. Ленина"):
        disp, lat, lon = gazetteer.parse_city(alias, "")
        assert disp == "Худжанд" and round(lat, 3) == 40.283


def test_parse_city_uratyube_alias():
    assert gazetteer.parse_city("уротеппа", "")[0] == "Истаравшан"


def test_parse_city_none_for_non_sughd():
    assert gazetteer.parse_city("Москва", "")[0] is None       # РФ — не anchor Согда


def test_lookup_broad_cities():
    assert gazetteer.lookup("Москва", "")[2] == "москва"
    assert gazetteer.lookup("", "г. Ташкент")[2] == "ташкент"


def test_lookup_longest_key_first():
    # «санкт-петербург» не должен матчиться коротким «петербург» раньше длинного ключа
    lat, lon, key = gazetteer.lookup("санкт-петербург", "")
    assert key == "санкт-петербург"


def test_jitter_deterministic():
    a = gazetteer.jitter(40.0, 69.0, "seedX")
    b = gazetteer.jitter(40.0, 69.0, "seedX")
    c = gazetteer.jitter(40.0, 69.0, "seedY")
    assert a == b and a != c


def test_sughd_centroid_roundtrip():
    assert gazetteer.sughd_centroid("Худжанд") == (40.2833, 69.6228)
    assert gazetteer.sughd_centroid("Нет такого") is None
