"""
Нормализация адреса перед геокодингом — слой 1 из роадмапа:
**libpostal → Nominatim/Photon → GeoNames(аул) → ручная метка**.

Зачем: реестр СНГ — это кривой кириллический адрес («кӯчаи Т. Хомидов 40»,
«маҳаллаи Гулистон», смесь таджикской/русской форм, сокращения, номер дома в хвосте).
Чистый локалити-токен поднимает попадание и точного геокодера, и центроида аула.

Три уровня (graceful — как с VROOM: боевой сервис если есть, иначе офлайн-фолбэк):
  1) `LIBPOSTAL_URL` — REST-обёртка libpostal в Docker (`/expand` + `/parser`);
  2) пакет `postal` (если libpostal собран локально);
  3) встроенный нормализатор — офлайн, без зависимостей: таджикская кириллица → русская,
     разворот «кӯча/маҳалла/деҳа/ноҳия…», срез номера дома.

По умолчанию работает офлайн и НИЧЕГО не шлёт в сеть. libpostal — апгрейд по env.
Поднять REST на ПК:  docker run -d -p 8088:8080 johnlonganecker/libpostal-rest
                     → set LIBPOSTAL_URL=http://127.0.0.1:8088
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

LIBPOSTAL_URL = os.getenv("LIBPOSTAL_URL", "").strip().rstrip("/")

# Таджикская кириллица → обычная (GeoNames/OSM чаще знают русские формы).
_TJ = str.maketrans({
    "ӣ": "и", "ӯ": "у", "ҳ": "х", "ҷ": "дж", "қ": "к", "ғ": "г", "ё": "е",
    "Ӣ": "и", "Ӯ": "у", "Ҳ": "х", "Ҷ": "дж", "Қ": "к", "Ғ": "г", "Ё": "е",
})

# Разворот «адресных слов» (после транслита, в нижнем регистре). Группы — таджикские
# и русские формы. Цель — не идеальный перевод, а отделить тип объекта от названия.
_EXPAND = [
    (re.compile(r"\bкуч(?:а|аи|и|аҳо)?\.?\b|\bкӯч\w*"), "улица"),
    (re.compile(r"\bхиебон(?:и)?\b"),                    "проспект"),
    (re.compile(r"\bма?халл(?:а|аи|и|яи?)?\b"),          "махалля"),
    (re.compile(r"\b(?:деха|кишлак|кишлок|кишлоки|каъа)(?:и)?\b"), "село"),
    (re.compile(r"\bнохия(?:и)?\b|\bр-?н\b"),            "район"),
    (re.compile(r"\bша[хh]р(?:и)?\b|\bг\.\b"),           "город"),
    (re.compile(r"\b(?:хона|хонаи|д\.|дом)\b"),          "дом"),
    (re.compile(r"\bҷамоат\w*|\bджамоат\w*"),            "джамоат"),
]
_PLACE_WORDS = {"улица", "проспект", "махалля", "село", "район", "город", "дом", "джамоат",
                "ул", "пр", "пер", "просп", "мкр", "кв", "корпус", "стр", "влд"}
_HOUSE = re.compile(r"\b(?:дом|д|№|#|кв|квартира)\s*\.?\s*\d+[а-яa-z/]*\b|\b\d+[а-яa-z/]*\b")
_PUNCT = re.compile(r"[^\wёЁ\s-]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


def clean(text: str | None) -> str:
    """Лёгкая офлайн-нормализация: транслит TJ→RU, разворот адресных слов, чистка."""
    s = (text or "").lower().translate(_TJ)
    s = _PUNCT.sub(" ", s)
    for rx, repl in _EXPAND:
        s = rx.sub(repl, s)
    return _SPACE.sub(" ", s).strip()


# --- libpostal (REST или пакет postal) -------------------------------------
def _rest(path: str, text: str):
    if not LIBPOSTAL_URL:
        return None
    try:
        req = urllib.request.Request(
            f"{LIBPOSTAL_URL}/{path}", data=json.dumps({"query": text}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _postal_expand(text: str):
    try:
        from postal.expand import expand_address           # type: ignore
        return list(expand_address(text)) or None
    except Exception:
        return None


def _postal_parse(text: str):
    try:
        from postal.parser import parse_address             # type: ignore
        return [{"value": v, "label": l} for v, l in parse_address(text)] or None
    except Exception:
        return None


# --- Публичный API ---------------------------------------------------------
def expand(text: str | None) -> list[str]:
    """Нормализованные формы адреса (для строкового запроса в Nominatim/Photon).
    libpostal даёт несколько вариантов; фолбэк — один очищенный вариант."""
    if not text:
        return []
    forms = _rest("expand", text) or _postal_expand(text)
    if forms:
        return [f for f in forms if f][:8]
    return [clean(text)]


def components(text: str | None) -> dict:
    """Разбор адреса на части: city/suburb/road/house_number/state/… (что нашлось)."""
    if not text:
        return {}
    parsed = _rest("parser", text) or _postal_parse(text)
    if parsed:
        out: dict[str, str] = {}
        for it in parsed:
            lbl, val = it.get("label"), it.get("value")
            if lbl and val and lbl not in out:
                out[lbl] = val
        return out
    return {}


def locality(text: str | None) -> str:
    """Лучшая строка локалити для центроида аула (GeoNames): из libpostal — city/suburb/
    state/island; офлайн — очищенный адрес без номера дома и без адресных слов-типов."""
    if not text:
        return ""
    comp = components(text)
    for key in ("city", "city_district", "suburb", "island", "state_district", "state"):
        if comp.get(key):
            return comp[key]
    s = _HOUSE.sub(" ", clean(text))
    toks = [w for w in s.split() if w not in _PLACE_WORDS and len(w) >= 3]
    return " ".join(toks).strip()


def query(text: str | None, city: str | None = None) -> str:
    """Готовая строка для точного геокодера: лучший expand + город (если задан отдельно)."""
    base = (expand(text) or [""])[0]
    if city:
        c = clean(city)
        # добавляем город, если его слов ещё нет ЦЕЛИКОМ (substring «худжанд» в «худжанди» не считается)
        if c and not set(c.split()) <= set(base.split()):
            base = f"{base}, {c}".strip(", ")
    return base or clean(text)
