"""Идемпотентность записи выездов и меток по op_id.

Оффлайн-очередь миниаппа при восстановлении связи досылает накопленные операции;
повторная доставка того же выезда/метки (тот же op_id) не должна плодить дубли в БД
и (для выездов) не должна повторно уведомлять группу. Гарантию даёт db-слой:
log_visit возвращает False на повторе (→ web._save не шлёт пост), add_marker возвращает
id уже сохранённой метки.
"""
import db


def test_log_visit_dedup_by_op():
    db.init_db()
    first = db.log_visit("КД-idemp-1", 111, "выезд", op_id="op-abc")
    second = db.log_visit("КД-idemp-1", 111, "выезд", op_id="op-abc")
    assert first is True        # первая доставка — новая строка, группу уведомляем
    assert second is False      # повтор — проигнорирован, повторного поста нет
    assert len(db.visit_history("КД-idemp-1")) == 1


def test_log_visit_without_op_backward_compatible():
    db.init_db()
    a = db.log_visit("КД-idemp-2", 111, "p1")
    b = db.log_visit("КД-idemp-2", 111, "p2")
    assert a is True and b is True
    assert len(db.visit_history("КД-idemp-2")) == 2   # без op_id — старое поведение


def test_add_marker_dedup_by_op():
    db.init_db()
    m1 = db.add_marker(9201, "agent", 41.0, 69.0, op_id="mk-idemp-1")
    m2 = db.add_marker(9201, "agent", 41.0, 69.0, op_id="mk-idemp-1")
    assert m1 == m2             # повтор вернул ту же метку
    assert len(db.markers_for_telegram(9201)) == 1


def test_add_marker_without_op_inserts_each_time():
    db.init_db()
    db.add_marker(9202, "agent", 1.0, 2.0)
    db.add_marker(9202, "agent", 3.0, 4.0)
    assert len(db.markers_for_telegram(9202)) == 2
