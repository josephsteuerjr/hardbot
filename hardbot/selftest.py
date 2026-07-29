"""
Тест целостности и новой модели данных.

Проверяет: импорт синтетики, объединение договоров в людей, одноразовый
самовыбор сотрудника, запись выезда во все договоры человека, настройки,
и побитовый round-trip import→export→reimport (даты/суммы не плывут).

Запуск: python selftest.py  (временная БД, рабочую не трогает)
"""

import os
import random
import sys
import tempfile

os.environ["HARDBOT_DATA"] = tempfile.mkdtemp(prefix="hardbot_selftest_")

import db        # noqa: E402
import synth     # noqa: E402
from fields import FIELDS  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("✅" if cond else "❌") + " " + msg)
    if not cond:
        FAIL.append(msg)


def snapshot() -> dict:
    out = {}
    for r in db.all_clients(limit=10_000):
        out[r["contract_id"]] = {f.key: r[f.key] for f in FIELDS}
    return out


def main():
    random.seed(42)
    db.init_db()

    # 1) импорт синтетики (один сотрудник, часть людей — с 2 договорами)
    path = synth.generate(n=40, responsible=synth.DEV_EMPLOYEE)
    rep = db.import_xlsx(
        path,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    print("отчёт импорта:", rep)
    check(rep["inserted"] == 40, "импортировано 40 договоров")
    check(rep["skipped_no_id"] == 0 and rep["unknown_columns"] == [], "колонки распознаны, без потерь")

    # 2) сотрудники и объединение договоров
    emps = db.employees_in_registry()
    check(any(e["employee"] == synth.DEV_EMPLOYEE for e in emps), "сотрудник виден в реестре")
    persons = db.persons_for_employee(synth.DEV_EMPLOYEE)
    check(sum(p["n"] for p in persons) == 40, "сумма договоров по людям = 40")
    check(len(persons) < 40, "есть люди с несколькими договорами (объединение работает)")
    multi = next(p for p in persons if p["n"] > 1)

    # 3) одноразовый самовыбор
    check(db.bind_agent(111, synth.DEV_EMPLOYEE) is True, "первый самовыбор закрепился")
    check(db.bind_agent(111, "Другой") is False, "повторный самовыбор заблокирован")
    check(db.get_binding(111) == synth.DEV_EMPLOYEE, "привязка верна")
    check(db.telegram_for_employee(synth.DEV_EMPLOYEE) == 111, "обратный поиск telegram по сотруднику")

    # 4) выезд пишется во ВСЕ договоры человека
    db.update_person(multi["rep"], {"call_status": "В работе", "visit_date": "2026-06-20"})
    contracts = db.person_contracts(multi["rep"])
    check(all(c["call_status"] == "В работе" for c in contracts),
          f"выезд записан во все {len(contracts)} договора человека")

    # 5) настройки
    db.set_setting("group_chat_id", "-100500")
    check(db.get_setting("group_chat_id") == "-100500", "настройка беседы сохраняется")

    # 6) round-trip: export → reimport в чистую БД → побитовая сверка
    before = snapshot()
    export_path = os.path.join(os.environ["HARDBOT_DATA"], "roundtrip.xlsx")
    db.export_xlsx(export_path)

    os.environ["HARDBOT_DATA"] = tempfile.mkdtemp(prefix="hardbot_rt_")
    import importlib
    importlib.reload(db)
    db.init_db()
    db.import_xlsx(
        export_path,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    after = snapshot()

    mism = 0
    for cid, f in before.items():
        for k, v in f.items():
            if (after.get(cid, {}).get(k) or None) != (v or None):
                mism += 1
                if mism <= 5:
                    print(f"   ≠ {cid}.{k}: {v!r} → {after.get(cid,{}).get(k)!r}")
    check(mism == 0, "round-trip побитово точен (даты/суммы/контракты не плывут)")

    print()
    if FAIL:
        print(f"ИТОГ: ПРОВАЛ ({len(FAIL)})")
        sys.exit(1)
    print("ИТОГ: всё чисто ✅")


if __name__ == "__main__":
    main()
