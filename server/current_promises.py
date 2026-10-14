"""Текущие обязательства Софта: отдельно от периодной оценки KP.

Владелец — сегодняшнее закрепление, автор — неизменяемый журнал звонка.
Последующее обещание заменяет прежнее для соответствующих договоров.
Полная реестровая оплата закрывает обязательство даже после срока; ручная
оплата и произвольный следующий статус звонка не доказывают зачисления.
"""
from collections import defaultdict
from decimal import Decimal

import db
import soft_reporting as reporting


def soft_data(employees: list[str], asof: str) -> dict:
    today = db._as_date(asof)
    if today is None:
        raise ValueError("date")
    contracts = []
    with db.connect() as conn:
        for start in range(0, len(employees), 250):
            names = employees[start:start + 250]
            marks = ",".join("?" * len(names))
            contracts.extend(conn.execute(f"SELECT contract_id,client,responsible_soft,partner FROM clients WHERE responsible_soft IN ({marks})", names))
    rows = {r["contract_id"]: r for r in contracts}
    history = db.calls_for_contracts(rows, asof)
    if not history:
        return {"screen": "promises", "profile": "soft", "asof": asof, "employees": [],
                "totals": {"persons": 0, "worked": 0, "untouched": 0, "deferred": 0, "incomplete": 0}}
    # Общее обещание остаётся общим после переноса части договоров. Для расчёта
    # нужны все связанные платежи и более поздние обещания, а для выдачи —
    # только исходные договоры разрешённых сотрудников.
    visible_ids = set(rows)
    seen_ids = set(rows)
    while True:
        extra_ids = sorted({cid for event in history for cid in reporting.contract_ids(event)} - seen_ids)
        if not extra_ids:
            break
        seen_ids.update(extra_ids)
        with db.connect() as conn:
            for start in range(0, len(extra_ids), 400):
                chunk = extra_ids[start:start + 400]
                marks = ",".join("?" * len(chunk))
                for row in conn.execute(f"SELECT contract_id,client,responsible_soft,partner FROM clients WHERE contract_id IN ({marks})", chunk):
                    rows[row["contract_id"]] = row
        history = db.calls_for_contracts(seen_ids, asof)
    history_by_contract = defaultdict(list)
    for event in history:
        for cid in reporting.contract_ids(event):
            history_by_contract[cid].append(event)
    promises = [reporting._promise(c, rows) for c in history
                if reporting.status(c) == "Обещает оплатить"]
    if not promises:
        return {"screen": "promises", "profile": "soft", "asof": asof, "employees": [],
                "totals": {"persons": 0, "worked": 0, "untouched": 0, "deferred": 0, "incomplete": 0}}
    by_contract = defaultdict(list)
    for p in promises:
        for cid in p["ids"]:
            by_contract[cid].append(p)
    for candidates in by_contract.values():
        candidates.sort(key=lambda p: (p["day"], p["at"], p["id"]))
    # Платёж относится к последнему на тот день обещанию, независимо от автора.
    for payment in db.registry_payments_for(by_contract):
        if payment["paid_at"] > asof:
            continue
        candidates = [p for p in by_contract[payment["contract_id"]]
                      if p["day"] <= payment["paid_at"]]
        if candidates:
            p = candidates[-1]
            if p["unit"] == payment["currency"]:
                p["received"] += Decimal(str(payment["amount"]))
    latest = {cid: candidates[-1]["id"] for cid, candidates in by_contract.items()}
    result = defaultdict(list)
    deferred = incomplete = 0
    for p in promises:
        owned = [rows[cid] for cid in p["ids"] if cid in visible_ids and latest.get(cid) == p["id"]]
        if not owned:
            continue
        if not p["valid"] or any(cid not in rows for cid in p["ids"]):
            incomplete += 1
            continue
        if p["received"] >= p["amount"]:
            continue
        if p["due"] >= asof:
            deferred += int(p["due"] > asof)
            continue
        # Перенос части договоров может дать двум владельцам одну общую сумму:
        # не складываем её в итог; строка прямо указывает общий состав обещания.
        for owner in sorted({r["responsible_soft"] for r in owned}):
            owner_rows = [r for r in owned if r["responsible_soft"] == owner]
            rep = next((r["contract_id"] for r in owner_rows if r["contract_id"] == p["rep"]), owner_rows[0]["contract_id"])
            later = [c for r in owner_rows for c in history_by_contract[r["contract_id"]]
                     if c["employee"] == owner and c["created_at"] >= p["due"]]
            result[owner].append({"rep": rep, "client": owner_rows[0]["client"],
                "call_id": p["id"], "author": p["employee"], "contracts": [r["contract_id"] for r in owner_rows],
                "shared": len(owner_rows) < len(p["ids"]),
                "promise_date": p["due"], "promised_sum": float(p["amount"]),
                "received": float(p["received"]), "unit": p["unit"],
                "money_text": f"{p['amount']:,.2f} {p['unit']}",
                "days_overdue": (today - db._as_date(p["due"])).days,
                "last_call": max((c["created_at"] for c in later), default=None),
                "visited_after_promise": bool(later), "state": "partial" if p["received"] else "overdue"})
    items = [{"employee": e, "persons": len(ps), "items": sorted(ps, key=lambda p: -p["days_overdue"])}
             for e, ps in sorted(result.items())]
    everyone = [p for e in items for p in e["items"]]
    worked = sum(p["visited_after_promise"] for p in everyone)
    return {"screen": "promises", "profile": "soft", "asof": asof,
            "employees": items, "totals": {"persons": len(everyone), "worked": worked,
                "untouched": len(everyone) - worked, "deferred": deferred, "incomplete": incomplete}}
