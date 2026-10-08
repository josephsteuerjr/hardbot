"""Операционная воронка Софта по шаблону встречи 08.10.2026.

RPC здесь определяется статусами шаблона, включая «Передана инф 3 лицу».
PTP — записи звонков с обещанием; KP — полная реестровая оплата в срок.
Суммы разных валют сохраняются отдельно, ручная запись оплаты не даёт KP.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from decimal import Decimal

import db
import debt

STATUSES = ("Передана инф 3 лицу", "Нет контакта", "В работе", "Отказ от оплаты",
            "Не обработан", "Доп. Обогащение", "Вывод из работы", "Тюрьма",
            "Встреча в офисе", "Смерть", "Кредит не брал", "Оплачен", "Рассрочка",
            "Обещает оплатить")
RPC_STATUSES = frozenset(("Рассрочка", "Отказ от оплаты", "Обещает оплатить",
                          "Оплачен", "Передана инф 3 лицу", "Кредит не брал",
                          "Встреча в офисе"))


def values(call) -> dict:
    try:
        data = json.loads(call["values_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def status(call) -> str:
    data = values(call)
    if "soft_call_status" in data:
        return str(data["soft_call_status"] or "").strip().removesuffix(" sms")
    match = re.search(r"Статус:\s*([^;\n]*)", call["payload"] or "")
    return match.group(1).strip().removesuffix(" sms") if match else "Без статуса"


def contract_ids(call) -> list[str]:
    try:
        data = json.loads(call["contracts_json"] or "[]")
    except (TypeError, ValueError):
        data = []
    ids = [x for x in data if isinstance(x, str)] if isinstance(data, list) else []
    return list(dict.fromkeys(ids or [call["contract_id"]]))


def _money(amounts) -> list[dict]:
    return [{"unit": unit, "sum": float(amount)} for unit, amount in sorted(amounts.items()) if amount]


def portfolio_money(contracts) -> list[dict]:
    """Те же исходные колонки долга, с копейками для нового Excel-отчёта."""
    amounts = defaultdict(Decimal)
    for row in contracts:
        current = debt._cell(row, "debt_today")
        source = current if current not in (None, "") else debt._cell(row, "debt_total")
        number = db.registry_payment_amount(source)
        if number is not None:
            amounts[debt.unit([debt._cell(row, "partner")])] += Decimal(str(number))
    return _money(amounts)


def _promise(call, rows) -> dict:
    data = values(call)
    ids = contract_ids(call)
    units = {debt.unit([rows[cid]["partner"]]) for cid in ids if cid in rows}
    amount = Decimal(db.canon_soft_amount(data.get("forecast_sum")) or "0")
    day, due = db._as_date(call["created_at"]), db._as_date(data.get("promise_date"))
    unit = next(iter(units)) if len(units) == 1 else ""
    valid = bool(day and due and 0 <= (due - day).days <= 3 and amount > 0 and unit)
    return {"id": call["id"], "employee": call["employee"], "rep": call["contract_id"],
            "day": call["created_at"], "due": due.isoformat() if due else "",
            "amount": amount, "unit": unit, "ids": ids, "valid": valid,
            "at": call["saved_at"] or call["received_at"] or call["created_at"],
            "received": Decimal(0)}


def build(calls, rows, history, payments, *, asof: str) -> dict:
    """Одна оплата относится к последнему обещанию до даты этой оплаты.

    История включает предыдущих владельцев тех же КД: перевод сотруднику
    не даёт одновременно засчитать оплату обоим. Старые обещания без даты,
    суммы или известной валюты видны в счётчике, но не считаются исполненными.
    """
    counts = Counter(status(c) for c in calls)
    promises = [_promise(c, rows) for c in history if status(c) == "Обещает оплатить"]
    by_contract = defaultdict(list)
    for promise in promises:
        for cid in promise["ids"]:
            by_contract[cid].append(promise)
    for candidates in by_contract.values():
        candidates.sort(key=lambda p: (p["day"], p["at"], p["id"]))
    for payment in payments:
        if payment["paid_at"] > asof:
            continue
        candidates = [p for p in by_contract[payment["contract_id"]]
                      if p["day"] <= payment["paid_at"]]
        if not candidates:
            continue
        promise = candidates[-1]
        if promise["valid"] and payment["currency"] == promise["unit"] and payment["paid_at"] <= promise["due"]:
            promise["received"] += Decimal(str(payment["amount"]))
    selected_ids = {c["id"] for c in calls}
    selected = [p for p in promises if p["id"] in selected_ids]
    ptp_money, kp_money, partial_money = (defaultdict(Decimal) for _ in range(3))
    kp, partial, missing, amount_missing = 0, 0, 0, 0
    details = []
    for promise in selected:
        if promise["unit"] and promise["amount"] > 0:
            ptp_money[promise["unit"]] += promise["amount"]
        else:
            amount_missing += 1
        kept = promise["valid"] and promise["received"] >= promise["amount"]
        state = "kept" if kept else "partial" if promise["received"] > 0 else "pending" if promise["valid"] else "incomplete"
        missing += not promise["valid"]
        if kept:
            kp += 1
            kp_money[promise["unit"]] += promise["received"]
        elif promise["received"] > 0:
            partial += 1
            partial_money[promise["unit"]] += promise["received"]
        details.append({"call_id": promise["id"], "rep": promise["rep"], "date": promise["day"],
                        "due": promise["due"], "amount": float(promise["amount"]),
                        "unit": promise["unit"], "received": float(promise["received"]), "state": state})
    rpc = sum(counts[s] for s in RPC_STATUSES)
    ptp = counts["Обещает оплатить"]
    return {"statuses": {s: counts[s] for s in STATUSES},
            "other": sum(n for s, n in counts.items() if s not in STATUSES),
            "rpc": rpc, "ptp": ptp, "kp": kp, "partial": partial,
            "incomplete": missing, "amount_missing": amount_missing, "ptp_money": _money(ptp_money),
            "kp_money": _money(kp_money), "partial_money": _money(partial_money),
            "rpc_rate": rpc / len(calls) if calls else None,
            "ptp_rate": ptp / rpc if rpc else None,
            "kp_rate": kp / ptp if ptp else None, "promises": details}
