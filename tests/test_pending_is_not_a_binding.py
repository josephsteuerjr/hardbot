"""Заявка на привязку — не привязка, и в списках выглядеть ею не должна.

04.08.2026: в сводке у Акназарова показалось «два устройства», второе — аккаунт
@funkeyn с именем «Someone». По списку это читалось как выданный посторонему
доступ к 312 договорам с настоящими ПД.

На деле правило «одно устройство» отработало: строка была заявкой с approved=0,
доступа не давала. Но список показывал заявку и привязку одинаково, и отличить
их было нельзя.

Ложная тревога здесь дороже пропущенной: владелец бежит отзывать доступ, которого
нет, а в следующий раз перестаёт верить списку.
"""

import db

UID_OK, UID_PENDING = 970001, 970002


def _clean():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM agent_bind WHERE telegram_id IN (?,?)",
                     (UID_OK, UID_PENDING))
        conn.commit()


def test_pending_request_is_not_listed_as_a_binding():
    _clean()
    db.bind_agent(UID_OK, "Тестов Т.", approved=True)
    db.bind_agent(UID_PENDING, "Тестов Т.", approved=False)

    listed = {r["telegram_id"] for r in db.list_bindings()}
    assert UID_OK in listed
    assert UID_PENDING not in listed, "заявка показана как действующая привязка"


def test_pending_request_grants_no_access():
    """Главное свойство: доступа нет, что бы ни показывал список."""
    _clean()
    db.bind_agent(UID_PENDING, "Тестов Т.", approved=False)
    assert db.get_binding(UID_PENDING) is None


def test_pending_is_visible_when_explicitly_asked():
    """Заявки не исчезают — их показывают по явному запросу."""
    _clean()
    db.bind_agent(UID_PENDING, "Тестов Т.", approved=False)
    listed = {r["telegram_id"] for r in db.list_bindings(include_pending=True)}
    assert UID_PENDING in listed


def test_approval_moves_the_row_into_the_list():
    _clean()
    db.bind_agent(UID_PENDING, "Тестов Т.", approved=False)
    assert UID_PENDING not in {r["telegram_id"] for r in db.list_bindings()}
    db.approve_binding(UID_PENDING)
    assert UID_PENDING in {r["telegram_id"] for r in db.list_bindings()}
