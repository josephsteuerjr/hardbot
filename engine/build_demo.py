"""
Сборка демо аналитического движка на СИНТЕТИКЕ (без ПД): генерирует людей →
геокодит (офлайн) → скорит (логика scoring.py) → строит оптимальный объезд →
пишет web/data.json и self-contained web/dashboard.html.

Запуск:  python engine/build_demo.py
Реальные ПД здесь НЕ участвуют — боевой прогон делается на машине/сервере владельца.
"""
from __future__ import annotations
import json, math, random, sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "server"))   # переиспользуем модули бота
import geo, routing                                  # noqa: E402
import scoring                                       # ЕДИНЫЙ скоринг (был дубль build_demo.score)

random.seed(42)

FAM_RU = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Соколов", "Морозов", "Волков"]
NAM_RU = ["Пётр С.", "Иван О.", "Алексей П.", "Дмитрий И.", "Сергей А."]
FAM_UZ = ["Абдуллаев", "Каримов", "Рахимов", "Юлдашев", "Турсунов", "Назаров"]
NAM_UZ = ["Абдулла", "Бахтиёр", "Фарход", "Дилшод", "Жасур", "Шерзод"]
FAM_TJ = ["Рахмонов", "Назаров", "Сафаров", "Шарипов", "Холов", "Бободжонов", "Хисомов"]
NAM_TJ = ["Фирдавс", "Сухроб", "Далер", "Бахром", "Нодир", "Хуршед", "Наим"]

EMP = ["Хайдаров С. Дж.", "Смирнов А. А.", "Каримов Б. Х."]
# Пул городов: кластер в Конибодоме/Согде (ТДж), плюс УЗ и РФ (аналитика «и в России»)
POOL = ([("Конибодом", "tj")] * 30 + [("Худжанд", "tj")] * 16 +
        [("Истаравшан", "tj")] * 10 + [("Исфара", "tj")] * 8 +
        [("Гулистон", "tj")] * 6 + [("Ташкент", "uz")] * 14 +
        [("Самарканд", "uz")] * 8 + [("Москва", "ru")] * 8 +
        [("Санкт-Петербург", "ru")] * 6)
ST_TJ = ["кучаи Совети", "кучаи Ленин", "кучаи Хусейнзода", "кучаи Т. Хомидов 40", "махаллаи Гулистон"]
ST_UZ = ["ул. Навои", "ул. Амира Темура", "ул. Бабура"]
ST_RU = ["Ленинский пр-т", "ул. Гагарина", "пр. Мира"]
PARTNERS = ["ЗК", "ЗК", "Делимобиль"]


def _name(region):
    if region == "uz":
        return f"{random.choice(FAM_UZ)} {random.choice(NAM_UZ)}"
    if region == "ru":
        return f"{random.choice(FAM_RU)} {random.choice(NAM_RU)}"
    return f"{random.choice(FAM_TJ)} {random.choice(NAM_TJ)}"


def gen(n=150):
    people = []
    for i in range(n):
        city, region = random.choice(POOL)
        st = {"tj": ST_TJ, "uz": ST_UZ, "ru": ST_RU}[region]
        addr = f"{city} {random.choice(st)} {random.randint(1, 90)}"
        debt = int(round(math.exp(random.uniform(8.6, 14.7))))          # ~5k..2.4M
        r = random.random()
        visits = 0 if r < 0.68 else (1 if r < 0.88 else random.randint(2, 3))
        result = who = location = None
        if visits:
            result = random.choice([None, "Обещает оплатить", "Нет контакта",
                                    "Передана инф 3 лицу", "Оплачен", None])
            who = random.choice(["проживает клиент", "родственники", "не знают", None])
        if region == "tj":   # для курьера важно местоположение клиента
            location = random.choice(["на родине", "на родине", "в РФ",
                                      "в другой стране", "не установлено"])
        people.append({
            "rep": str(100000000 + i), "client": _name(region),
            "city": city, "address": addr, "debt": debt, "visits": visits,
            "result": result, "who": who, "location": location,
            "employee": random.choice(EMP), "partner": random.choice(PARTNERS),
        })
    return people


def main():
    audience = "courier"
    weights = {"debt": 50, "cluster": 30, "fresh": 20}
    # Единый скоринг бота: синтетика уже с чистым city → ядро зовём напрямую.
    people = scoring.score_items(gen(150), weights, audience)

    # геокод (офлайн)
    geocoded = 0
    for p in people:
        g = geo.geocode_jittered(p["city"], p["address"], p["rep"])
        if g:
            p["lat"], p["lon"], p["geo_src"] = g[0], g[1], "city"
            geocoded += 1
        else:
            p["lat"] = p["lon"] = None

    pts = [p for p in people if p.get("lat") is not None]

    # маршрут: топ-точки крупнейшего сотрудника в крупнейшем кластере (не dropped)
    counts = Counter(p["city"] for p in pts)
    top_city = counts.most_common(1)[0][0]
    emp = Counter(p["employee"] for p in pts if p["city"] == top_city).most_common(1)[0][0]
    cluster = [p for p in pts if p["city"] == top_city and p["employee"] == emp and not p["dropped"]]
    cluster.sort(key=lambda x: x["score"], reverse=True)
    day = cluster[:10]
    start = geo.GAZ.get(geo._norm(top_city))
    route = routing.optimize(day, start=start)
    route_reps = [day[i]["rep"] for i in route["order"]]

    # аналитика
    active = [p for p in pts if not p["dropped"]]
    analytics = {
        "people": len(people), "geocoded": geocoded, "dropped": sum(1 for p in pts if p["dropped"]),
        "debt_total": sum(p["debt"] for p in people),
        "debt_active": sum(p["debt"] for p in active),
        "mode_split": dict(Counter(p["mode"] for p in active)),
        "by_city": [{"name": c, "count": n, "lat": geo.GAZ.get(geo._norm(c), (None, None))[0],
                     "lon": geo.GAZ.get(geo._norm(c), (None, None))[1],
                     "debt": sum(p["debt"] for p in pts if p["city"] == c)}
                    for c, n in counts.most_common()],
        "route_city": top_city, "route_emp": emp,
    }

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "audience": audience, "weights": weights,
        "points": [{k: p.get(k) for k in ("rep", "client", "city", "partner", "debt",
                    "visits", "lat", "lon", "score", "mode", "reason", "dropped",
                    "debt_s", "cluster_s", "fresh_s", "adj")} for p in pts],
        "route": {**route, "reps": route_reps},
        "analytics": analytics,
    }

    web = HERE / "web"
    web.mkdir(parents=True, exist_ok=True)
    (web / "data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tpl = (web / "dashboard_template.html").read_text(encoding="utf-8")
    html = tpl.replace("/*__DATA__*/", json.dumps(payload, ensure_ascii=False))
    banner = ("<!-- АВТОСГЕНЕРИРОВАНО build_demo.py из dashboard_template.html. "
              "НЕ РЕДАКТИРОВАТЬ руками — правки затрутся. Источник истины: dashboard_template.html. -->\n")
    (web / "dashboard.html").write_text(banner + html, encoding="utf-8")

    # отчёт + cross-check боевого scoring.py
    print(f"people={len(people)} geocoded={geocoded} dropped={analytics['dropped']} "
          f"cluster={top_city}/{emp} day_stops={len(day)} "
          f"route_km={route['km']} route_min={route['minutes']} "
          f"profit/min={route['profit_per_min']:,}₽".replace(",", " "))
    print("top-5 по скору:")
    for p in sorted(active, key=lambda x: x["score"], reverse=True)[:5]:
        print(f"  {p['score']:>5}  {p['mode']:<8} {p['debt']:>10,} ₽  {p['city']:<14} {p['client']}".replace(",", " "))
    print("[единый скоринг] points через scoring.score_items — дубля больше нет ✓")


if __name__ == "__main__":
    main()
