"""Дайджест руководителя: модель managers, фильтр «обработан сегодня», рендер экранов.

Проверяем, что:
  • CRUD руководителей работает (JSON-списки городов/сотрудников целы);
  • persons_processed берёт только визиты за нужную дату (по clients.visit_date);
  • три экрана дайджеста строятся, содержат стату и корректные callback'и;
  • фото (photostore): кнопки в карточке/у сотрудника и выборка файлов за день.
"""
import os
import shutil
import time
from datetime import date, timedelta

import db
import digest
import photostore

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


def test_digest_never_adds_sums_to_rubles():
    """Смешанная команда (ЗК в сумах + Делимобиль в рублях): дайджест показывает обе
    валюты раздельно. Раньше «Сумма по кейсам дня» была сумом+рублём в одном числе,
    да ещё и без единицы измерения (unit молча становился пустым)."""
    _seed()
    _add("D-DM", "Каримова Г.", "Рахимов Алишер", visit_date=TODAY,
         partner="Делимобиль", debt="132 900", overall_result="Оплачен")
    mgr = db.get_manager(MGR_TID)

    text, _ = digest.summary_view(mgr, TODAY)
    assert "132 900 ₽" in text
    assert "сум" in text
    assert "1 132 900" not in text          # сумы, сложенные с рублями

    # у одного человека тоже: D-DM и D-2 — один клиент, два контрагента
    emps = digest.sorted_employees(mgr)
    ei = emps.index("Рахимов Алишер")
    persons = db.persons_processed("Рахимов Алишер", TODAY)
    ci = next(i for i, p in enumerate(persons) if p["client"] == "Каримова Г.")
    assert len(persons[ci]["partners"]) == 2
    card, _ = digest.person_card(mgr, TODAY, ei, ci)
    assert "132 900 ₽" in card and "1 000 000 сум" in card


# --- фото ---------------------------------------------------------------------
def _photo(cid, name, days_ago=0):
    d = photostore.PHOTO_DIR / cid
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"jpegdata")
    ts = time.time() - days_ago * 86400
    os.utime(p, (ts, ts))
    return p


def _seed_photos():
    for cid in ("D-1", "D-2", "D-4"):
        shutil.rmtree(photostore.PHOTO_DIR / cid, ignore_errors=True)
    _photo("D-1", "a.jpg")
    _photo("D-1", "b.jpg")
    _photo("D-1", "old.jpg", days_ago=3)   # старое: в кейсе есть, в «за день» нет
    _photo("D-4", "c.jpg")                 # другой сотрудник — не подмешивается


def test_photo_buttons_and_helpers():
    _seed()
    _seed_photos()
    mgr = db.get_manager(MGR_TID)
    emps = digest.sorted_employees(mgr)
    ei = emps.index("Рахимов Алишер")

    # экран сотрудника: кнопка «за день» считает только сегодняшние фото
    _, kb = digest.employee_view(mgr, TODAY, ei)
    btns = {b.callback_data: b.text for row in kb.inline_keyboard for b in row}
    assert f"dg:pe:{TODAY}:{ei}" in btns
    assert "(2)" in btns[f"dg:pe:{TODAY}:{ei}"]

    # карточка кейса: все фото человека, включая старое
    persons = db.persons_processed("Рахимов Алишер", TODAY)
    ci = next(i for i, p in enumerate(persons) if p["client"] == "Абдуллаев А.")
    _, kcard = digest.person_card(mgr, TODAY, ei, ci)
    btns = {b.callback_data: b.text for row in kcard.inline_keyboard for b in row}
    assert f"dg:p:{TODAY}:{ei}:{ci}" in btns
    assert "(3)" in btns[f"dg:p:{TODAY}:{ei}:{ci}"]

    # кейс без фото — кнопки нет
    ci2 = next(i for i, p in enumerate(persons) if p["client"] == "Каримова Г.")
    _, k2 = digest.person_card(mgr, TODAY, ei, ci2)
    assert not any(b.callback_data.startswith("dg:p:")
                   for row in k2.inline_keyboard for b in row)

    # хелперы: карточный — всё (новые сверху), дневной — без старого и без чужого
    p, files = digest.photos_for_person(mgr, TODAY, ei, ci)
    assert p["client"] == "Абдуллаев А." and len(files) == 3
    assert files[-1].name == "old.jpg"
    emp, items = digest.photos_for_employee_day(mgr, TODAY, ei)
    assert emp == "Рахимов Алишер"
    assert [(x["client"], len(fs)) for x, fs in items] == [("Абдуллаев А.", 2)]

    # сбитые индексы не роняют
    assert digest.photos_for_person(mgr, TODAY, 999, 0) == (None, [])
    assert digest.photos_for_person(mgr, TODAY, ei, 999) == (None, [])
    assert digest.photos_for_employee_day(mgr, TODAY, 999) == (None, [])


def test_photo_coverage_in_stats():
    """«Для скольких обработанных есть фото»: сводка и экран сотрудника."""
    _seed()
    _seed_photos()
    mgr = db.get_manager(MGR_TID)

    text, _ = digest.summary_view(mgr, TODAY)
    assert "📸 С фото: 2 из 3" in text     # Абдуллаев + Назаров; Каримова без фото
    assert "📸 1/2" in text                 # строка Рахимова
    assert "📸 1/1" in text                 # строка Каримова Бахтиёра

    ei = digest.sorted_employees(mgr).index("Рахимов Алишер")
    etext, kb = digest.employee_view(mgr, TODAY, ei)
    assert "📸 С фото: 1 из 2" in etext
    # метка 📸 — только на кнопке кейса с фото
    persons = db.persons_processed("Рахимов Алишер", TODAY)
    ci = next(i for i, p in enumerate(persons) if p["client"] == "Абдуллаев А.")
    ci2 = next(i for i, p in enumerate(persons) if p["client"] == "Каримова Г.")
    btns = {b.callback_data: b.text for row in kb.inline_keyboard for b in row}
    assert btns[f"dg:c:{TODAY}:{ei}:{ci}"].endswith("📸")
    assert not btns[f"dg:c:{TODAY}:{ei}:{ci2}"].endswith("📸")


def test_period_view_week():
    """Недельная сводка: вчерашний кейс попадает, конверсия и фото-дисциплина считаются."""
    _seed()
    _seed_photos()
    mgr = db.get_manager(MGR_TID)
    text, kb = digest.period_view(mgr, TODAY, 7)
    assert "Сводка за 7 дн" in text
    assert "Старый кейс" not in text            # имена людей в сводке не светятся
    # вчерашний «Оплачен» (D-3) теперь в счётчиках: 4 человека за неделю
    assert "Обработано: <b>4</b>" in text
    assert "✅ Оплачен: " in text and "📸 С фото: " in text
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"dg:w:{TODAY}:30" in cbs             # переключатель периода
    assert f"dg:h:{TODAY}" in cbs

    # из сводки есть вход
    _, skb = digest.summary_view(mgr, TODAY)
    assert any(b.callback_data == f"dg:w:{TODAY}:7"
               for row in skb.inline_keyboard for b in row)

    # кривые days откатываются к 7
    t2, _ = digest.period_view(mgr, TODAY, 999)
    assert "Сводка за 7 дн" in t2
