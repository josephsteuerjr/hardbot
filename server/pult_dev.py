"""
Дев-стенд для пультов (Пульты 2): сервер на 127.0.0.1 с синтетической командой.

Три офиса двух стран и незакреплённые, выезды за две недели со следами —
далеко от адреса, задним числом, ночью, «наличные» в тексте, быстрые карточки —
и журнал доступа (открытия карточек, фото, входы). Реальную базу не трогает:
данные в server/_devdata_pult (вне git). Все ФИО и номера выдуманы.

    python server/pult_dev.py            # порт PULT_DEV_PORT, по умолчанию 8090

На старте печатает свежие ссылки входа: руководитель, безопасник, директор
(«full»). Ссылку вставляют в пульт как ключ.
"""

import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

_here = os.path.dirname(os.path.abspath(__file__))
os.environ["APP_DATA"] = os.path.join(_here, "_devdata_pult")
os.environ["APP_DEV"] = "1"
os.environ.setdefault("BOT_TOKEN", "dev")
sys.path.insert(0, _here)

import db      # noqa: E402
import synth   # noqa: E402
import web     # noqa: E402
from aiohttp import web as aw  # noqa: E402

TEAM = [
    # ФИО, офис, «характер»: honest | sloppy | shady
    ("Рахмонов Фирдавс", "Худжанд", "honest"),
    ("Сафаров Далер", "Худжанд", "honest"),
    ("Шарипов Бахром", "Худжанд", "shady"),
    ("Холов Нодир", "Худжанд", "honest"),
    ("Бободжонов Хуршед", "Худжанд", "sloppy"),
    ("Назаров Сухроб", "Душанбе", "honest"),
    ("Каримов Жасур", "Душанбе", "honest"),
    ("Абдуллаев Шерзод", "Душанбе", "sloppy"),
    ("Юлдашев Фарход", "Душанбе", "honest"),
    ("Турсунов Дилшод", "Ташкент", "honest"),
    ("Рахимов Абдулла", "Ташкент", "shady"),
    ("Каримов Бахтиёр", "Ташкент", "honest"),
    ("Назаров Жасур", "Ташкент", "honest"),
    ("Исмоилов Анвар", "", "sloppy"),
]
UID0 = 1001
MANAGERS = [(222, "Руководитель (дев)", "manager"),
            (333, "Безопасник (дев)", "security"),
            (444, "Директор (дев)", "full")]

STATUSES = [("Обещает оплатить", "Обещает оплатить клиент"), ("Нет контакта", ""),
            ("В работе", ""), ("Отказ от оплаты", "На оплату договориться не удалось"),
            ("Оплачен", ""), ("Нет контакта", ""), ("Обещает оплатить", "Обещает оплатить 3-е лицо")]
CONTACTS = [("Клиент", "Клиент"), ("Родственник", "Мать"), ("", "Никого"), ("Соседи", "Соседи")]
CLEAN = ["Дома никого, оставил уведомление в двери.", "Разговаривал с матерью, передаст.",
         "Клиент обещал оплатить до пятницы через банк.", "Адрес верный, клиент на работе.",
         "Соседи говорят, уехал в Россию.", "Клиент отказывается, говорит нет денег.",
         "Позвонил при мне сыну, договорились на частичную оплату."]
CASH = ["Клиент передал мне наличными 500 сомони, сдам в офис.",
        "Пулро ба ман дод, 300 сомонӣ.", "Отдал деньги мне, квитанцию выдам завтра.",
        "Naqd pul berdi 400 ming, ertaga topshiraman."]
TZ5 = timezone(timedelta(hours=5))


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ5)


def _naive_utc(t: datetime) -> str:
    """Как пишет сервер в контейнере: наивное время UTC."""
    return t.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def seed() -> None:
    random.seed(29)
    synth.EMPLOYEES[:] = [t[0] for t in TEAM]
    db.import_xlsx(synth.generate(n=260), source_kind=db.IMPORT_SOURCE_TEST_SUBSET)
    synth.seed_geo()
    for i, (emp, office, _) in enumerate(TEAM):
        db.bind_agent(UID0 + i, emp)
        if office:
            db.set_employee_city(emp, office)
    for uid, name, role in MANAGERS:
        db.set_manager(uid, name, [], [], direction="all", role=role)
    today = date.today()
    op = 0
    audit = []
    for i, (emp, office, kind) in enumerate(TEAM):
        uid = UID0 + i
        clients = [r["contract_id"] for r in db.contracts_of_responsible(emp)]
        if not clients:
            continue
        for back in range(13, -1, -1):
            day = today - timedelta(days=back)
            if day.weekday() == 6:
                continue
            n = random.randint(4, 11) if back else random.randint(2, 7)
            hour = 9
            audit.append((_at(day, 8, random.randint(30, 59)), uid, "app_login", None))
            for _ in range(n):
                cid = random.choice(clients)
                st, agreed = random.choice(STATUSES)
                cw, who = random.choice(CONTACTS)
                shady = kind == "shady" and random.random() < 0.35
                sloppy = kind == "sloppy" and random.random() < 0.4
                comment = random.choice(CASH) if shady and random.random() < 0.5 else random.choice(CLEAN)
                hour = min(20, hour + random.choice([0, 1, 1]))
                t = _at(day, hour, random.randint(0, 59))
                if shady and random.random() < 0.3:
                    t = _at(day, 22, random.randint(0, 50))
                now5 = datetime.now(TZ5)
                if t > now5:
                    t = now5 - timedelta(minutes=random.randint(5, 240))
                level = "confirmed"
                if shady and random.random() < 0.6:
                    level = "far"
                elif sloppy and random.random() < 0.6:
                    level = random.choice(["nogeo", "nofix"])
                elif random.random() < 0.08:
                    level = "far"
                fill = random.randint(15, 35) if (shady and random.random() < 0.5) else random.randint(70, 600)
                late = (sloppy and random.random() < 0.5) or (shady and random.random() < 0.2)
                got = t + (timedelta(days=random.randint(1, 2)) if late else timedelta(minutes=1))
                if got.date() > today:
                    got = t + timedelta(minutes=1)
                op += 1
                payload = (f"Выезд (миниапп). Статус: {st}; Договорённость: {agreed}; "
                           f"Контакт: {cw}; На адресе: {who}; {comment}")
                db.log_visit(cid, uid, payload, op_id=f"dev-{op}",
                             lat=41.3 if level != "nogeo" else None,
                             lon=69.2 if level != "nogeo" else None,
                             on_date=day.isoformat(), fill_seconds=fill,
                             saved_at=t.astimezone(timezone.utc).isoformat(timespec="seconds"),
                             evidence_level=level, employee=emp)
                with db.connect() as conn:
                    conn.execute("UPDATE visits SET received_at = ? WHERE op_id = ?",
                                 (_naive_utc(got),
                                  f"dev-{op}"))
                    conn.commit()
                audit.append((t - timedelta(minutes=random.randint(3, 25)), uid, "view_card", cid))
                if random.random() < 0.6:
                    audit.append((t - timedelta(minutes=2), uid, "photo", cid))
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO audit (ts, actor, action, target, detail) VALUES (?,?,?,?,NULL)",
            [(_naive_utc(a), u, act, tgt)
             for a, u, act, tgt in audit])
        conn.commit()
    print(f"засеяно: {len(TEAM)} выездников, выездов {op}")
    seed_payments()


def seed_payments() -> None:
    """Оплаты по реестру («Дата/Сумма последнего платежа») — для блока «Оплаты»
    сводки: часть должников платит в пару дней после выезда, часть — сама.
    Реестр приходит импортом раз в день, поэтому последний платёж — вчера."""
    rnd = random.Random(31)
    last = date.today() - timedelta(days=1)
    with db.connect() as conn:
        visited = dict(conn.execute("SELECT contract_id, max(substr(created_at, 1, 10)) "
                                    "FROM visits GROUP BY contract_id").fetchall())
        for (cid,) in conn.execute("SELECT contract_id FROM clients").fetchall():
            if cid in visited and rnd.random() < .3:
                d = date.fromisoformat(visited[cid]) + timedelta(days=rnd.randint(0, 4))
            elif rnd.random() < .08:
                d = last - timedelta(days=rnd.randint(0, 20))
            else:
                continue
            conn.execute("UPDATE clients SET last_payment_date=?, last_payment_sum=? "
                         "WHERE contract_id=?",
                         (min(d, last).isoformat(),
                          str(rnd.choice([1500, 3000, 5000, 7500, 12000, 20000])), cid))
        conn.commit()


def main() -> None:
    db.init_db()
    if not db.employees_in_registry():
        seed()
    port = int(os.getenv("PULT_DEV_PORT", "8090"))
    base = f"http://127.0.0.1:{port}"
    print("Дев-стенд пультов:", base)
    for uid, name, role in MANAGERS:
        print(f"  {name:<22} ключ: {base}/i/{db.create_app_link(uid, max_uses=20)}")
    sys.stdout.flush()
    aw.run_app(web.build_app(), host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
