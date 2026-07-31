"""Сервер-витрина не ходит в публичные геокодеры.

Найдено адверсарным разбором 31.07.2026 и подтверждено чтением кода: ручка
`/admin/geocode` вызывала `geocode.geocode_all()` БЕЗ аргументов, а её значение
по умолчанию — `use_precise=True`. В `geocode_address` точные геокодеры стоят
ПЕРВЫМИ, до офлайн-справочника, а `NOMINATIM_URL`/`PHOTON_URL` по умолчанию
публичные и в `.env.example` не переопределены. То есть одно нажатие кнопки
«Запустить геокодинг» в пульте отправляло бы адрес каждого должника без координат
в nominatim.openstreetmap.org и photon.komoot.io — трансграничная передача ПД
третьим лицам, прямо запрещённая SECURITY.md и докстрингом самого geocode.py.

Офлайн-справочник на сервере есть (geoindex + дампы GeoNames/OSM), точный
геокодинг живёт в пульте на машине владельца — там интернет допустим.
"""
import asyncio
import json

from aiohttp.test_utils import make_mocked_request

import geocode
import web


def test_admin_geocode_does_not_use_public_services(monkeypatch):
    monkeypatch.setattr(web, "_admin", lambda _r: None)
    monkeypatch.setattr(web, "GEOCODER_ALLOW_PUBLIC", False)
    seen = {}

    def fake(*args, **kwargs):
        seen.update(kwargs)
        return {"total": 0}
    monkeypatch.setattr(geocode, "geocode_all", fake)

    async def run():
        resp = await web._admin_geocode(make_mocked_request("POST", "/admin/geocode"))
        await asyncio.sleep(0)          # даём executor'у выполниться
        return resp
    resp = asyncio.run(run())
    assert json.loads(resp.body.decode())["precise"] is False
    assert seen.get("use_precise") is False, "сервер запустил ТОЧНЫЙ геокодинг"


def test_opt_in_flag_exists_and_is_off_by_default():
    """Включить можно только явным env-флагом — как у geoindex с докачкой дампов."""
    assert web.GEOCODER_ALLOW_PUBLIC is False


def test_public_urls_are_still_the_defaults():
    """Фиксируем причину запрета: дефолты именно публичные, а не свой инстанс.
    Если однажды в проекте поднимут свой Nominatim и поменяют дефолт — этот тест
    упадёт и заставит пересмотреть запрет осознанно."""
    assert "openstreetmap.org" in geocode.NOMINATIM_URL
    assert "komoot" in geocode.PHOTON_URL


def test_offline_path_needs_no_network(monkeypatch):
    """Офлайн-ветка geocode_address не должна трогать сеть вообще."""
    def boom(*_a, **_k):
        raise AssertionError("выход в сеть из офлайн-режима")
    monkeypatch.setattr(geocode, "nominatim_lookup", boom)
    monkeypatch.setattr(geocode, "photon_lookup", boom)
    monkeypatch.setattr(geocode, "yandex_lookup", boom)
    geocode.geocode_address("ЎЗБЕКИСТОН, АНДИЖОН ВИЛОЯТИ, ШАХРИХОН ТУМАНИ",
                            use_precise=False)
