"""Legacy mixed activity links resolve an authorized Soft card without widening scope."""
import asyncio
import json

import pytest
from aiohttp import web as aio_web
from aiohttp.test_utils import make_mocked_request

import db
import web
from test_soft_manager_stats import world, call, DAY


def card(monkeypatch, *, employee='S1', rep='A', mode='hard', current=False,
         role='manager', team=None):
    monkeypatch.setattr(web, '_manager_gate', lambda request: (999, {'role': role}, team or ['S1']))
    request = make_mocked_request('GET', '/api/manager/person?employee='+employee+'&rep='+rep+
                                 '&date='+DAY+('&mode='+mode if mode else '')+
                                 ('&current_portfolio=1' if current else ''))
    return json.loads(asyncio.run(web._manager_person(request)).text)


@pytest.mark.parametrize('role', ['manager', 'security'])
@pytest.mark.parametrize('mode', ['hard', ''])
def test_old_activity_hint_opens_current_soft_card(world, monkeypatch, role, mode):
    data = card(monkeypatch, role=role, mode=mode)
    assert data['profile'] == 'soft'
    assert {row['contract_id'] for row in data['contracts']} == {'A', 'B'}
    assert data['employee'] == 'S1'


def test_old_activity_hint_opens_own_historical_call(world, monkeypatch):
    call()
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='S2' WHERE contract_id IN ('A','B')")
        conn.commit()
    data = card(monkeypatch)
    assert data['profile'] == 'soft'
    assert {row['contract_id'] for row in data['contracts']} == {'A', 'B'}
    assert all(db.employee_called('S1', [row['contract_id']]) for row in data['contracts'])


def test_explicit_current_portfolio_does_not_switch_profile(world, monkeypatch):
    with pytest.raises(aio_web.HTTPNotFound):
        card(monkeypatch, current=True)
    assert card(monkeypatch, current=True, mode='soft')['profile'] == 'soft'


def test_activity_fallback_cannot_open_foreign_employee_or_contract(world, monkeypatch):
    for employee, rep in [('S2', 'C'), ('S1', 'C'), ('S1', 'missing')]:
        with pytest.raises(aio_web.HTTPNotFound):
            card(monkeypatch, employee=employee, rep=rep)


def test_valid_hard_activity_keeps_hard_card(world, monkeypatch):
    data = card(monkeypatch, employee='H1', team=['H1'])
    assert data.get('profile') != 'soft'
    assert {row['contract_id'] for row in data['contracts']} == {'A'}


def test_explicit_soft_does_not_turn_into_hard_access(world, monkeypatch):
    with pytest.raises(aio_web.HTTPNotFound):
        card(monkeypatch, employee='H1', team=['H1'], mode='soft')


@pytest.mark.parametrize('mode', ['hard', 'soft', ''])
def test_activity_account_name_resolves_explicit_soft_portfolio(world, monkeypatch, mode):
    import staff_roster
    items = [{'employee': 'S1', 'products': ['hard', 'soft'],
              'product_names': {'hard': 'S1', 'soft': 'S1 SOFT'}}]
    checked = staff_roster.preview(items)
    staff_roster.apply(items, checked['sha256'], by='synthetic')
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='S1 SOFT' WHERE responsible_soft='S1'")
    data = card(monkeypatch, mode=mode, team=['S1', 'S1 SOFT'])
    assert data['profile'] == 'soft' and data['employee'] == 'S1 SOFT'
    assert {r['contract_id'] for r in data['contracts']} == {'A', 'B'}
    # Being able to name the base account cannot grant its other portfolio.
    with pytest.raises(aio_web.HTTPNotFound):
        card(monkeypatch, mode=mode, team=['S1'])


def test_activity_does_not_guess_soft_suffix(world, monkeypatch):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='S1 SOFT'")
    with pytest.raises(aio_web.HTTPNotFound):
        card(monkeypatch, team=['S1', 'S1 SOFT'])
