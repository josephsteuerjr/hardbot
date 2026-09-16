"""Политика переезда обязана знать КАЖДУЮ таблицу базы.

Незнакомая таблица останавливает перенос целиком, и узнаётся это в худший из
возможных моментов — когда копию уже снимают с боевого сервера, под срок.
16.09.2026 ровно так и вышло: четыре таблицы, заведённые за месяц (следы
телефона, выученные адреса, офисы сотрудников и коды привязки устройств), не
были названы ни в одном списке, и снятие отказалось выпускать архив.

Отказ сам по себе правильный — молча увезти незнакомую таблицу нельзя, в ней
может лежать очередной секрет. Неправильно было то, что узнали об этом там, а
не здесь: таблицу заводят в одном коммите, а переезжают через месяц.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import db

ROOT = Path(__file__).resolve().parent.parent


def _policy():
    spec = importlib.util.spec_from_file_location(
        "export_for_handover", ROOT / "tools" / "export_for_handover.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fresh_tables(tmp_path, monkeypatch) -> set[str]:
    """Таблицы чистой базы — то же, что увидит инструмент переноса на сервере."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()
    with sqlite3.connect(tmp_path / "hardbot.db") as conn:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")}


def test_every_table_is_named_in_the_policy(tmp_path, monkeypatch):
    policy = _policy()
    unknown = sorted(_fresh_tables(tmp_path, monkeypatch) - policy.KNOWN)
    assert not unknown, (
        "новые таблицы не названы в политике переезда (tools/export_for_handover.py,"
        f" списки CARRY / ALWAYS_CLEAR / PEOPLE / SPECIAL): {', '.join(unknown)}.\n"
        "Пока они безымянные, перенос живой базы на другой сервер не состоится "
        "вообще — инструмент остановится на первом же снятии копии.\n"
        "Решать по одному вопросу: это сделанная работа (CARRY), дверь или "
        "секрет этой установки (ALWAYS_CLEAR), люди и их привязки (PEOPLE) или "
        "смесь, которую чистят построчно (SPECIAL)."
    )


def test_policy_has_no_ghost_tables(tmp_path, monkeypatch):
    """Обратная сторона: таблицы больше нет, а строка в списке осталась и
    делает вид, что решение по ней принято. Дальше её копируют в новый список
    «по образцу», и политика тихо расходится с базой."""
    policy = _policy()
    ghosts = sorted(policy.KNOWN - _fresh_tables(tmp_path, monkeypatch))
    assert not ghosts, (
        "в политике переезда названы таблицы, которых в базе нет: "
        f"{', '.join(ghosts)}. Удалите строки или верните таблицы."
    )


def test_enrollment_codes_never_travel_and_are_checked_after_cleanup():
    """Неиспользованный код привязки — дверь, которую ещё не открывали: он
    остаётся годным и на новом сервере. Мало вычистить — надо ещё и проверить,
    что вычистилось, иначе «очищено» остаётся обещанием."""
    policy = _policy()
    assert "admin_enroll_codes" in policy.ALWAYS_CLEAR
    assert "admin_enroll_codes" not in policy.CARRY | policy.PEOPLE | policy.SPECIAL
    assert any("admin_enroll_codes" in sql for _label, sql in policy.POSTCHECKS)
