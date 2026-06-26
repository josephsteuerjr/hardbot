"""
Офлайн-геокодер движка — ТОНКИЙ ШИМ на единый газеттир (hardbot/gazetteer.py).

Раньше здесь лежала своя копия таблицы центроидов (GAZ) — третья в проекте, она
расходилась с hardbot/scoring._CITIES. Теперь данные и логика — в одном месте,
а этот модуль лишь ре-экспортирует их под привычными движку именами
(GAZ / geocode / jitter / geocode_jittered / _norm), чтобы build_demo.py и
app/main.py не меняли вызовы.

Это НЕ замена боевому слоёному геокодеру (libpostal → Nominatim/Photon → GeoNames),
а его лёгкое офлайн-ядро для демо/фолбэка (1 vCPU / 3 ГБ). Боевой геокодинг — батч в пульте.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Газеттир живёт в hardbot/ — добавляем в путь независимо от порядка импортов.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hardbot"))

from gazetteer import (  # noqa: E402
    CENTROIDS as GAZ,
    _norm,
    geocode_jittered,
    jitter,
    lookup as geocode,   # широкий офлайн-матч: (lat, lon, key) | None
)

__all__ = ["GAZ", "geocode", "jitter", "geocode_jittered", "_norm"]
