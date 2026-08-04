"""Дайджест руководителя направления не должен быть пустым.

03.08.2026: Михаил, руководитель по Узбекистану, видел «Команда: 0 · активны: 0 ·
обработано: 0» — при том что у Уткирова в тот момент было 18 закрытых выездов, а у
Исмоилова 10. Данные были на месте, дайджест смотрел не туда.

Причина: `sorted_employees` читал сохранённое поле `mgr["employees"]`, а у
руководителя направления оно пусто НАМЕРЕННО — состав считается живьём по стране
кейса. Промах системный: он бил по всем четырём направленцам сразу.
"""

import db
import digest


def _clean():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'DGS-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id IN (7101, 7102)")
        conn.commit()


def _import(rows):
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    for cid, responsible, city in rows:
        grid.append([{"contract_id": cid, "client": "К", "birth_date": "1990-01-01",
                      "responsible": responsible, "address": city, "city": city,
                      "debt_total": "1000"}.get(f.key) for f in db.FIELDS])
    db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)


def test_direction_manager_sees_his_team():
    _clean()
    _import([("DGS-1", "Узбеков У.", "Ташкент"), ("DGS-2", "Самаркандов С.", "Самарканд")])
    db.set_manager(7101, "Рук УЗ", ["Узбекистан"], [], direction="uz")
    mgr = db.get_manager(7101)

    assert mgr["employees"] == [], "предпосылка теста: у направленца список пуст"
    team = digest.sorted_employees(mgr)
    assert "Узбеков У." in team and "Самаркандов С." in team, (
        "дайджест направленца снова читает сохранённый список — сводка будет пустой")


def test_new_employee_appears_without_touching_the_role():
    """Состав живой: разброс добавил человека — он в сводке, роль не трогали."""
    _clean()
    _import([("DGS-1", "Узбеков У.", "Ташкент")])
    db.set_manager(7101, "Рук УЗ", ["Узбекистан"], [], direction="uz")
    before = digest.sorted_employees(db.get_manager(7101))
    _import([("DGS-3", "Новичок Н.", "Ташкент")])
    after = digest.sorted_employees(db.get_manager(7101))
    assert set(after) - set(before) == {"Новичок Н."}


def test_pointed_manager_keeps_his_explicit_list():
    """Точечный руководитель из пульта не должен внезапно увидеть всю страну."""
    _clean()
    _import([("DGS-1", "Узбеков У.", "Ташкент"), ("DGS-2", "Самаркандов С.", "Самарканд")])
    db.set_manager(7102, "Точечный", ["Ташкент"], ["Узбеков У."])
    assert digest.sorted_employees(db.get_manager(7102)) == ["Узбеков У."]


def test_summary_counts_the_live_team():
    """Сквозная проверка: в шапке сводки не ноль."""
    _clean()
    _import([("DGS-1", "Узбеков У.", "Ташкент"), ("DGS-2", "Самаркандов С.", "Самарканд")])
    db.set_manager(7101, "Рук УЗ", ["Узбекистан"], [], direction="uz")
    text, _kb = digest.summary_view(db.get_manager(7101), digest.today_iso())
    assert "Команда: 0" not in text, "сводка направленца снова пустая"


def test_owner_without_a_manager_record_still_sees_his_team():
    """Владелец — синтетический руководитель: записи в managers у него нет.

    04.08.2026: починив дайджест направленцев, я стал спрашивать состав только у
    БД — и владелец увидел «Команда: 0 · обработано: 0» при живой работе у
    пятерых. Его список собран прямо в объекте, и пустой ответ БД должен
    отправлять именно туда.
    """
    _clean()
    _import([("DGS-1", "Узбеков У.", "Ташкент"), ("DGS-2", "Самаркандов С.", "Самарканд")])
    synthetic = {"telegram_id": 999999, "name": "Владелец", "cities": [],
                 "employees": ["Узбеков У.", "Самаркандов С."]}
    assert db.get_manager(999999) is None, "предпосылка: записи в managers нет"
    team = digest.sorted_employees(synthetic)
    assert team == ["Самаркандов С.", "Узбеков У."]
    text, _kb = digest.summary_view(synthetic, digest.today_iso())
    assert "Команда: 0" not in text


def test_db_answer_wins_over_a_stale_stored_list():
    """У настоящего руководителя живой состав важнее сохранённого."""
    _clean()
    _import([("DGS-1", "Узбеков У.", "Ташкент")])
    db.set_manager(7101, "Рук УЗ", ["Узбекистан"], [], direction="uz")
    mgr = dict(db.get_manager(7101))
    mgr["employees"] = ["Кто-то Устаревший"]      # как будто осталось от пульта
    assert "Кто-то Устаревший" not in digest.sorted_employees(mgr)
