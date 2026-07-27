"""
Движок приоритета выезда — «куда выгоднее ехать». ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ скоринга
(импортируется и ботом, и движком engine/ — раньше логика дублировалась в
engine/build_demo.score, что давало «две правды о том, кого везти»).

Реальность портфеля СНГ: он ХОЛОДНЫЙ. По большинству должников известны только
долг и адрес (см. SPEC + анализ реального реестра). Поэтому скоринг в два режима:

  A. РАЗВЕДКА (умолчание, нет исходов): лог(долг) + кластер по городу + свежесть
     (штраф за число прошлых выездов). Цель визита — нагенерить данные.
  B. ДОЖИМ (есть исход выезда): к базе добавляются сигналы — «Обещает оплатить»,
     «проживает клиент» (плюс); «Нет контакта», «Передана инф 3 лицу» (минус).

Killer-фильтры (зеркало выездник/курьер): для аудитории courier (Таджикистан)
«Клиент в РФ / в другой стране» — недосягаем → вниз; «на родине» → плюс. «Адрес не
существует», «Оплачен/Смерть/Тюрьма/Кредит не брал» — снимаем из очереди.

Веса (долг / кластер / свежесть) задаются снаружи и крутятся в пульте. Числовые
«магические» коэффициенты сигналов/killer-фильтров вынесены в scoring_config.py.
Города-центроиды — в едином gazetteer.py. Разбор чисел — в едином util.py.

Архитектура: score_items() — ЧИСТОЕ ядро (город уже определён вызывающим, вход не
мутирует). compute_priority() — обёртка для бота: разрешает город через parse_city,
зовёт ядро, формирует выход со списком кластеров. Движок зовёт score_items() напрямую.
"""

from __future__ import annotations
import math
from collections import Counter
from datetime import date, datetime

from debt import contract_debt                     # «долг на сегодня», иначе «долг всего»
from gazetteer import parse_city, sughd_centroid   # единый газеттир (был _CITIES)
from util import num as _num                       # единый разбор чисел (был локальный _num)
from scoring_config import (
    SIGNALS, KILLER_RESULTS, CLAMP_MAX, COURIER_UNREACHABLE,
    RESPECT_PROMISE_DATE, MIN_DAYS_BETWEEN_VISITS,
)

_int = _num
_money = _num


def _fmt_debt(d: int) -> str:
    return f"{d:,} ₽".replace(",", " ")


def _norm_weights(w: dict) -> dict:
    d = max(float(w.get("debt", 0)), 0)
    c = max(float(w.get("cluster", 0)), 0)
    f = max(float(w.get("fresh", 0)), 0)
    s = d + c + f
    if s <= 0:
        return {"debt": 0.5, "cluster": 0.3, "fresh": 0.2}
    return {"debt": d / s, "cluster": c / s, "fresh": f / s}


# --- Сигналы и killer-фильтры (числа — в scoring_config.py, прозрачно для ИБ/директора) ---
def _drop_reason(res: str, loc: str, audience: str) -> str | None:
    """Killer-фильтр: причина снятия из очереди (или None). res/loc — уже lower()."""
    if "не существ" in res:
        return "адрес не существует"
    if res in KILLER_RESULTS:
        return res
    if audience == "courier" and any(k in loc for k in COURIER_UNREACHABLE):
        return "клиент не в Таджикистане"
    return None


def _parse_date(v) -> date | None:
    if not v:
        return None
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _regulatory_drop(it: dict, today: date) -> str | None:
    """ЖЁСТКИЕ лимиты 230-ФЗ как killer-фильтры (см. scoring_config):
    обещанная дата в будущем → отложить; ≤1 визита в неделю на должника."""
    if RESPECT_PROMISE_DATE:
        pd = _parse_date(it.get("promise_date"))
        if pd and pd > today:
            return f"обещание {pd.strftime('%d.%m')}"
    if MIN_DAYS_BETWEEN_VISITS > 0:
        vd = _parse_date(it.get("visit_date"))
        if vd and 0 <= (today - vd).days < MIN_DAYS_BETWEEN_VISITS:
            return "недавно выезжали (≤1/нед)"
    return None


def _signal_adjust(res: str, loc: str, who: str, audience: str) -> tuple[int, list[str]]:
    """Сумма поправок сигналов дожима + теги-причины. res/loc/who — уже lower()."""
    adj, sig = 0, []
    for s in SIGNALS:
        if s["audience"] and audience != s["audience"]:
            continue
        haystack = {"result": res, "location": loc, "who": who}[s["field"]]
        if s["needle"] in haystack:
            adj += s["points"]
            sig.append(s["tag"])
    return adj, sig


def _reason(drop: str | None, debt: int, city: str | None, cn: int,
            visits: int, sig: list[str]) -> str:
    if drop:
        return f"⛔ {drop}"
    tags = [_fmt_debt(debt)]
    if cn >= 4 and city and city != "—":
        tags.append(f"кластер {city} ({cn})")
    if visits == 0:
        tags.append("не выезжали")
    elif visits >= 2:
        tags.append(f"{visits} выезда")
    tags += sig
    return " · ".join(tags[:4])


def score_items(items: list[dict], weights: dict, audience: str = "courier",
                today: date | None = None) -> list[dict]:
    """ЧИСТОЕ ядро скоринга. items: [{city, debt, visits, result, location, who, ...}],
    где city уже определён вызывающим (бот — parse_city; движок — из синтетики/БД).
    Возвращает НОВЫЙ список: исходные поля + компоненты (debt_s/cluster_s/fresh_s/adj)
    + score/mode/dropped/reason. Вход НЕ мутирует, НЕ сортирует (порядок — забота caller).
    today — для регуляторных killer-фильтров (даты), по умолчанию date.today()."""
    w = _norm_weights(weights)
    today = today or date.today()
    city_counts = Counter(c for c in (it.get("city") for it in items) if c and c != "—")
    max_cluster = max(city_counts.values()) if city_counts else 1
    max_debt = max((_num(it.get("debt")) for it in items), default=0) or 1
    log_max = math.log10(1 + max_debt)

    out = []
    for it in items:
        debt = _num(it.get("debt"))
        debt_s = (math.log10(1 + debt) / log_max) if log_max else 0.0
        city = it.get("city")
        cn = city_counts.get(city, 0)
        cluster_s = (cn / max_cluster) if max_cluster else 0.0
        visits = _num(it.get("visits"))
        fresh_s = 1.0 / (1.0 + visits)

        base = w["debt"] * debt_s + w["cluster"] * cluster_s + w["fresh"] * fresh_s
        score = base * 100.0

        res = (it.get("result") or "").strip().lower()
        loc = (it.get("location") or "").strip().lower()
        who = (it.get("who") or "").strip().lower()
        drop = _drop_reason(res, loc, audience) or _regulatory_drop(it, today)
        adj, sig = _signal_adjust(res, loc, who, audience)
        final = 0.0 if drop else max(0.0, min(CLAMP_MAX, score + adj))
        scouted = bool(res and res != "не обработан") or ("не установ" not in loc and loc)

        out.append({
            **it,
            "debt": debt, "visits": visits, "city": city,
            "debt_s": round(debt_s, 4), "cluster_s": round(cluster_s, 4),
            "fresh_s": round(fresh_s, 4), "adj": adj,
            "score": round(final, 1), "mode": "дожим" if scouted else "разведка",
            "dropped": bool(drop), "drop_reason": drop,
            "reason": _reason(drop, debt, city, cn, visits, sig),
        })
    return out


def compute_priority(items: list[dict], weights: dict, audience: str = "courier") -> dict:
    """Бот: разрешает город (parse_city), зовёт чистое ядро, формирует выход со списком
    кластеров. Вход НЕ мутирует. items: [{client, debt, address, city, visits, result,
    location, who, partners, rep}]. → {weights, audience, total, items, cities}."""
    resolved = []
    for it in items:
        disp, lat, lon = parse_city(it.get("address"), it.get("city"))
        resolved.append({**it, "city": disp or "—", "lat": lat, "lon": lon})

    scored = score_items(resolved, weights, audience)
    scored.sort(key=lambda x: x["score"], reverse=True)

    out = [{
        "rep": it.get("rep"), "client": it.get("client") or "—",
        "birth": it.get("birth"),
        "city": it["city"], "lat": it.get("lat"), "lon": it.get("lon"),
        "debt": it["debt"], "visits": it["visits"], "n": it.get("n", 1),
        "score": it["score"], "mode": it["mode"], "reason": it["reason"],
        "dropped": it["dropped"],
    } for it in scored]

    city_counts = Counter(it["city"] for it in resolved if it["city"] != "—")
    cities = []
    for name, count in city_counts.most_common():
        c = sughd_centroid(name)
        cities.append({"name": name, "count": count,
                       "lat": c[0] if c else None, "lon": c[1] if c else None})
    return {"weights": _norm_weights(weights), "audience": audience, "total": len(out),
            "items": out, "cities": cities}


def _items_from_db() -> list[dict]:
    import db
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM clients").fetchall()
    persons = db._group_persons(rows)
    items = []
    for p in persons:
        cs = p["contracts"]

        def first(key):
            for r in cs:
                if key in r.keys():
                    v = r[key]
                    if v not in (None, ""):
                        return v
            return None

        visits = 0
        for r in cs:
            if "visits_count" in r.keys():
                visits = max(visits, _int(r["visits_count"]))
        items.append({
            "rep": p["rep"], "client": p["client"], "birth": p["birth_date"],
            "debt": p["total"],
            "address": first("address"), "city": first("city"),
            "visits": visits, "result": first("overall_result"),
            "location": first("client_location"),
            "who": first("who_at_address") or "",
            "promise_date": first("promise_date"),
            # Ограничение «не чаще раза в неделю» относится только к реально
            # завершённым визитам, а не к импортному HARD visit_date.
            "visit_date": first("bot_visit_at"),
            "partners": p["partners"], "n": p["n"],
        })
    return items


def priority_from_db(weights: dict, audience: str = "courier", limit: int | None = None) -> dict:
    res = compute_priority(_items_from_db(), weights, audience)
    if limit:
        res = {**res, "items": res["items"][:limit]}
    return res


_PRIORITY_CACHE: dict = {}   # (data_version, audience, weights) -> индекс приоритета


def priority_index(weights: dict | None = None, audience: str = "courier") -> dict:
    """{client|birth -> {score, reason, city, mode}} для подсветки списка/карты в миниаппе.
    Кэшируется по версии данных БД — миниапп дёргает bootstrap часто, а реестр меняется
    редко, поэтому пересчёт только при реальном изменении (инвалидация — db._bump_version)."""
    import db
    w = weights or {"debt": 50, "cluster": 30, "fresh": 20}
    key = (db.data_version(), audience, tuple(sorted(w.items())))
    cached = _PRIORITY_CACHE.get(key)
    if cached is not None:
        return cached
    res = compute_priority(_items_from_db(), w, audience)
    idx = {f"{it.get('client') or ''}|{it.get('birth') or ''}":
           {"score": it["score"], "reason": it["reason"],
            "city": it["city"], "mode": it["mode"]} for it in res["items"]}
    _PRIORITY_CACHE.clear()         # держим только актуальную версию (ограничиваем память)
    _PRIORITY_CACHE[key] = idx
    return idx


def geocode_city_centroids(overwrite: bool = False) -> int:
    """Быстрый офлайн-геокодер: адрес → центроид города/махалли (+детерминированный
    джиттер ~±2 км, чтобы точки одного города не слипались). Дом в Таджикистане не
    геокодим. Метку выездника (geo_src='manual') не трогаем. Возвращает число точек."""
    import hashlib
    import db
    n = 0
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT contract_id, address, city, geo_lat, geo_src FROM clients").fetchall()
        for r in rows:
            if r["geo_src"] == "manual":
                continue
            if r["geo_lat"] is not None and not overwrite:
                continue
            _disp, lat, lon = parse_city(r["address"], r["city"])
            if lat is None:
                continue
            h = int(hashlib.md5((r["contract_id"] or "").encode()).hexdigest(), 16)
            jlat = lat + ((h % 1000) / 1000 - 0.5) * 0.04
            jlon = lon + ((h // 1000 % 1000) / 1000 - 0.5) * 0.04
            conn.execute(
                "UPDATE clients SET geo_lat=?, geo_lon=?, geo_src='city' WHERE contract_id=?",
                (jlat, jlon, r["contract_id"]))
            n += 1
        conn.commit()
    return n


def _items_from_xlsx(path: str) -> list[dict]:
    """Автономно для __main__: прочитать сырой xlsx без БД."""
    from openpyxl import load_workbook
    from fields import FIELDS
    from db import _norm_title

    title_to_key = {_norm_title(f.title): f.key for f in FIELDS}
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    best_h, col_keys, best_n = 0, [], -1
    for i, row in enumerate(rows[:10]):
        keys = [title_to_key.get(_norm_title(c)) for c in row]
        n = sum(1 for k in keys if k)
        if n > best_n:
            best_n, best_h, col_keys = n, i, keys

    groups: dict[tuple, list] = {}
    for raw in rows[best_h + 1:]:
        if raw is None or all(v is None for v in raw):
            continue
        d = {k: (str(v).strip() if v is not None else None)
             for k, v in zip(col_keys, raw) if k}
        if not d.get("contract_id"):
            continue
        groups.setdefault((d.get("client") or "", d.get("birth_date") or ""), []).append(d)

    items = []
    for (name, _dob), ds in groups.items():
        debt = sum(contract_debt(x) for x in ds)
        visits = max((_int(x.get("visits_count")) for x in ds), default=0)

        def first(key):
            for x in ds:
                if x.get(key):
                    return x[key]
            return None

        items.append({
            "rep": ds[0].get("contract_id"), "client": name, "birth": _dob, "debt": debt,
            "address": first("address"), "city": first("city"), "visits": visits,
            "result": first("overall_result"), "location": first("client_location"),
            "who": first("who_at_address") or "",
            "partners": sorted({x.get("partner") for x in ds if x.get("partner")}),
            "n": len(ds),
        })
    return items


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else \
        r"C:\Users\yegor\Downloads\portfolio_tool\Худжанд\Кейсы на обогащение из Общего.xlsx"
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute_priority(_items_from_xlsx(path),
                           {"debt": 50, "cluster": 30, "fresh": 20}, "courier")
    print(f"людей: {res['total']}   веса(норм): {res['weights']}")
    print("кластеры:", " · ".join(f"{c['name']}({c['count']})" for c in res["cities"]))
    print("\n  #  score  реж.      долг        город        выезд  клиент / почему")
    print("  " + "-" * 96)
    for i, it in enumerate(res["items"][:20], 1):
        print(f"{i:>3} {it['score']:>5}  {it['mode']:<8} {it['debt']:>10,}  "
              f"{it['city']:<12} {it['visits']:>3}    {(it['client'] or '')[:22]:<22} {it['reason']}"
              .replace(",", " "))
