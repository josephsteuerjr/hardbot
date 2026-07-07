"""Дайджест руководителя: модель managers, фильтр «обработан сегодня», рендер экранов.

Проверяем, что:
  • CRUD руководителей работает (JSON-списки городов/сотрудников целы);
  • persons_processed берёт только визиты за нужную дату (по clients.visit_date);
  • три экрана дайджеста строятся, содержат стату и корректные callback'и.
"""
from datetime import date, timedelta

import db
import digest

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
MGR_TID = 700001


def _add(cid, client, resp, **f):
    row = dict(contract_id=cid, client=client, responsible=resp,
               partner="ЗК", debt_today=f.pop("debt", "1 000 000"))
    row.update(f)
    with db.connect() as conn:
        keys = ", ".join(f'"{k}"' for k in row)
        qs = ", ".join("?" for _ in row)
        conn.execute(f"INSERT OR REPLACE INTO clients ({keys}) VALUES ({qs})",
                     tuple(row.values()))
        conn.commit()


def _seed():
    db.init_db()
    _add("D-1", "Абдуллаев А.", "Рахимов Алишер", visit_date=TODAY,
         overall_result="Обещает оплатить", agreed_payment="Обещает оплатить клиент",
         contact_with="Должник", call_comment="ок")
    _add("D-2", "Каримова Г.", "Рахимов Алишер", visit_date=TODAY,
         overall_result="Нет контакта")
    _add("D-3", "Старый кейс", "Рахимов Алишер", visit_date=YESTERDAY,
         overall_result="Оплачен")            # НЕ сегодня — не должен попасть
    _add("D-4", "Назаров Ф.", "Каримов Бахтиёр", visit_date=TODAY,
         overall_result="Рассрочка")
    _add("D-5", "Без выезда", "Тихонов Т.")     # 0 обработанных
    db.set_manager(MGR_TID, "Рустам", ["Душанбе", "Худжанд"],
                   ["Рахимов Алишер", "Каримов Бахтиёр", "Тихонов Т."])


def test_manager_crud():
    _seed()
    assert db.is_manager(MGR_TID)
    m = db.get_manager(MGR_TID)
    assert m["name"] == "Рустам"
    assert m["cities"] == ["Душанбе", "Худжанд"]
    assert "Рахимов Алишер" in m["employees"]
    assert any(x["telegram_id"] == MGR_TID for x in db.list_managers())
    # upsert обновляет, не плодит
    db.set_manager(MGR_TID, "Рустам Р.", ["Душанбе"], ["Рахимов Алишер"])
    m2 = db.get_manager(MGR_TID)
    assert m2["name"] == "Рустам Р." and m2["cities"] == ["Душанбе"]
    db.delete_manager(MGR_TID)
    assert not db.is_manager(MGR_TID)


def test_persons_processed_filters_by_date():
    _seed()
    rah = db.persons_processed("Рахимов Алишер", TODAY)
    names = {p["client"] for p in rah}
    assert names == {"Абдуллаев А.", "Каримова Г."}          # вчерашний исключён
    assert db.persons_processed("Рахимов Алишер", YESTERDAY)[0]["client"] == "Старый кейс"
    assert db.persons_processed("Тихонов Т.", TODAY) == []    # без выезда


def test_summary_view_has_per_employee_stats():
    _seed()
    mgr = db.get_manager(MGR_TID)
    text, kb = digest.summary_view(mgr, TODAY)
    assert "Рахимов Алишер" in text
    assert "Обработано" in text or "обработано" in text
    assert "Нет контакта" in text                            # исход в сводке
    # кнопка на каждого сотрудника + «Обновить»
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(c.startswith("dg:e:") for c in cbs)
    assert f"dg:h:{TODAY}" in cbs


def test_employee_and_person_views():
    _seed()
    mgr = db.get_manager(MGR_TID)
    emps = digest.sorted_employees(mgr)
    ei = emps.index("Рахимов Алишер")
    text, kb = digest.employee_view(mgr, TODAY, ei)
    assert "Исходы за день" in text
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(c.startswith(f"dg:c:{TODAY}:{ei}:") for c in cbs)

    tcard, kcard = digest.person_card(mgr, TODAY, ei, 0)
    assert "Итог выезда" in tcard
    assert "💰" in tcard                                       # референс-долг
    back = [b.callback_data for row in kcard.inline_keyboard for b in row]
    assert f"dg:e:{TODAY}:{ei}" in back


def test_view_bounds_safe():
    _seed()
    mgr = db.get_manager(MGR_TID)
    # некорректные индексы не роняют — откат к сводке/сотруднику
    assert digest.employee_view(mgr, TODAY, 999)
    assert digest.person_card(mgr, TODAY, 0, 999)
