"""
Генератор синтетического реестра (xlsx) — для прогона всего пайплайна без
реальных ПД. Заполняет сх-поля + «ответственный Сотрудник» (для самовыбора),
часть людей — с несколькими договорами (для проверки объединения).

Отдельно: python synth.py  ->  data/synthetic_registry.xlsx
"""

import random
from datetime import date, timedelta
from pathlib import Path

import db

DEV_EMPLOYEE = "Тестовый Выездник"
EMPLOYEES = ["Смирнов А. А.", "Каримов Б. Х.", DEV_EMPLOYEE]

_FAM_RU = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Соколов", "Попов",
           "Лебедев", "Новиков", "Морозов", "Волков"]
_NAME_RU = ["Пётр Сергеевич", "Иван Олегович", "Алексей Павлович",
            "Дмитрий Игоревич", "Сергей Андреевич", "Никита Романович"]
_FAM_UZ = ["Абдуллаев", "Каримов", "Рахимов", "Юлдашев", "Турсунов", "Назаров"]
_NAME_UZ = ["Абдулла", "Бахтиёр", "Фарход", "Дилшод", "Жасур", "Шерзод"]
_FAM_TJ = ["Рахмонов", "Назаров", "Сафаров", "Шарипов", "Холов", "Бободжонов"]
_NAME_TJ = ["Фирдавс", "Сухроб", "Далер", "Бахром", "Нодир", "Хуршед"]
_CITY_RU = ["Москва", "Санкт-Петербург", "Казань", "Самара"]
_ST_RU = ["Ленинский пр-т", "ул. Тверская", "ул. Гагарина", "пр. Мира"]
_CITY_UZ = ["Ташкент", "Самарканд", "Бухара", "Наманган"]
_ST_UZ = ["ул. Навои", "ул. Амира Темура", "ул. Бабура"]
# Таджикские населённые пункты (есть в GeoNames) — демонстрируют фолбэк на центроид аула
_PLACE_TJ = ["Истаравшан", "Куляб", "Худжанд", "Бохтар", "Турсунзаде", "Вахдат",
             "Пенджикент", "Канибадам", "Исфара", "Дангара"]


def _m(v: int) -> str:
    return f"{v:,}".replace(",", " ")


def _identity(region: str) -> dict:
    """region: 'uz' (город+улица), 'tj' (кишлак — фолбэк на GeoNames), 'ru' (город+улица)."""
    if region == "uz":
        client = f"{random.choice(_FAM_UZ)} {random.choice(_NAME_UZ)}"
        phone = f"99890{random.randint(1000000, 9999999)}"
        addr = f"г. {random.choice(_CITY_UZ)}, {random.choice(_ST_UZ)} {random.randint(1,120)}, кв. {random.randint(1,200)}"
    elif region == "tj":
        client = f"{random.choice(_FAM_TJ)} {random.choice(_NAME_TJ)}"
        phone = f"99290{random.randint(1000000, 9999999)}"
        addr = f"Таджикистан, кишлак {random.choice(_PLACE_TJ)}, дом {random.randint(1,90)}"
    else:
        client = f"{random.choice(_FAM_RU)} {random.choice(_NAME_RU)}"
        phone = f"79{random.randint(100000000, 999999999)}"
        addr = f"г. {random.choice(_CITY_RU)}, {random.choice(_ST_RU)} {random.randint(1,80)}, кв. {random.randint(1,200)}"
    return {
        "client": client,
        "birth_date": f"19{random.randint(72,99)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
        "phone": phone,
        "address": addr,
    }


def _zk(idn: dict, i: int) -> dict:
    overdue = random.randint(2_000_000, 15_000_000)
    interest = int(overdue * random.uniform(0.05, 0.2))
    total = overdue + interest
    return {**idn, "partner": "ЗК", "pool": "Узбекистан",
            "contract_id": f"ДГ-{1000 + i}", "debt_overdue": _m(overdue),
            "interest_overdue": _m(interest), "debt_total": _m(total),
            "debt_today": _m(total), "overall_result": "Не обработан",
            "product_type": random.choice(["Рассрочка", "Кредит наличными"])}


def _deli(idn: dict, i: int) -> dict:
    parts = {"dtp": random.choice([0, random.randint(20_000, 150_000)]),
             "rent": random.randint(3_000, 30_000),
             "camera_fine": random.choice([0, random.randint(500, 8_000)]),
             "peni": random.randint(0, 5_000)}
    total = sum(parts.values())
    row = {**idn, "partner": "Делимобиль", "pool": "Каршеринг РФ",
           "contract_id": f"DL-{77000 + i}", "debt_total": _m(total),
           "debt_today": _m(total), "overall_result": "Не обработан"}
    for k, v in parts.items():
        row[k] = _m(v) if v else None
    return row


# Примерные центры городов (для демо-координат; реальные — через геокодинг в пульте).
_CITY_GEO = {
    "Ташкент": (41.311, 69.240), "Самарканд": (39.654, 66.975),
    "Бухара": (39.767, 64.421), "Наманган": (40.998, 71.671),
    "Москва": (55.751, 37.618), "Санкт-Петербург": (59.939, 30.314),
    "Казань": (55.796, 49.106), "Самара": (53.195, 50.100),
}


def seed_geo() -> int:
    """Проставить синтетические координаты клиентам без гео (чтобы демо-карта была с пинами)."""
    seeded = 0
    for r in db.all_clients(limit=10000):
        if r["geo_lat"]:
            continue
        where = (r["city"] or "") + " " + (r["address"] or "")
        base = next((g for c, g in _CITY_GEO.items() if c in where), _CITY_GEO["Ташкент"])
        db.update_fields(r["contract_id"], {
            "geo_lat": round(base[0] + random.uniform(-0.06, 0.06), 6),
            "geo_lon": round(base[1] + random.uniform(-0.06, 0.06), 6)})
        seeded += 1
    return seeded


def generate(n: int = 30, path: str | None = None, responsible: str | None = None) -> str:
    if path is None:
        path = str(Path(db.DB_PATH).parent / "synthetic_registry.xlsx")
    assign = (date.today() - timedelta(days=random.randint(1, 20))).isoformat()
    rows, i = [], 0
    while len(rows) < n:
        emp = responsible or random.choice(EMPLOYEES)
        idn = _identity(random.choice(["uz", "uz", "tj", "ru"]))
        makers = [_zk] if random.random() < 0.7 else [_zk, _deli]   # 30% — 2 договора
        for maker in makers:
            row = maker(idn, i)
            row["responsible"] = emp
            row["assign_date"] = assign
            rows.append(row)
            i += 1
            if len(rows) >= n:
                break
    return db.write_xlsx(path, rows)


if __name__ == "__main__":
    db.init_db()
    print("Сгенерировано:", generate())
